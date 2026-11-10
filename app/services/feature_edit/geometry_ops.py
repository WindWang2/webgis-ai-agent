"""几何编辑算子 —— repair_geometry / move_by / snap_to_ref（纯函数）。

坐标语义纪律：
- 数据集载荷恒 EPSG:4326（摄入管线归一；防御性检查非 4326 → 拒绝）。
- 一切位移/容差以**米**显式给定（缺数值 = unit_required，绝不隐式假设）。
- 位移用 pyproj.Geod（WGS84 大地测量）逐顶点变换——不使用度近似；
  单次位移上限 MAX_MOVE_METERS 防呆。
- snap 容差内无候选 → no_snap_candidate，整体 fail-closed（不允许
  「部分吸附」的静默成功）。
- 顶点数超 MAX_GEOMETRY_POSITIONS → geometry_too_complex（diff/undo
  保真不可承受，宁可拒绝）。
"""
from __future__ import annotations

from app.contracts.geojson import GeoJSONFeatureCollection, GeoJSONFeature, GeoJSONGeometry

from app.services.feature_edit.wire_types import EditFinding, FeatureDelta, SnapSummary

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyproj import Geod

from shapely.geometry.base import BaseGeometry
import math
from typing import Any, Dict, List, Optional, Tuple, cast

from shapely.geometry import mapping, shape
from shapely.ops import nearest_points, transform as shapely_transform, split as shapely_split, unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid

from app.services.feature_edit.contracts import (
    MAX_MOVE_METERS,
    MAX_SNAP_TOLERANCE_M,
    REPAIR_FIXES,
    WFID_PROP,
    finding,
)
from app.services.feature_edit.identity import wfid_for_created
from app.services.feature_edit.journal import MAX_GEOMETRY_POSITIONS, count_positions

__all__ = [
    "repair_geometry",
    "move_by",
    "snap_to_ref",
    "geodesic_distance_m",
    "replace_geometry",
    "split_feature",
    "merge_features",
]

_GEOD = None


def _geod() -> Geod:
    global _GEOD
    if _GEOD is None:
        from pyproj import Geod

        _GEOD = Geod(ellps="WGS84")
    return _GEOD


def geodesic_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """两点大地距离（米）。"""
    _, _, dist = _geod().inv(lon1, lat1, lon2, lat2)
    return float(dist)


def _iter_target_features(fc: Optional[GeoJSONFeatureCollection]) -> List[GeoJSONFeature]:
    if not isinstance(fc, dict):
        return []
    return [f for f in (fc.get("features") or []) if isinstance(f, dict)]


def _select_targets(fc: GeoJSONFeatureCollection, wfids: set) -> List[Tuple[str, GeoJSONFeature, int]]:
    """(wfid, feature, positions) —— 目标要素 + 顶点规模门 + 存在性证据。"""
    out: List[Tuple[str, GeoJSONFeature, int]] = []
    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        wfid = props.get(WFID_PROP) if isinstance(props, dict) else None
        if not isinstance(wfid, str) or wfid not in wfids:
            continue
        positions = count_positions(feature.get("geometry"))
        out.append((wfid, feature, positions))
    return out


def _common_guards(
    fc: GeoJSONFeatureCollection, wfids: set
) -> Tuple[List[Tuple[str, GeoJSONFeature, int]], List[EditFinding]]:
    findings: List[EditFinding] = []
    crs = fc.get("crs")
    if crs is not None:
        # 摄入已归一 4326；载荷显式声明其它 CRS 属未预期形态，fail-closed。
        findings.append(finding("crs_assumption_required", "payload declares a non-default crs member"))
        return [], findings
    targets = _select_targets(fc, wfids)
    if len(targets) != len(wfids):
        found = {t[0] for t in targets}
        missing = sorted(wfids - found)[:8]
        findings.append(
            finding("feature_not_found", "target features missing in current payload", wfid=missing)
        )
    for wfid, _feature, positions in targets:
        if positions > MAX_GEOMETRY_POSITIONS:
            findings.append(
                finding(
                    "geometry_too_complex",
                    "feature vertex count exceeds the editable bound",
                    wfid=wfid,
                    positions=positions,
                )
            )
    return targets, findings


