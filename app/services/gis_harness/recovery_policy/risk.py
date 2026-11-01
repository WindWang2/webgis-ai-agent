"""工具风险解析（GOAL 05 / M2）—— 复用既有 SideEffectClass/tier，不发明新分级。

事实源：``app/tools/descriptor.py`` 的 ``SideEffectClass``（含
``retry_safe`` 语义）与工具 tier（≥3 = 破坏性，registry 唯一闸点）。
本模块只做**有界快照**：把描述符事实折叠成 RecoveryPolicy 可消费的
纯数据；描述符缺席/未分类 → 保守缺省（retry_safe=False），
与 replay.py 的 allow-list 纪律同向。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

#: 未分类工具的保守缺省（不放宽自动重试）。
CONSERVATIVE_SIDE_EFFECT_DEFAULT = "unclassified"

#: descriptor.SideEffectClass → 盲目重试是否安全（与 descriptor.retry_safe
#: 同语义的本地快照；未知类 → False）。
_SIDE_EFFECT_RETRY_SAFE = {
    "pure": True,
    "deterministic_compute": True,
    "cacheable_read": True,
    "state_mutation": False,
    "artifact_creation": False,
    "external_side_effect": False,
    "destructive": False,
    "unclassified": False,
}


@dataclass(frozen=True)
class ToolRiskProfile:
    """一次决策所需的工具风险事实（有界快照，零 IO）。"""
    tool_name: str
    side_effect_class: str = CONSERVATIVE_SIDE_EFFECT_DEFAULT
    tier: int = 0

    @property
    def retry_safe(self) -> bool:
        """盲目重试是否安全（未知 = False，保守）。"""
        return _SIDE_EFFECT_RETRY_SAFE.get(self.side_effect_class, False)

    @property
    def destructive(self) -> bool:
        return self.tier >= 3


def resolve_tool_risk(
    tool_name: str,
    *,
    descriptor: Optional[Any] = None,
) -> ToolRiskProfile:
    """从工具描述符（或描述符形 dict）折叠风险快照。

    ``descriptor`` 接受 ToolDescriptor 实例或含 ``side_effect_class``/
    ``tier`` 键的 Mapping；缺席 → 保守缺省。绝不抛出。
    """
    name = str(tool_name or "")[:64]
    sec = CONSERVATIVE_SIDE_EFFECT_DEFAULT
    tier = 0
    d: Optional[Mapping] = None
    if isinstance(descriptor, Mapping):
        d = descriptor
    elif descriptor is not None:
        # ToolDescriptor：side_effect_class 可能是枚举
        raw_sec = getattr(descriptor, "side_effect_class", None)
        sec = str(getattr(raw_sec, "value", raw_sec) or
                  CONSERVATIVE_SIDE_EFFECT_DEFAULT)
        tier = int(getattr(descriptor, "effective_security_tier",
                           getattr(descriptor, "tier", 0)) or 0)
        return ToolRiskProfile(name, sec, tier)
    if d is not None:
        raw_sec = d.get("side_effect_class")
        sec = str(getattr(raw_sec, "value", raw_sec) or
                  CONSERVATIVE_SIDE_EFFECT_DEFAULT)
        try:
            tier = int(d.get("tier", 0) or 0)
        except (TypeError, ValueError):
            tier = 0
    return ToolRiskProfile(name, sec, tier)


__all__ = [
    "CONSERVATIVE_SIDE_EFFECT_DEFAULT",
    "ToolRiskProfile",
    "resolve_tool_risk",
]
