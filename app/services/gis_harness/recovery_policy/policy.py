"""RecoveryPolicy —— 纯决策（GOAL 05 / M2，ADR-0279）。

输入：GISRecoveryDiagnostic + ToolRiskProfile（+ 幂等键/取消旗标）。
输出：RecoveryDecision —— 允许/拒绝的动作清单、所需确认门、退避
schedule。**零执行、零 IO、零 sleep**：执行永远归既有通道（LLM 自愈、
runtime_repair、finalizer、analysis workflow recompute、owner 确认端点）；
退避只给 schedule，等待由既有 vendor/LLM 循环消化。

安全不变量（测试钉死）：
- 自动重试仅限 retry_safe 工具（读/确定性/可缓存读）或携带幂等键的
  state_mutation；预算耗尽 → abort_with_disclosure；
- tier-3 破坏性 / 外部副作用 / 未分类 → 绝不自动重试，按类别挂确认门；
- 取消（cancelled 旗标或 terminal 处置）→ 零动作立即让路；
- denied_actions 带封闭 denial reason（审计面）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from app.services.gis_harness.recovery_policy.contracts import (
    GISRecoveryDiagnostic,
    RecoveryDisposition,
)
from app.services.gis_harness.recovery_policy.risk import (
    ToolRiskProfile,
    resolve_tool_risk,
)

#: 确认门类别（封闭；映射到既有确认入口，由调用方路由）。
CONFIRMATION_KINDS = frozenset({
    "owner_write",     # 状态变更（数据/要素写）→ #1873 预览/确认/撤销族
    "paid_call",       # 付费外部调用 → 预算确认
    "export",          # 导出 → #1874 final confirmation 族
    "destructive",     # tier-3 → registry 确认闸（唯一权威）
})

#: denial reason 码（封闭）。
DENIAL_REASONS = frozenset({
    "side_effect_not_retry_safe",
    "budget_exhausted",
    "terminal_disposition",
    "cancelled",
    "needs_user_input",
    "unsafe_target",
})

#: 退避 schedule（毫秒；纯确定性基准 —— jitter 由执行侧叠加）。
_BACKOFF_BASE_MS = {
    "rate_limited": (1000, 2000, 4000, 8000),
    "upstream_server_error": (500, 1000, 2000, 4000),
    "transient_timeout": (500, 1000, 2000, 4000),
}
_BACKOFF_DEFAULT: Tuple[int, ...] = (500, 1000, 2000)

#: 动作词表（封闭；RemediationAction + 用户面 + 终止披露）。
_POLICY_ACTIONS = frozenset({
    "retry", "retry_with_backoff", "fallback_tool", "substitute_operator",
    "requery_profile", "replan", "reobserve", "ask_user_data",
    "owner_confirmation", "abort_with_disclosure",
})

#: 无副作用的修复动作（repairable 处置下允许自动建议）。
_AUTO_SAFE_REPAIRS = frozenset({
    "fallback_tool", "substitute_operator", "requery_profile",
    "replan", "reobserve",
})

#: side_effect_class → 确认门类别。
_SIDE_EFFECT_CONFIRMATION = {
    "state_mutation": "owner_write",
    "external_side_effect": "paid_call",
    "artifact_creation": "export",
}


def backoff_schedule_ms(reason: str) -> Tuple[int, ...]:
    """确定性退避基准（毫秒）；非瞬态 reason → 空 schedule。"""
    return _BACKOFF_BASE_MS.get(str(reason or "").lower(), ())


@dataclass(frozen=True)
class RecoveryDecision:
    """一次恢复裁决（纯数据）。"""
    reason: str
    disposition: str
    auto_retry: bool
    allowed_actions: Tuple[str, ...] = ()
    denied_actions: Tuple[str, ...] = ()
    denial_reasons: Dict[str, str] = field(default_factory=dict)
    required_confirmation: Optional[str] = None
    backoff_ms: Tuple[int, ...] = ()
    terminal: bool = False

    def to_bounded_dict(self) -> Dict:
        return {
            "reason": str(self.reason)[:48],
            "disposition": str(self.disposition)[:32],
            "auto_retry": bool(self.auto_retry),
            "allowed_actions": [a for a in self.allowed_actions[:6]],
            "denied_actions": [a for a in self.denied_actions[:6]],
            "denial_reasons": {
                str(k)[:32]: str(v)[:48]
                for k, v in list(self.denial_reasons.items())[:6]},
            "required_confirmation": self.required_confirmation,
            "backoff_ms": [int(m) for m in self.backoff_ms[:4]],
            "terminal": bool(self.terminal),
        }


def _deny(action: str, why: str, dec: Dict) -> None:
    dec["denied_actions"].append(action)
    dec["denial_reasons"][action] = why


def _finalize(dec: Dict) -> RecoveryDecision:
    """dict 累积器 → frozen 裁决（动作序列元组化，单一出口）。"""
    return RecoveryDecision(
        reason=dec["reason"], disposition=dec["disposition"],
        auto_retry=dec["auto_retry"],
        allowed_actions=tuple(dec["allowed_actions"]),
        denied_actions=tuple(dec["denied_actions"]),
        denial_reasons=dec["denial_reasons"],
        required_confirmation=dec["required_confirmation"],
        backoff_ms=tuple(dec["backoff_ms"]),
        terminal=dec["terminal"],
    )


def decide_recovery(
    diagnostic: GISRecoveryDiagnostic,
    *,
    risk: Optional[ToolRiskProfile] = None,
    descriptor: Optional[object] = None,
    idempotency_key_present: bool = False,
    cancelled: bool = False,
) -> RecoveryDecision:
    """诊断 + 风险 → 恢复裁决（唯一决策入口；确定性纯函数）。

    ``risk`` 优先（ToolRiskProfile，或描述符形 Mapping —— 自动折叠）；
    两者都缺 → 保守（无自动重试）。
    """
    profile = risk if isinstance(risk, ToolRiskProfile) else resolve_tool_risk(
        diagnostic.attribution.tool_name, descriptor=risk or descriptor)
    dec: Dict = {
        "reason": diagnostic.reason.value,
        "disposition": diagnostic.disposition.value,
        "auto_retry": False,
        "allowed_actions": [],
        "denied_actions": [],
        "denial_reasons": {},
        "required_confirmation": None,
        "backoff_ms": (),
        "terminal": False,
    }

    hints = [h for h in diagnostic.hints if h in _POLICY_ACTIONS]

    # 0) 取消立即让路 —— 不产生任何补偿副作用（取消即任务终态）
    if cancelled:
        dec["terminal"] = True
        _deny("retry", "cancelled", dec)
        return _finalize(dec)

    # 1) terminal 处置：诚实终止（取消/预算耗尽/无进展）
    if diagnostic.disposition is RecoveryDisposition.TERMINAL:
        dec["terminal"] = True
        dec["allowed_actions"] = ["abort_with_disclosure"]
        _deny("retry", "terminal_disposition", dec)
        return _finalize(dec)

    # 2) 预算耗尽（durable 账本已判 retry_allowed=False）—— 只终结
    # retryable 处置（那是要消耗预算的路径）；needs_user_* / unsafe 的
    # 出路不是重试，预算无关，照常给出用户路径。
    budget = diagnostic.retry
    if (diagnostic.disposition is RecoveryDisposition.RETRYABLE
            and budget is not None and not budget.retry_allowed):
        dec["terminal"] = True
        dec["allowed_actions"] = ["abort_with_disclosure"]
        _deny("retry", "budget_exhausted", dec)
        return _finalize(dec)

    # 3) 破坏性工具：无论处置如何，绝不自动重试
    if profile.destructive:
        dec["required_confirmation"] = "destructive"
        _deny("retry", "side_effect_not_retry_safe", dec)
        dec["allowed_actions"] = [
            a for a in hints if a in _AUTO_SAFE_REPAIRS][:4]
        return _finalize(dec)

    # 4) 需要用户的处置：挂对应入口，不自动重试
    if diagnostic.disposition is RecoveryDisposition.NEEDS_USER_DATA:
        dec["allowed_actions"] = ["ask_user_data"]
        _deny("retry", "needs_user_input", dec)
        return _finalize(dec)
    if diagnostic.disposition is RecoveryDisposition.NEEDS_USER_APPROVAL:
        dec["required_confirmation"] = (
            dec["required_confirmation"]
            or _SIDE_EFFECT_CONFIRMATION.get(profile.side_effect_class)
            or "owner_write")
        dec["allowed_actions"] = ["owner_confirmation"]
        _deny("retry", "needs_user_input", dec)
        return _finalize(dec)
    if diagnostic.disposition is RecoveryDisposition.UNSAFE_TO_RETRY:
        _deny("retry", "unsafe_target", dec)
        _deny("requery_profile", "unsafe_target", dec)
        dec["allowed_actions"] = ["abort_with_disclosure"]
        return _finalize(dec)

    # 5) retryable：风险门 + 预算内的有界自动重试
    if diagnostic.disposition is RecoveryDisposition.RETRYABLE:
        retry_ok = profile.retry_safe or (
            idempotency_key_present
            and profile.side_effect_class == "state_mutation")
        if retry_ok:
            dec["auto_retry"] = True
            dec["allowed_actions"] = ["retry"]
            if diagnostic.reason.value in _BACKOFF_BASE_MS:
                dec["allowed_actions"] = ["retry_with_backoff"]
                dec["backoff_ms"] = _slice_backoff(
                    backoff_schedule_ms(diagnostic.reason.value), budget)
        else:
            confirm = _SIDE_EFFECT_CONFIRMATION.get(profile.side_effect_class)
            dec["required_confirmation"] = confirm
            _deny("retry", "side_effect_not_retry_safe", dec)
        return _finalize(dec)

    # 6) repairable：无副作用修复动作允许；retry 走风险门
    dec["allowed_actions"] = [
        a for a in hints if a in _AUTO_SAFE_REPAIRS][:4]
    if "retry" in hints:
        if budget is not None and not budget.retry_allowed:
            _deny("retry", "budget_exhausted", dec)
        elif profile.retry_safe:
            dec["allowed_actions"].append("retry")
        else:
            _deny("retry", "side_effect_not_retry_safe", dec)
    return _finalize(dec)


def _slice_backoff(schedule: Tuple[int, ...],
                   budget) -> Tuple[int, ...]:
    """从当前 attempts 起取剩余退避（纯切片；≤4 段）。"""
    if not schedule:
        return ()
    start = 0
    if budget is not None:
        try:
            start = max(0, int(budget.attempts))
        except (TypeError, ValueError):
            start = 0
    return tuple(schedule[start:start + 4])


__all__ = [
    "CONFIRMATION_KINDS",
    "DENIAL_REASONS",
    "RecoveryDecision",
    "backoff_schedule_ms",
    "decide_recovery",
]
