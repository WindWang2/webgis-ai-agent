"""编辑期局部拓扑检查（G14 / WP06）。

纪律：
- **规则按声明生效**：只检查数据集 envelope ``topology`` 键（或 op 显式
  ``validate_topology``）声明的规则 —— 未声明的业务层绝不套用行政区
  覆盖规则（V10）。
- **局部邻域**：空间索引（STRtree）限定受影响要素的邻域，不做全域
  两两 O(n²)；候选对数有界（≤ MAX_TOPOLOGY_PAIRS）。
- **检查不修复**：发现即 finding（带定位 bbox 与受影响对），修复必须走
  显式操作重新预览 —— 绝不在校验时静默吞洞/丢 part/改边界。
- 单位：重叠面积容差以平方米声明，经 Geod 在要素局部尺度换算为平方度
  判定（小邻域近似，误差披露于 detail.tolerance_approx）。
"""
from __future__ import annotations

from app.contracts.geojson import GeoJSONFeatureCollection

from app.services.feature_edit.wire_types import EditFinding

from shapely.geometry import shape
from shapely.strtree import STRtree
from typing import Any, Dict, List, Optional, Tuple

from app.services.feature_edit.contracts import (
    DEFAULT_TOPOLOGY_TOLERANCE_M,
    TOPOLOGY_RULES,
    WFID_PROP,
    finding,
)

__all__ = ["extract_topology_declaration", "check_topology"]

# 邻域检查候选对上限（超限 → 仅检查受影响要素与最近邻，披露 truncated）。
MAX_TOPOLOGY_PAIRS = 200
# no_overlap 判定的 bbox 预过滤扩展（度；≈ 容差对应尺度 + 4 邻域缓冲）。
_BBOX_BUFFER_DEG = 0.01


def extract_topology_declaration(payload: Any) -> Dict[str, Any]:
    """从载荷 envelope 提取拓扑声明（无声明 → 空 dict = 无规则）。"""
    if not isinstance(payload, dict):
        return {}
    decl = payload.get("topology")
    if not isinstance(decl, dict):
        return {}
    rules = [r for r in (decl.get("rules") or []) if r in TOPOLOGY_RULES]
    out: Dict[str, Any] = {}
    if rules:
        out["rules"] = list(dict.fromkeys(rules))
    tolerance = decl.get("tolerance_m")
    if isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool):
        out["tolerance_m"] = float(tolerance)
    return out


def _wfid_of(feature: Any) -> Optional[str]:
    if not isinstance(feature, dict):
        return None
    props = feature.get("properties")
    wfid = props.get(WFID_PROP) if isinstance(props, dict) else None
    return wfid if isinstance(wfid, str) else None


def _m2_to_deg2_approx(m2: float, lat_deg: float) -> float:
    """平方米 → 平方度的局部尺度换算（小邻域平面近似；判定容差用）。"""
    from app.services.feature_edit.geometry_ops import geodesic_distance_m

    # 该纬度 1 度经度的米数（近似），取 0.01° 步长实测再平方缩放。
    d = geodesic_distance_m(0.0, lat_deg, 0.01, lat_deg)
    if d <= 0:
        return 0.0
    deg_per_m = 0.01 / d
    return m2 * (deg_per_m * deg_per_m)


