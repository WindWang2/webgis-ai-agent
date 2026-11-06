"""Harness 循环预算 × governor RetryBudget（R6，ADR-0213 D5）。

``durable_context.LOOP_BUDGETS`` 是**次数**预算（跨重启 durable）；governor
``RetryBudget`` 是**令牌**预算（进程内、session 份额 + 全局池）。本模块把
两闸串联：

    次数闸（既有调用点，不动）→ loop_retry_admissible（令牌闸）→ 回路动作
    → loop_charge（实扣）

- replan → RetryClass.PI；repair → SELF_HEAL；deepen/requalify → DATA_FABRIC；
- governor 缺席/异常 → 每 session/loop 最多三次本地原子预留；
  本地表满时拒绝新键，不驱逐旧预算以免重获配额；
- 拒绝路径零副作用：不 charge、不置位、不改 recovery_state —— 调用方以
  ``abort_with_disclosure`` 诚实退出（与 #1401 fail-closed 纪律同向）。
"""
from __future__ import annotations

from typing import Optional, Tuple
import logging
import threading
from collections import OrderedDict

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


logger = logging.getLogger(__name__)
_LOCAL_LOCK = threading.Lock()
_LOCAL_ATTEMPTS: OrderedDict[tuple[str, str], int] = OrderedDict()
_LOCAL_CAP = 3
_LOCAL_SESSIONS = 4096
_CHARGE_UNAVAILABLE = False


def _fallback_allowed(session_id: str, loop: str) -> Tuple[bool, str]:
    with _LOCAL_LOCK:
        key = (session_id, str(loop).strip().lower())
        if key not in _LOCAL_ATTEMPTS and len(_LOCAL_ATTEMPTS) >= _LOCAL_SESSIONS:
            return False, "local_retry_capacity_exhausted"
        # Reserve at admission, avoiding concurrent failover consultations
        # passing before the eventual action charge.
        attempts = _LOCAL_ATTEMPTS.get(key, 0)
        if attempts >= _LOCAL_CAP:
            return False, "local_retry_budget_exhausted"
        _LOCAL_ATTEMPTS[key] = attempts + 1
    return True, "governor_unavailable_local_reserved"


def loop_retry_class(loop: str) -> Optional[RetryClass]:
    return _LOOP_RETRY_CLASS.get(str(loop or "").strip().lower())


def _retry_budget():
    """进程 governor 的 RetryBudget（缺席 → None；绝不抛）。"""
    try:
        from app.services.governor.governor import get_governor
        return get_governor().retries
    except Exception:  # noqa: BLE001 — caller applies the bounded local gate
        return None


def loop_retry_admissible(session_id: str, loop: str,
                          *, degrade_taken: bool = False
                          ) -> Tuple[bool, str]:
    """Consult the governor, or reserve one bounded local failover attempt."""
    cls = loop_retry_class(loop)
    if cls is None or not session_id:
        return True, "no_class"
    if _CHARGE_UNAVAILABLE:
        return _fallback_allowed(session_id, loop)
    budget = _retry_budget()
    if budget is None:
        return _fallback_allowed(session_id, loop)
    try:
        allowed, why = budget.retry_allowed(
            session_id, cls, operation=str(loop), degrade_taken=degrade_taken)
        return bool(allowed), str(why)
    except Exception:  # noqa: BLE001 — fail-open
        return _fallback_allowed(session_id, loop)


def loop_charge(session_id: str, loop: str) -> bool:
    """Charge a completed action; charge failure forces bounded failover admission."""
    cls = loop_retry_class(loop)
    if cls is None or not session_id:
        return False
    budget = _retry_budget()
    if budget is None:
        return False
    try:
        budget.charge(session_id, cls)
        return True
    except Exception:  # noqa: BLE001 — failover admission remains bounded locally
        global _CHARGE_UNAVAILABLE
        _CHARGE_UNAVAILABLE = True
        _fallback_allowed(session_id, loop)
        logger.warning("Harness governor retry charge unavailable loop=%s", loop)
        return False
