"""修复分级策略（C13）：AUTO_SAFE 与 NEEDS_APPROVAL 的机器可测边界。

设计立场（保守默认）：

- **blocked**：taxonomy 不在 healer 可映射闭包内 —— 没有修复路径，任何
  分级都不产生意图（crop / legibility / legend_mismatch / empty_space /
  hierarchy 只能披露或进入确定性通道）；
- **needs_approval**：severity=error（收敛硬停/复现级证据）或触达用户
  锁定图层（user origin 是锁的唯一 override —— 自动路径在 lifecycle
  guard 结构上被拒，分级必须与之一致）；
- **auto_safe**：warning 级 + 可映射 + 不触锁 —— 可编译为 system origin
  的 heal 意图（受 per-revision / per-session 预算与 healer 收敛账本
  三重 bounded），user-pinned 决策由锁 guard 兜底，绝不覆盖。

纯函数、零 I/O：同样的 finding + 上下文在任何进程得到同一分级
（Definition of Done：边界可机器测试）。
"""
from __future__ import annotations

from typing import Any, Sequence

#: 分级词表（冻结；账本/UI/测试共用）。
APPROVAL_AUTO_SAFE = "auto_safe"
APPROVAL_NEEDS_APPROVAL = "needs_approval"
APPROVAL_BLOCKED = "blocked"

#: 与 repair_bridge._TAXO_TO_HEAL_SHAPE 同源的可自愈闭包（漂移由
#: test_visual_repair_policy 互锁）。
HEALABLE_TAXONOMY = frozenset({"label_collision", "contrast", "overlap"})


def _taxonomy_of(finding: Any) -> str:
    from app.services.gis_harness.visual_observation.repair_bridge import (
        _taxonomy_of_finding,
    )

    return _taxonomy_of_finding(finding)


def _severity_of(finding: Any, override: str) -> str:
    if override:
        return str(override)
    if isinstance(finding, dict):
        return str(finding.get("severity") or "warning")
    return str(getattr(finding, "severity", "") or "warning")


def _affected_entities(finding: Any) -> Sequence[str]:
    if isinstance(finding, dict):
        raw = finding.get("affected_entity")
    else:
        raw = getattr(finding, "affected_entity", "")
    if isinstance(raw, (list, tuple, set)):
        return [str(x) for x in raw if x]
    return [str(raw)] if raw else []


def touches_locked_layers(finding: Any, locked_ids: Sequence[str]) -> bool:
    """finding 受影响实体是否与用户锁定集相交（保守：实体未知 ≠ 触锁）。"""
    locked = {str(x) for x in (locked_ids or ()) if x}
    if not locked:
        return False
    return any(entity in locked for entity in _affected_entities(finding))


def approval_class_for(
    finding: Any,
    *,
    touches_locked: bool = False,
    severity: str = "",
) -> str:
    """finding → 修复分级（纯函数；规则顺序即优先级）。"""
    if _taxonomy_of(finding) not in HEALABLE_TAXONOMY:
        return APPROVAL_BLOCKED
    if _severity_of(finding, severity) == "error":
        return APPROVAL_NEEDS_APPROVAL
    if touches_locked:
        return APPROVAL_NEEDS_APPROVAL
    return APPROVAL_AUTO_SAFE


__all__ = [
    "APPROVAL_AUTO_SAFE",
    "APPROVAL_NEEDS_APPROVAL",
    "APPROVAL_BLOCKED",
    "HEALABLE_TAXONOMY",
    "approval_class_for",
    "touches_locked_layers",
]
