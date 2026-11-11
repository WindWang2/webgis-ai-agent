"""Export Label Collision Solver — portable twin subset (V6, ADR-0120 W6).

``layout.labels.collision == "deterministic"`` 时导出孪生（Python/TS）共用
的确定性碰撞求解子集。与 ``label_engine.solve_labels``（harness 工具链，
shapely 候选）的关系：**同一几何口径的便携子集** —— estimate_label_box/
DECLUTTER 序/collides 语义逐常量一致；子集差异（诚实登记）：

- line/polygon 单锚点候选（锚点与 keep-upright 角度由调用方传入 —— 孪生
  编译器已在要素循环中算好中点/质心与段方位角）；不做等弧长多站点点位、
  不做 representative_point（shapely 不可移植）。
- 不做 callout 引线（导出件引线渲染属后续 wave）；放不下 = 抑制 +
  ``label_collision_relaxed`` 披露。

纯函数、无随机、无 locale；Python/TS 由共享差分 fixtures 锁定
（tests/cartography/golden_corpus/label_collision/，坐标按 3 位小数对齐）。

V11 W0.2（ADR-0160）：排版原语（CJK 判定/标签框估算/角度/AABB/格网/
8 方位序）收敛至 :mod:`app.lib.cartography.label_typography` 单一实现；
本模块只保留孪生请求模型与 ``solve_export_labels`` 求解语义。注意本模块
使用孪生语义 :func:`keep_upright_export_twin`（与 TS ``keepUpright`` 逐
分支等价、被 parity corpus 冻结），而非引擎语义 :func:`keep_upright`。
"""
from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.label_typography import (
    DECLUTTER_CANDIDATE_OFFSETS,
    Box,
    LabelGrid as _Grid,
    centered_box as _centered_box,
    corner_box as _corner_box,
    estimate_label_box,
    inside_viewport as _inside_viewport,
    keep_upright_export_twin as _keep_upright,
    overlaps as _overlaps,
)

#: 单次导出标签请求上限（超出部分抑制 + budget 披露；R1 资源包络）。
MAX_LABELS_PER_EXPORT = 400

#: 单次求解接受的图元障碍盒上限（超出截断，截断数记 stats
#: ``obstacles_truncated``；W2.1/ADR-0283 D3 资源包络 —— 障碍×候选为
#: O(N×8) 逐盒判定，200 上限与调用侧每层 200 点的采样口径对齐；
#: geometry_v1 抽取侧做视口预筛 + 跨层公平交错后仍以此帽为硬界）。
MAX_OBSTACLES_PER_SOLVE = 200

#: 点标注 8 方位候选序（label_engine.DECLUTTER_CANDIDATE_OFFSETS 同表）。
DECLUTTER_OFFSETS = DECLUTTER_CANDIDATE_OFFSETS

#: 自身图元豁免的障碍种类（G16 geometry_v1）：线/面边界的标签沿其自身
#: 墨迹设计放置，自身墨迹不拦自身标签；circle 不豁免（点符号避让是
#: 设计意图，标签 8 方位候选本就绕开符号）。跨要素/跨层恒不豁免。
OWN_ANCHOR_EXEMPT_KINDS = frozenset({"line", "polygon_boundary"})

#: 自动项 rank 排序基（G16）：带 user_priority 的自动项恒先于纯自动项
#: （rank + 1e6 —— 用户优先级不必与抽稀名次换算；MAX_AUTHORING_PRIORITY
#: = 1000 << 1e6，词表内恒成立）。
_AUTO_RANK_BASE = 1_000_000


@dataclass
class ObstacleBox:
    """带身份的图元障碍（G16 geometry_v1）。

    兼容：``solve_export_labels`` 同时接受裸 :data:`Box` 元组（legacy
    circles_legacy 语义 —— 无身份、无豁免、行为逐字节不变）。带身份
    障碍的豁免规则见 :data:`OWN_ANCHOR_EXEMPT_KINDS`。
    """

    box: Box
    kind: str = "circle"      # circle | line | polygon_boundary | zone | component
    layer_id: Optional[str] = None
    feature_id: Optional[str] = None


