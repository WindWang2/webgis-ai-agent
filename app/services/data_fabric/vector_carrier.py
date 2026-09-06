"""GeoArrow/Arrow 族向量搬运载体（ADR-0101 D8，V4 §16）。

目标：大数据量向量在 Data Plane 内部缝隙（scan → filter → aggregate →
materialize）之间以 Arrow/GeoArrow 语义传输，避免巨型 GeoJSON 序列化；
schema/null/CRS 元数据保留，分块传输，可与 GeoParquet 互操作。

边界（红线）：
- **可选依赖**：pyarrow 未声明为仓库依赖 —— 导入可用才启用；不可用时
  一切入口抛类型化 ``VectorCarrierUnavailable``（诚实降级，绝不假装）；
- **内部载体**：Arrow 载荷绝不进入 tool/LLM 上下文（tool 层永远只看到
  GeoJSON/摘要/ArtifactRef）；大结果仍按既有 feature/byte 上界约束；
- 几何编码采用 GeoArrow 规范的 **WKB 扩展**（schema 级 ``geo`` 元数据
  携带 encoding/CRS）—— 与 GeoParquet 1.1 同族，可零拷贝互转。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.services.data_fabric.errors import DataFabricError

logger = logging.getLogger(__name__)

#: 载体命名空间（schema 元数据版本；编码语义变化时 bump）。
GEOARROW_META_VERSION = "1.0.0"


class VectorCarrierUnavailable(DataFabricError):
    code = "VECTOR_CARRIER_UNAVAILABLE"


def arrow_available() -> bool:
    """pyarrow 可用性探测（进程内缓存一次的诚实探测）。"""
    global _ARROW_OK
    try:
        import pyarrow  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 - 缺依赖/损坏 = 不可用
        return False


_ARROW_OK: Optional[bool] = None


def _require_arrow() -> Any:
    import pyarrow  # noqa: F401

    return pyarrow


def _require_pa() -> Tuple[Any, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq  # noqa: F401

    return pa, pq


def _geo_metadata(crs: Optional[str]) -> Dict[str, Any]:
    """GeoArrow/GeoParquet 同族的 ``geo`` schema 元数据（WKB 编码）。"""
    meta: Dict[str, Any] = {
        "version": GEOARROW_META_VERSION,
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "wkb",
                "metadata": {},
            }
        },
    }
    if crs:
        meta["columns"]["geometry"]["crs"] = crs
    return meta


def _features_to_columns(features: List[Dict[str, Any]]) -> Tuple[List[str], Dict[str, List[Any]], List[bytes]]:
    """列式拆解：属性列（稀疏 null 保留）+ geometry WKB 列表。"""
    order: List[str] = []
    for feat in features:
        for k in (feat.get("properties") or {}):
            if k not in order:
                order.append(k)
    columns: Dict[str, List[Any]] = {k: [] for k in order}
    wkb_list: List[bytes] = []
    for feat in features:
        props = feat.get("properties") or {}
        for k in order:
            columns[k].append(props.get(k))
        wkb_list.append(_geometry_to_wkb(feat.get("geometry")))
    return order, columns, wkb_list


def _geometry_to_wkb(geom: Optional[Dict[str, Any]]) -> bytes:
    if not geom:
        return b""
    try:
        import shapely

        shape = shapely.from_geojson(json.dumps(geom))
        import shapely.io

        return shapely.to_wkb(shape)
    except Exception:
        return b""


def _wkb_to_geometry(wkb: bytes) -> Optional[Dict[str, Any]]:
    if not wkb:
        return None
    try:
        import shapely

        shape = shapely.from_wkb(bytes(wkb))
        if shape.is_empty:
            return None
        return json.loads(shapely.to_geojson(shape))
    except Exception:
        return None


def features_to_arrow(
    features: List[Dict[str, Any]], *, crs: Optional[str] = None
) -> Any:
    """GeoJSON features → pyarrow.Table（geo WKB 元数据；schema/null 保留）。"""
    if not arrow_available():
        raise VectorCarrierUnavailable(
            "GeoArrow carrier requires the optional 'pyarrow' dependency; "
            "fall back to feature payloads or enable the carrier",
        )
    pa, _ = _require_pa()
    order, columns, wkb_list = _features_to_columns(features)
    arrays = [pa.array(columns[k], type=pa.string() if _mostly_str(columns[k]) else None)
              for k in order]
    fields = []
    for k, arr in zip(order, arrays):
        fields.append(pa.field(k, arr.type))
    geometry_array = pa.array(wkb_list, type=pa.binary())
    table = pa.Table.from_arrays(
        [*arrays, geometry_array],
        schema=pa.schema([*fields, pa.field("geometry", pa.binary())],
                         metadata={"geo": json.dumps(_geo_metadata(crs))}),
    )
    return table


def _mostly_str(values: List[Any]) -> bool:
    present = [v for v in values if v is not None]
    if not present:
        return True
    return sum(1 for v in present if isinstance(v, str)) >= len(present) / 2


def arrow_to_features(table: Any) -> List[Dict[str, Any]]:
    """pyarrow.Table（本载体形状）→ GeoJSON features（CRS 元数据在 schema）。"""
    pa, _ = _require_pa()
    names = table.column_names
    prop_names = [n for n in names if n != "geometry"]
    out: List[Dict[str, Any]] = []
    geom_col = table.column("geometry").to_pylist() if "geometry" in names else [b""] * table.num_rows
    pydict = {n: table.column(n).to_pylist() for n in prop_names}
    for i in range(table.num_rows):
        # schema 列集合即属性真值：null 保留为显式 None（不丢键）。
        props = {n: pydict[n][i] for n in prop_names}
        out.append({
            "type": "Feature",
            "geometry": _wkb_to_geometry(geom_col[i]),
            "properties": props,
        })
    return out


def iter_arrow_chunks(
    features: List[Dict[str, Any]], *, crs: Optional[str] = None, chunk_size: int = 4096
) -> Iterator[Any]:
    """分块传输：GeoJSON features → RecordBatch 迭代（零整表物化）。"""
    table = features_to_arrow(features, crs=crs)
    for batch in table.to_batches(max_chunksize=max(1, chunk_size)):
        yield batch


def table_to_geoparquet(table: Any, path: str, *, compression: str = "zstd") -> None:
    """Arrow Table → GeoParquet（geo 元数据随 schema 落盘）。"""
    _, pq = _require_pa()
    pq.write_table(table, path, compression=compression)


def geoparquet_to_features(path: str) -> List[Dict[str, Any]]:
    """GeoParquet → GeoJSON features（走 pyarrow；依赖可选）。"""
    _, pq = _require_pa()
    table = pq.read_table(path)
    return arrow_to_features(table)


def table_crs(table: Any) -> Optional[str]:
    """从 schema geo 元数据读回 CRS（round-trip 保留证据）。"""
    import pyarrow  # noqa: F401

    meta = table.schema.metadata or {}
    raw = meta.get(b"geo")
    if not raw:
        return None
    try:
        geo = json.loads(raw)
        return geo.get("columns", {}).get("geometry", {}).get("crs")
    except Exception:  # noqa: BLE001
        return None
