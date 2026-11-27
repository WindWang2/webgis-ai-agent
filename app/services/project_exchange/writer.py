"""工程包物化 writer（WP2）—— staging 目录组装 + 标准 GIS 格式 + 保真回执。

复用纪律（不写第二套读写器）：
- 矢量 → ``data_delivery.writers.PyogrioWriter``（GDAL GPKG 驱动），
  每数据集一个 writer 实例但共享 ``standard/layers.gpkg``（多图层）；
- 表格 → ``data_delivery.writers.CSVWriter``（含 schema sidecar）；
- GeoJSON 产物 → ``data_delivery.writers.GeoJSONWriter``（RFC 7946）；
- 栅格 → 字节保真拷贝（.tif/.png 原样，不重投影/不重采样）。

保真回执（fidelity）：写前读源、写后用**独立读路径**（pyogrio/rasterio/
pandas 的读 API）重读比对；任何丢弃/截断必须披露，不假称全保真。
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app.services.data_delivery import contracts as dl_contracts
from app.services.data_delivery.writers import (
    CSVWriter, GeoJSONWriter, PyogrioWriter, WriterContext,
)
from app.services.project_exchange import contracts as pc

logger = logging.getLogger(__name__)

#: 矢量/表格批大小（与 data_delivery READ_BATCH_ROWS 对齐）。
WRITE_BATCH_ROWS = 2000

#: 字段数硬帽（投影一致）。
MAX_FIELDS = dl_contracts.MAX_FIELDS


class MaterializeError(Exception):
    def __init__(self, error_code: str, reason: str) -> None:
        super().__init__(f"{error_code}: {reason}")
        self.error_code = error_code
        self.reason = reason


def _fields_from_features(features: List[Dict[str, Any]]) -> List[str]:
    """首批要素的属性键 → 稳定字段序（有界）。"""
    seen: Dict[str, None] = {}
    for f in features[:50]:
        props = f.get("properties")
        if isinstance(props, dict):
            for k in props.keys():
                if len(seen) >= MAX_FIELDS:
                    break
                seen[str(k)] = None
    return list(seen.keys())


def stream_fc_features(path: Path, batch: int = WRITE_BATCH_ROWS
                       ) -> Iterable[List[Dict[str, Any]]]:
    """流式读取 FeatureCollection 文件（ijson；不整读进内存）。"""
    import ijson

    buf: List[Dict[str, Any]] = []
    with open(path, "rb") as fh:
        for item in ijson.items(fh, "features.item"):
            if isinstance(item, dict):
                buf.append(item)
                if len(buf) >= batch:
                    yield buf
                    buf = []
    if buf:
        yield buf


def fc_features_from_payload(payload: Dict[str, Any],
                             batch: int = WRITE_BATCH_ROWS
                             ) -> Iterable[List[Dict[str, Any]]]:
    """内存 FeatureCollection → 批次。"""
    features = payload.get("features")
    if not isinstance(features, list):
        return
    for i in range(0, len(features), batch):
        yield [f for f in features[i:i + batch] if isinstance(f, dict)]


@dataclass
class DatasetFidelityInput:
    """保真比对输入（写前侧快照）。"""

    logical_id: str
    source_fields: List[str] = field(default_factory=list)
    source_count: Optional[int] = None
    source_crs: str = ""
    source_bbox: Optional[List[float]] = None
    source_geometry: str = ""
    source_sha256: str = ""       # 字节拷贝形态（栅格）的源摘要
    materialized_path: str = ""   # 包内路径
    materialized_member: Optional[str] = None  # gpkg 图层名
    kind: str = "vector"


class PackageMaterializer:
    """staging 目录组装器（一个导出任务一个实例）。"""

    def __init__(self, staging_root: Path) -> None:
        self.root = staging_root
        (self.root / "standard" / "rasters").mkdir(parents=True, exist_ok=True)
        (self.root / "standard" / "tables").mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(parents=True, exist_ok=True)
        (self.root / "maps").mkdir(parents=True, exist_ok=True)
        self._gpkg_started = False
        self._gpkg_path = self.root / pc.LAYERS_GPKG_PATH
        self._fidelity_inputs: List[DatasetFidelityInput] = []
        self._writer_versions: Dict[str, str] = {}

    # ── 矢量（多图层单 gpkg）───────────────────────────────────────────
    def write_vector_dataset(
        self, logical_id: str, features_iter: Iterable[List[Dict[str, Any]]],
        crs: str, geometry_type: str = "Unknown",
        *, cancel_check: Optional[Callable[[], None]] = None,
    ) -> Tuple[int, List[str]]:
        """把一个数据集写入 layers.gpkg 的一个图层。

        Returns (written_rows, fields)。零要素数据集走
        ``write_empty_vector_layer``（GPKG 支持空图层，诚实保留）。
        """
        ctx = WriterContext(
            fmt="gpkg", out_dir=self.root / "standard", base_filename="layers",
            fields=[], crs_out=crs or None, geometry_grouping="single",
            coordinate_precision=None, is_table=False,
        )
        writer = PyogrioWriter(ctx, driver="GPKG", layer_name=logical_id,
                               geometry_type=geometry_type or "Unknown")
        total = 0
        fields: List[str] = []
        first = True
        for batch in features_iter:
            if cancel_check is not None:
                cancel_check()
            if first:
                fields = _fields_from_features(batch)
                ctx.fields = fields or ["id"]
                writer.ctx.fields = ctx.fields
                first = False
            writer.write_batch(batch, layer=logical_id)
            total += len(batch)
            if total > pc.MAX_FEATURES_PER_DATASET:
                writer.abort()
                raise MaterializeError(
                    pc.ERR_SIZE_BUDGET,
                    f"数据集 {logical_id} 超过单数据集要素帽 "
                    f"{pc.MAX_FEATURES_PER_DATASET}")
        self._gpkg_started = True
        self._fidelity_inputs.append(DatasetFidelityInput(
            logical_id=logical_id, source_fields=fields,
            source_count=total, source_crs=crs,
            source_geometry=geometry_type,
            materialized_path=pc.LAYERS_GPKG_PATH,
            materialized_member=logical_id, kind="vector",
        ))
        return total, fields

    def write_empty_vector_layer(self, logical_id: str, crs: str,
                                 geometry_type: str = "Unknown") -> None:
        """零要素数据集 → 空图层（诚实保留，不静默丢弃）。"""
        import geopandas as gpd
        import pandas as pd
        import pyogrio

        df = pd.DataFrame({ "id": pd.Series(dtype="object") })
        gs = gpd.GeoSeries([], crs=crs or None)
        gdf = gpd.GeoDataFrame(df, geometry=gs, crs=crs or None)
        pyogrio.write_dataframe(
            gdf, str(self._gpkg_path), driver="GPKG", layer=logical_id,
            geometry_type=geometry_type or "Unknown",
            append=self._gpkg_started)
        self._gpkg_started = True
        self._fidelity_inputs.append(DatasetFidelityInput(
            logical_id=logical_id, source_fields=["id"], source_count=0,
            source_crs=crs, source_geometry=geometry_type,
            materialized_path=pc.LAYERS_GPKG_PATH,
            materialized_member=logical_id, kind="vector",
        ))

    # ── 栅格（字节保真拷贝）────────────────────────────────────────────
    def write_raster_copy(self, logical_id: str, src: Path, ext: str,
                          *, crs: str = "") -> None:
        target = self.root / "standard" / "rasters" / f"{logical_id}{ext}"
        shutil.copyfile(src, target)
        digest = pc.sha256_file(target)
        self._fidelity_inputs.append(DatasetFidelityInput(
            logical_id=logical_id, kind="raster_copy",
            source_sha256=pc.sha256_file(src), source_crs=crs,
            materialized_path=str(target.relative_to(self.root)),
            source_count=target.stat().st_size,
        ))
        del digest  # 保真在 verify_materialized 里统一读回比对

    # ── 表格 ────────────────────────────────────────────────────────────
    def write_table(self, logical_id: str, rows: List[Dict[str, Any]],
                    *, crs: str = "",
                    cancel_check: Optional[Callable[[], None]] = None) -> None:
        ctx = WriterContext(
            fmt="csv", out_dir=self.root / "standard" / "tables",
            base_filename=logical_id, fields=_fields_from_features(rows),
            spreadsheet_safe=False, is_table=True,
        )
        writer = CSVWriter(ctx)
        total = 0
        for i in range(0, len(rows), WRITE_BATCH_ROWS):
            if cancel_check is not None:
                cancel_check()
            batch = rows[i:i + WRITE_BATCH_ROWS]
            writer.write_batch(batch)
            total += len(batch)
        writer.close()
        self._fidelity_inputs.append(DatasetFidelityInput(
            logical_id=logical_id, kind="table",
            source_fields=ctx.fields, source_count=total, source_crs=crs,
            materialized_path=f"{pc.TABLES_DIR}{logical_id}.csv",
        ))

    # ── 产物 ────────────────────────────────────────────────────────────
    def write_artifact_payload(self, logical_id: str, ext: str,
                               data: Optional[Dict[str, Any]] = None,
                               src_path: Optional[Path] = None) -> str:
        """产物载荷 → artifacts/<lid>.<ext>（fc 手写 / 字节拷贝）。"""
        target = self.root / "artifacts" / f"{logical_id}{ext}"
        if src_path is not None:
            shutil.copyfile(src_path, target)
            return str(target.relative_to(self.root))
        if data is not None:
            blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"),
                              default=str)
            target.write_text(blob, encoding="utf-8", newline="\n")
            return str(target.relative_to(self.root))
        raise MaterializeError(pc.ERR_WRITE_FAILED,
                               f"产物 {logical_id} 无可写载荷")

    # ── 地图 ────────────────────────────────────────────────────────────
    def write_map(self, logical_id: str, mapspec: Dict[str, Any]) -> str:
        path = self.root / "maps" / f"{logical_id}.mapspec.json"
        path.write_text(
            json.dumps(mapspec, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8", newline="\n")
        return f"{pc.MAPS_DIR}{logical_id}.mapspec.json"

    def write_disclosure_md(self, lines: List[str]) -> str:
        path = self.root / pc.DISCLOSURE_PATH
        path.write_text("\n".join(lines) + "\n", encoding="utf-8",
                        newline="\n")
        return pc.DISCLOSURE_PATH

    # ── 保真读回（写后独立读路径核对）───────────────────────────────────
    def verify_materialized(self) -> Tuple[List[Any], Dict[str, str]]:
        """逐数据集独立读回比对 → FidelityDatasetEntry 列表 + writer 版本。"""
        entries: List[Any] = []
        for inp in self._fidelity_inputs:
            if inp.kind == "vector":
                entries.append(self._verify_vector(inp))
            elif inp.kind == "raster_copy":
                entries.append(self._verify_raster_copy(inp))
            elif inp.kind == "table":
                entries.append(self._verify_table(inp))
        self._writer_versions = dl_contracts.writer_versions()
        return entries, self._writer_versions

    def _verify_vector(self, inp: DatasetFidelityInput) -> Any:
        import pyogrio

        detail: Dict[str, Any] = {}
        try:
            frame = pyogrio.read_dataframe(
                str(self._gpkg_path), layer=inp.materialized_member)
        except Exception as e:  # noqa: BLE001 — 读回失败 = verify 失败（诚实）
            return pc.FidelityDatasetEntry(
                logical_id=inp.logical_id, verdict=pc.FIDELITY_PARTIAL,
                details={"readback_error": str(e)[:300]})
        count_ok = inp.source_count == len(frame)
        fields_after = [c for c in frame.columns if c != frame.geometry.name]
        fields_ok = (fields_after == inp.source_fields) if inp.source_fields else None
        crs_after = (str(frame.crs) if frame.crs is not None else "")
        crs_ok = _crs_match(inp.source_crs, crs_after)
        bbox_after = None
        try:
            total = frame.total_bounds
            bbox_after = [float(x) for x in total] if len(total) == 4 else None
        except Exception:  # noqa: BLE001
            bbox_after = None
        bbox_ok = None
        if inp.source_bbox and bbox_after:
            bbox_ok = _bbox_close(inp.source_bbox, bbox_after)
        verdict = pc.FIDELITY_EXACT
        if not count_ok or fields_ok is False or crs_ok is False:
            verdict = pc.FIDELITY_PARTIAL
        detail.update({
            "count": {"source": inp.source_count, "materialized": len(frame)},
            "fields": {"source": inp.source_fields[:32],
                       "materialized": fields_after[:32]},
            "crs": {"source": inp.source_crs, "materialized": crs_after},
        })
        return pc.FidelityDatasetEntry(
            logical_id=inp.logical_id,
            fields_verified=fields_ok, crs_match=crs_ok,
            feature_count_match=count_ok, bbox_match=bbox_ok,
            verdict=verdict, details=detail)

    def _verify_raster_copy(self, inp: DatasetFidelityInput) -> Any:
        target = self.root / inp.materialized_path
        digest = pc.sha256_file(target)
        byte_identical = (digest == inp.source_sha256)
        detail: Dict[str, Any] = {"byte_identical": byte_identical,
                                  "sha256": digest}
        crs_after = ""
        shape_after = None
        try:
            import rasterio

            with rasterio.open(str(target)) as src:
                crs_after = str(src.crs) if src.crs else ""
                shape_after = [src.height, src.width]
                detail["nodata"] = src.nodata
        except Exception as e:  # noqa: BLE001 — PNG 等非 GDAL 栅格按字节等价判定
            detail["rasterio_note"] = str(e)[:200]
        crs_ok = _crs_match(inp.source_crs, crs_after) if (inp.source_crs and
                                                           crs_after) else None
        verdict = pc.FIDELITY_EXACT if byte_identical else pc.FIDELITY_PARTIAL
        return pc.FidelityDatasetEntry(
            logical_id=inp.logical_id, crs_match=crs_ok,
            verdict=verdict, details=detail)

    def _verify_table(self, inp: DatasetFidelityInput) -> Any:
        import pandas as pd

        target = self.root / inp.materialized_path
        frame = pd.read_csv(str(target))
        count_ok = inp.source_count == len(frame)
        fields_after = [str(c) for c in frame.columns
                        if c != "geometry_wkt"]
        fields_ok = (fields_after == inp.source_fields) if inp.source_fields else None
        verdict = (pc.FIDELITY_EXACT if count_ok and fields_ok is not False
                   else pc.FIDELITY_PARTIAL)
        return pc.FidelityDatasetEntry(
            logical_id=inp.logical_id, fields_verified=fields_ok,
            feature_count_match=count_ok, verdict=verdict,
            details={"count": {"source": inp.source_count,
                               "materialized": len(frame)},
                     "fields": {"source": inp.source_fields[:32],
                                "materialized": fields_after[:32]}})

    # ── 台账与压缩 ──────────────────────────────────────────────────────
    def inventory(self) -> List[pc.PackageFileEntry]:
        entries: List[pc.PackageFileEntry] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.root))
            size = path.stat().st_size
            if size > pc.MAX_ENTRY_BYTES:
                raise MaterializeError(
                    pc.ERR_SIZE_BUDGET, f"包内文件超限: {rel}")
            entries.append(pc.PackageFileEntry(
                path=rel, sha256=pc.sha256_file(path), bytes=size,
                mime=_guess_mime(rel)))
        if len(entries) > pc.MAX_PACKAGE_ENTRIES:
            raise MaterializeError(
                pc.ERR_ENTRY_LIMIT,
                f"包内文件数 {len(entries)} 超过 {pc.MAX_PACKAGE_ENTRIES}")
        return entries

    def total_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())


def _crs_match(source: str, materialized: str) -> Optional[bool]:
    """CRS 语义比对（EPSG:4326 与 EPSG:4326 的 WKT 展开形态等价）。"""
    if not source and not materialized:
        return None
    s = (source or "").strip().upper()
    m = (materialized or "").strip().upper()
    if not s or not m:
        return None
    if s == m:
        return True
    import re

    pat = re.compile(r"EPSG[:\s]*(\d+)", re.IGNORECASE)
    sm = pat.search(s)
    mm = pat.search(m)
    if sm and mm:
        return sm.group(1) == mm.group(1)
    return None


def _bbox_close(a: List[float], b: List[float], tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol * max(1.0, abs(x), abs(y))
               for x, y in zip(a, b))


def _guess_mime(rel: str) -> str:
    lower = rel.lower()
    if lower.endswith(".gpkg"):
        return dl_contracts.FORMAT_MIME["gpkg"]
    if lower.endswith(".geojson"):
        return dl_contracts.FORMAT_MIME["geojson"]
    if lower.endswith(".csv"):
        return dl_contracts.FORMAT_MIME["csv"]
    if lower.endswith(".parquet"):
        return dl_contracts.FORMAT_MIME["geoparquet"]
    if lower.endswith((".tif", ".tiff")):
        return "image/tiff; application=geotiff; profile=cloud-optimized"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".md"):
        return "text/markdown; charset=utf-8"
    return "application/octet-stream"
