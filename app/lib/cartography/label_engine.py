"""Label Engine Foundation — 确定性制图标注布局引擎（Cartographic Design V4）.

与前端 MapLibre symbol 标注的关系：MapLibre 在 GPU 上逐帧做交互态标注，
其碰撞避让随缩放/相机实时变化、结果依赖渲染时序。本引擎**不替代**它 ——
交互面的标注仍完全由 MapLibre 承担。本引擎服务于两个纯确定性场景：

1. **导出画布 / SVG 的确定性标注**：离线渲染没有 MapLibre 的碰撞器，
   导出产物必须可复现、可审计；
2. **语义检查 ``carto.label.*`` 的确定性估计**：``collision_est`` 目前只做
   标注盒面积占比的粗估计，本引擎提供同口径的免费升级路径（真实盒碰撞、
   位移与抑制计数），且同样不做任何渲染。

确定性契约：同输入两次求解的 ``model_dump()`` **完全相等**（含浮点 ——
无随机数、无时间因子、无集合迭代序依赖；``sorted`` 为稳定排序，候选生成
顺序固定）。golden 测试可直接锁定输出。

算法（单遍贪心 + 均匀格网空间索引，同输入永远同输出）：

1. 门控（按 (priority 升序, id 字典序) 逐要素）：空文本 → ``empty_text``；
   zoom < min_zoom → ``below_min_zoom``（先判空文本后判 zoom）；
2. 候选生成：
   - point：8 方位 GIS 惯例序（``DECLUTTER_CANDIDATE_OFFSETS``，首选右上），
     offset = font_size × 0.75，角度 0，代价 = 序号；
   - line：沿线等弧长取 k 个锚点（shield 取线中点单候选），角度取所在线段
     方位角并 keep-upright 翻转到 [-90, 90]；同文本已放置锚点间距 <
     repeat_distance 的候选跳过；
   - polygon：质心 + representative_point 两个候选，角度 0；
3. 标签框估算 ``estimate_label_box``：宽按字符加权（CJK 1.0em / 其他
   0.6em）× font_size，高 = font_size × 1.2；
4. 放置循环：候选按 cost 升序（稳定），AABB 相交（均匀格网查询，自动格宽
   下为 3×3 邻域量级）+ 标签框完全落在视口内 → 放置并记入索引；
   - point 三段退让：全候选碰撞 → 右上 max_displacement 处再试一次 →
     仍碰撞则 callout（允许与已放置标签重叠，由引线消歧；仍要求完整在视口
     内，出视口才最终抑制）。三段式自洽的关键：callout 阶段放宽盒碰撞 ——
     否则它与第二段同位同判、永远不会触发；
   - line/polygon 不做位移退让：沿线 repeat 与第二内点是它们的疏散手段，
     把线标注推离线或面标注推离面在制图上无意义；
5. 输出：placements（placed + callout）、suppressed（含 reason）、warnings
   （抑制比例 >50% 时一条中文告警）与 stats。

抑制理由枚举：``empty_text`` / ``below_min_zoom`` / ``collision`` /
``repeat_distance``（同文本候选全部被 repeat_distance 跳过）/
``no_candidates``（几何退化无候选）/ ``label_too_long``（放置失败且文本
超过 ``LABEL_TOO_LONG_THRESHOLD`` 字符 —— 长文本的抑制根因是框过大，
如实标注而不是笼统报 collision；门控理由 empty_text/below_min_zoom 优先，
不被覆盖）。标签框锚定约定：point 标注 (x, y) 为标签框**左下角**（位移
方向语义自然），line/polygon 为标签框**中心**。

文本适配契约（W3，纯函数增量，不改变求解语义）：``fit_label_text`` 按
max_chars 截断加省略号、``wrap_label_text`` 按 CJK 宽度口径贪心断行，
``MAX_SVG_LABEL_CHARS`` 是导出孪生（Python SVG 编译器 / 前端 exporter）
共享的单标签字符上限。
"""
from __future__ import annotations

import math
from typing import Dict, List, Literal, Tuple

from pydantic import BaseModel
from shapely.geometry import Polygon

