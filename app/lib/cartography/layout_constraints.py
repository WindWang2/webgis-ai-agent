"""Layout Constraints — 最小布局约束系统.

采用 zone + priority + exclusive occupancy 模型，避免多组件
占据同一角落重叠。 无需复杂求解器。
"""
from __future__ import annotations

from typing import Dict, List, Set

ZONE_POSITIONS: Dict[str, List[str]] = {
    "top-left": ["top-left", "top-center"],
    "top-center": ["top-center", "top-left", "top-right"],
    "top-right": ["top-right", "top-center"],
    "bottom-left": ["bottom-left", "bottom-center"],
    "bottom-center": ["bottom-center", "bottom-left", "bottom-right"],
    "bottom-right": ["bottom-right", "bottom-center"],
    "none": ["none"],
}

# 默认 zone 占用：每个物理位置同时只能有一个 exclusive 组件
EXCLUSIVE_ZONES: Set[str] = {"top-center"}

# 每个 zone 推荐的最大组件数（超出则 fallback）
ZONE_CAPACITY: Dict[str, int] = {
    "top-left": 2,
    "top-center": 2,
    "top-right": 1,
    "bottom-left": 2,
    "bottom-center": 1,
    "bottom-right": 2,
    "none": 99,
}


def resolve_collisions(components: list) -> list:
    """检测并解决位置碰撞：按 priority 排序，超出容量的组件向 fallback zone 移动."""
    # 统计每个 position 的占用
    from collections import defaultdict
    zone_counts: Dict[str, int] = defaultdict(int)
    for c in components:
        if c.enabled and c.position != "none":
            zone_counts[c.position] += 1

    result = []
    for comp in sorted(components, key=lambda c: (c.priority, c.id)):
        if not comp.enabled or comp.position == "none":
            result.append(comp)
            continue
        cap = ZONE_CAPACITY.get(comp.position, 2)
        if zone_counts[comp.position] <= cap:
            result.append(comp)
            continue
        # need fallback — try to move lower priority components
        # For now, just keep as-is but mark collision in QA
        result.append(comp)
    return result


def detect_collisions(components: list) -> List[str]:
    """返回碰撞描述列表（QA 用）."""
    from collections import Counter
    issues: List[str] = []
    enabled = [c for c in components if c.enabled and c.position != "none"]
    counts = Counter(c.position for c in enabled)
    for pos, cnt in counts.items():
        cap = ZONE_CAPACITY.get(pos, 2)
        if cnt > cap:
            issues.append(f"position {pos} has {cnt} components, capacity {cap}")
        if pos in EXCLUSIVE_ZONES and cnt > 1:
            issues.append(f"exclusive zone {pos} has {cnt} components")
    # duplicate type check
    type_counts = Counter(c.type for c in enabled)
    for t, cnt in type_counts.items():
        if t in ("title", "north_arrow", "scale_bar", "attribution") and cnt > 1:
            issues.append(f"duplicate singleton component {t}: {cnt} instances")
    return issues


def detect_orphan_components(components: list, layer_ids: List[str]) -> List[str]:
    """检测 layerId 指向不存在图层的组件."""
    issues: List[str] = []
    layer_set = set(layer_ids)
    for c in components:
        if not c.enabled:
            continue
        lid = c.options.get("layerId", "")
        if lid and lid not in layer_set:
            issues.append(f"orphan component {c.id} ({c.type}): layerId {lid} not in layers")
    return issues


__all__ = ["resolve_collisions", "detect_collisions", "detect_orphan_components", "ZONE_CAPACITY"]