def _validate_after(geom_after: BaseGeometry | None) -> Optional[EditFinding]:
    if geom_after is None or geom_after.is_empty:
        return finding("geometry_invalid_after", "operation produced an empty geometry")
    if geom_after.geom_type in ("Polygon", "MultiPolygon") and not geom_after.is_valid:
        return finding("geometry_invalid_after", "operation produced an invalid polygon")
    return None


def _jsonable(node: Any) -> Any:
    """shapely mapping() 产出坐标 tuple → list（与源 GeoJSON 形态一致，
    保证 journal diff 的 before/after 可直接比较/序列化）。"""
    if isinstance(node, (list, tuple)):
        return [_jsonable(c) for c in node]
    if isinstance(node, dict):
        return {k: _jsonable(v) for k, v in node.items()}
    return node


def _geometry_out(geom: BaseGeometry) -> GeoJSONGeometry:
    return _jsonable(mapping(geom))


# ─── repair_geometry ───────────────────────────────────────────────────────

def repair_geometry(
    fc: GeoJSONFeatureCollection, wfids: set, fixes: List[str]
) -> Tuple[List[FeatureDelta], List[EditFinding]]:
    """确定性几何修复（make_valid / close_ring / dedupe_vertices）。

    复用 data_quality 的算子语义（per-feature 化）：闭环=外环首尾相接；
    去重顶点=移除相邻重复坐标；make_valid=shapely 拓扑修复。
    """
    unknown = [f for f in (fixes or []) if f not in REPAIR_FIXES]
    if unknown:
        return [], [finding("invalid_op", "unknown repair fix", fixes=unknown[:4])]
    targets, findings = _common_guards(fc, wfids)
    if findings:
        return [], findings
    deltas: List[FeatureDelta] = []
    for wfid, feature, _positions in targets:
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        try:
            geom = shape(geometry)
        except Exception:
            findings.append(finding("invalid_op", "existing geometry unparseable", wfid=wfid))
            continue
        geom = _repair_structure(
            geom, dedupe="dedupe_vertices" in fixes, close="close_ring" in fixes
        )
        if "make_valid" in fixes:
            geom = make_valid(geom)
        bad = _validate_after(geom)
        if bad is not None:
            findings.append({**bad, "wfid": wfid})
            continue
        new_geometry = _geometry_out(geom)
        if new_geometry != geometry:
            deltas.append({"wfid": wfid, "geometry_before": geometry, "geometry_after": new_geometry})
    return deltas, findings


# ─── 结构修复（显式构造器：dedupe/close 是点集操作，不走坐标数组变换） ────

def _dedupe_coords(coords: List) -> List:
    out: List = []
    for c in coords:
        if not out or c != out[-1]:
            out.append(c)
    return out


def _close_if_ring(coords: List) -> List:
    if len(coords) >= 3 and coords[0] != coords[-1]:
        return coords + [coords[0]]
    return coords


def _repair_structure(geom: BaseGeometry, dedupe: bool, close: bool) -> BaseGeometry:
    """LineString/Polygon(/Multi*) 的相邻去重与环闭合（递归构造新几何）。"""
    from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

    if geom.is_empty or geom.geom_type == "Point":
        return geom
    if geom.geom_type == "LineString":
        coords = list(geom.coords)
        if dedupe:
            coords = _dedupe_coords(coords)
        return LineString(coords) if len(coords) >= 2 else geom
    if geom.geom_type == "Polygon":
        rings = [list(geom.exterior.coords)] + [list(r.coords) for r in geom.interiors]
        fixed = []
        for ring in rings:
            if dedupe:
                ring = _dedupe_coords(ring)
            if close:
                ring = _close_if_ring(ring)
            fixed.append(ring)
        if len(fixed[0]) >= 4:
            return Polygon(fixed[0], fixed[1:])
        return geom  # 退化外环不动（由 make_valid/校验面披露）
    if geom.geom_type == "MultiLineString":
        parts = [_repair_structure(g, dedupe, close) for g in geom.geoms]
        keep = [list(p.coords) for p in parts if p.geom_type == "LineString" and not p.is_empty and len(p.coords) >= 2]
        return MultiLineString(keep) if keep else geom
    if geom.geom_type == "MultiPolygon":
        parts = [_repair_structure(g, dedupe, close) for g in geom.geoms]
        valid = [g for g in parts if not g.is_empty]
        return MultiPolygon(valid) if valid else geom
    return geom


# ─── move_by ───────────────────────────────────────────────────────────────

