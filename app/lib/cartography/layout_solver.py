"""Layout Constraint Solver V2 — 确定性组件布局求解（ADR-0101 D5）.

把 layout_constraints.py 的「检测」升级为「求解」：多图例、图例+色条、
面板栈叠、窄视口、A4 版式下的槽位分配与溢出处置。

算法（单遍、无循环、无随机 —— 同输入永远同输出）：

1. 参与者排序：required 优先 → priority 升序 → id 字典序（稳定全序）；
2. 逐个分配：requested zone 容量未满且无 exclusive 冲突且无 singleton
   重复 → 接受；否则按 slot fallback zones → 邻接 fallback（ZONE_POSITIONS）
   → 溢出处置（optional 确定性抑制 / required 保留并 warning）；
3. ``none`` 槽（全画布型：map_border/graticule）不占容量，直接通过。

容量与堆叠预算按 page profile 缩放（viewport / a4_portrait / a4_landscape /
presentation_16x9 / academic_figure）；profile 只改数值不改算法。

与既有路径的关系：``detect_collisions``（QA 报警）与 composition_validation
行为完全不变；本模块是纯函数增量，golden corpus 消费其结构化输出。

V3（Design System）＝ V2 的严格超集（``solve_layout_v3``）：
- 参与者维度域：width_units（列单位，缺省 1 → 与 V2 计数语义一致）；
- 碰撞组：同组同槽互斥（图例族/披露族的组级防重叠）；
- avoid_zones：地图内容占用的槽位降级（图例不得压在密集主表达上）；
- compact 模式：容量/预算收紧（小视口/窄图幅）；
- 诊断输出：conflicts（未解决冲突）+ fallback_plan（降级方案）；
- V2 形输入经 V3 求解 → 与 V2 结果一致（回归锁定）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from app.lib.cartography.layout_constraints import (
    EXCLUSIVE_ZONES,
    ZONE_CAPACITY,
    ZONE_POSITIONS,
)

# 单例类型：全图最多一个实例（与 detect_collisions 同表）
SINGLETON_TYPES = frozenset({"title", "north_arrow", "scale_bar", "attribution"})

# 全画布型：position=none，不占槽位容量
CANVAS_TYPES = frozenset({"map_border", "graticule", "export_layout", "basemap"})

ZONE_ORDER = ("top-left", "top-center", "top-right",
              "bottom-left", "bottom-center", "bottom-right")

# ── Page profiles：容量/堆叠预算按输出形态缩放 ─────────────────────────
# multiplier 语义：相对 ZONE_CAPACITY 基准的整数倍（宽画幅槽位能容纳更多
# 面板；窄视口反而收紧）。budget 为顶/底槽累计组件数预算（替代像素估算的
# 结构化代理 —— 求解器不做像素假设，前端 resolve-layout 才有像素真值）。
_PROFILES: Dict[str, Dict[str, Any]] = {
    "viewport":          {"multiplier": 1, "top_budget": 3, "bottom_budget": 3},
    "a4_portrait":       {"multiplier": 1, "top_budget": 3, "bottom_budget": 4},
    "a4_landscape":      {"multiplier": 2, "top_budget": 4, "bottom_budget": 4},
    "presentation_16x9": {"multiplier": 2, "top_budget": 4, "bottom_budget": 3},
    "academic_figure":   {"multiplier": 2, "top_budget": 3, "bottom_budget": 4},
}


class LayoutParticipant(BaseModel):
    """求解输入：一个组件实例的布局意图。"""

    id: str
    type: str
    requested_zone: str = "none"
    priority: int = 50
    optional: bool = True          # required 溢出时保留 + warning，不抑制
    fallback_zones: List[str] = Field(default_factory=list)


class LayoutPlacement(BaseModel):
    id: str
    type: str
    zone: str                       # "none" 透传；suppressed 组件不出现
    moved: bool = False             # 是否被挪离 requested_zone
    reason: str = ""                # moved 时的判定原因


class LayoutSolution(BaseModel):
    placements: List[LayoutPlacement] = Field(default_factory=list)
    suppressed: List[LayoutPlacement] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    page_profile: str = "viewport"

    def zone_for(self, component_id: str) -> Optional[str]:
        for p in self.placements:
            if p.id == component_id:
                return p.zone
        return None

    @property
    def ok(self) -> bool:
        return not self.suppressed


def _capacity_for(zone: str, profile: str) -> int:
    conf = _PROFILES.get(profile, _PROFILES["viewport"])
    return ZONE_CAPACITY.get(zone, 2) * conf["multiplier"]


def _candidate_zones(participant: LayoutParticipant) -> List[str]:
    """确定性候选槽序：requested → slot fallback → 邻接 fallback。"""
    zones: List[str] = []
    if participant.requested_zone and participant.requested_zone != "none":
        zones.append(participant.requested_zone)
    for z in participant.fallback_zones:
        if z not in zones and z != "none":
            zones.append(z)
    for z in ZONE_POSITIONS.get(participant.requested_zone, []):
        if z not in zones and z != "none":
            zones.append(z)
    return zones


def solve_component_layout(
    participants: Iterable[LayoutParticipant],
    *,
    page_profile: str = "viewport",
    zone_capacity: Optional[Dict[str, int]] = None,
) -> LayoutSolution:
    """确定性布局求解（单遍）。

    ``zone_capacity`` 允许调用方覆写基准容量表（测试/嵌入场景）；profile
    multiplier 仍在其上生效。
    """
    conf = _PROFILES.get(page_profile, _PROFILES["viewport"])
    base_capacity = dict(zone_capacity) if zone_capacity is not None else dict(ZONE_CAPACITY)

    # 1. 确定性排序：required 优先 → priority 升序 → id 字典序
    ordered = sorted(
        participants,
        key=lambda p: (p.optional, p.priority, p.id),
    )

    occupied: Dict[str, int] = {}
    exclusive_used: set = set()
    singleton_used: set = set()
    placements: List[LayoutPlacement] = []
    suppressed: List[LayoutPlacement] = []
    warnings: List[str] = []

    def _slot_load(zone: str) -> int:
        # 面板/chrome 挤占度量：该槽已放置组件数
        return occupied.get(zone, 0)

    top_load = 0
    bottom_load = 0

    for p in ordered:
        # 全画布型 / none：不占容量，直接通过
        if p.type in CANVAS_TYPES or p.requested_zone == "none":
            placements.append(LayoutPlacement(id=p.id, type=p.type, zone="none"))
            continue

        placed = False
        for zone in _candidate_zones(p):
            if zone in EXCLUSIVE_ZONES and zone in exclusive_used:
                continue
            if p.type in SINGLETON_TYPES and p.type in singleton_used:
                # singleton 重复：不再寻找第二位置（重复本身非法）
                break
            cap = base_capacity.get(zone, 2) * conf["multiplier"]
            if _slot_load(zone) >= cap:
                continue
            if zone.startswith("top-") and top_load >= conf["top_budget"]:
                continue
            if zone.startswith("bottom-") and bottom_load >= conf["bottom_budget"]:
                continue
            # 接受
            occupied[zone] = occupied.get(zone, 0) + 1
            if zone in EXCLUSIVE_ZONES:
                exclusive_used.add(zone)
            if p.type in SINGLETON_TYPES:
                singleton_used.add(p.type)
            if zone.startswith("top-"):
                top_load += 1
            elif zone.startswith("bottom-"):
                bottom_load += 1
            moved = zone != p.requested_zone
            placements.append(LayoutPlacement(
                id=p.id, type=p.type, zone=zone, moved=moved,
                reason=("requested" if not moved else "fallback_zone"),
            ))
            placed = True
            break

        if not placed:
            if p.type in SINGLETON_TYPES and p.type in singleton_used:
                suppressed.append(LayoutPlacement(
                    id=p.id, type=p.type, zone="none",
                    reason=f"duplicate_singleton:{p.type}",
                ))
                warnings.append(
                    f"component {p.id} ({p.type}) suppressed: duplicate singleton")
            elif p.optional:
                suppressed.append(LayoutPlacement(
                    id=p.id, type=p.type, zone="none",
                    reason="overflow_suppressed",
                ))
                warnings.append(
                    f"component {p.id} ({p.type}) suppressed: all candidate zones "
                    f"at capacity (profile={page_profile})")
            else:
                # required 溢出不抑制 —— 保留在 requested 位置并告警
                #（QA/导出侧会看到超容，但功能性组件不得静默消失）
                zone = p.requested_zone if p.requested_zone != "none" else "bottom-right"
                occupied[zone] = occupied.get(zone, 0) + 1
                placements.append(LayoutPlacement(
                    id=p.id, type=p.type, zone=zone,
                    reason="required_overflow_kept",
                ))
                warnings.append(
                    f"required component {p.id} ({p.type}) kept at '{zone}' "
                    f"despite overflow (profile={page_profile})")

    return LayoutSolution(
        placements=placements, suppressed=suppressed,
        warnings=warnings, page_profile=page_profile,
    )


def page_profiles() -> List[str]:
    return sorted(_PROFILES)


# ═══ V3（Design System）：约束式布局求解 ═══════════════════════════════


class LayoutParticipantV3(BaseModel):
    """V3 求解输入：组件实例的布局意图 + 维度域 + 碰撞组。"""

    id: str
    type: str
    requested_zone: str = "none"
    priority: int = 50
    optional: bool = True
    fallback_zones: List[str] = Field(default_factory=list)
    # V3：列单位宽度（1 = V2 计数语义；宽面板 = 2 → 同槽可叠两个窄组件
    # 或一个宽组件）。前端像素真值由 resolve-layout 承担，此处是结构化代理。
    width_units: int = Field(default=1, ge=1, le=4)
    # V3：碰撞组（同组组件不得落同槽 —— 图例族/披露族组级互斥）
    collision_group: str = ""
    # V3：本组件要求避开的槽位（地图内容占用语义，由调用方从
    # map-content occupancy / UI panels 投影而来）
    avoid_zones: List[str] = Field(default_factory=list)


class LayoutConstraints(BaseModel):
    """V3 求解环境约束（全部缺省 → 与 V2 等价）。"""

    # 地图内容/UI 占用的槽位（键=zone，值=占用列单位；槽剩余容量会被扣减）
    zone_occupancy: Dict[str, int] = Field(default_factory=dict)
    # 紧凑模式：容量与预算减半（小视口/窄图幅响应式）
    compact: bool = False
    # 输出形态覆写（缺省跟随 page_profile；print 输出禁用交互件由
    # composition_validation 负责，此处不管）
    print_mode: bool = False


class LayoutConflict(BaseModel):
    """未解决冲突诊断（可解释性输出）。"""

    component_id: str
    conflict_type: str      # zone_exhausted / collision_group / duplicate_singleton
    detail_zh: str = ""


class LayoutPlacementV3(BaseModel):
    id: str
    type: str
    zone: str
    moved: bool = False
    reason: str = ""            # requested/fallback_zone/required_overflow_kept/
                                # collision_group_move/avoid_zone_move/compact_reflow
    width_units: int = 1


class LayoutSolutionV3(BaseModel):
    placements: List[LayoutPlacementV3] = Field(default_factory=list)
    suppressed: List[LayoutPlacementV3] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    conflicts: List[LayoutConflict] = Field(default_factory=list)
    fallback_plan_zh: str = ""
    page_profile: str = "viewport"
    compact: bool = False

    def zone_for(self, component_id: str) -> Optional[str]:
        for p in self.placements:
            if p.id == component_id:
                return p.zone
        return None

    @property
    def ok(self) -> bool:
        return not self.suppressed and not self.conflicts


def _v3_profile_conf(profile: str, constraints: LayoutConstraints) -> Dict[str, Any]:
    conf = dict(_PROFILES.get(profile, _PROFILES["viewport"]))
    if constraints.compact:
        conf["multiplier"] = max(1, conf["multiplier"] // 2)
        conf["top_budget"] = max(1, conf["top_budget"] // 2)
        conf["bottom_budget"] = max(1, conf["bottom_budget"] // 2)
    return conf


def solve_layout_v3(
    participants: Iterable[LayoutParticipantV3],
    *,
    page_profile: str = "viewport",
    zone_capacity: Optional[Dict[str, int]] = None,
    constraints: Optional[LayoutConstraints] = None,
) -> LayoutSolutionV3:
    """V3 约束式求解（确定性单遍 + 列单位 first-fit）。

    V2 形输入（全部缺省 V3 字段、空 constraints）产出与
    ``solve_component_layout`` 一致的结果（回归由测试锁定）。
    """
    cons = constraints or LayoutConstraints()
    conf = _v3_profile_conf(page_profile, cons)
    base_capacity = dict(zone_capacity) if zone_capacity is not None else dict(ZONE_CAPACITY)

    # zone → 列单位剩余容量（扣掉环境占用）
    remaining: Dict[str, int] = {}
    for z in ZONE_ORDER:
        cap = base_capacity.get(z, 2) * conf["multiplier"]
        occ = cons.zone_occupancy.get(z, 0)
        remaining[z] = max(0, cap - occ)

    # top/bottom 组件数预算（compact 收紧已折入 conf）
    top_load = 0
    bottom_load = 0

    ordered = sorted(participants, key=lambda p: (p.optional, p.priority, p.id))
    occupied: Dict[str, int] = {z: 0 for z in ZONE_ORDER}
    exclusive_used: set = set()
    singleton_used: set = set()
    group_zones: Dict[str, set] = {}     # collision_group → 已占 zones
    placements: List[LayoutPlacementV3] = []
    suppressed: List[LayoutPlacementV3] = []
    warnings: List[str] = []
    conflicts: List[LayoutConflict] = []

    def _candidate_zones(p: LayoutParticipantV3) -> List[str]:
        zones: List[str] = []
        if p.requested_zone and p.requested_zone != "none":
            zones.append(p.requested_zone)
        for z in p.fallback_zones:
            if z not in zones and z != "none":
                zones.append(z)
        for z in ZONE_POSITIONS.get(p.requested_zone, []):
            if z not in zones and z != "none":
                zones.append(z)
        return zones

    for p in ordered:
        # 全画布型 / none：不占容量
        if p.type in CANVAS_TYPES or p.requested_zone == "none":
            if p.avoid_zones:
                warnings.append(
                    f"component {p.id} ({p.type}) 为全画布型，avoid_zones 忽略")
            placements.append(LayoutPlacementV3(
                id=p.id, type=p.type, zone="none",
                width_units=p.width_units, reason="requested"))
            continue

        placed = False
        rejected_group = False
        rejected_avoid = False
        # 请求槽被跳过的原因追踪（最终放置原因的可解释性依据）
        requested_capacity_blocked = False
        for zone in _candidate_zones(p):
            if zone in EXCLUSIVE_ZONES and zone in exclusive_used:
                continue
            if p.type in SINGLETON_TYPES and p.type in singleton_used:
                break
            # V3：avoid_zones —— 被避槽位直接跳过（内容占用语义），
            # 全候选被避时 optional 抑制 / required 保留原位
            if zone in p.avoid_zones:
                rejected_avoid = True
                if zone == p.requested_zone:
                    requested_capacity_blocked = True
                continue
            # V3：碰撞组互斥
            if p.collision_group and zone in group_zones.get(p.collision_group, set()):
                rejected_group = True
                continue
            if remaining.get(zone, 0) < p.width_units:
                if zone == p.requested_zone:
                    requested_capacity_blocked = True
                continue
            if zone.startswith("top-") and top_load + 1 > conf["top_budget"]:
                if zone == p.requested_zone:
                    requested_capacity_blocked = True
                continue
            if zone.startswith("bottom-") and bottom_load + 1 > conf["bottom_budget"]:
                if zone == p.requested_zone:
                    requested_capacity_blocked = True
                continue
            # V3：放置原因可解释 —— 组迁移 > 内容避让 > 紧凑重排 > 普通 fallback
            reason = "requested"
            if zone != p.requested_zone:
                if rejected_group and p.collision_group:
                    reason = "collision_group_move"
                elif requested_capacity_blocked and p.requested_zone in p.avoid_zones:
                    reason = "avoid_zone_move"
                elif requested_capacity_blocked and cons.zone_occupancy.get(p.requested_zone):
                    reason = "avoid_zone_move"
                elif cons.compact:
                    reason = "compact_reflow"
                else:
                    reason = "fallback_zone"
            remaining[zone] -= p.width_units
            occupied[zone] += 1
            if zone in EXCLUSIVE_ZONES:
                exclusive_used.add(zone)
            if p.type in SINGLETON_TYPES:
                singleton_used.add(p.type)
            if p.collision_group:
                group_zones.setdefault(p.collision_group, set()).add(zone)
            if zone.startswith("top-"):
                top_load += 1
            elif zone.startswith("bottom-"):
                bottom_load += 1
            placements.append(LayoutPlacementV3(
                id=p.id, type=p.type, zone=zone, moved=zone != p.requested_zone,
                reason=reason, width_units=p.width_units,
            ))
            placed = True
            break

        if not placed:
            if p.type in SINGLETON_TYPES and p.type in singleton_used:
                suppressed.append(LayoutPlacementV3(
                    id=p.id, type=p.type, zone="none", reason="duplicate_singleton"))
                warnings.append(
                    f"component {p.id} ({p.type}) suppressed: duplicate singleton")
            elif rejected_avoid and not rejected_group:
                conflicts.append(LayoutConflict(
                    component_id=p.id, conflict_type="avoid_zone_exhausted",
                    detail_zh="全部候选槽位均被 avoid_zones 排除"
                              "（地图内容/UI 占用语义）",
                ))
                warnings.append(
                    f"component {p.id} ({p.type}) unresolved: all candidate zones "
                    f"excluded by avoid_zones")
            elif rejected_group and p.collision_group:
                conflicts.append(LayoutConflict(
                    component_id=p.id, conflict_type="collision_group",
                    detail_zh=f"碰撞组 '{p.collision_group}' 的既有组件占满全部候选槽位",
                ))
                warnings.append(
                    f"component {p.id} ({p.type}) unresolved: collision_group "
                    f"'{p.collision_group}' occupies all candidate zones")
            elif p.optional:
                suppressed.append(LayoutPlacementV3(
                    id=p.id, type=p.type, zone="none", reason="overflow_suppressed"))
                warnings.append(
                    f"component {p.id} ({p.type}) suppressed: all candidate zones "
                    f"at capacity (profile={page_profile}, compact={cons.compact})")
            else:
                zone = p.requested_zone if p.requested_zone != "none" else "bottom-right"
                placements.append(LayoutPlacementV3(
                    id=p.id, type=p.type, zone=zone,
                    reason="required_overflow_kept", width_units=p.width_units))
                warnings.append(
                    f"required component {p.id} ({p.type}) kept at '{zone}' "
                    f"despite overflow (profile={page_profile})")

    # 降级方案（确定性文案）
    fallback_plan_zh = ""
    if suppressed or conflicts:
        n_sup = len(suppressed)
        n_conf = len(conflicts)
        parts_zh = []
        if n_sup:
            parts_zh.append(f"{n_sup} 个可选组件被抑制")
        if n_conf:
            parts_zh.append(f"{n_conf} 个未解决冲突")
        fallback_plan_zh = (
            f"布局存在 {'；'.join(parts_zh)}。建议："
            "1) 收纳/折叠低优先级组件；2) 切换更宽的 page_profile"
            "（如 a4_landscape/presentation_16x9）；3) 减少 avoid_zones 占用。"
        )

    return LayoutSolutionV3(
        placements=placements, suppressed=suppressed, warnings=warnings,
        conflicts=conflicts, fallback_plan_zh=fallback_plan_zh,
        page_profile=page_profile, compact=cons.compact,
    )


__all__ = [
    "LayoutParticipant",
    "LayoutPlacement",
    "LayoutSolution",
    "solve_component_layout",
    "page_profiles",
    "SINGLETON_TYPES",
    "CANVAS_TYPES",
    # V3
    "LayoutParticipantV3",
    "LayoutConstraints",
    "LayoutConflict",
    "LayoutPlacementV3",
    "LayoutSolutionV3",
    "solve_layout_v3",
]