def check_topology(
    fc: GeoJSONFeatureCollection,
    affected_wfids: List[str],
    declaration: Dict[str, Any],
) -> List[EditFinding]:
    """对编辑后的数据集做声明规则的局部检查。

    fc 是**操作后**形态；affected_wfids 是本操作触碰（创建/修改/保留
    但相邻）的稳定身份集合 —— no_overlap 只检查受影响要素与其邻域，
    不做全域重扫。返回 findings（空 = 通过）。
    """
    rules = declaration.get("rules") or []
    if not rules:
        return []
    findings: List[EditFinding] = []
    features = [f for f in (fc.get("features") or []) if isinstance(f, dict)]
    geoms: List[Any] = []
    wfids: List[str] = []
    for feature in features:
        geometry = feature.get("geometry")
        wfid = _wfid_of(feature)
        if not isinstance(geometry, dict) or wfid is None:
            continue
        try:
            geom = shape(geometry)
        except Exception:
            continue
        if geom.is_empty:
            continue
        geoms.append(geom)
        wfids.append(wfid)
    affected = set(affected_wfids)
    by_wfid = dict(zip(wfids, geoms))
    index_of = {w: i for i, w in enumerate(wfids)}
    tolerance_m = float(declaration.get("tolerance_m") or DEFAULT_TOPOLOGY_TOLERANCE_M)

    if "no_self_intersection" in rules:
        for wfid in sorted(affected):
            geom = by_wfid.get(wfid)
            if geom is None:
                continue
            if geom.geom_type in ("Polygon", "MultiPolygon") and not geom.is_valid:
                findings.append(
                    finding(
                        "topology_violation",
                        "feature self-intersects (declared no_self_intersection)",
                        rule="no_self_intersection",
                        wfid=wfid,
                        bbox=list(geom.bounds),
                    )
                )
            elif geom.geom_type in ("LineString", "MultiLineString") and not geom.is_simple:
                findings.append(
                    finding(
                        "topology_violation",
                        "line feature self-intersects (declared no_self_intersection)",
                        rule="no_self_intersection",
                        wfid=wfid,
                        bbox=list(geom.bounds),
                    )
                )

    if "no_duplicate_points" in rules:
        seen: Dict[Tuple[float, float], str] = {}
        for wfid in sorted(affected & set(wfids)):
            geom = by_wfid.get(wfid)
            if geom is None or not geom.geom_type.startswith("Point"):
                continue
            key = (round(geom.x, 9), round(geom.y, 9))
            if key in seen:
                findings.append(
                    finding(
                        "topology_violation",
                        "duplicate point feature (declared no_duplicate_points)",
                        rule="no_duplicate_points",
                        wfid=wfid,
                        duplicate_of=seen[key],
                        bbox=[geom.x, geom.y, geom.x, geom.y],
                    )
                )
            else:
                seen[key] = wfid
        # 与非受影响点也比对（受影响点可能撞上既有静止点）
        for i, geom in enumerate(geoms):
            w = wfids[i]
            if w in affected or not geom.geom_type.startswith("Point"):
                continue
            key = (round(geom.x, 9), round(geom.y, 9))
            if key in seen:
                findings.append(
                    finding(
                        "topology_violation",
                        "edited point duplicates an existing point (declared no_duplicate_points)",
                        rule="no_duplicate_points",
                        wfid=seen[key],
                        duplicate_of=w,
                        bbox=[geom.x, geom.y, geom.x, geom.y],
                    )
                )

    if "no_overlap" in rules:
        polygons = [(w, g) for w, g in zip(wfids, geoms) if g.geom_type in ("Polygon", "MultiPolygon")]
        if polygons:
            poly_list = [g for _w, g in polygons]
            tree = STRtree(poly_list)
            pairs_checked = 0
            truncated = False
            reported: set = set()
            for w, geom in polygons:
                if w not in affected and not _near_affected(geom, affected, by_wfid):
                    continue  # 局部检查：只看受影响要素的邻域
                for j in tree.query(geom.buffer(_BBOX_BUFFER_DEG)):
                    other = polygons[int(j)][0]
                    other_geom = polygons[int(j)][1]
                    if other == w:
                        continue
                    pair = tuple(sorted((w, other)))
                    if pair in reported:
                        continue
                    if w not in affected and other not in affected:
                        continue  # 两个静止要素的历史重叠不由本次编辑背书
                    pairs_checked += 1
                    if pairs_checked > MAX_TOPOLOGY_PAIRS:
                        truncated = True
                        break
                    inter = geom.intersection(other_geom)
                    if inter.is_empty:
                        continue
                    area_deg2 = inter.area
                    lat = (geom.bounds[1] + geom.bounds[3]) / 2.0
                    tol_deg2 = _m2_to_deg2_approx(tolerance_m * tolerance_m, lat)
                    if area_deg2 > max(tol_deg2, 1e-14):
                        reported.add(pair)
                        findings.append(
                            finding(
                                "topology_violation",
                                "features overlap (declared no_overlap)",
                                rule="no_overlap",
                                wfid=w,
                                other_wfid=other,
                                overlap_area_m2=_deg2_to_m2_approx(area_deg2, lat),
                                bbox=list(inter.bounds),
                                tolerance_m=tolerance_m,
                            )
                        )
                if truncated:
                    break
            if truncated:
                findings.append(
                    finding(
                        "topology_violation",
                        "overlap neighborhood exceeded the pair budget; narrow the edit scope",
                        rule="no_overlap",
                        truncated=True,
                    )
                )
    return findings[:24]


def _near_affected(geom: Any, affected: set, by_wfid: Dict[str, Any]) -> bool:
    """静止要素是否落在任一受影响要素的邻域（bbox 级快速判定）。"""
    minx, miny, maxx, maxy = geom.bounds
    for w in affected:
        other = by_wfid.get(w)
        if other is None:
            continue
        ominx, ominy, omaxx, omaxy = other.bounds
        if not (maxx + _BBOX_BUFFER_DEG < ominx or minx - _BBOX_BUFFER_DEG > omaxx
                or maxy + _BBOX_BUFFER_DEG < ominy or miny - _BBOX_BUFFER_DEG > omaxy):
            return True
    return False


def _deg2_to_m2_approx(deg2: float, lat_deg: float) -> float:
    """平方度 → 平方米（披露用近似值）。"""
    from app.services.feature_edit.geometry_ops import geodesic_distance_m

    d = geodesic_distance_m(0.0, lat_deg, 0.01, lat_deg)
    if d <= 0:
        return 0.0
    m_per_deg = d / 0.01
    return deg2 * (m_per_deg * m_per_deg)
