"""Data Delivery Writers — 真实矢量/表格写入器（G18 WP05/WP06）。

所有 writer 批式消费已变换的 Feature/行批次（≤ READ_BATCH_ROWS），输出
真实文件并返回成员清单与实际损失应用记录。写路径全部经真实驱动：

- **GeoJSON**：流式手写（RFC 7946；坐标精度可控；顶层 ``id`` 保留）；
- **GeoPackage / FlatGeobuf / Shapefile**：pyogrio（GDAL）批量
  ``write_dataframe(append=...)``；Shapefile 写成员 .shp 后 ZIP 归档
  （.cpg/.prj 组成文件齐全）；
- **GeoParquet**：pyarrow ParquetWriter + 批 RecordBatch（geo metadata
  保留 CRS；无 FC 中间体）；
- **CSV**：csv 模块流式（字段顺序稳定、null/编码约定可复现，
  schema sidecar 弥补无类型）。

纪律：writer 之间不共享单例状态；每批可检查取消；输出字节受硬帽约束
（超限 → typed WRITE/OUTPUT 错误并清理 staging）。
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.data_delivery import contracts as dl_contracts

logger = logging.getLogger(__name__)


class WriterError(Exception):
    """typed 写入失败（error_code + reason）。"""

    def __init__(self, error_code: str, reason: str):
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass
class WriterContext:
    """writer 需要的规格上下文（由 transform/service 构造）。"""

    fmt: str
    out_dir: Path
    base_filename: str
    fields: List[str]
    """输出字段顺序（已应用投影/重命名后的目标名）。"""
    field_map: Optional[Dict[str, str]] = None
    crs_out: Optional[str] = None
    geometry_grouping: str = "single"
    spreadsheet_safe: bool = False
    include_geometry_wkt: bool = False
    coordinate_precision: Optional[int] = None
    is_table: bool = False


@dataclass
class WriterResult:
    members: List[Dict[str, Any]] = field(default_factory=list)
    rows: int = 0
    losses_applied: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _member(path: Path, role: str, rows: Optional[int] = None,
            rel: Optional[str] = None) -> Dict[str, Any]:
    return {
        "path": rel or path.name,
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "rows": rows,
    }


# ── GeoJSON 流式 writer（RFC 7946）────────────────────────────────────────

def _round_coords(node: Any, precision: Optional[int]) -> Any:
    if precision is None:
        return node
    if isinstance(node, list) and node and isinstance(node[0], (int, float)):
        return [round(float(x), precision) for x in node]
    if isinstance(node, list):
        return [_round_coords(n, precision) for n in node]
    return node


class GeoJSONWriter:
    """FeatureCollection 流式 writer —— O(batch) 内存，顶层 id 保留。"""

    def __init__(self, ctx: WriterContext):
        self.ctx = ctx
        self.path = ctx.out_dir / f"{ctx.base_filename}.geojson"
        self._fh = open(self.path, "w", encoding="utf-8", newline="\n")
        self._fh.write('{"type":"FeatureCollection","features":[')
        self._first = True
        self.rows = 0

    def write_batch(self, features: List[Dict[str, Any]]) -> None:
        buf = io.StringIO()
        for f in features:
            if not self._first:
                buf.write(",")
            self._first = False
            fid = f.get("id")
            buf.write('{"type":"Feature"')
            if fid is not None:
                buf.write(f',"id":{json.dumps(fid, ensure_ascii=False)}')
            geom = f.get("geometry")
            if isinstance(geom, dict) and geom:
                g = dict(geom)
                g["coordinates"] = _round_coords(g.get("coordinates"),
                                                 self.ctx.coordinate_precision)
                if isinstance(g.get("geometries"), list):
                    g["geometries"] = [
                        {**gg, "coordinates": _round_coords(gg.get("coordinates"),
                                                            self.ctx.coordinate_precision)}
                        for gg in g["geometries"]
                    ]
                buf.write(f',"geometry":{json.dumps(g, ensure_ascii=False, separators=(",", ":"))}')
            else:
                buf.write(',"geometry":null')
            props = json.dumps(f.get("properties") or {},
                               ensure_ascii=False, separators=(",", ":"),
                               default=str)
            buf.write(f',"properties":{props}}}')
            self.rows += 1
        self._fh.write(buf.getvalue())

    def close(self) -> WriterResult:
        self._fh.write("]}")
        self._fh.close()
        return WriterResult(
            members=[_member(self.path, "data", self.rows)],
            rows=self.rows,
        )


# ── pyogrio 批量 writer（GPKG / FlatGeobuf / Shapefile 成员）──────────────

class PyogrioWriter:
    """geopandas GeoDataFrame 批 → pyogrio（GDAL）真实驱动写入。

    append 批次纪律：首批建层（带 CRS/几何类型），后续 ``append=True``。
    Shapefile：每层一个 ``<layer>.shp``（GDAL 生成 .shx/.dbf/.prj/.cpg），
    close 时归档为单一 ZIP。
    """

    def __init__(self, ctx: WriterContext, driver: str, layer_name: str,
                 geometry_type: str = "Unknown"):
        import pyogrio  # noqa: F401 — 早期失败：依赖缺失立即暴露

        self.ctx = ctx
        self.driver = driver
        self.layer_name = layer_name
        self.geometry_type = geometry_type
        self.rows = 0
        self._layer_files: Dict[str, Path] = {}
        self._started: Dict[str, bool] = {}
        if driver == "ESRI Shapefile":
            self.shape_dir = ctx.out_dir / "_shp_members"
            self.shape_dir.mkdir(parents=True, exist_ok=True)
            self.main_path: Optional[Path] = None
        else:
            ext = {"GPKG": ".gpkg", "FlatGeobuf": ".fgb"}[driver]
            self.main_path = ctx.out_dir / f"{ctx.base_filename}{ext}"
            self.shape_dir = None

    def _layer_target(self, layer: str) -> Path:
        if self.driver == "ESRI Shapefile":
            return self.shape_dir / f"{layer}.shp"
        return self.main_path  # type: ignore[return-value]

    def write_batch(self, features: List[Dict[str, Any]],
                    layer: Optional[str] = None) -> None:
        import pyogrio

        layer = layer or self.layer_name
        frame = self._to_frame(features)
        if frame is None or len(frame) == 0:
            return
        target = self._layer_target(layer)
        first = not self._started.get(layer, False)
        kwargs: Dict[str, Any] = {"driver": self.driver, "append": not first}
        if self.driver == "GPKG":
            kwargs["layer"] = layer
            if first and self.geometry_type:
                kwargs["geometry_type"] = self.geometry_type
        try:
            pyogrio.write_dataframe(frame, str(target), **kwargs)
        except Exception as e:
            raise WriterError(
                dl_contracts.ERR_WRITE_FAILED,
                f"{self.driver} 写入失败: {e}") from e
        self._started[layer] = True
        self._layer_files[layer] = target
        self.rows += len(frame)

    def _to_frame(self, features: List[Dict[str, Any]]):
        import geopandas as gpd
        import pandas as pd
        from shapely.geometry import shape

        geoms, prop_rows = [], []
        for f in features:
            g = f.get("geometry")
            if isinstance(g, dict) and g.get("type"):
                try:
                    geoms.append(shape(g))
                except Exception as e:
                    raise WriterError(
                        dl_contracts.ERR_WRITE_FAILED,
                        f"几何无法解析为地理对象: {e}") from e
            else:
                geoms.append(None)
            prop_rows.append(f.get("properties") or {})
        if not prop_rows:
            return None
        data: Dict[str, List[Any]] = {k: [] for k in self.ctx.fields}
        for props in prop_rows:
            for k in self.ctx.fields:
                data[k].append(props.get(k))
        df = pd.DataFrame(data, columns=self.ctx.fields)
        if self.ctx.is_table:
            return df
        gs = gpd.GeoSeries(geoms, crs=self.ctx.crs_out)
        return gpd.GeoDataFrame(df, geometry=gs, crs=self.ctx.crs_out)

    def close(self) -> WriterResult:
        losses: List[str] = []
        if self.driver == "ESRI Shapefile":
            zip_path = self.ctx.out_dir / f"{self.ctx.base_filename}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                # 成员文件名天然带 layer 前缀（<layer>.shp/.dbf/…）——
                # 共享目录一次遍历即唯一成员集（多批/多层不重复入包）。
                for member_file in sorted(self.shape_dir.iterdir()):
                    if member_file.suffix.lower() in (
                            ".shp", ".shx", ".dbf", ".prj", ".cpg"):
                        zf.write(member_file, member_file.name)
            members = [_member(zip_path, "data", self.rows)]
            if self.ctx.field_map:
                losses.append(dl_contracts.LOSS_FIELD_NAME_MAPPED)
        else:
            members = [_member(self.main_path, "data", self.rows)]  # type: ignore[arg-type]
        return WriterResult(members=members, rows=self.rows, losses_applied=losses)


class ShapefileGroupWriter(PyogrioWriter):
    """混合几何分组 writer：每类型一个成员层（计数相加守恒）。"""

    _PREFIX = {"Point": "points", "MultiPoint": "multipoints",
               "LineString": "lines", "MultiLineString": "multilines",
               "Polygon": "polygons", "MultiPolygon": "multipolygons"}

    def write_batch(self, features: List[Dict[str, Any]],
                    layer: Optional[str] = None) -> None:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for f in features:
            g = f.get("geometry")
            gtype = (g or {}).get("type") if isinstance(g, dict) else None
            groups.setdefault(self._PREFIX.get(gtype or "", "other"), []).append(f)
        for gname, feats in sorted(groups.items()):
            # 行数由父类累计（write_batch 内 rows += len(frame)）
            super().write_batch(feats, layer=gname)

    def close(self) -> WriterResult:
        res = super().close()
        res.extra["layers"] = sorted(self._layer_files)
        return res


# ── GeoParquet 批量 writer（pyarrow ParquetWriter；无 FC 中间体）──────────

class GeoParquetWriter:
    """批 RecordBatch → Parquet（geo metadata 首批确定 schema）。"""

    def __init__(self, ctx: WriterContext, crs_source: Optional[str]):
        self.ctx = ctx
        self.path = ctx.out_dir / f"{ctx.base_filename}.parquet"
        self._crs = crs_source
        self._writer = None
        self.rows = 0
        self._schema = None

    def _arrow_schema_for(self, sample_features: List[Dict[str, Any]]):
        import pyarrow as pa

        fields = []
        for name in self.ctx.fields:
            values = [(f.get("properties") or {}).get(name) for f in sample_features]
            present = [v for v in values if v is not None]
            if any(isinstance(v, (dict, list)) for v in present):
                t = pa.string()
            elif present and all(isinstance(v, bool) for v in present):
                t = pa.bool_()
            elif present and all(isinstance(v, int) and not isinstance(v, bool)
                                 for v in present):
                t = pa.int64()
            elif present and all(isinstance(v, (int, float)) for v in present):
                t = pa.float64()
            else:
                t = pa.string()
            fields.append(pa.field(name, t))
        fields.append(pa.field("geometry", pa.binary()))
        geo = {
            "version": "1.0.0",
            "primary_column": "geometry",
            "columns": {"geometry": {"encoding": "WKB", "crs": self._crs or None}},
        }
        return pa.schema(fields, metadata={"geo": json.dumps(geo)})

    def write_batch(self, features: List[Dict[str, Any]]) -> None:
        import json as _json

        import pyarrow as pa
        import pyarrow.parquet as pq
        from shapely import from_geojson, to_wkb

        if not features:
            return
        if self._writer is None:
            self._schema = self._arrow_schema_for(features)
            self._writer = pq.ParquetWriter(str(self.path), self._schema,
                                            compression="zstd")

        def _coerce(v: Any, arrow_type: "pa.DataType") -> Any:
            # 防御性：schema 定型为 string 的复杂值在此 JSON 化（服务层
            # transform 已先行编码；此处保证批间一致性，绝不静默换类型）。
            if isinstance(v, (dict, list)) and pa.types.is_string(arrow_type):
                return _json.dumps(v, ensure_ascii=False, default=str)
            if isinstance(v, bool) and not pa.types.is_boolean(arrow_type):
                return int(v)
            return v

        try:
            arrays = []
            for name in self.ctx.fields:
                typ = self._schema.field(name).type
                arrays.append(pa.array(
                    [_coerce((f.get("properties") or {}).get(name), typ)
                     for f in features],
                    type=typ))
            wkb = []
            for f in features:
                g = f.get("geometry")
                if isinstance(g, dict) and g.get("type"):
                    try:
                        shape_geom = from_geojson(_json.dumps(g))
                        wkb.append(to_wkb(shape_geom) if shape_geom is not None else None)
                    except Exception as e:
                        raise WriterError(
                            dl_contracts.ERR_WRITE_FAILED,
                            f"GeoParquet WKB 编码失败: {e}") from e
                else:
                    wkb.append(None)
            arrays.append(pa.array(wkb, type=pa.binary()))
            batch_table = pa.Table.from_arrays(arrays, schema=self._schema)
            self._writer.write_table(batch_table)
        except Exception:
            if self._writer is not None:
                try:
                    self._writer.close()
                finally:
                    self._writer = None
            raise
        self.rows += len(features)

    def close(self) -> WriterResult:
        if self._writer is None:
            raise WriterError(
                dl_contracts.ERR_WRITE_FAILED,
                "空数据集不能交付 GeoParquet（拒绝写空文件冒充成功）")
        self._writer.close()
        return WriterResult(
            members=[_member(self.path, "data", self.rows)], rows=self.rows)


# ── CSV writer（含 schema sidecar）────────────────────────────────────────

def _csv_cell(v: Any, spreadsheet_safe: bool, counter: List[int],
              nan_counter: Optional[List[int]] = None) -> str:
    from app.services.data_delivery.loss import sanitize_csv_cell

    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            if nan_counter is not None:
                nan_counter[0] += 1  # 如实计数 → 回执披露
            return ""  # CSV 无法表达 NaN/Inf → 空值（sidecar 约定）
        return repr(v)
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    elif isinstance(v, int):
        return str(v)
    else:
        s = str(v)
    if spreadsheet_safe:
        s, changed = sanitize_csv_cell(s)
        if changed:
            counter[0] += 1
    return s


class CSVWriter:
    """属性表流式 CSV + schema sidecar（字段顺序稳定/编码约定可复现）。

    ``geometry_column`` 开启时追加 ``geometry_wkt`` 列（目标坐标系）。
    """

    def __init__(self, ctx: WriterContext, fields_meta: Optional[List[Dict[str, Any]]] = None,
                 geometry_column: bool = False):
        self.ctx = ctx
        self.geometry_column = geometry_column and not ctx.is_table
        self.path = ctx.out_dir / f"{ctx.base_filename}.csv"
        self._fh = open(self.path, "w", encoding="utf-8", newline="")
        self._writer = csv.writer(self._fh, lineterminator="\n")
        self._writer.writerow(list(ctx.fields) + (["geometry_wkt"] if self.geometry_column else []))
        self.rows = 0
        self._sanitized = [0]
        self._nan_inf = [0]
        self._fields_meta = fields_meta or []

    def write_batch(self, rows: List[Dict[str, Any]]) -> None:
        from shapely.geometry import shape

        out_rows = []
        for row in rows:
            props = row.get("properties") if isinstance(row, dict) and "properties" in row else row
            vals = [_csv_cell((props or {}).get(k), self.ctx.spreadsheet_safe,
                              self._sanitized, self._nan_inf)
                    for k in self.ctx.fields]
            if self.geometry_column:
                g = row.get("geometry") if isinstance(row, dict) else None
                wkt = ""
                if isinstance(g, dict) and g.get("type"):
                    try:
                        wkt = shape(g).wkt
                    except Exception:
                        wkt = ""
                vals.append(wkt)
            out_rows.append(vals)
        self._writer.writerows(out_rows)
        self.rows += len(out_rows)

    def close(self) -> WriterResult:
        self._fh.close()
        losses = []
        if self._sanitized[0]:
            losses.append(dl_contracts.LOSS_FORMULA_PREFIX_SANITIZED)
        members = [_member(self.path, "data", self.rows)]
        sidecar = {
            "format": "csv",
            "fields": self._fields_meta or [{"name": n} for n in self.ctx.fields],
            "geometry_column": "geometry_wkt" if self.geometry_column else None,
            "encoding": "utf-8",
            "delimiter": ",",
            "null_representation": "empty string",
            "boolean_representation": "true/false",
            "nan_inf_representation": "empty string (see null_representation)",
            "complex_value_encoding": "json string",
            "rows": self.rows,
            "spreadsheet_safe": self.ctx.spreadsheet_safe,
            "sanitized_cells": self._sanitized[0],
            "nan_inf_to_empty": self._nan_inf[0],
            "crs": self.ctx.crs_out,
        }
        sidecar_path = self.ctx.out_dir / f"{self.ctx.base_filename}.schema.json"
        sidecar_path.write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
        members.append(_member(sidecar_path, "schema"))
        result = WriterResult(members=members, rows=self.rows, losses_applied=losses)
        result.extra["sanitized_cells"] = self._sanitized[0]
        result.extra["nan_inf_to_empty"] = self._nan_inf[0]
        return result
