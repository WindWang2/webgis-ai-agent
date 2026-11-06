"""Envelope v2 实测事实采集（ADR-0284 D1）—— 纯函数、全有界。

纪律：
- **未知 ≠ 猜测**：采集不到的事实保持缺省（空 tuple/dict/""），绝不虚填；
- **有界扫描**：几何类型/extent 只扫前 ``MAX_FEATURE_SCAN``（2000）个特征、
  坐标迭代上限 ``MAX_COORD_SCAN``（20000）——大数据集的 envelope 采集开销
  与 preflight 同阶（≤O(常数)），与 5k/50k 数据集规模无关；
- **实测口径**优先取自工具结果 ``spatial_semantics`` 证据块（ADR-0279 投影）
  与 stats 里的 *_unit 键；栅格事实只收白名单键（nodata/resolution/
  band_count/dtype/valid_pixel_coverage），其余一律不进 envelope；
- 聚合基数（aggregation_basis）是**步骤语义的声明**（工具×参数静态映射），
  不是对数据的推断——声明与算子行为的一致性由 receipt/单测钉住。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

#: 几何类型/extent 扫描的特征数上界（与 spatial_semantics preflight 的
#: feature 扫描界同阶——descriptor 级开销契约）。
MAX_FEATURE_SCAN = 2000

#: extent 坐标迭代上界（防御性：单特征超密几何不放大扫描）。
MAX_COORD_SCAN = 20000

#: envelope.raster / result_semantics 的键数上界。
MAX_SEMANTIC_KEYS = 8

#: result_semantics 从 spatial_semantics 证据块采集的白名单键（有界投影）。
_SEMANTICS_BLOCK_KEYS = (
    "source_crs", "crs_source", "crs_class", "working_crs",
    "calculation_mode", "density_unit", "rate_unit", "stat_denominator",
)

#: 栅格事实白名单键（工具结果 stats / 根键 / data 子键里如实存在才收）。
#: 只收栅格特异键——通用键（crs/width/height/resolution）矢量结果也可能
#: 携带，误进 envelope.raster 会把矢量 step: 输入误判为 raster 直通
#: （R1 评审 P3）。
_RASTER_FACT_KEYS = (
    "nodata", "target_resolution", "band_count", "dtype",
    "valid_pixel_coverage", "new_shape", "resampling", "resolution_unit", "target_crs",
)

#: 优先保留键（8 键截断时按此序优先——覆盖率/分辨率是科学披露承重键）。
_RASTER_FACT_PRIORITY = (
    "valid_pixel_coverage", "nodata", "target_resolution", "resolution_unit", "target_crs", "band_count",
)

#: 聚合基数静态映射（工具名 → 基数语义；参数级覆盖见 aggregation_basis_for）。
#: 只声明真正的聚合算子；几何变换（overlay/buffer 之外的合成算子）不进映射
#: （"" = 未声明——比错误声明诚实）。
_AGG_BASIS_BY_TOOL: Dict[str, str] = {
    "spatial_aggregate": "many_to_one_sum",
    "spatial_join": "m_n_join",
    "zonal_stats": "zonal_mean",
    "buffer_analysis": "one_to_one",
    "h3_binning": "many_to_one_sum",
    "isochrone_network": "one_to_one",
    "network_closest_facility": "one_to_one",
}


@dataclass(frozen=True)
class FcFacts:
    """FC 实测事实（未知=空）。"""

    geometry_types: Tuple[str, ...] = ()
    extent: Tuple[float, float, float, float] = ()
    crs: str = ""
    feature_count_scanned: int = 0
    facts: Dict[str, Any] = field(default_factory=dict)


def _iter_coords(geometry: Any, budget: Dict[str, int]):
    """有界坐标迭代（Point/LineString/Polygon/Multi*/GeometryCollection）。"""
    if not isinstance(geometry, dict):
        return
    gtype = geometry.get("type")
    if gtype == "Point":
        coords = geometry.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            budget["n"] += 1
            yield coords[0], coords[1]
        return
    if gtype in ("MultiPoint", "LineString"):
        for c in geometry.get("coordinates") or []:
            if budget["n"] >= MAX_COORD_SCAN:
                return
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                budget["n"] += 1
                yield c[0], c[1]
        return
    if gtype in ("MultiLineString", "Polygon"):
        for ring in geometry.get("coordinates") or []:
            for c in ring or []:
                if budget["n"] >= MAX_COORD_SCAN:
                    return
                if isinstance(c, (list, tuple)) and len(c) >= 2:
                    budget["n"] += 1
                    yield c[0], c[1]
        return
    if gtype in ("MultiPolygon", "GeometryCollection"):
        items = geometry.get("coordinates") or geometry.get("geometries") or []
        for item in items:
            if budget["n"] >= MAX_COORD_SCAN:
                return
            sub = ({"type": "Polygon", "coordinates": item}
                   if gtype == "MultiPolygon" else item)
            yield from _iter_coords(sub, budget)


def _parse_crs_member(raw: Any) -> str:
    """GeoJSON crs member（RFC 7946 前遗留 + 本仓 GIS-682 纪律）→ 规范名。"""
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, dict):
        name = (raw.get("properties") or {}).get("name")
        if isinstance(name, str) and name:
            return name
    return ""


def collect_fc_facts(fc: Dict[str, Any]) -> FcFacts:
    """FC → 实测几何事实（有界扫描；非 FC 输入返回空事实）。"""
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        return FcFacts()
    features = fc.get("features")
    if not isinstance(features, (list, tuple)):
        return FcFacts(crs=_parse_crs_member(fc.get("crs")))
    geometry_types: list[str] = []
    budget = {"n": 0}
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    scanned = 0
    for feature in features[:MAX_FEATURE_SCAN]:
        if not isinstance(feature, dict):
            continue
        scanned += 1
        geom = feature.get("geometry")
        if isinstance(geom, dict):
            gtype = str(geom.get("type") or "")
            if gtype and gtype not in geometry_types and len(
                    geometry_types) < 4:
                geometry_types.append(gtype)
        for x, y in _iter_coords(geom, budget):
            if x < minx:
                minx = x
            if x > maxx:
                maxx = x
            if y < miny:
                miny = y
            if y > maxy:
                maxy = y
        if budget["n"] >= MAX_COORD_SCAN:
            break
    extent: Tuple[float, float, float, float] = ()
    if minx != float("inf") and budget["n"]:
        extent = (minx, miny, maxx, maxy)
    return FcFacts(
        geometry_types=tuple(sorted(geometry_types)),
        extent=extent,
        crs=_parse_crs_member(fc.get("crs")),
        feature_count_scanned=scanned,
    )


def collect_result_semantics(result: Dict[str, Any]) -> Dict[str, str]:
    """结果 → 实测口径事实（spatial_semantics 证据块 + stats 的 *_unit 键）。"""
    semantics: Dict[str, str] = {}
    block = result.get("spatial_semantics")
    if isinstance(block, dict):
        for key in _SEMANTICS_BLOCK_KEYS:
            value = block.get(key)
            if isinstance(value, str) and value:
                semantics[key] = value
    stats = result.get("stats")
    if isinstance(stats, dict):
        for key, value in stats.items():
            if (key in _SEMANTICS_BLOCK_KEYS or key.endswith("_unit")) \
                    and isinstance(value, str) and value:
                semantics.setdefault(key, value)
    # 根键直挂的单位口径（个别工具把 density_unit 放根键）
    for key in _SEMANTICS_BLOCK_KEYS:
        value = result.get(key)
        if (key.endswith("_unit") and isinstance(value, str) and value):
            semantics.setdefault(key, value)
    return dict(sorted(semantics.items())[:MAX_SEMANTIC_KEYS])


def collect_raster_facts(result: Dict[str, Any]) -> Dict[str, Any]:
    """结果 → 栅格事实（白名单键；stats/根键/data 子键如实采集，缺者不进）。"""
    facts: Dict[str, Any] = {}
    sources = [result.get("stats"), result, result.get("data")]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in _RASTER_FACT_KEYS:
            if key in facts:
                continue
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool, list)):
                facts[key] = value
    ordered = sorted(
        facts.items(),
        key=lambda kv: (_RASTER_FACT_PRIORITY.index(kv[0])
                        if kv[0] in _RASTER_FACT_PRIORITY
                        else len(_RASTER_FACT_PRIORITY), kv[0]))
    return dict(ordered[:MAX_SEMANTIC_KEYS])


def aggregation_basis_for(tool_name: str, params: Dict[str, Any]) -> str:
    """工具×参数 → 聚合基数声明（静态映射；参数级特例如实覆盖）。"""
    return _AGG_BASIS_BY_TOOL.get(tool_name, "")
