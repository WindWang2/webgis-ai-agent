"""Topology Workbench —— 科学语义预检与度量核心（WP04）。

职责（求值器之前的统一闸）：

- **计算 CRS 选择**：联合范围 → ``crs_safety.recommend_metric_crs``（UTM/UPS
  纯计算推荐）。以下区域 typed blocked（诚实阻断，T08）：
  跨反经线（w > e 的联合 bbox）、极区（|lat| > 83.5°）、超大范围
  （经度跨度 > 30° 或纬度跨度 > 20°，局部投影度量误差不可声明）。
- **几何预检**：类型校验、坐标有限性、shapely 有效性；无效/空/null 几何
  **剔除并计数**（进入 unknown 披露），绝不 buffer(0) 洗白（T09）。
- **度量**：面面积/线长/点距在计算 CRS 平面度量；报告 CRS 与单位。
  地理坐标上的米制容差经投影换算，不做赤道固定换算。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

#: 区域支持边界（typed blocked 阈值）
MAX_EXTENT_DEGREES_LON = 30.0
MAX_EXTENT_DEGREES_LAT = 20.0
POLAR_LAT_LIMIT = 83.5

#: 单几何顶点护栏（超出 → 该要素剔除 + 披露；与 contracts 上限一致）
MAX_VERTICES = 100_000


class RegionUnsupported(Exception):
    """计算区域不支持（typed blocked）—— code ∈ {antimeridian, polar, extent}。"""

    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass
class PreparedGeometry:
    """单个要素的预检产物（原始 locator 一起携带）。"""

    index: int                      # 快照内下标（locator 回指）
    identity: Dict[str, Any]        # locator identity（wfid/natural_id/snapshot_index）
    geometry: Optional[BaseGeometry]  # None = 剔除（invalid/empty/null/oversized）
    exclude_reason: str = ""        # 剔除原因代码（invalid_geometry/empty_geometry/
                                    # null_geometry/too_complex/unsupported_type）
    geometry_type: str = ""         # shapely geom_type（Point/LineString/Polygon/…）


@dataclass
class PreparedSource:
    """一个角色快照的预检结果。"""

    role: str
    source_kind: str
    ref: str
    content_revision: Optional[int]
    fingerprint: str
    crs: Optional[str]
    features: List[PreparedGeometry] = field(default_factory=list)
    total_input: int = 0
    excluded: Dict[str, int] = field(default_factory=dict)   # reason → count
    examined: int = 0                                       # 进入关系运算的数量

    def excluded_total(self) -> int:
        return sum(self.excluded.values())


def choose_computation_crs(*bboxes: Tuple[float, float, float, float]) -> str:
    """联合范围 → 计算 CRS（EPSG 码）。不支持区域抛 RegionUnsupported。

    输入 bbox 为 WGS84 度（w,s,e,n）。语义：
    - 任一 bbox w > e（跨反经线）→ blocked antimeridian；
    - 联合纬度触及极区 → blocked polar；
    - 联合范围超出局部投影适用域 → blocked extent。
    """
    valid = [b for b in bboxes if b and len(b) == 4]
    if not valid:
        raise RegionUnsupported("extent", "缺少可用的源范围（无有效几何）")
    for w, _s, e, _n in valid:
        if w > e:
            raise RegionUnsupported(
                "antimeridian",
                "分析范围跨越反经线（w > e）—— 当前不支持该区域的平面度量",
                bbox=[w, _s, e, _n],
            )
    w_min = min(b[0] for b in valid)
    s_min = min(b[1] for b in valid)
    e_max = max(b[2] for b in valid)
    n_max = max(b[3] for b in valid)
    if s_min < -POLAR_LAT_LIMIT or n_max > POLAR_LAT_LIMIT:
        raise RegionUnsupported(
            "polar",
            f"分析范围触及极区（|lat| > {POLAR_LAT_LIMIT}°）—— 平面度量不可靠",
            bbox=[w_min, s_min, e_max, n_max],
        )
    if (e_max - w_min) > MAX_EXTENT_DEGREES_LON or (n_max - s_min) > MAX_EXTENT_DEGREES_LAT:
        raise RegionUnsupported(
            "extent",
            (
                f"分析范围 {e_max - w_min:.1f}°×{n_max - s_min:.1f}° 超出局部投影"
                f"适用域（≤{MAX_EXTENT_DEGREES_LON}°×{MAX_EXTENT_DEGREES_LAT}°）"
            ),
            bbox=[w_min, s_min, e_max, n_max],
        )
    from app.lib.gis.crs_safety import recommend_metric_crs

    crs = recommend_metric_crs((w_min, s_min, e_max, n_max))
    if not crs:
        raise RegionUnsupported(
            "extent", "无法为该范围推荐度量 CRS（recommend_metric_crs 返回空）"
        )
    return str(crs)


def _coords_finite(coords: Any) -> bool:
    """递归校验坐标是有限数值（防 NaN/Inf 进入 GEOS）。"""
    if isinstance(coords, (int, float)):
        return math.isfinite(float(coords))
    if isinstance(coords, (list, tuple)):
        return all(_coords_finite(c) for c in coords)
    return False


#: 期望几何族（按角色语义传入）：'polygon' | 'line' | 'point' | 'any'
_FAMILY_OF: Dict[str, set] = {
    "polygon": {"Polygon", "MultiPolygon"},
    "line": {"LineString", "MultiLineString"},
    "point": {"Point", "MultiPoint"},
    "any": {
        "Point", "MultiPoint", "LineString", "MultiLineString",
        "Polygon", "MultiPolygon",
    },
}


def prepare_source(
    *,
    role: str,
    source_kind: str,
    ref: str,
    content_revision: Optional[int],
    fingerprint: str,
    crs: Optional[str],
    features: List[Dict[str, Any]],
    expected_family: str,
    id_keys: Tuple[str, ...] = ("_wfid", "id", "OBJECTID", "fid", "osm_id", "@id", "featureId", "feature_id"),
) -> PreparedSource:
    """快照要素列表 → 预检产物（剔除计数 + locator 身份）。

    剔除原因（closed）：null_geometry / empty_geometry / invalid_geometry /
    unsupported_type / too_complex / bad_coords。剔除只影响该要素进入关系
    运算，检测事实与计数保留（never buffer(0)）。
    """

    prepared = PreparedSource(
        role=role, source_kind=source_kind, ref=ref,
        content_revision=content_revision, fingerprint=fingerprint, crs=crs,
    )
    prepared.total_input = len(features)
    family = _FAMILY_OF.get(expected_family, _FAMILY_OF["any"])

    for i, feat in enumerate(features):
        props = (feat or {}).get("properties") or {}
        identity = _locator_identity(i, props, fingerprint, id_keys)
        geom_raw = (feat or {}).get("geometry")
        if geom_raw is None:
            prepared.features.append(PreparedGeometry(
                index=i, identity=identity, geometry=None, exclude_reason="null_geometry",
            ))
            prepared.excluded["null_geometry"] = prepared.excluded.get("null_geometry", 0) + 1
            continue
        try:
            geom = shape(geom_raw)
        except Exception:
            prepared.features.append(PreparedGeometry(
                index=i, identity=identity, geometry=None, exclude_reason="invalid_geometry",
            ))
            prepared.excluded["invalid_geometry"] = prepared.excluded.get("invalid_geometry", 0) + 1
            continue
        if not _coords_finite(geom_raw.get("coordinates")):
            prepared.features.append(PreparedGeometry(
                index=i, identity=identity, geometry=None, exclude_reason="bad_coords",
            ))
            prepared.excluded["bad_coords"] = prepared.excluded.get("bad_coords", 0) + 1
            continue
        if geom.is_empty:
            prepared.features.append(PreparedGeometry(
                index=i, identity=identity, geometry=None, exclude_reason="empty_geometry",
            ))
            prepared.excluded["empty_geometry"] = prepared.excluded.get("empty_geometry", 0) + 1
            continue
        if geom.geom_type not in family:
            prepared.features.append(PreparedGeometry(
                index=i, identity=identity, geometry=None,
                exclude_reason="unsupported_type", geometry_type=geom.geom_type,
            ))
            prepared.excluded["unsupported_type"] = prepared.excluded.get("unsupported_type", 0) + 1
            continue
        n_vertices = _count_vertices(geom)
        if n_vertices > MAX_VERTICES:
            prepared.features.append(PreparedGeometry(
                index=i, identity=identity, geometry=None,
                exclude_reason="too_complex", geometry_type=geom.geom_type,
            ))
            prepared.excluded["too_complex"] = prepared.excluded.get("too_complex", 0) + 1
            continue
        if not geom.is_valid:
            # 无效几何：如实剔除（绝不在检测前修复 —— never buffer(0)）。
            prepared.features.append(PreparedGeometry(
                index=i, identity=identity, geometry=None,
                exclude_reason="invalid_geometry", geometry_type=geom.geom_type,
            ))
            prepared.excluded["invalid_geometry"] = prepared.excluded.get("invalid_geometry", 0) + 1
            continue
        prepared.features.append(PreparedGeometry(
            index=i, identity=identity, geometry=geom, geometry_type=geom.geom_type,
        ))
    prepared.examined = sum(1 for p in prepared.features if p.geometry is not None)
    return prepared


def _locator_identity(
    index: int, props: Dict[str, Any], fingerprint: str, id_keys: Tuple[str, ...]
) -> Dict[str, Any]:
    """要素 → locator identity（链与 FEATURE_ID_KEYS 同源；兜底快照内 index）。"""
    for key in id_keys:
        v = props.get(key)
        if v is None:
            continue
        s = str(v)
        if s:
            kind = "wfid" if key == "_wfid" else "natural_id"
            return {"kind": kind, "value": s[:128]}
    return {
        "kind": "snapshot_index", "value": str(index),
        "snapshot_fingerprint": fingerprint,
    }


def _count_vertices(geom: BaseGeometry) -> int:
    try:
        import shapely
        return int(shapely.get_num_coordinates(geom))
    except Exception:  # noqa: BLE001 — 计数失败按超限处理（保守）
        return MAX_VERTICES + 1


def transform_to(geoms: List[BaseGeometry], src_crs: str, dst_crs: str) -> List[Optional[BaseGeometry]]:
    """批量坐标变换（pyproj）；不可变换元素返回 None（诚实剔除，调用方计数）。"""
    if src_crs == dst_crs:
        return list(geoms)
    try:
        from pyproj import Transformer
        from shapely.ops import transform as shp_transform
        transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
        out: List[Optional[BaseGeometry]] = []
        for g in geoms:
            if g is None:
                out.append(None)
                continue
            try:
                out.append(shp_transform(transformer.transform, g))
            except Exception:
                out.append(None)
        return out
    except ImportError:
        return [None] * len(geoms)


def source_bbox(prepared: PreparedSource) -> Optional[Tuple[float, float, float, float]]:
    """预检后有效几何的联合 bbox（度，WGS84 输入假设下）。"""
    boxes = [p.geometry.bounds for p in prepared.features if p.geometry is not None]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )


def transform_source_in_place(prepared: PreparedSource, dst_crs: str) -> None:
    """把已预检源的全部有效几何变换到计算 CRS（原地；失败 → transform_failed 剔除）。"""
    src = prepared.crs or "EPSG:4326"
    if src == dst_crs:
        return
    idx = [i for i, p in enumerate(prepared.features) if p.geometry is not None]
    geoms = [prepared.features[i].geometry for i in idx]
    transformed = transform_to(geoms, src, dst_crs)  # type: ignore[arg-type]
    for slot, new_geom in zip(idx, transformed):
        if new_geom is None or new_geom.is_empty or not new_geom.is_valid:
            p = prepared.features[slot]
            p.geometry = None
            p.exclude_reason = "transform_failed"
            prepared.excluded["transform_failed"] = prepared.excluded.get("transform_failed", 0) + 1
        else:
            prepared.features[slot].geometry = new_geom
    prepared.examined = sum(1 for p in prepared.features if p.geometry is not None)
    prepared.crs = dst_crs


def geometry_to_geojson(geom: BaseGeometry, precision: int = 7) -> Dict[str, Any]:
    """shapely 几何 → 坐标取整的 GeoJSON geometry dict（witness 存储/展示）。"""
    from shapely.geometry import mapping

    def _round(obj: Any) -> Any:
        if isinstance(obj, (int, float)):
            return round(float(obj), precision)
        if isinstance(obj, (list, tuple)):
            return [_round(o) for o in obj]
        return obj

    mapped = mapping(geom)
    mapped["coordinates"] = _round(mapped.get("coordinates"))
    return mapped


__all__ = [
    "RegionUnsupported", "PreparedGeometry", "PreparedSource",
    "choose_computation_crs", "prepare_source", "transform_to", "source_bbox",
    "transform_source_in_place", "geometry_to_geojson",
    "MAX_EXTENT_DEGREES_LON", "MAX_EXTENT_DEGREES_LAT", "POLAR_LAT_LIMIT",
]
