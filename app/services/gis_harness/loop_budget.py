"""Harness 循环预算 × governor RetryBudget（R6，ADR-0213 D5）。

``durable_context.LOOP_BUDGETS`` 是**次数**预算（跨重启 durable）；governor
``RetryBudget`` 是**令牌**预算（进程内、session 份额 + 全局池）。本模块把
两闸串联：

    次数闸（既有调用点，不动）→ loop_retry_admissible（令牌闸）→ 回路动作
    → loop_charge（实扣）

- replan → RetryClass.PI；repair → SELF_HEAL；deepen/requalify → DATA_FABRIC；
- governor 缺席/异常 → 令牌闸放行（fail-open 到既有次数语义；governor 是
  进程内存态，不存在持久化降级面）；
- 拒绝路径零副作用：不 charge、不置位、不改 recovery_state —— 调用方以
  ``abort_with_disclosure`` 诚实退出（与 #1401 fail-closed 纪律同向）。
"""
from __future__ import annotations

from typing import Optional, Tuple

from app.services.governor.contract import RetryClass

__all__ = [
    "loop_retry_class",
    "loop_retry_admissible",
    "loop_charge",
]

#: 循环 → RetryClass（封闭词表；未列出的循环不接令牌闸）
_LOOP_RETRY_CLASS = {
    "replan": RetryClass.PI,
    "repair": RetryClass.SELF_HEAL,
    "deepen": RetryClass.DATA_FABRIC,
    "requalify": RetryClass.DATA_FABRIC,
}


def loop_retry_class(loop: str) -> Optional[RetryClass]:
    return _LOOP_RETRY_CLASS.get(str(loop or "").strip().lower())


def _retry_budget():
    """进程 governor 的 RetryBudget（缺席 → None；绝不抛）。"""
    try:
        from app.services.governor.governor import get_governor
        return get_governor().retries
    except Exception:  # noqa: BLE001 — fail-open
        return None


def loop_retry_admissible(session_id: str, loop: str,
                          *, degrade_taken: bool = False
                          ) -> Tuple[bool, str]:
    """令牌闸咨询（不扣 token）。governor 缺席 → (True, "governor_unavailable")。"""
    cls = loop_retry_class(loop)
    if cls is None or not session_id:
        return True, "no_class"
    budget = _retry_budget()
    if budget is None:
        return True, "governor_unavailable"
    try:
        allowed, why = budget.retry_allowed(
            session_id, cls, operation=str(loop), degrade_taken=degrade_taken)
        return bool(allowed), str(why)
    except Exception:  # noqa: BLE001 — fail-open
        return True, "governor_unavailable"


def loop_charge(session_id: str, loop: str) -> bool:
    """回路动作成立后的实扣（幂等无害；故障静默）。返回是否实扣。"""
    cls = loop_retry_class(loop)
    if cls is None or not session_id:
        return False
    budget = _retry_budget()
    if budget is None:
        return False
    try:
        budget.charge(session_id, cls)
        return True
    except Exception:  # noqa: BLE001 — 记账故障绝不影响回路结果
        return False