# V11 W0.2（ADR-0160）：排版原语单一实现收敛至 label_typography；本模块
# 只保留候选生成与求解语义。公共名在此 re-export（既有 import 不变）。
from app.lib.cartography.label_typography import (
    LABEL_TOO_LONG_THRESHOLD,
    MAX_SVG_LABEL_CHARS,
    DECLUTTER_CANDIDATE_OFFSETS,
    Box,
    LabelGrid,
    centered_box,
    corner_box,
    estimate_label_box,
    fit_label_text,
    inside_viewport,
    keep_upright,
    normalize_angle,
    wrap_label_text,
)

# ── 候选生成前的公共契约（文本适配/角度/AABB/格网均见 label_typography）──


class LabelCandidate(BaseModel):
    """单条候选：锚点 + 沿线角度（度）+ 代价（越小越优先）。"""

    x: float
    y: float
    angle: float = 0.0
    cost: float = 0.0


class LabelFeature(BaseModel):
    """求解输入：一个待标注要素。

    geometry 约定：point 为 ``[[x, y]]``；line 为有序点列；polygon 为环点列
    （闭合与否不强求，shapely 自动闭合）。坐标可以是地图坐标或画布坐标 ——
    本引擎只做几何，无投影概念。
    """

    id: str
    text: str
    kind: Literal["point", "line", "polygon"]
    priority: int = 50              # 小值优先（与 layout solver 同约定）
    min_zoom: float = 0.0           # 当前 zoom < min_zoom 则不参与
    geometry: list[list[float]]
    font_size: float = 12.0
    max_displacement: float = 24.0  # 点标注最大位移（画布单位）
    allow_callout: bool = True      # 位移超限时是否允许引线标注（callout）
    shield: bool = False            # 盾牌牌匾标注（道路编号）


class LabelPlacement(BaseModel):
    """单条输出：标注落位 / 引线标注 / 抑制。"""

    feature_id: str
    x: float
    y: float
    angle: float
    status: Literal["placed", "callout", "suppressed"]
    leader: list[list[float]] | None = None  # callout 引线 [[锚点],[标签左下角]]
    reason: str = ""                          # placed 默认空；抑制时必填


class LabelSolution(BaseModel):
    """求解输出：placements 含 placed + callout，suppressed 单列。"""

    placements: list[LabelPlacement] = []
    suppressed: list[LabelPlacement] = []
    warnings: list[str] = []
    stats: Dict[str, int] = {}


class LabelEngineInput(BaseModel):
    """求解请求包。"""

    features: list[LabelFeature]
    viewport: list[float]           # [minx, miny, maxx, maxy]，标签框须完全落在其中
    zoom: float = 10.0
    repeat_distance: float = 200.0  # 线标注同文本重复的最小间距
    grid_cell: float = 0.0          # 空间索引格宽；0 = 自动（平均标签宽高 ×2）


# ── 标签框估算 / 文本适配（fit / wrap）/ 角度 / AABB / 格网 ────────────────
# V11 W0.2：全部收敛到 label_typography 单一实现（模块头 re-export）；
# ``estimate_label_box`` 等 golden 语义不变，既有测试无需改动。
# ── 候选生成（确定性）────────────────────────────────────────────────────
def _point_candidates(feat: LabelFeature) -> List[LabelCandidate]:
    """8 方位候选：dx, dy = ±offset（offset = font_size × 0.75），代价 = 序号。"""
    if not feat.geometry:
        return []
    offset = feat.font_size * 0.75
    px, py = float(feat.geometry[0][0]), float(feat.geometry[0][1])
    return [
        LabelCandidate(x=px + dx * offset, y=py + dy * offset, angle=0.0, cost=float(i))
        for i, (dx, dy) in enumerate(DECLUTTER_CANDIDATE_OFFSETS)
    ]