def move_by(
    fc: GeoJSONFeatureCollection, wfids: set, dx_m: Any, dy_m: Any
) -> Tuple[List[FeatureDelta], List[EditFinding]]:
    """显式位移（米，dx=东正 / dy=北正）；逐顶点大地测量平移。"""
    findings: List[EditFinding] = []
    if not isinstance(dx_m, (int, float)) or not isinstance(dy_m, (int, float)) or isinstance(dx_m, bool) or isinstance(dy_m, bool):
        return [], [finding("unit_required", "move_by requires numeric dx_m/dy_m in meters")]
    dx_m, dy_m = float(dx_m), float(dy_m)
    if dx_m == 0.0 and dy_m == 0.0:
        return [], [finding("invalid_op", "move_by displacement is zero")]
    magnitude = math.hypot(dx_m, dy_m)
    if magnitude > MAX_MOVE_METERS:
        return [], [
            finding("invalid_op", "move_by displacement exceeds the safety bound", meters=magnitude)
        ]
    geod = _geod()
    azimuth = math.degrees(math.atan2(dx_m, dy_m))  # 0°=北，90°=东
    targets, guard_findings = _common_guards(fc, wfids)
    if guard_findings:
        return [], guard_findings
    deltas: List[FeatureDelta] = []

    def shift(x: float | tuple[float, ...], y: float | tuple[float, ...]) -> tuple[float | tuple[float, ...], float | tuple[float, ...]]:
        # shapely.ops.transform 的 (x, y) 坐标数组签名；Geod.fwd 原生支持
        # 数组/标量 —— 等方位/等距离的测地平移（形状保持）。
        lon2, lat2, _ = geod.fwd(x, y, azimuth, magnitude)
        return lon2, lat2

    for wfid, feature, _positions in targets:
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        geom = shape(geometry)
        moved = shapely_transform(shift, geom)
        bad = _validate_after(moved)
        if bad is not None:
            findings.append({**bad, "wfid": wfid})
            continue
        deltas.append({"wfid": wfid, "geometry_before": geometry, "geometry_after": _geometry_out(moved)})
    return deltas, findings


# ─── snap_to_ref ───────────────────────────────────────────────────────────

def snap_to_ref(
    fc: GeoJSONFeatureCollection,
    wfids: set,
    target_fc: Optional[GeoJSONFeatureCollection],
    tolerance_m: Any,
) -> Tuple[List[FeatureDelta], List[EditFinding], SnapSummary]:
    """吸附到目标线数据集（每要素：质心 → 最近目标线最近点，容差内）。

    返回 (deltas, findings, summary)。整体 fail-closed：任一要素无容差内
    候选 → no_snap_candidate finding（plan 不可提交）。
    平移向量按各要素质心的 Geod 逆解计算，逐顶点等量平移（形状不变）。
    """
    findings: List[EditFinding] = []
    if not isinstance(tolerance_m, (int, float)) or isinstance(tolerance_m, bool):
        return [], [finding("unit_required", "snap requires numeric tolerance_m in meters")], {}
    tolerance_m = float(tolerance_m)
    if tolerance_m <= 0 or tolerance_m > MAX_SNAP_TOLERANCE_M:
        return [], [finding("invalid_op", "snap tolerance out of bounds")], {}
    lines = []
    for feature in _iter_target_features(target_fc):
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        try:
            geom = shape(geometry)
        except Exception:
            continue
        if geom.geom_type in ("LineString", "MultiLineString") and not geom.is_empty:
            lines.append(geom)
    if not lines:
        return [], [finding("no_snap_candidate", "target dataset has no line geometries")], {}
    tree = STRtree(lines)
    geod = _geod()
    targets, guard_findings = _common_guards(fc, wfids)
    if guard_findings:
        return [], guard_findings, {}
    deltas: List[FeatureDelta] = []
    snap_distances: Dict[str, float] = {}
    for wfid, feature, _positions in targets:
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        geom = shape(geometry)
        centroid = geom.centroid
        idx = tree.nearest(centroid)
        target_line = lines[int(idx)]
        # nearest_points(gA, gB) 返回 (A 上的最近点, B 上的最近点)——
        # 吸附目标是线上的点 → 取 [1]。
        snap_point = nearest_points(centroid, target_line)[1]
        dist = geodesic_distance_m(centroid.x, centroid.y, snap_point.x, snap_point.y)
        if dist > tolerance_m:
            findings.append(
                finding(
                    "no_snap_candidate",
                    "nearest target line is outside tolerance",
                    wfid=wfid,
                    nearest_m=round(dist, 2),
                    tolerance_m=tolerance_m,
                )
            )
            continue
        # 等量平移：centroid → snap_point 的大地逆解位移（方位/距离）
        az, _, dist_move = geod.inv(centroid.x, centroid.y, snap_point.x, snap_point.y)
        if dist_move <= 0:
            continue  # 已在目标上，零位移不入 diff

        def shift(x: float | tuple[float, ...], y: float | tuple[float, ...], _az: float=az, _dist: float=dist_move) -> tuple[float | tuple[float, ...], float | tuple[float, ...]]:
            lon2, lat2, _ = geod.fwd(x, y, _az, _dist)
            return lon2, lat2

        moved = shapely_transform(shift, geom)
        bad = _validate_after(moved)
        if bad is not None:
            findings.append({**bad, "wfid": wfid})
            continue
        deltas.append({"wfid": wfid, "geometry_before": geometry, "geometry_after": _geometry_out(moved)})
        snap_distances[wfid] = round(dist, 2)
    summary: SnapSummary = {
        "snapped": len(deltas),
        "snap_distances_m": dict(sorted(snap_distances.items())[:50]),
        "tolerance_m": tolerance_m,
    }
    return deltas, findings, summary