@dataclass
class CollisionLabel:
    """求解输入（孪生标签请求）。

    G16 authoring 扩展（全部 Optional —— 缺省即 legacy 行为逐字节
    不变）：hidden/pinned/offset_em/user_priority/layer_id/feature_id。
    ``offset_em`` 为 canonical em 偏移（相对自身字号；+dy 屏幕向下，
    与 MapLibre ``text-offset`` 同向 —— live/导出同式换算）。
    """

    id: str
    text: str
    kind: str                 # point | line | polygon
    x: float                  # point: 锚点；line/polygon: 中心
    y: float
    angle: float = 0.0        # line/polygon 沿线角（度，调用方 keep-upright 后传入）
    font_size: float = 12.0
    priority: int = 0         # 小值优先；同 priority 按 id 字典序
    # ── G16 authoring（additive optional）──────────────────────────────
    hidden: Optional[bool] = None
    pinned: Optional[bool] = None
    offset_em: Optional[Tuple[float, float]] = None
    user_priority: Optional[int] = None
    layer_id: Optional[str] = None
    feature_id: Optional[str] = None


@dataclass
class CollisionPlacement:
    id: str
    x: float
    y: float
    angle: float
    status: str               # placed | suppressed
    reason: str = ""
    # ── G16：pinned 放置携带冲突旗标（user-wins —— 恒放置、冲突如实
    # 报告，绝不自动移动/解锁；stats 给分型计数）───────────────────────
    pinned: bool = False
    conflict: bool = False

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": self.id, "x": self.x, "y": self.y,
                               "angle": self.angle, "status": self.status,
                               "reason": self.reason}
        if self.pinned:
            out["pinned"] = True
        if self.conflict:
            out["conflict"] = True
        return out


@dataclass
class CollisionSolution:
    placements: List[CollisionPlacement] = _dc_field(default_factory=list)
    stats: Dict[str, int] = _dc_field(default_factory=dict)
    budget_exceeded: bool = False

    @property
    def suppressed_count(self) -> int:
        return sum(1 for p in self.placements if p.status == "suppressed")


