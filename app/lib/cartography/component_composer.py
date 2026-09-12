"""Component Composer — 版面合成决策层（AC-07, ADR-0156）.

把「摆上去」升级为「排出来」的三个纯函数面（本模块不做渲染、不持有状态）：

1. ``plan_layout_repairs`` —— LAYOUT_COLLISION 的确定性修复规划器。
   策略链（§0.5 默认决策序，逐级降级）：改 anchor → 缩尺寸 → 折叠进
   溢出面板 → 隐藏最低优先组件。输入现状组件列表，输出 suggested_fix
   actions（semantic_checks 的 LAYOUT_COLLISION 修复建议段消费；前端
   composition-repair 执行同链）。冲突无解时按链尾降级，不抛异常。

2. ``required_components_for(purpose, content)`` —— 缺项自动补全。
   按输出用途（screen_16_9 / screen_4_3 / a4_portrait / a4_landscape）
   与内容要素（投影信息 / 数据来源 / 专题层 / 统计面板 / 区位语境）
   决定必配清单。数据来源未知 → 补「数据来源：—（待补充）」占位 +
   advisory（禁止省略署名，§0.5）。替代 live 侧 ``__fallback_*`` 的
   被动注入：主动补全 + 记录决策；fallback 保留为安全网。

3. ``CompositionDecision`` —— 版面决策工件（可审计）。09 线评审、
   10 线回归、live/export 两侧对账都消费同一结构。

与既有模块的关系：``layout_solver``（V2/V3/V4）管槽位求解；本模块把
求解/检测输出翻译成「动作 + 理由 + 证据」。单一职责：决策与修复语义。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from app.lib.cartography.layout_constraints import (
    EXCLUSIVE_ZONES,
    ZONE_CAPACITY,
    ZONE_POSITIONS,
)

#: 全部合法槽位（与 layout_solver.ZONE_ORDER 同表；复制以避免私有依赖）。
_ZONES = ("top-left", "top-center", "top-right",
          "bottom-left", "bottom-center", "bottom-right")

#: singleton 词表（与 layout_solver.SINGLETON_TYPES 同表）。
SINGLETON_TYPES = frozenset({"title", "north_arrow", "scale_bar", "attribution"})

#: 可折叠面板族（折叠为溢出面板的合法类型；与前端 COLLAPSIBLE_PANEL_TYPES
#: 同域并扩展披露族 —— 前端词表收敛由消费端各自的折叠语义决定）。
COLLAPSIBLE_TYPES = frozenset({
    "statistics_panel", "chart_panel", "table_panel",
    "decision_panel", "uncertainty_panel", "methodology_note",
})

#: 策略链动作词表（QA suggested_fix 与前端执行器共用的唯一词表）。
REPAIR_ACTIONS = ("change_anchor", "shrink", "collapse_to_overflow", "hide_lowest_priority")


# ── 决策工件 ─────────────────────────────────────────────────────────────


class CompositionDecision(BaseModel):
    """一条版面决策（可审计工件；live/export/评审三方同构）。"""

    step: int = Field(ge=0, description="决策序号（0 起，确定性）")
    kind: str = Field(description="autofill | fallback_hit | repair | suppress")
    component_id: str = ""
    component_type: str = ""
    before: str = ""
    after: str = ""
    reason: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)


# ── 1. LAYOUT_COLLISION 修复规划 ─────────────────────────────────────────


class _Comp:
    """规划器的内部组件视图（从 raw dict / SimpleNamespace 归一）。"""

    __slots__ = ("id", "type", "enabled", "position", "priority", "optional", "floating")

    def __init__(
        self, cid: str, ctype: str, enabled: bool, position: str,
        priority: int, optional: bool = True, floating: bool = False,
    ) -> None:
        self.id = cid
        self.type = ctype
        self.enabled = enabled
        self.position = position
        self.priority = priority
        self.optional = optional
        self.floating = floating


def _normalize(components: Iterable[Any]) -> List[_Comp]:
    out: List[_Comp] = []
    for c in components:
        if isinstance(c, dict):
            placement = c.get("placement") if isinstance(c.get("placement"), dict) else {}
            out.append(_Comp(
                cid=str(c.get("id") or ""),
                ctype=str(c.get("type") or ""),
                enabled=c.get("enabled") is not False,
                position=(
                    "none" if placement.get("mode") == "floating"
                    else str(c.get("position") or "none")
                ),
                priority=int(c.get("priority") or 0),
                optional=c.get("required") is not True,
                floating=placement.get("mode") == "floating",
            ))
        else:
            # SimpleNamespace 适配（semantic_checks._adapt 同形）
            out.append(_Comp(
                cid=str(getattr(c, "id", "") or ""),
                ctype=str(getattr(c, "type", "") or ""),
                enabled=bool(getattr(c, "enabled", True)),
                position=str(getattr(c, "position", "none") or "none"),
                priority=int(getattr(c, "priority", 0) or 0),
                optional=getattr(c, "required", False) is not True,
                floating=False,
            ))
    return out


def _zone_loads(comps: List[_Comp]) -> Dict[str, List[_Comp]]:
    loads: Dict[str, List[_Comp]] = {z: [] for z in _ZONES}
    for c in comps:
        if c.enabled and c.position in loads:
            loads[c.position].append(c)
    return loads


def floating_overlap_pairs(components: List[Any]) -> List[tuple]:
    """floating 组件矩形相交的 id 对（归一化 x/y/width/height 几何的
    **单一实现** —— 字符串披露（semantic_checks）与修复建议两侧共用）。"""
    floating: List[Dict[str, float]] = []
    for c in components:
        if isinstance(c, dict):
            placement = c.get("placement") if isinstance(c.get("placement"), dict) else {}
            enabled = c.get("enabled") is not False
            cid = str(c.get("id") or c.get("type") or "?")
        else:
            placement = getattr(c, "placement", None)
            placement = placement if isinstance(placement, dict) else {}
            enabled = bool(getattr(c, "enabled", True))
            cid = str(getattr(c, "id", "") or getattr(c, "type", "") or "?")
        if not enabled or placement.get("mode") != "floating":
            continue
        try:
            floating.append({
                "id": cid,
                "x": float(placement.get("x", 0)),
                "y": float(placement.get("y", 0)),
                "w": float(placement.get("width", 0) or 0),
                "h": float(placement.get("height", 0) or 0),
            })
        except (TypeError, ValueError):
            continue
    pairs: List[tuple] = []
    for i in range(len(floating)):
        for j in range(i + 1, len(floating)):
            a, b = floating[i], floating[j]
            if a["w"] <= 0 or a["h"] <= 0 or b["w"] <= 0 or b["h"] <= 0:
                continue
            overlap_x = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            overlap_y = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if overlap_x > 0 and overlap_y > 0:
                pairs.append((a["id"], b["id"]))
    return pairs


def plan_layout_repairs(
    components: Iterable[Any],
    *,
    zone_capacity: Optional[Dict[str, int]] = None,
    floating_overlaps: Optional[List[tuple]] = None,
) -> List[Dict[str, Any]]:
    """LAYOUT_COLLISION → 确定性修复动作链（suggested_fix.actions）。

    纯函数：同输入同输出；不动原列表。动作词表见 ``REPAIR_ACTIONS``。
    处理序（确定性全序）：

    1. 超容 / exclusive 重复：对每个超容槽，槽内按 (priority desc, id asc)
       保留前 N（N=容量），其余逐个走策略链（改 anchor → 缩尺寸 → 折叠 →
       让位隐藏）。缩尺寸在此建模为 width_units 收敛声明（前端像素真值
       自行投影），对槽位计数超容的根因无效果时自动落到下一级。
    2. singleton 重复：同型多实例保留 priority 最小（最贴边）者，其余
       hide_lowest_priority（reason=duplicate_singleton）。
    3. floating 重叠：重叠对（调用方注入几何真值 —— semantic_checks 的
       ``_detect_floating_overlaps`` 已有单一实现，本侧不做二次几何）中
       priority 大者建议折叠（user-wins：不挪用户位置，只建议收纳）。
    """
    comps = _normalize(components)
    caps = dict(ZONE_CAPACITY)
    if zone_capacity:
        caps.update(zone_capacity)
    actions: List[Dict[str, Any]] = []
    step = [0]

    def _emit(action: str, comp: _Comp, **kw: Any) -> None:
        step[0] += 1
        item: Dict[str, Any] = {
            "action": action,
            "component_id": comp.id,
            "component_type": comp.type,
            "step": step[0],
            "reason": kw.pop("reason", ""),
        }
        item.update(kw)
        actions.append(item)

    live = [c for c in comps if c.enabled]

    # 1. 槽位超容 / exclusive 重复（exclusive 槽有效容量恒 1 —— top-center）
    loads = _zone_loads(live)
    for zone in _ZONES:
        cap = 1 if zone in EXCLUSIVE_ZONES else caps.get(zone, 2)
        occupants = sorted(loads.get(zone, []), key=lambda c: (c.priority, c.id))
        if len(occupants) <= cap:
            continue
        for c in occupants[cap:]:
            _rehome(c, loads, caps, _emit)

    # 2. singleton 重复（同型多实例：保 priority 最小，余者隐藏）
    by_type: Dict[str, List[_Comp]] = {}
    for c in live:
        if c.type in SINGLETON_TYPES:
            by_type.setdefault(c.type, []).append(c)
    for ctype, group in sorted(by_type.items()):
        if len(group) <= 1:
            continue
        group_sorted = sorted(group, key=lambda c: (c.priority, c.id))
        for c in group_sorted[1:]:
            _emit(
                "hide_lowest_priority", c,
                reason=f"duplicate_singleton:{ctype}",
                from_=c.position,
            )

    # 3. floating 重叠（未注入时按单一几何实现自检；priority 大者建议折叠）
    by_id = {c.id: c for c in comps}
    seen_pairs: set = set()
    for pair_raw in (floating_overlaps if floating_overlaps is not None
                     else floating_overlap_pairs(components)):
        try:
            a_id, b_id = str(pair_raw[0]), str(pair_raw[1])
        except (TypeError, IndexError, KeyError):
            continue
        pair = tuple(sorted((a_id, b_id)))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        a, b = by_id.get(pair[0]), by_id.get(pair[1])
        loser = max((a, b), key=lambda c: (c.priority if c else 0, c.id if c else ""))
        if loser is not None and loser.type in COLLAPSIBLE_TYPES:
            _emit(
                "collapse_to_overflow", loser,
                reason="floating_overlap:user_wins_advisory",
                overlap_with=pair[0] if loser.id == pair[1] else pair[1],
            )
    return actions


def _rehome(
    c: _Comp,
    loads: Dict[str, List[_Comp]],
    caps: Dict[str, int],
    emit: Any,
) -> None:
    """策略链：改 anchor → 缩尺寸 → 折叠 → 隐藏（§0.5 序，逐级降级）。"""
    from_pos = c.position
    # L1 改 anchor：按 ZONE_POSITIONS 邻接序找有容量的槽（确定性）
    for zone in ZONE_POSITIONS.get(from_pos, []):
        if zone == from_pos or zone not in loads:
            continue
        if len(loads[zone]) < caps.get(zone, 2):
            loads[zone].append(c)
            loads[from_pos].remove(c)
            emit("change_anchor", c, from_=from_pos, to=zone, reason="zone_capacity")
            c.position = zone
            return
    # 全部邻接槽满 → 跨 ZONE_ORDER 兜底
    for zone in _ZONES:
        if zone == from_pos or zone not in loads:
            continue
        if len(loads[zone]) < caps.get(zone, 2):
            loads[zone].append(c)
            loads[from_pos].remove(c)
            emit("change_anchor", c, from_=from_pos, to=zone, reason="zone_capacity_cross")
            c.position = zone
            return
    # L2 缩尺寸：宽度收敛声明（宽度组件让同槽可叠 —— 前端按像素投影）
    emit("shrink", c, from_=from_pos, width_units=1, reason="all_neighbor_zones_full")
    # L3 折叠为溢出面板（可折叠族）：镜像 V4 —— 落到专属溢出槽
    # bottom-center（requested 即溢出槽时镜像 top-center），collapsed 由
    # 消费端标记（动作载荷只带位置裁决）。
    if c.type in COLLAPSIBLE_TYPES:
        overflow_slot = "top-center" if from_pos == "bottom-center" else "bottom-center"
        emit("collapse_to_overflow", c, from_=from_pos, to=overflow_slot,
             reason="still_at_capacity")
        loads[from_pos].remove(c)
        loads.setdefault(overflow_slot, []).append(c)
        c.position = overflow_slot
        return
    # L4 隐藏最低优先（仅 optional；required 保留在原位 —— 功能件不静默消失）
    if c.optional:
        emit("hide_lowest_priority", c, from_=from_pos, reason="unresolvable_optional")
    else:
        emit("keep_required", c, from_=from_pos, reason="required_overflow_kept")


# ── 2. 缺项自动补全 ──────────────────────────────────────────────────────

#: 输出用途词表（画幅 × 横竖；与 layout_solver._PROFILES 版式族对齐）。
OUTPUT_PURPOSES = (
    "screen_16_9", "screen_4_3", "a4_portrait", "a4_landscape",
)

_SCREEN_PURPOSES = frozenset({"screen_16_9", "screen_4_3"})

#: content 词表（布尔开关；缺省 False —— 未知按缺失处理，§0.5）。
CONTENT_FLAGS = (
    "has_projection_info",    # spec 携带投影/坐标系信息
    "has_data_source",        # attribution 已有真实署名
    "has_thematic_layer",     # 存在专题渲染层（需要图例）
    "has_statistics_panel",   # 已有统计面板
    "has_location_context",   # 研究区区位语境（需要插图）
)

_DATA_SOURCE_PLACEHOLDER = "数据来源：—（待补充）"


class RequiredComponent(BaseModel):
    """必配清单条目：类型 + 理由 + （可选）占位建议。"""

    type: str
    reason: str
    advisory: str = ""
    placeholder_options: Dict[str, Any] = Field(default_factory=dict)


class RequiredComponentsPlan(BaseModel):
    """required_components_for 的输出（前端补全器与 QA 对账同构）。"""

    purpose: str
    required: List[RequiredComponent] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)
    advisories: List[str] = Field(default_factory=list)
    decisions: List[CompositionDecision] = Field(default_factory=list)


def required_components_for(
    purpose: str,
    content: Optional[Dict[str, Any]] = None,
) -> RequiredComponentsPlan:
    """按输出用途 × 内容要素决定必配组件清单（§0.5 默认决策）。

    规则表（制图学必配基线 + 用途收紧）：
    - 全用途：title / scale_bar / north_arrow / attribution（署名不可省）；
    - print（a4_*）：追加 legend（专题层在场时）、graticule（投影信息在场
      时 —— 缺投影经纬网降级为经纬网 alone 的决策由渲染端做，清单仍列出
      并附 advisory）、inset_map（区位语境在场时）；
    - screen：统计面板 / 时序族不强制（交互面板由用户按需开）；
    - 数据来源未知：attribution 补占位 options.text + advisory。
    """
    flags = {k: bool((content or {}).get(k)) for k in CONTENT_FLAGS}
    is_print = purpose not in _SCREEN_PURPOSES
    plan = RequiredComponentsPlan(purpose=purpose if purpose in OUTPUT_PURPOSES else "screen_16_9")

    def _add(rtype: str, reason: str, **kw: Any) -> None:
        plan.required.append(RequiredComponent(type=rtype, reason=reason, **kw))

    _add("title", "图名必配（全用途基线）")
    _add("scale_bar", "比例尺必配（全用途基线）")
    _add("north_arrow", "指北针必配（全用途基线）")
    if flags["has_data_source"]:
        _add("attribution", "数据署名必配（全用途基线）")
    else:
        _add(
            "attribution", "数据署名必配（全用途基线）",
            advisory="数据来源未知：已补占位署名，禁止省略（§0.5）",
            placeholder_options={"text": _DATA_SOURCE_PLACEHOLDER},
        )
        plan.missing.append("data_source")
        plan.advisories.append("数据来源未知：attribution 以「数据来源：—（待补充）」占位")

    if flags["has_thematic_layer"]:
        _add("legend", "专题层在场 → 图例必配")
    if is_print:
        if flags["has_projection_info"]:
            _add("graticule", "印刷品投影信息在场 → 经纬网必配")
        else:
            plan.advisories.append(
                "缺投影信息：经纬网降级为经纬网 alone（角注记省略），比例尺不省略（§0.5）")
            plan.advisories.append("建议补全投影/坐标系信息后重排")
        if flags["has_location_context"]:
            _add("inset_map", "区位语境在场 → 位置插图必配（印刷品）")
    else:
        if flags["has_projection_info"]:
            _add("graticule", "投影信息在场 → 经纬网建议（屏幕）")

    step = 0
    for req in plan.required:
        plan.decisions.append(CompositionDecision(
            step=step, kind="autofill", component_type=req.type,
            after="present", reason=req.reason,
            evidence={"purpose": plan.purpose},
        ))
        step += 1
    return plan


__all__ = [
    "CompositionDecision",
    "plan_layout_repairs",
    "floating_overlap_pairs",
    "required_components_for",
    "RequiredComponent",
    "RequiredComponentsPlan",
    "OUTPUT_PURPOSES",
    "CONTENT_FLAGS",
    "REPAIR_ACTIONS",
    "COLLAPSIBLE_TYPES",
    "SINGLETON_TYPES",
]