# ─── G14：replace_geometry / split_feature / merge_features ────────────────

def _base_geom_type(geom: BaseGeometry) -> str:
    """几何基类型（Multi 折叠到单型面：Point/LineString/Polygon）。"""
    name = geom.geom_type
    if name.startswith("Multi") or name == "GeometryCollection":
        return name[5:] if name.startswith("Multi") else "Geometry"
    return name


def _parse_geometry(raw: Any) -> Tuple[Optional[BaseGeometry], Optional[EditFinding]]:
    """输入几何解析（畸形 → invalid_geometry finding，不抛异常）。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        return None, finding("invalid_geometry", "geometry must be a GeoJSON geometry object")
    try:
        geom = shape(raw)
    except Exception:
        return None, finding("invalid_geometry", "geometry coordinates are malformed")
    if geom.is_empty:
        return None, finding("invalid_geometry", "geometry is empty")
    return geom, None


def replace_geometry(
    fc: GeoJSONFeatureCollection,
    wfids: set,
    geometry: Any,
) -> Tuple[List[FeatureDelta], List[EditFinding]]:
    """整几何替换（顶点编辑的提交面：前端手势结束时给最终几何，一次一计划）。

    类型守卫：新几何基类型必须与目标既有基类型一致（Point↔Point、
    Line↔Line（含 Multi）、Polygon↔Polygon（含 Multi））—— 跨基类型变形
    属不同语义（typed 拒绝，不允许静默变类）。有效性走 _validate_after。
    """
    findings: List[EditFinding] = []
    geom, bad = _parse_geometry(geometry)
    if bad is not None:
        return [], [bad]
    targets, guard_findings = _common_guards(fc, wfids)
    if guard_findings:
        return [], guard_findings
    deltas: List[FeatureDelta] = []
    for wfid, feature, _positions in targets:
        existing = feature.get("geometry")
        if not isinstance(existing, dict):
            findings.append(finding("invalid_geometry", "existing geometry unparseable", wfid=wfid))
            continue
        try:
            existing_geom = shape(existing)
        except Exception:
            findings.append(finding("invalid_geometry", "existing geometry unparseable", wfid=wfid))
            continue
        if _base_geom_type(geom) != _base_geom_type(existing_geom):
            findings.append(
                finding(
                    "geometry_type_mismatch",
                    "replacement geometry base type differs from the feature",
                    wfid=wfid,
                    existing=_base_geom_type(existing_geom),
                    replacement=_base_geom_type(geom),
                )
            )
            continue
        bad = _validate_after(geom)
        if bad is not None:
            findings.append({**bad, "wfid": wfid})
            continue
        new_geometry = _geometry_out(geom)
        if new_geometry != existing:
            deltas.append({"wfid": wfid, "geometry_before": existing, "geometry_after": new_geometry})
    return deltas, findings


def _validate_split_parts(parent: BaseGeometry, parts: List[BaseGeometry]) -> Optional[str]:
    """分割结果的诚实性检查（<2 部分 = 未真正分割）。"""
    if len(parts) < 2:
        return "split_not_dividing"
    if any(p.is_empty for p in parts):
        return "split_not_dividing"
    return None


def _split_polygon_or_line(
    geom: BaseGeometry, splitter: BaseGeometry
) -> Tuple[Optional[List[BaseGeometry]], Optional[str]]:
    """几何分割（shapely split；返回 (parts, reason)。

    - Polygon：splitter 为 LineString —— 需贯穿外环；洞随所在部分保留
      （split 是几何级操作，洞自动归属包含它的部分）。
    - LineString：splitter 为 LineString/Point —— 交点切段。
    - Multi：逐 part 分割，全部 parts 汇总；无任何 part 被分 → not dividing。
    """
    from shapely.geometry import MultiPolygon

    try:
        result = shapely_split(geom, splitter)
    except Exception:
        return None, "split_not_dividing"
    parts = [g for g in getattr(result, "geoms", [result]) if not g.is_empty]
    if geom.geom_type == "Polygon":
        # shapely split 对洞的处理：交点在洞环上时结果可能重组 —— 以
        # 「部分 ∪ = 原几何（容差内）」为不变量，逐部分保留洞。
        return (parts if len(parts) >= 2 else None), (None if len(parts) >= 2 else "split_not_dividing")
    if geom.geom_type == "MultiPolygon":
        out: List[BaseGeometry] = []
        divided_any = False
        for part in geom.geoms:
            sub = _split_polygon_or_line(part, splitter)
            sub_parts, sub_reason = sub
            if sub_parts is None:
                out.append(part)  # 该 part 未被分割线穿过 —— 原样保留
                continue
            divided_any = True
            out.extend(sub_parts)
        if not divided_any:
            return None, "split_not_dividing"
        # 未命中的 part 与命中 parts 同级返回（调用方逐个建 child）
        return out, None
    if geom.geom_type in ("LineString", "MultiLineString"):
        return (parts if len(parts) >= 2 else None), (None if len(parts) >= 2 else "split_not_dividing")
    return None, "split_not_dividing"


def split_feature(
    fc: GeoJSONFeatureCollection,
    wfid: str,
    line: Any,
    *,
    plan_seed: str,
    op_index: int,
) -> Tuple[List[FeatureDelta], List[EditFinding], Optional[Dict[str, Any]]]:
    """单要素分割：parent 退役（removed 快照）+ children 出生（继承字段）。

    返回 (deltas, findings, lineage_event)。children 字段完整继承 parent
    （显式规则；血缘 parent→children 记录在 lineage）。Point 不可分割
    （typed 拒绝）。child wfid 由 (plan_seed, op_index, child_index)
    决定性派生 —— redo/重放同身份。
    """
    findings: List[EditFinding] = []
    splitter, bad = _parse_geometry(line)
    if bad is not None:
        return [], [bad], None
    if splitter.geom_type not in ("LineString", "MultiLineString"):
        return [], [finding("invalid_op", "split line must be a (Multi)LineString")], None
    targets, guard_findings = _common_guards(fc, {wfid})
    if guard_findings:
        return [], guard_findings, None
    if not targets:
        return [], [finding("feature_not_found", "split target missing", wfid=wfid)], None
    parent_index = next(
        (
            i
            for i, f in enumerate(fc.get("features") or [])
            if isinstance(f, dict)
            and isinstance(f.get("properties"), dict)
            and f["properties"].get(WFID_PROP) == wfid
        ),
        None,
    )
    _w, feature, _positions = targets[0]
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return [], [finding("invalid_geometry", "target geometry unparseable", wfid=wfid)], None
    try:
        parent_geom = shape(geometry)
    except Exception:
        return [], [finding("invalid_geometry", "target geometry unparseable", wfid=wfid)], None
    base = _base_geom_type(parent_geom)
    if base == "Point":
        return [], [finding("invalid_op", "point features cannot be split", wfid=wfid)], None
    parts, reason = _split_polygon_or_line(parent_geom, splitter)
    if parts is None:
        return [], [
            finding(
                reason or "split_not_dividing",
                "split line does not divide the target geometry into >=2 parts",
                wfid=wfid,
            )
        ], None
    # 每部分独立校验（洞保真由 shapely 结构保证；非法部分拒绝整单）
    out_parts: List[BaseGeometry] = []
    for part in parts:
        bad = _validate_after(part)
        if bad is not None:
            return [], [{**bad, "wfid": wfid}], None
        out_parts.append(part)
    props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    parent_snapshot = copy_feature_snapshot(feature)
    parent_delta: FeatureDelta = {"wfid": wfid, "presence": "removed", "feature_before": parent_snapshot}
    if parent_index is not None:
        parent_delta["index"] = parent_index  # undo 原位复活（恢复列表次序）
    # R3 P2-7：分割不变量强制执行 —— children 的并集必须覆盖原几何（面积
    # 容差内）。shapely split 的洞/边界边缘形态若重组或丢洞，在此拒绝而非
    # 静默落地（注释声称的不变量不再只是声称）。
    union_check = unary_union(out_parts)
    if abs(union_check.area - parent_geom.area) > max(parent_geom.area * 1e-9, 1e-16):
        return [], [
            finding(
                "split_not_dividing",
                "split parts do not reconstruct the source geometry (holes/parts would be lost)",
                wfid=wfid,
            )
        ], None
    deltas: List[FeatureDelta] = [parent_delta]
    children: List[str] = []
    existing_ids = {
        cast(dict, f.get("properties") or {}).get(WFID_PROP)
        for f in fc.get("features") or []
        if isinstance(f, dict)
    }
    for child_index, part in enumerate(out_parts):
        child_wfid = wfid_for_created(plan_seed, op_index, child_index)
        shift = 0
        while child_wfid in existing_ids:  # R3 P2-1：撞既有身份即偏移，不静默丢 child
            shift += 1000
            child_wfid = wfid_for_created(plan_seed, op_index, child_index + shift)
        existing_ids.add(child_wfid)
        child_props = {k: v for k, v in props.items()}
        child_props[WFID_PROP] = child_wfid
        child_feature = {
            "type": "Feature",
            "geometry": _geometry_out(part),
            "properties": child_props,
        }
        deltas.append(
            {"wfid": child_wfid, "presence": "created", "feature_after": child_feature}
        )
        children.append(child_wfid)
    lineage = {"kind": "split", "parent_wfids": [wfid], "child_wfids": children}
    return deltas, findings, lineage


def merge_features(
    fc: GeoJSONFeatureCollection,
    wfids: List[str],
    field_policy: Dict[str, Any],
    *,
    plan_seed: str,
    op_index: int,
) -> Tuple[List[FeatureDelta], List[EditFinding], Optional[Dict[str, Any]]]:
    """同类要素合并：parents 全退役 + 单一 child 出生（unary_union）。

    字段继承（显式规则，op summary 披露差异面）：
    - mode=first（缺省）：按 wfids 列出顺序取第一个非缺失值；与后续父要素
      不同的字段列入 summary.conflict_fields（可见，不静默）。
    - mode=require_identical：任一字段在父要素间不一致 → merge_conflict
      finding（除非 overrides 显式给值）。
    合并结果必须单部分（Polygon/LineString/Point；Multi 结果 = 不相邻
    → merge_disjoint 拒绝）。Line 合并用 linemerge 语义（unary_union 对
    共端点线自动合并；分离线 → MultiLineString → 拒绝）。
    """
    findings: List[EditFinding] = []
    wfid_set = set(wfids)
    targets, guard_findings = _common_guards(fc, wfid_set)
    if guard_findings:
        return [], guard_findings, None
    if len(targets) < 2:
        return [], [finding("invalid_op", "merge requires at least 2 target features")], None
    by_wfid = {w: f for w, f, _p in targets}
    ordered = [by_wfid[w] for w in wfids if w in by_wfid]
    base_types = set()
    for w, f, _p in targets:
        geometry = f.get("geometry")
        if not isinstance(geometry, dict):
            return [], [finding("invalid_geometry", "merge target geometry unparseable", wfid=w)], None
        try:
            g = shape(geometry)
        except Exception:
            return [], [finding("invalid_geometry", "merge target geometry unparseable", wfid=w)], None
        if g.is_empty:
            return [], [finding("invalid_geometry", "merge target geometry is empty", wfid=w)], None
        base_types.add(_base_geom_type(g))
    if len(base_types) != 1:
        return [], [
            finding("merge_type_mixed", "merge targets mix geometry base types", types=sorted(base_types))
        ], None
    mode = (field_policy or {}).get("mode", "first")
    overrides = (field_policy or {}).get("overrides") or {}
    if mode not in ("first", "require_identical"):
        return [], [finding("invalid_op", "field_policy.mode must be first|require_identical")], None
    # 字段继承预演（差异面披露）
    merged_props: Dict[str, Any] = {}
    conflict_fields: List[str] = []
    all_keys: List[str] = []
    seen_keys = set()
    for f in ordered:
        props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
        for key in props:
            if key not in seen_keys:
                seen_keys.add(key)
                all_keys.append(key)
    for key in all_keys:
        if key == WFID_PROP:
            continue
        values = [
            (f.get("properties") or {}).get(key, _MISSING) for f in ordered
        ]
        present_vals = [v for v in values if v is not _MISSING]
        if not present_vals:
            merged_props[key] = None
            continue
        distinct = []
        for v in present_vals:
            # 类型敏感去重（R3 P3）：Python 相等把 0==False/1==1.0 视为同值，
            # 会把布尔/数字混合误判为「一致字段」而静默取首值。
            if not any(type(v) is type(d) and v == d for d in distinct):
                distinct.append(v)
        if key in overrides:
            merged_props[key] = overrides[key]
            if len(distinct) > 1:
                conflict_fields.append(key)
            continue
        if len(distinct) == 1:
            merged_props[key] = distinct[0]
            continue
        # 多值
        if mode == "require_identical":
            findings.append(
                finding(
                    "merge_conflict",
                    "field differs across merge parents and require_identical is set",
                    field=key,
                    values=[str(v)[:48] for v in distinct[:6]],
                )
            )
            continue
        merged_props[key] = distinct[0]  # first（按列出顺序）
        conflict_fields.append(key)
    if findings:
        return [], findings, None
    # 几何合并
    geoms = [shape(f.get("geometry")) for f in ordered]
    try:
        union = unary_union(geoms)
    except Exception:
        return [], [finding("invalid_op", "geometry union failed")], None
    if union.geom_type.startswith("Multi") or union.geom_type == "GeometryCollection":
        return [], [finding("merge_disjoint", "merge result is multi-part (targets not adjacent)")], None
    bad = _validate_after(union)
    if bad is not None:
        return [], [bad], None
    # 新生身份查重（R3 P2-1）：与 create 同款偏移守卫 —— 撞上既有身份时
    # 偏移派生，绝不静默吞掉 child。
    child_wfid = wfid_for_created(plan_seed, op_index, 0)
    existing_ids = {
        cast(dict, f.get("properties") or {}).get(WFID_PROP)
        for f in fc.get("features") or []
        if isinstance(f, dict)
    }
    shift = 0
    while child_wfid in existing_ids:
        shift += 1000
        child_wfid = wfid_for_created(plan_seed, op_index, shift)
    merged_props[WFID_PROP] = child_wfid
    child_feature = {
        "type": "Feature",
        "geometry": _geometry_out(union),
        "properties": merged_props,
    }
    # 父要素原位 index（undo 恢复次序）。R3 P1-1：removed deltas 按列表内
    # 出现顺序（index 升序）发射 —— 用户选择序（如 [B,A]）直接发射会让
    # undo 的原位重插得到漂移列表序，链指纹断言 409。
    index_of = {
        cast(dict, f.get("properties") or {}).get(WFID_PROP): i
        for i, f in enumerate(fc.get("features") or [])
        if isinstance(f, dict)
    }
    deltas: List[FeatureDelta] = []
    for w, f in sorted(zip(wfids, ordered), key=lambda pair: index_of.get(pair[0], len(index_of))):
        d: FeatureDelta = {"wfid": w, "presence": "removed", "feature_before": copy_feature_snapshot(f)}
        if w in index_of:
            d["index"] = index_of[w]
        deltas.append(d)
    deltas.append({"wfid": child_wfid, "presence": "created", "feature_after": child_feature})
    lineage = {"kind": "merge", "parent_wfids": [w for w in wfids if w in by_wfid], "child_wfids": [child_wfid]}
    summary_extra = {"conflict_fields": conflict_fields[:24]} if conflict_fields else {}
    return deltas, findings, {"lineage": lineage, "extra": summary_extra}


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover — 调试可读性
        return "<missing>"


_MISSING = _Missing()


def copy_feature_snapshot(feature: GeoJSONFeature) -> Dict[str, Any]:
    """完整要素快照（undo 复活用；deep copy 隔离后续变更）。"""
    import copy as _copy

    return _copy.deepcopy(feature)
