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

import math
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import mapping, shape
from shapely.ops import nearest_points, transform as shapely_transform
from shapely.strtree import STRtree
from shapely.validation import make_valid

from app.services.feature_edit.contracts import (
    MAX_MOVE_METERS,
    MAX_SNAP_TOLERANCE_M,
    REPAIR_FIXES,
    WFID_PROP,
    finding,
)
from app.services.feature_edit.journal import MAX_GEOMETRY_POSITIONS, count_positions

__all__ = [
    "repair_geometry",
    "move_by",
    "snap_to_ref",
    "geodesic_distance_m",
]

_GEOD = None


def _geod():
    global _GEOD
    if _GEOD is None:
        from pyproj import Geod

        _GEOD = Geod(ellps="WGS84")
    return _GEOD


def geodesic_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """两点大地距离（米）。"""
    _, _, dist = _geod().inv(lon1, lat1, lon2, lat2)
    return float(dist)


def _iter_target_features(fc: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(fc, dict):
        return []
    return [f for f in (fc.get("features") or []) if isinstance(f, dict)]


def _select_targets(fc: Dict[str, Any], wfids: set) -> List[Tuple[str, Dict[str, Any], int]]:
    """(wfid, feature, positions) —— 目标要素 + 顶点规模门 + 存在性证据。"""
    out: List[Tuple[str, Dict[str, Any], int]] = []
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
    fc: Dict[str, Any], wfids: set
) -> Tuple[List[Tuple[str, Dict[str, Any], int]], List[Dict[str, Any]]]:
    findings: List[Dict[str, Any]] = []
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


def _validate_after(geom_after) -> Optional[Dict[str, Any]]:
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


def _geometry_out(geom) -> Dict[str, Any]:
    return _jsonable(mapping(geom))


# ─── repair_geometry ───────────────────────────────────────────────────────

def repair_geometry(
    fc: Dict[str, Any], wfids: set, fixes: List[str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
    deltas: List[Dict[str, Any]] = []
    for wfid, feature, _positions in targets:
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        try:
            geom = shape(geometry)
        except Exception:
            findings.append(finding("geometry_invalid_after", "existing geometry unparseable", wfid=wfid))
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


def _repair_structure(geom, dedupe: bool, close: bool):
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
    fc: Dict[str, Any], wfids: set, dx_m: Any, dy_m: Any
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """显式位移（米，dx=东正 / dy=北正）；逐顶点大地测量平移。"""
    findings: List[Dict[str, Any]] = []
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
    deltas: List[Dict[str, Any]] = []

    def shift(x, y):
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
    fc: Dict[str, Any],
    wfids: set,
    target_fc: Optional[Dict[str, Any]],
    tolerance_m: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """吸附到目标线数据集（每要素：质心 → 最近目标线最近点，容差内）。

    返回 (deltas, findings, summary)。整体 fail-closed：任一要素无容差内
    候选 → no_snap_candidate finding（plan 不可提交）。
    平移向量按各要素质心的 Geod 逆解计算，逐顶点等量平移（形状不变）。
    """
    findings: List[Dict[str, Any]] = []
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
    deltas: List[Dict[str, Any]] = []
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

        def shift(x, y, _az=az, _dist=dist_move):
            lon2, lat2, _ = geod.fwd(x, y, _az, _dist)
            return lon2, lat2

        moved = shapely_transform(shift, geom)
        bad = _validate_after(moved)
        if bad is not None:
            findings.append({**bad, "wfid": wfid})
            continue
        deltas.append({"wfid": wfid, "geometry_before": geometry, "geometry_after": _geometry_out(moved)})
        snap_distances[wfid] = round(dist, 2)
    summary = {
        "snapped": len(deltas),
        "snap_distances_m": dict(sorted(snap_distances.items())[:50]),
        "tolerance_m": tolerance_m,
    }
    return deltas, findings, summary
