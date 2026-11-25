"""#2058 / #2060：全局重试池会按时间回补；turn/goal 账本有上限。"""
from app.services.governor.contract import Dimension, RetryClass
from app.services.governor.retry_budget import DENY_GLOBAL_EXHAUSTED, RetryBudget
from app.services.governor.session_budget import SessionBudgetLedger, _ScopeLedger


def test_cancel_session_is_bounded_when_never_charged():
    """取消但从未重试的会话也必须有界（cancel 路径触发驱逐）。"""
    budget = RetryBudget(global_tokens=10, session_tokens=1)
    budget._MAX_SESSIONS = 3
    for i in range(10):
        budget.cancel_session(f"gone-{i}")
    assert len(budget.snapshot()["sessions"]) <= 3


def test_context_tokens_accumulate_in_global_scope():
    """chat 面 token 必须同时累加进 global 累计账。"""
    ledger = SessionBudgetLedger({})
    ledger.record_context_tokens("s", "t1", 5000)
    ledger.record_context_tokens("s", "t2", 7000)
    snap = ledger.snapshot("s", turn_id="t2")
    assert (snap["global"]["cumulative"][Dimension.CONTEXT_TOKENS.value]
            == 12000)


def test_global_retry_pool_refills_after_the_window():
    now = [0.0]
    budget = RetryBudget(
        global_tokens=2, session_tokens=10, clock=lambda: now[0], refill_window_s=600.0,
    )
    budget.charge("a", RetryClass.TOOL)
    budget.charge("a", RetryClass.TOOL)
    allowed, why = budget.retry_allowed("b", RetryClass.LLM)
    assert allowed is False and why == DENY_GLOBAL_EXHAUSTED

    now[0] = 299.0
    allowed, why = budget.retry_allowed("b", RetryClass.LLM)
    assert allowed is False and why == DENY_GLOBAL_EXHAUSTED

    now[0] = 600.0
    allowed, why = budget.retry_allowed("b", RetryClass.LLM)
    assert allowed is True and why == "allow"
    assert budget.snapshot()["global_left"] == 2


def test_turn_and_goal_ledgers_are_evicted():
    ledger = SessionBudgetLedger({})
    ledger._MAX_TRANSIENT_LEDGERS = 3
    ledger._EVICT_TRANSIENT_TO = 1
    for i in range(5):
        ledger._ledgers["goal"][f"g{i}"] = _ScopeLedger(last_activity=float(i))
    ledger.record_context_tokens("sess", "turn-0", 1)
    for i in range(1, 6):
        ledger.record_context_tokens("sess", f"turn-{i}", 1)
    assert len(ledger._ledgers["goal"]) <= 3
    assert len(ledger._ledgers["turn"]) <= 3
