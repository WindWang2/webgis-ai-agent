"""带身份的标注图元障碍抽取（G16 geometry_v1）。

在既有 circle 障碍（W2.1，匿名 AABB、保留前 200）之上，按显式
``layout.labels.obstaclePolicy == "geometry_v1"`` 提取：

- **线墨迹**：逐段 AABB（外扩实际 stroke 半宽）—— 长线绝不用整线巨型
  bbox 占满画布；每要素段数有界（均匀步长采样）+ 视口预筛；
- **面边界墨迹**：外环逐段 AABB（面**填充默认不是障碍** —— 区内标签可
  放；「禁止文字压入此区域」用 forbiddenZones 显式声明）；
- **circle 点符号**：沿用 W2.1 外扩口径，补身份；
- **显式禁入面**：ring 网格分解（中心点偶奇判定进栅格单元；披露
  近似口径）；
- **页面组件遮挡**：floating placement 的 legend/north_arrow/scale_bar/
  title/subtitle 矩形（≤16）。

全部携带 :class:`~app.lib.cartography.label_collision.ObstacleBox` 身份
（kind/layer_id/feature_id），供求解器做自身墨迹豁免与跨层区分。预算：
分组公平交错（zones → components → 图层轮转 → circles），总量以
:data:`MAX_OBSTACLES_PER_SOLVE` 为硬界，截断数如实返回（调用方披露）。
legacy ``circles_legacy`` 策略不走本模块 —— 行为逐字节不变。

纯函数、确定性（同输入恒同输出）；Python/TS 双孪生（frontend/lib/
mapspec-compiler/label-obstacles.ts）。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.lib.cartography.label_collision import MAX_OBSTACLES_PER_SOLVE, ObstacleBox

__all__ = [
    "MAX_SEGMENTS_PER_FEATURE",
    "MAX_SEGMENT_OBSTACLES_PER_LAYER",
    "ZONE_GRID_TARGET_RANK",
    "ZONE_GRID_MIN_CELL_PX",
    "ZONE_GRID_MAX_CELL_PX",
    "MAX_ZONE_GRID_CELLS",
    "COMPONENT_OBSTACLE_TYPES",
    "MAX_COMPONENT_OBSTACLES",
    "extract_line_obstacles",
    "extract_polygon_boundary_obstacles",
    "extract_zone_obstacles",
    "extract_component_obstacles",
    "interleave_budgeted",
    "project_box_intersects_viewport",
]

#: 单要素段数上限（均匀步长采样 —— 长线确定性降采样，不是巨型 bbox）。
MAX_SEGMENTS_PER_FEATURE = 64

#: 单图层线/面边界段障碍上限（图层级预算，交错前）。
MAX_SEGMENT_OBSTACLES_PER_LAYER = 320

#: 禁入面网格分解口径：目标格边 = bbox 长边 / 20，钳制 [16, 64]px
#:（格大于标签盒高 → 缝隙放不下整标签；格小于 16px → 单元数爆炸）；
#: 单个禁入面格数上限（超限按行主序确定性抽稀 + 披露）。
ZONE_GRID_TARGET_RANK = 20
ZONE_GRID_MIN_CELL_PX = 16.0
ZONE_GRID_MAX_CELL_PX = 64.0
MAX_ZONE_GRID_CELLS = 96

#: 作为页面组件遮挡的组件类型（渲染在地图之上、占实际版面）。
COMPONENT_OBSTACLE_TYPES = (
    "legend", "continuous_colorbar", "categorical_legend",
    "north_arrow", "scale_bar", "title", "subtitle",
)
MAX_COMPONENT_OBSTACLES = 16

Box = Tuple[float, float, float, float]
ProjectFn = Callable[[Sequence[float]], Tuple[Any, Any]]
StrokeWidthFn = Callable[[Dict[str, Any]], float]


def project_box_intersects_viewport(box: Box, viewport: Sequence[float]) -> bool:
    """盒与视口相交预筛（完全在外 → False）。"""
    return not (
        box[2] <= viewport[0] or box[0] >= viewport[2]
        or box[3] <= viewport[1] or box[1] >= viewport[3]
    )


def _iter_rings(coords: Any) -> List[List[Sequence[float]]]:
    """LineString/MultiLineString/Polygon/MultiPolygon → 线串列表。

    Polygon 只取外环（边界墨迹语义 —— 洞边界的墨迹同样真实，但保守
    起见 v1 只取外环；洞内放置由外环障碍与文本盒实际碰撞决定）。"""
    out: List[List[Sequence[float]]] = []
    if not isinstance(coords, list):
        return out

    def _is_pt(c: Any) -> bool:
        return (
            isinstance(c, (list, tuple)) and len(c) >= 2
            and isinstance(c[0], (int, float)) and isinstance(c[1], (int, float))
        )

    if coords and _is_pt(coords[0]):
        out.append(coords)  # LineString / 单环
        return out
    for sub in coords:
        if not isinstance(sub, list):
            continue
        if sub and _is_pt(sub[0]):
            out.append(sub)  # Multi 线 / Polygon 环层
        else:
            for ring in sub:
                if isinstance(ring, list) and ring and _is_pt(ring[0]):
                    out.append(ring)  # MultiPolygon
    return out


def _segment_boxes(
    coords: Any,
    *,
    layer_id: str,
    feature_id: Optional[str],
    kind: str,
    project: ProjectFn,
    stroke_px: float,
    viewport: Sequence[float],
    per_feature_cap: int,
    budget_left: int,
) -> Tuple[List[ObstacleBox], int]:
    """几何 → 逐段 AABB 障碍（确定性均匀步长采样 + 视口预筛）。"""
    obstacles: List[ObstacleBox] = []
    used = 0
    half = abs(stroke_px) / 2.0 + 0.5  # 半墨宽 + 半像素防贴边漏检
    for ring in _iter_rings(coords):
        n = len(ring)
        if n < 2 or used >= per_feature_cap or budget_left - used <= 0:
            break
        stride = max(1, math.ceil((n - 1) / per_feature_cap))
        i = 0
        while i < n - 1 and used < per_feature_cap and used < budget_left:
            j = min(i + stride, n - 1)
            ax, ay = project(ring[i])
            bx, by = project(ring[j])
            try:
                ax, ay, bx, by = float(ax), float(ay), float(bx), float(by)
            except (TypeError, ValueError):
                i = j
                continue
            x1, x2 = sorted((ax, bx))
            y1, y2 = sorted((ay, by))
            box = (x1 - half, y1 - half, x2 + half, y2 + half)
            if project_box_intersects_viewport(box, viewport):
                obstacles.append(ObstacleBox(
                    box=box, kind=kind, layer_id=layer_id, feature_id=feature_id))
                used += 1
            i = j
    return obstacles, used


def extract_line_obstacles(
    features: Sequence[Dict[str, Any]],
    *,
    layer_id: str,
    project: ProjectFn,
    stroke_width: StrokeWidthFn,
    viewport: Sequence[float],
    budget: int = MAX_SEGMENT_OBSTACLES_PER_LAYER,
) -> List[ObstacleBox]:
    """line 图层要素 → 逐段墨迹障碍（stroke 由 paint 解析回调给 px）。"""
    out: List[ObstacleBox] = []
    remaining = budget
    for feat in features:
        if remaining <= 0:
            break
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        if not isinstance(geom, dict) or geom.get("type") not in (
                "LineString", "MultiLineString"):
            continue
        props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
        from app.lib.cartography.label_authoring import resolve_feature_identity
        fid, _ = resolve_feature_identity(feat)
        segs, _ = _segment_boxes(
            geom.get("coordinates"), layer_id=layer_id, feature_id=fid,
            kind="line", project=project, stroke_px=stroke_width(props),
            viewport=viewport,
            per_feature_cap=MAX_SEGMENTS_PER_FEATURE, budget_left=remaining)
        out.extend(segs)
        remaining -= len(segs)
    return out


def extract_polygon_boundary_obstacles(
    features: Sequence[Dict[str, Any]],
    *,
    layer_id: str,
    project: ProjectFn,
    stroke_width: StrokeWidthFn,
    viewport: Sequence[float],
    budget: int = MAX_SEGMENT_OBSTACLES_PER_LAYER,
) -> List[ObstacleBox]:
    """polygon 图层要素 → 外环边界墨迹障碍（填充不参与 —— 语义纪律）。"""
    out: List[ObstacleBox] = []
    remaining = budget
    for feat in features:
        if remaining <= 0:
            break
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        if not isinstance(geom, dict) or geom.get("type") not in (
                "Polygon", "MultiPolygon"):
            continue
        props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
        from app.lib.cartography.label_authoring import resolve_feature_identity
        fid, _ = resolve_feature_identity(feat)
        coords = geom.get("coordinates")
        rings: Any = coords
        if geom.get("type") == "Polygon":
            rings = [coords] if isinstance(coords, list) else []
        segs, _ = _segment_boxes(
            rings, layer_id=layer_id, feature_id=fid, kind="polygon_boundary",
            project=project, stroke_px=stroke_width(props), viewport=viewport,
            per_feature_cap=MAX_SEGMENTS_PER_FEATURE, budget_left=remaining)
        out.extend(segs)
        remaining -= len(segs)
    return out


def _point_in_ring(x: float, y: float, ring: Sequence[Sequence[float]]) -> bool:
    """偶奇规则点在多边形内判定（边界视为内 —— 保守）。"""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def extract_zone_obstacles(
    zones: Sequence[Dict[str, Any]],
    *,
    project: ProjectFn,
    viewport: Sequence[float],
) -> List[ObstacleBox]:
    """显式禁入面 → 网格分解障碍（kind="zone"，feature_id=zone:<id>）。

    近似口径：ZONE_GRID_RANK×ZONE_GRID_RANK 网格、中心点偶奇判定 ——
    边界处最多半个格宽的容差，调用方在披露中注明 grid 近似。
    """
    out: List[ObstacleBox] = []
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        zid = zone.get("id")
        ring = zone.get("ring")
        if not isinstance(zid, str) or not isinstance(ring, list) or len(ring) < 4:
            continue
        try:
            pts = [(float(p[0]), float(p[1])) for p in ring if len(p) >= 2]
        except (TypeError, ValueError):
            continue
        if len(pts) < 4:
            continue
        # 投影环 → 屏幕网格（经度跨越投影非线性由逐点 project 承担）。
        proj_pts: List[Tuple[float, float]] = []
        for p in pts:
            try:
                px, py = project(p)
                proj_pts.append((float(px), float(py)))
            except (TypeError, ValueError):
                continue
        if len(proj_pts) < 4:
            continue
        xs = [p[0] for p in proj_pts]
        ys = [p[1] for p in proj_pts]
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        if max_x <= min_x or max_y <= min_y:
            continue
        long_side = max(max_x - min_x, max_y - min_y)
        cell = min(max(long_side / ZONE_GRID_TARGET_RANK, ZONE_GRID_MIN_CELL_PX),
                   ZONE_GRID_MAX_CELL_PX)

        def _decompose(cell_px: float) -> List[ObstacleBox]:
            step_x = (max_x - min_x) / max(1, math.ceil((max_x - min_x) / cell_px))
            step_y = (max_y - min_y) / max(1, math.ceil((max_y - min_y) / cell_px))
            rank_x = max(1, math.ceil((max_x - min_x) / step_x))
            rank_y = max(1, math.ceil((max_y - min_y) / step_y))
            cells_i: List[ObstacleBox] = []
            for gy in range(rank_y):
                for gx in range(rank_x):
                    cx = min_x + (gx + 0.5) * step_x
                    cy = min_y + (gy + 0.5) * step_y
                    if not _point_in_ring(cx, cy, proj_pts):
                        continue
                    box = (cx - step_x / 2, cy - step_y / 2,
                           cx + step_x / 2, cy + step_y / 2)
                    if project_box_intersects_viewport(box, viewport):
                        cells_i.append(ObstacleBox(
                            box=box, kind="zone", layer_id=None,
                            feature_id=f"zone:{zid}"))
            return cells_i

        # 超单元上限 → 逐级放大格（2×，≤4 级）直到进预算 —— 放大保持
        # 铺满（无缝隙），比抽稀（留大洞）诚实；仍超才抽稀兜底。
        cells = _decompose(cell)
        _grown = 0
        while len(cells) > MAX_ZONE_GRID_CELLS and _grown < 4:
            cell *= 2.0
            cells = _decompose(cell)
            _grown += 1
        if len(cells) > MAX_ZONE_GRID_CELLS:
            stride = math.ceil(len(cells) / MAX_ZONE_GRID_CELLS)
            cells = cells[::stride]
        out.extend(cells)
    return out


def extract_component_obstacles(
    components: Sequence[Dict[str, Any]],
    *,
    viewport: Sequence[float],
    scale: float = 1.0,
) -> List[ObstacleBox]:
    """页面组件（floating placement 有宽高者）→ 矩形遮挡障碍。

    placement 坐标是**页面 px**（layout_geometry CanvasSpec 口径）；
    ``scale`` = 目标空间 / 页面 px（导出侧 dpi/72）—— 障碍盒必须落在
    与标签同一坐标系里。缺省 1.0（页面 px 空间自用）。
    """
    out: List[ObstacleBox] = []
    for comp in components:
        if len(out) >= MAX_COMPONENT_OBSTACLES:
            break
        if not isinstance(comp, dict):
            continue
        ctype = comp.get("type")
        if ctype not in COMPONENT_OBSTACLE_TYPES:
            continue
        placement = comp.get("placement")
        if not isinstance(placement, dict) or placement.get("mode") != "floating":
            continue
        try:
            x = float(placement.get("x")) * scale
            y = float(placement.get("y")) * scale
            w = float(placement.get("width")) * scale
            h = float(placement.get("height")) * scale
        except (TypeError, ValueError):
            continue
        if not (w > 0 and h > 0):
            continue
        cid = comp.get("id") if isinstance(comp.get("id"), str) else None
        box = (x, y, x + w, y + h)
        if project_box_intersects_viewport(box, viewport):
            out.append(ObstacleBox(
                box=box, kind="component", layer_id=None,
                feature_id=f"component:{cid}" if cid else None))
    return out


def interleave_budgeted(
    groups: Sequence[Sequence[ObstacleBox]],
    *,
    total_cap: int = MAX_OBSTACLES_PER_SOLVE,
) -> Tuple[List[ObstacleBox], int]:
    """分组公平交错到总预算（确定性；后层关键墨迹不被首层静默挤掉）。

    组间轮转（groups 序即优先序：zones → components → 图层绘制序 →
    circles），每轮各组取一个；总截断数如实返回供披露。
    """
    queues = [list(g) for g in groups if g]
    out: List[ObstacleBox] = []
    truncated = 0
    total = sum(len(q) for q in queues)
    idx = [0] * len(queues)
    while len(out) < total_cap:
        progressed = False
        for qi, queue in enumerate(queues):
            if len(out) >= total_cap:
                break
            if idx[qi] < len(queue):
                out.append(queue[idx[qi]])
                idx[qi] += 1
                progressed = True
        if not progressed:
            break
    truncated = max(0, total - len(out))
    return out, truncated