def _line_candidates(feat: LabelFeature, text_width: float) -> List[LabelCandidate]:
    """沿线等弧长锚点 + 段方位角 keep-upright；shield 取线中点单候选。"""
    pts = [(float(p[0]), float(p[1])) for p in feat.geometry]
    if len(pts) < 2:
        return []
    seg_len: List[float] = []
    cum: List[float] = [0.0]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        d = math.hypot(x2 - x1, y2 - y1)
        seg_len.append(d)
        cum.append(cum[-1] + d)
    total = cum[-1]
    if total <= 0.0:
        return []
    if feat.shield:
        stations = [total / 2.0]
    else:
        k = min(len(pts), max(1, int(total / (text_width * 1.5))))
        stations = [(i + 0.5) * total / k for i in range(k)]

    n_seg = len(seg_len)
    cands: List[LabelCandidate] = []
    for idx, s in enumerate(stations):
        j = 0
        while j < n_seg - 1 and cum[j + 1] <= s:
            j += 1
        while j < n_seg - 1 and seg_len[j] == 0.0:  # 跳过重复顶点的零长段
            j += 1
        d = seg_len[j]
        if d <= 0.0:
            continue
        t = min(max((s - cum[j]) / d, 0.0), 1.0)
        x1, y1 = pts[j]
        x2, y2 = pts[j + 1]
        cands.append(LabelCandidate(
            x=x1 + (x2 - x1) * t,
            y=y1 + (y2 - y1) * t,
            angle=keep_upright(math.degrees(math.atan2(y2 - y1, x2 - x1))),
            cost=float(idx),
        ))
    return cands


def _polygon_candidates(feat: LabelFeature) -> List[LabelCandidate]:
    """质心 + representative_point 两个候选（后者保证落在环内）。"""
    if len(feat.geometry) < 3:
        return []
    try:
        poly = Polygon([(float(p[0]), float(p[1])) for p in feat.geometry])
        centroid = poly.centroid
        inside = poly.representative_point()
    except Exception:  # 自交/退化环：shapely 抛错则无候选（确定性）
        return []
    cands: List[LabelCandidate] = []
    if math.isfinite(centroid.x) and math.isfinite(centroid.y):
        cands.append(LabelCandidate(x=centroid.x, y=centroid.y, angle=0.0, cost=0.0))
    if math.isfinite(inside.x) and math.isfinite(inside.y):
        cands.append(LabelCandidate(x=inside.x, y=inside.y, angle=0.0, cost=1.0))
    return cands


def _box_for(feat: LabelFeature, cand: LabelCandidate, w: float, h: float) -> Box:
    if feat.kind == "point":
        return corner_box(cand.x, cand.y, w, h)  # 点标注角度恒 0
    return centered_box(cand.x, cand.y, w, h, cand.angle)


def _feature_anchor(feat: LabelFeature) -> Tuple[float, float]:
    """抑制记录用的锚点（首顶点；空几何回原点）。"""
    if feat.geometry:
        return (float(feat.geometry[0][0]), float(feat.geometry[0][1]))
    return (0.0, 0.0)


