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


__all__ = [
    "LayoutParticipant",
    "LayoutPlacement",
    "LayoutSolution",
    "solve_component_layout",
    "page_profiles",
    "SINGLETON_TYPES",
    "CANVAS_TYPES",
]
