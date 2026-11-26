"""图符布局 —— 锚定策略、确定性避让、密度预算与全量会计（WP2）。

职责边界：给定已资格化的要素/数值/图符包络，产出"每个图符放不放、
放在哪、要不要引线"的**布局计划**（不改变统计口径 —— 被避让的图符
只隐藏并计数，绝不把它的值合并进别的图符）；几何/数值语义仍在
geometry/qualification，本模块不碰。

锚定策略（可解释、可披露）：
- Point → 坐标原样（``point``）；区县统计的"面质心"不是学校真实位置 ——
  面要素一律走代表点并如实标注 ``polygon_representative_point``；
- Polygon/MultiPolygon → shapely ``representative_point()``（保证落在
  多边形内；质心可能落在外 —— 两者差异显式建模，不用质心）；
- LineString/MultiLineString → 参数 0.5 归一化插值（``line_midpoint``）；
- 几何缺失/无效/坐标越界 → 要素 blocked（typed reason 进 evidence），
  不静默修复（G47 cartogram geography 同纪律）。

避让算法（确定性，无随机）：Web Mercator 参考缩放级别下按优先级
（Σ|value| 降序，平局 feature_id 字典序升序）贪心放置；冲突时先沿 8
方位螺旋位移（步长 = 两包络和的一半），位移超阈值标 ``leader_line``
（引线指回真实锚点）；仍冲突 → 隐藏并计入 ``omitted_collision``。
TS 孪生（frontend/lib/mapkit-…/diagram-symbols-layout）逐位同算法，
golden 数值 parity 锁定。

防注入/防爆炸：类别/身份字符串只进 JSON（canonical_json），SVG 转义
是 SVG 消费方职责；坐标有限性 + 纬度域显式校验；要素数/图符数双重
硬帽（超帽 typed BUDGET_EXCEEDED / omitted_budget 会计）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.lib.cartography.diagram_symbols.contracts import (
    DiagramSymbolError,
    canonical_json,
)

#: 布局计划尺寸硬帽（与 AGG_SCAN_LIMIT 5e5、MAX_SPEC_FRAMES 50 同纪律）。
MAX_LAYOUT_FEATURES = 20_000
DEFAULT_MAX_GLYPHS = 1_000
#: 包络间最小净距（px @参考缩放）。
DEFAULT_COLLISION_PADDING_PX = 2.0
#: 位移超过该值（px）→ 标记引线（读者需要知道符号被挪过）。
DEFAULT_LEADER_THRESHOLD_PX = 6.0
#: 8 方位螺旋位移的步数上限（每步位移 = 两包络和的一半）。
MAX_DISPLACEMENT_STEPS = 12

#: Web Mercator 常数（与 MapLibre/前端孪生同口径；EPSG:3857 球面版）。
_WEB_MERCATOR_BASE_PX = 256.0
_WEB_MERCATOR_MAX_LAT = 85.05112878
_PI_4 = math.pi / 4.0

_SQRT2_2 = math.sqrt(2.0) / 2.0
#: 8 方位单位向量（E→NE→N→NW→W→SW→S→SE；固定顺序 = 确定性）。
_NUDGE_DIRECTIONS: Tuple[Tuple[float, float], ...] = (
    (1.0, 0.0), (_SQRT2_2, _SQRT2_2), (0.0, 1.0), (-_SQRT2_2, _SQRT2_2),
    (-1.0, 0.0), (-_SQRT2_2, -_SQRT2_2), (0.0, -1.0), (_SQRT2_2, -_SQRT2_2),
)


# ── Web Mercator（纯数学；与前端孪生逐位一致）──────────────────────────


def lnglat_to_world(lng: float, lat: float) -> Tuple[float, float]:
    """(lng,lat) → Web Mercator 归一化平面 (0..1)。纬度越界 typed 拒绝。"""
    if not (math.isfinite(lng) and math.isfinite(lat)):
        raise DiagramSymbolError(
            "INVALID_COORDINATES",
            f"锚点坐标非有限：lng={lng!r} lat={lat!r}",
            correction_hint="几何锚点必须为有限数值",
        )
    if not (-180.0 <= lng <= 180.0):
        raise DiagramSymbolError(
            "INVALID_COORDINATES", f"经度越界 lng={lng}",
            correction_hint="WGS84 经度域 [-180, 180]",
        )
    if not (-_WEB_MERCATOR_MAX_LAT <= lat <= _WEB_MERCATOR_MAX_LAT):
        raise DiagramSymbolError(
            "INVALID_COORDINATES", f"纬度超出 Web Mercator 域 lat={lat}",
            correction_hint=f"Web Mercator 纬度域 ±{_WEB_MERCATOR_MAX_LAT}",
        )
    x = (lng + 180.0) / 360.0
    sin_lat = math.sin(math.radians(lat))
    y = 0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)
    return x, y


def world_to_lnglat(x: float, y: float) -> Tuple[float, float]:
    """归一化平面 → (lng,lat)（位移回写用）。"""
    lng = x * 360.0 - 180.0
    n = math.pi * (1.0 - 2.0 * y)
    lat = math.degrees(math.atan(math.sinh(n)))
    return lng, lat


def world_px_scale(zoom: float) -> float:
    """该缩放级别下全世界对应的像素宽度。"""
    return _WEB_MERCATOR_BASE_PX * (2.0 ** zoom)


# ── 锚定策略 ───────────────────────────────────────────────────────────


def _first_coord(coords: Any) -> Optional[Tuple[float, float]]:
    if (
        isinstance(coords, (list, tuple)) and len(coords) >= 2
        and isinstance(coords[0], (int, float)) and isinstance(coords[1], (int, float))
    ):
        return (float(coords[0]), float(coords[1]))
    return None


def compute_feature_anchor(feature: Mapping[str, Any]) -> Dict[str, Any]:
    """要素 → 锚点（typed：anchor/anchor_source 或 blocked_reason）。

    Point 原样；Polygon/MultiPolygon 用 shapely representative_point
    （环内保证；与质心的差异是建模事实而非实现细节）；线用 0.5 插值。
    几何缺失/类型不支持/无效 → blocked（不静默修复）。
    """
    geom = feature.get("geometry")
    if not isinstance(geom, Mapping) or geom.get("coordinates") is None:
        return {"blocked_reason": "missing_geometry"}
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    try:
        if gtype == "Point":
            pt = _first_coord(coords)
            if pt is None:
                return {"blocked_reason": "invalid_geometry", "detail": "point coordinates malformed"}
            src = "point"
        elif gtype in ("Polygon", "MultiPolygon"):
            from shapely.geometry import shape
            from shapely.validation import explain_validity

            shp = shape(geom)
            if not shp.is_valid:
                return {
                    "blocked_reason": "invalid_geometry",
                    "detail": explain_validity(shp),
                }
            rep = shp.representative_point()
            pt = (float(rep.x), float(rep.y))
            src = "polygon_representative_point"
        elif gtype in ("LineString", "MultiLineString"):
            from shapely.geometry import shape

            shp = shape(geom)
            if not shp.is_valid:
                from shapely.validation import explain_validity
                return {
                    "blocked_reason": "invalid_geometry",
                    "detail": explain_validity(shp),
                }
            mid = shp.interpolate(0.5, normalized=True)
            pt = (float(mid.x), float(mid.y))
            src = "line_midpoint"
        else:
            return {"blocked_reason": "unsupported_geometry_type", "detail": str(gtype)}
    except Exception as exc:  # shapely 构造失败（嵌套/类型畸形）→ typed 阻塞
        return {"blocked_reason": "invalid_geometry", "detail": str(exc)[:200]}
    return {"anchor": [pt[0], pt[1]], "anchor_source": src}


# ── 布局求解 ───────────────────────────────────────────────────────────


def _normalize_layout_policy(policy: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    p = dict(policy or {})
    max_glyphs = p.get("max_glyphs", DEFAULT_MAX_GLYPHS)
    if not isinstance(max_glyphs, int) or isinstance(max_glyphs, bool) or max_glyphs < 1:
        raise DiagramSymbolError(
            "INVALID_SIZE_POLICY",
            f"layout_policy.max_glyphs 必须是正整数，got {max_glyphs!r}",
            correction_hint=f"1 ≤ max_glyphs ≤ {MAX_LAYOUT_FEATURES}",
        )
    if max_glyphs > MAX_LAYOUT_FEATURES:
        raise DiagramSymbolError(
            "BUDGET_EXCEEDED",
            f"max_glyphs={max_glyphs} 超过硬帽 {MAX_LAYOUT_FEATURES}（无界图符数量拒绝）",
        )
    hide_below = p.get("hide_below_radius_px", 0.0)
    if not isinstance(hide_below, (int, float)) or isinstance(hide_below, bool) \
            or not math.isfinite(float(hide_below)) or hide_below < 0:
        raise DiagramSymbolError(
            "INVALID_SIZE_POLICY", f"hide_below_radius_px 非法：{hide_below!r}")
    padding = p.get("collision_padding_px", DEFAULT_COLLISION_PADDING_PX)
    if not isinstance(padding, (int, float)) or isinstance(padding, bool) \
            or not math.isfinite(float(padding)) or padding < 0:
        raise DiagramSymbolError(
            "INVALID_SIZE_POLICY", f"collision_padding_px 非法：{padding!r}")
    leader_thr = p.get("leader_threshold_px", DEFAULT_LEADER_THRESHOLD_PX)
    if not isinstance(leader_thr, (int, float)) or isinstance(leader_thr, bool) \
            or not math.isfinite(float(leader_thr)) or leader_thr < 0:
        raise DiagramSymbolError(
            "INVALID_SIZE_POLICY", f"leader_threshold_px 非法：{leader_thr!r}")
    min_zoom_floor = p.get("min_zoom_floor", 0)
    if not isinstance(min_zoom_floor, int) or isinstance(min_zoom_floor, bool) \
            or not (0 <= min_zoom_floor <= 22):
        raise DiagramSymbolError(
            "INVALID_SIZE_POLICY", f"min_zoom_floor 非法：{min_zoom_floor!r}",
            correction_hint="取 [0, 22] 整数",
        )
    return {
        "max_glyphs": max_glyphs,
        "hide_below_radius_px": float(hide_below),
        "collision_padding_px": float(padding),
        "leader_threshold_px": float(leader_thr),
        "min_zoom_floor": min_zoom_floor,
        "displacement_enabled": bool(p.get("displacement_enabled", True)),
        "reference_zoom": float(p.get("reference_zoom", 12.0)),
    }


def _glyph_min_zoom(radius_px: float, floor: int) -> int:
    """小图符抬升 min_zoom（屏幕 px 恒定 → 小图符在低缩放下不可辨）。

    确定性分层：<4px → floor+2；<8px → floor+1；否则 floor。
    """
    if radius_px < 4.0:
        return min(floor + 2, 22)
    if radius_px < 8.0:
        return min(floor + 1, 22)
    return floor


def solve_diagram_symbol_layout(
    features: Sequence[Mapping[str, Any]],
    qualified: Mapping[str, Any],
    bound_radii: Sequence[float],
    *,
    layout_policy: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """确定性图符布局（schema ``diagram_symbol_layout.v1``）。

    features 与 qualified["values"]/["glyph_feature_ids"] 同序；bound_radii
    与之一一对应（geometry.glyph_bound_radius 的输出）。

    输出 glyphs（仅可见项）每项：
    ``{feature_id, anchor:[lng,lat], anchor_source, position:[lng,lat],
    displacement_px, leader_line, radius_px, priority_rank, min_zoom}``，
    plus ``evidence``（placed/omitted_collision/omitted_budget/
    omitted_small/blocked/displaced/leader 全计数 + blocked 明细 + digest）。
    """
    policy = _normalize_layout_policy(layout_policy)
    values = qualified["values"]
    ids = qualified["glyph_feature_ids"]
    if not (len(features) == len(values) == len(bound_radii)):
        raise DiagramSymbolError(
            "FIELD_NOT_FOUND",
            f"features({len(features)})/values({len(values)})"
            f"/bound_radii({len(bound_radii)}) 数量不一致",
            correction_hint="三者必须同序等长（qualification 输出直接喂入）",
        )
    if len(features) > MAX_LAYOUT_FEATURES:
        raise DiagramSymbolError(
            "BUDGET_EXCEEDED",
            f"要素数 {len(features)} 超过硬帽 {MAX_LAYOUT_FEATURES}",
            correction_hint="先聚合/抽稀（有会计），不要无界渲染",
        )
    if len(set(ids)) != len(ids):
        raise DiagramSymbolError(
            "DUPLICATE_CATEGORY", "glyph_feature_ids 存在重复（图符 identity 必须稳定唯一）",
            correction_hint="检查数据集 id 唯一性",
        )

    # 1) 锚定 + 阻塞收集
    candidates: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    anchor_counts: Dict[str, int] = {}
    for idx, (feature, fid, row, radius) in enumerate(zip(features, ids, values, bound_radii)):
        anchor_res = compute_feature_anchor(feature)
        if "blocked_reason" in anchor_res:
            blocked.append({"feature_id": str(fid), **anchor_res})
            continue
        lng, lat = float(anchor_res["anchor"][0]), float(anchor_res["anchor"][1])
        try:
            wx, wy = lnglat_to_world(lng, lat)
        except DiagramSymbolError as exc:
            blocked.append({"feature_id": str(fid), "blocked_reason": exc.code,
                            "detail": exc.message})
            continue
        src = str(anchor_res["anchor_source"])
        anchor_counts[src] = anchor_counts.get(src, 0) + 1
        importance = math.fsum(abs(float(v)) for _, v in row)
        candidates.append({
            "feature_id": str(fid), "wx": wx, "wy": wy,
            "anchor": [lng, lat], "anchor_source": src,
            "radius_px": float(radius), "importance": importance, "index": idx,
        })

    # 2) 确定性优先级：importance 降序，平局 feature_id 字典序升序
    candidates.sort(key=lambda c: (-c["importance"], c["feature_id"]))

    # 3) 贪心放置（参考缩放像素平面）
    scale = world_px_scale(policy["reference_zoom"])
    padding = policy["collision_padding_px"]
    placed_boxes: List[Tuple[float, float, float]] = []  # (px, py, r_eff)
    glyphs: List[Dict[str, Any]] = []
    omitted_collision = 0
    omitted_budget = 0
    omitted_small = 0
    displaced_count = 0
    leader_count = 0

    def _collides(px: float, py: float, r: float) -> bool:
        for ox, oy, orr in placed_boxes:
            rr = r + orr + padding
            if (px - ox) ** 2 + (py - oy) ** 2 < rr * rr:
                return True
        return False

    for cand in candidates:
        r = cand["radius_px"]
        if r < policy["hide_below_radius_px"]:
            omitted_small += 1
            continue
        if len(glyphs) >= policy["max_glyphs"]:
            omitted_budget += 1
            continue
        bx = cand["wx"] * scale
        by = cand["wy"] * scale
        position_px = (bx, by)
        displaced_px = 0.0
        if _collides(bx, by, r):
            placed_here = False
            if policy["displacement_enabled"]:
                step = (r + padding) / 2.0
                for step_idx in range(1, MAX_DISPLACEMENT_STEPS + 1):
                    dist = step * step_idx
                    for dx, dy in _NUDGE_DIRECTIONS:
                        nx, ny = bx + dx * dist, by + dy * dist
                        if not _collides(nx, ny, r):
                            position_px = (nx, ny)
                            displaced_px = dist
                            placed_here = True
                            break
                    if placed_here:
                        break
            if not placed_here:
                omitted_collision += 1
                continue
        placed_boxes.append((position_px[0], position_px[1], r))
        pos_lng, pos_lat = world_to_lnglat(
            position_px[0] / scale, position_px[1] / scale
        )
        leader = displaced_px > policy["leader_threshold_px"]
        if displaced_px > 0:
            displaced_count += 1
        if leader:
            leader_count += 1
        glyphs.append({
            "feature_id": cand["feature_id"],
            "anchor": cand["anchor"],
            "anchor_source": cand["anchor_source"],
            "position": [round(pos_lng, 9), round(pos_lat, 9)],
            "displacement_px": round(displaced_px, 3),
            "leader_line": leader,
            "radius_px": round(r, 3),
            "min_zoom": _glyph_min_zoom(r, policy["min_zoom_floor"]),
        })

    # priority_rank 按输出顺序（贪心放置序 = 重要性序）
    for rank, glyph in enumerate(glyphs):
        glyph["priority_rank"] = rank

    evidence = {
        "candidates": len(candidates),
        "placed": len(glyphs),
        "omitted_collision": omitted_collision,
        "omitted_budget": omitted_budget,
        "omitted_small": omitted_small,
        "blocked": len(blocked),
        "blocked_detail": blocked[:64],  # 有界明细（防响应放大）
        "blocked_truncated": len(blocked) > 64,
        "displaced": displaced_count,
        "leader_lines": leader_count,
        "anchor_source_counts": anchor_counts,
        "features_total": len(features),
        "policy": policy,
    }
    return {
        "schema": "diagram_symbol_layout.v1",
        "glyphs": glyphs,
        "evidence": evidence,
        "digest": _layout_digest(glyphs),
    }


def _layout_digest(glyphs: Sequence[Mapping[str, Any]]) -> str:
    import hashlib

    return hashlib.sha256(
        canonical_json(list(glyphs)).encode("utf-8")
    ).hexdigest()[:32]