# ── 主求解（单遍贪心）────────────────────────────────────────────────────
def solve_labels(payload: LabelEngineInput) -> LabelSolution:
    """确定性标注布局求解（单遍；同输入两次求解 ``model_dump()`` 完全相等）。"""
    vp = payload.viewport
    ordered = sorted(payload.features, key=lambda f: (f.priority, f.id))

    # 1. 门控 + 候选生成（gate 顺序：空文本 → min_zoom）
    gated: List[Tuple[LabelFeature, float, float, List[LabelCandidate]]] = []
    suppressed: List[LabelPlacement] = []
    for f in ordered:
        anchor = _feature_anchor(f)
        if not f.text.strip():
            suppressed.append(LabelPlacement(
                feature_id=f.id, x=anchor[0], y=anchor[1], angle=0.0,
                status="suppressed", reason="empty_text",
            ))
            continue
        if payload.zoom < f.min_zoom:
            suppressed.append(LabelPlacement(
                feature_id=f.id, x=anchor[0], y=anchor[1], angle=0.0,
                status="suppressed", reason="below_min_zoom",
            ))
            continue
        w, h = estimate_label_box(f.text, f.font_size)
        if f.kind == "point":
            cands = _point_candidates(f)
        elif f.kind == "line":
            cands = _line_candidates(f, w)
        else:
            cands = _polygon_candidates(f)
        gated.append((f, w, h, cands))

    # 2. 格网：显式格宽优先；自动 = 平均标签最大边 ×2
    if payload.grid_cell > 0.0:
        cell = payload.grid_cell
    elif gated:
        avg_w = sum(bw for _, bw, _, _ in gated) / len(gated)
        avg_h = sum(bh for _, _, bh, _ in gated) / len(gated)
        cell = 2.0 * max(avg_w, avg_h)
    else:
        cell = 48.0
    grid = LabelGrid(cell)

    # 3. 放置循环（候选按 cost 升序，稳定排序）
    placements: List[LabelPlacement] = []
    anchors_by_text: Dict[str, List[Tuple[float, float]]] = {}
    cand_total = 0
    placed_n = 0
    callout_n = 0

    for f, w, h, cands in gated:
        cand_total += len(cands)
        sorted_cands = sorted(cands, key=lambda c: c.cost)
        px, py = _feature_anchor(f)

        prev_anchors = anchors_by_text.get(f.text, ()) if f.kind == "line" else ()
        repeat_blocked = 0
        chosen: Tuple[LabelCandidate, Box, str] | None = None
        for c in sorted_cands:
            if prev_anchors:
                dmin = min(math.hypot(c.x - ax, c.y - ay) for ax, ay in prev_anchors)
                if dmin < payload.repeat_distance:
                    repeat_blocked += 1
                    continue
            box = _box_for(f, c, w, h)
            if inside_viewport(box, vp) and not grid.collides(box):
                chosen = (c, box, "placed")
                break

        # 点要素三段退让：全候选碰撞 → max_displacement 右上再试 → callout
        if chosen is None and f.kind == "point" and f.allow_callout and cands:
            sx = px + f.max_displacement
            sy = py + f.max_displacement
            box2 = corner_box(sx, sy, w, h)
            if inside_viewport(box2, vp):
                if not grid.collides(box2):
                    chosen = (LabelCandidate(x=sx, y=sy, angle=0.0,
                                             cost=float(len(sorted_cands))),
                              box2, "placed_max_displacement")
                else:
                    # callout：允许与已放置标签重叠（引线消歧），视口约束保留
                    chosen = (LabelCandidate(x=sx, y=sy, angle=0.0,
                                             cost=float(len(sorted_cands))),
                              box2, "callout")

        if chosen is None:
            if not cands:
                reason = "no_candidates"
            elif f.kind == "line" and repeat_blocked == len(sorted_cands):
                reason = "repeat_distance"
            else:
                reason = "collision"
            # W3：放置失败且文本超长者，抑制根因是标签框过大 —— 如实标注
            # label_too_long（门控理由 empty_text/below_min_zoom 在更早的
            # gate 分支已落地，不受此覆盖影响）。
            if len(f.text) > LABEL_TOO_LONG_THRESHOLD:
                reason = "label_too_long"
            suppressed.append(LabelPlacement(
                feature_id=f.id, x=px, y=py, angle=0.0,
                status="suppressed", reason=reason,
            ))
            continue

        c, box, mode = chosen
        if mode == "callout":
            callout_n += 1
            placements.append(LabelPlacement(
                feature_id=f.id, x=c.x, y=c.y, angle=normalize_angle(c.angle),
                status="callout", leader=[[px, py], [c.x, c.y]],
                reason="displacement_exceeded",
            ))
        else:
            placed_n += 1
            placements.append(LabelPlacement(
                feature_id=f.id, x=c.x, y=c.y, angle=normalize_angle(c.angle),
                status="placed",
                reason=("max_displacement" if mode == "placed_max_displacement" else ""),
            ))
        grid.insert(box)
        if f.kind == "line":
            anchors_by_text.setdefault(f.text, []).append((c.x, c.y))

    # 4. 汇总
    total = len(payload.features)
    sup_n = len(suppressed)
    warnings: List[str] = []
    if total > 0 and sup_n / total > 0.5:
        warnings.append(
            f"标注抑制比例过高：{sup_n}/{total} 个标注未能放置（碰撞或视口限制），"
            "建议抽稀要素、缩小字号或增大画布"
        )
    return LabelSolution(
        placements=placements,
        suppressed=suppressed,
        warnings=warnings,
        stats={
            "total": total,
            "candidates": cand_total,
            "placed": placed_n,
            "callout": callout_n,
            "suppressed": sup_n,
        },
    )


__all__ = [
    "LabelCandidate",
    "LabelFeature",
    "LabelPlacement",
    "LabelSolution",
    "LabelEngineInput",
    "solve_labels",
    "estimate_label_box",
    "fit_label_text",
    "wrap_label_text",
    "DECLUTTER_CANDIDATE_OFFSETS",
    "MAX_SVG_LABEL_CHARS",
    "LABEL_TOO_LONG_THRESHOLD",
]