def solve_export_labels(
    features: List[CollisionLabel],
    viewport: List[float],
    *,
    max_labels: int = MAX_LABELS_PER_EXPORT,
    obstacles: Optional[List[Any]] = None,
) -> CollisionSolution:
    """确定性导出标签求解（单遍贪心；同输入恒同输出）。

    预算：``len(auto_features) > max_labels`` 时，排序尾部溢出部分直接抑制
    （reason="budget"），``budget_exceeded=True`` —— 调用方据此发射
    ``label_budget_exceeded`` 诊断。

    图元障碍（W2.1，ADR-0283 D3）：``obstacles`` 为图元（点符号等）的
    AABB 盒列表 ``(x1, y1, x2, y2)`` 或 :class:`ObstacleBox`（G16
    geometry_v1 带身份）。候选位估算盒与任一障碍盒严格重叠（贴边不算，
    与 label-label collision 同口径）→ 该候选位不可用；全部候选位被占 →
    按既有抑制路径抑制（reason="obstacle"，stats 计 ``obstacle_hits``）。
    障碍数超 :data:`MAX_OBSTACLES_PER_SOLVE` 时**保留前 N**（确定性），
    截断数记 stats ``obstacles_truncated``。

    G16 authoring（全部 Optional；无 authoring 输入 = legacy 行为逐字节
    不变 —— 兼容由 golden corpus 双侧锁定）：

    - ``hidden`` → suppressed（reason="hidden_by_user"），不占预算；
    - ``pinned`` → **先于全部自动项**按 ``(user_priority ?? 0, id)`` 序
      放置在 锚点+offset_em×font_size 中心位（point 发左上角坐标、
      line/polygon 发中心坐标 —— 与渲染映射分支同口径）。pinned 恒放置
      （user-wins）：与已放标签/障碍/视口的冲突不移动它，只置
      ``conflict=True`` 并计 stats（``pinned_conflicts`` /
      ``pinned_obstacle_overlaps`` / ``pinned_offscreen``）。empty_text
      仍抑制（无字可放）；
    - ``user_priority``（未 pin 的自动项）→ 排序键 ``(user_priority,
      priority, id)`` 且恒先于纯自动项（rank 键 +_AUTO_RANK_BASE）——
      用户「这条重要」不需要与抽稀名次换算；
    - 障碍身份豁免：``ObstacleBox.kind ∈ OWN_ANCHOR_EXEMPT_KINDS`` 且
      (layer_id, feature_id) 与标签一致 → 该障碍不拦此标签（自身线/面
      墨迹语义）；circle 恒不豁免（点符号避让是设计）。匿名 Box 无
      豁免（legacy 契约）。

    兼容锁定：``obstacles`` 缺省/None/空列表 → 输出（placements + stats
    形态）与无障碍完全一致 —— stats 仅在传入非空障碍时携带
    ``obstacle_hits``/``obstacles_truncated``；authoring stats 键仅在
    存在 hidden/pinned/user_priority 输入时出现（对拍兼容）。

    锚点豁免（legacy 注释语义保留）：匿名障碍下调用方仍负责排除标签
    **自身锚点**的图元障碍（孪生编译器中标注来自 symbol 层、符号来自
    circle 层，天然不同层）。
    """
    # ── G16 分群：hidden / pinned / auto（legacy 输入三退化为纯 auto）──
    hidden_fs = [f for f in features if f.hidden]
    pinned_fs = sorted(
        (f for f in features if f.pinned and not f.hidden),
        key=lambda f: (f.user_priority if f.user_priority is not None else 0, f.id),
    )
    auto_ordered = sorted(
        (f for f in features if not f.hidden and not f.pinned),
        key=lambda f: (
            (f.user_priority if f.user_priority is not None
             else f.priority + _AUTO_RANK_BASE),
            f.priority if f.user_priority is not None else 0,
            f.id,
        ),
    )
    has_authoring = bool(
        hidden_fs or pinned_fs
        or any(f.user_priority is not None for f in features)
    )

    placements: List[CollisionPlacement] = []
    for f in hidden_fs:
        placements.append(CollisionPlacement(
            id=f.id, x=f.x, y=f.y, angle=0.0,
            status="suppressed", reason="hidden_by_user",
        ))

    budget_exceeded = len(auto_ordered) > max_labels
    gated = auto_ordered[:max_labels]
    overflow = auto_ordered[max_labels:]
    for f in overflow:
        placements.append(CollisionPlacement(
            id=f.id, x=f.x, y=f.y, angle=0.0, status="suppressed", reason="budget",
        ))

    active_obstacles: List[ObstacleBox] = []
    obstacles_truncated = 0
    if obstacles:
        normalized = [ob if isinstance(ob, ObstacleBox) else ObstacleBox(box=tuple(ob))  # type: ignore[arg-type]
                      for ob in obstacles]
        obstacles_truncated = max(0, len(normalized) - MAX_OBSTACLES_PER_SOLVE)
        active_obstacles = normalized[:MAX_OBSTACLES_PER_SOLVE]

    def _obstacle_hit(label: CollisionLabel, box: Box) -> bool:
        for ob in active_obstacles:
            if (
                ob.feature_id is not None
                and ob.kind in OWN_ANCHOR_EXEMPT_KINDS
                and label.layer_id is not None
                and label.feature_id is not None
                and ob.layer_id == label.layer_id
                and ob.feature_id == label.feature_id
            ):
                continue  # 自身线/面墨迹不拦自身标签（geometry_v1 语义）
            if _overlaps(box, ob.box):
                return True
        return False

    # ── 尺寸准备（pinned + auto 同批参与格网 cell 定径）─────────────────
    pinned_prepared: List[Tuple[CollisionLabel, float, float]] = []
    pinned_conflicts = 0
    pinned_obstacle_overlaps = 0
    pinned_offscreen = 0
    for f in pinned_fs:
        if not f.text.strip() or f.font_size <= 0:
            placements.append(CollisionPlacement(
                id=f.id, x=f.x, y=f.y, angle=0.0,
                status="suppressed", reason="empty_text",
            ))
            continue
        w, h = estimate_label_box(f.text, f.font_size)
        pinned_prepared.append((f, w, h))

    prepared: List[Tuple[CollisionLabel, float, float]] = []
    for f in gated:
        if not f.text.strip() or f.font_size <= 0:
            # R2-M11：font_size<=0 → 负/零尺寸盒使格网失效（互叠压）——同
            # empty_text 抑制；TS 孪生同口径
            placements.append(CollisionPlacement(
                id=f.id, x=f.x, y=f.y, angle=0.0, status="suppressed", reason="empty_text",
            ))
            continue
        w, h = estimate_label_box(f.text, f.font_size)
        prepared.append((f, w, h))

    all_sized = pinned_prepared + prepared
    cell = (2.0 * max(max(w, h) for _, w, h in all_sized)) if all_sized else 48.0
    grid = _Grid(max(cell, 1.0))

    # ── pinned 先放置（user-wins：恒放置，冲突如实报告）────────────────
    placed_n = 0
    for f, w, h in pinned_prepared:
        dx_em, dy_em = f.offset_em or (0.0, 0.0)
        cx = f.x + dx_em * f.font_size
        cy = f.y + dy_em * f.font_size
        if f.kind == "point":
            # point 渲染映射消费左上角坐标（盒角 → 基线补偿）。
            px, py = cx - w / 2.0, cy - h / 2.0
            box = (px, py, px + w, py + h)
            angle = 0.0
        else:
            angle = _keep_upright(f.angle)
            box = _centered_box(cx, cy, w, h, angle)
            px, py = cx, cy
        conflict = False
        if grid.collides(box):
            conflict = True
            pinned_conflicts += 1
        if active_obstacles and _obstacle_hit(f, box):
            conflict = True
            pinned_obstacle_overlaps += 1
        if not _inside_viewport(box, viewport):
            conflict = True
            pinned_offscreen += 1
        placed_n += 1
        placements.append(CollisionPlacement(
            id=f.id, x=px, y=py, angle=angle, status="placed", reason="",
            pinned=True, conflict=conflict,
        ))
        grid.insert(box)

    # ── 自动项（legacy 贪心；grid 已被 pinned 占位）────────────────────
    collision_n = 0
    obstacle_n = 0
    for f, w, h in prepared:
        chosen: Optional[Tuple[float, float, float, Box]] = None
        if f.kind == "point":
            offset = f.font_size * 0.75
            obstacle_blocked = False
            for dx, dy in DECLUTTER_OFFSETS:
                cx = f.x + dx * offset
                cy = f.y + dy * offset
                box = _corner_box(cx, cy, w, h)
                if not _inside_viewport(box, viewport) or grid.collides(box):
                    continue
                if active_obstacles and _obstacle_hit(f, box):
                    # 该候选位仅因障碍不可用（视口/标签碰撞均放行）——
                    # 记障碍受阻证据，继续尝试后续候选位。
                    obstacle_blocked = True
                    continue
                chosen = (cx, cy, 0.0, box)
                break
            if chosen is None:
                if obstacle_blocked:
                    obstacle_n += 1
                    placements.append(CollisionPlacement(
                        id=f.id, x=f.x, y=f.y, angle=0.0,
                        status="suppressed", reason="obstacle",
                    ))
                else:
                    collision_n += 1
                    placements.append(CollisionPlacement(
                        id=f.id, x=f.x, y=f.y, angle=0.0,
                        status="suppressed", reason="collision",
                    ))
                continue
        else:
            box = _centered_box(f.x, f.y, w, h, _keep_upright(f.angle))
            if not (_inside_viewport(box, viewport) and not grid.collides(box)):
                collision_n += 1
                placements.append(CollisionPlacement(
                    id=f.id, x=f.x, y=f.y, angle=0.0,
                    status="suppressed", reason="collision",
                ))
                continue
            if active_obstacles and _obstacle_hit(f, box):
                obstacle_n += 1
                placements.append(CollisionPlacement(
                    id=f.id, x=f.x, y=f.y, angle=0.0,
                    status="suppressed", reason="obstacle",
                ))
                continue
            chosen = (f.x, f.y, _keep_upright(f.angle), box)

        cx, cy, ang, box = chosen
        placed_n += 1
        placements.append(CollisionPlacement(
            id=f.id, x=cx, y=cy, angle=ang, status="placed", reason="",
        ))
        grid.insert(box)

    stats: Dict[str, int] = {
        "total": len(features),
        "placed": placed_n,
        "suppressed": len(features) - placed_n,
        "collisions": collision_n,
    }
    if active_obstacles:
        stats["obstacle_hits"] = obstacle_n
        stats["obstacles_truncated"] = obstacles_truncated
    if has_authoring:
        stats["hidden_by_user"] = len(hidden_fs)
        stats["pinned_placed"] = len(pinned_prepared)
        if pinned_conflicts:
            stats["pinned_conflicts"] = pinned_conflicts
        if pinned_obstacle_overlaps:
            stats["pinned_obstacle_overlaps"] = pinned_obstacle_overlaps
        if pinned_offscreen:
            stats["pinned_offscreen"] = pinned_offscreen
    return CollisionSolution(
        placements=placements,
        stats=stats,
        budget_exceeded=budget_exceeded,
    )
