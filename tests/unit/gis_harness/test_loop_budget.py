"""loop_budget 测试（ADR-0213 D5/R6：循环预算 × governor RetryBudget）。"""
import pytest

from app.services.gis_harness import loop_budget
from app.services.gis_harness.loop_budget import (
    loop_charge,
    loop_retry_admissible,
    loop_retry_class,
)
from app.services.governor.contract import RetryClass
from app.services.governor.governor import (
    get_governor,
    reset_governor_for_tests,
)


@pytest.fixture()
def fresh_governor():
    reset_governor_for_tests()
    yield get_governor()
    reset_governor_for_tests()


def test_loop_retry_class_mapping():
    assert loop_retry_class("replan") is RetryClass.PI
    assert loop_retry_class("repair") is RetryClass.SELF_HEAL
    assert loop_retry_class("deepen") is RetryClass.DATA_FABRIC
    assert loop_retry_class("unknown_loop") is None


def test_admissible_with_fresh_tokens(fresh_governor):
    allowed, why = loop_retry_admissible("sess-a", "replan")
    assert allowed is True
    assert why == "allow"


def test_charge_decrements_global_pool(fresh_governor):
    before = fresh_governor.retries.snapshot()["global_left"]
    assert loop_charge("sess-a", "repair") is True
    assert fresh_governor.retries.snapshot()["global_left"] == before - 1


def test_exhausted_session_tokens_denied(fresh_governor):
    for _ in range(12):   # 默认 session_tokens = 12
        loop_charge("sess-b", "replan")
    allowed, why = loop_retry_admissible("sess-b", "replan")
    assert allowed is False
    assert why == "deny_session_exhausted"
    # 其他会话不受影响（session 份额隔离）
    assert loop_retry_admissible("sess-c", "replan")[0] is True


def test_cancelled_session_denied(fresh_governor):
    fresh_governor.retries.cancel_session("sess-d")
    allowed, why = loop_retry_admissible("sess-d", "repair")
    assert allowed is False
    assert why == "deny_cancelled"


def test_degrade_retry_mutual_exclusion(fresh_governor):
    fresh_governor.retries.note_degrade("sess-e", "repair")
    allowed, why = loop_retry_admissible("sess-e", "repair")
    assert allowed is False
    assert why == "deny_degrade_taken"


def test_governor_unavailable_reserves_bounded_local_attempt(monkeypatch):
    monkeypatch.setattr(loop_budget, "_retry_budget", lambda: None)
    allowed, why = loop_retry_admissible("sess-f", "replan")
    assert allowed is True
    assert why == "governor_unavailable_local_reserved"
    assert loop_charge("sess-f", "replan") is False


@pytest.fixture()
def fresh_local_state():
    loop_budget._LOCAL_ATTEMPTS.clear()
    loop_budget._CHARGE_UNAVAILABLE_SINCE = None
    yield
    loop_budget._LOCAL_ATTEMPTS.clear()
    loop_budget._CHARGE_UNAVAILABLE_SINCE = None


def _raise(*_a, **_k):
    raise RuntimeError("governor down")


def test_charge_exception_trips_circuit_then_half_opens(fresh_governor, fresh_local_state,
                                                        monkeypatch):
    budget = fresh_governor.retries
    real_charge = budget.charge
    monkeypatch.setattr(budget, "charge", _raise)
    assert loop_charge("sess-h", "replan") is False
    # 熔断期内 admissible 不再触碰 governor，走本地兜底
    monkeypatch.setattr(budget, "retry_allowed", _raise)
    allowed, why = loop_retry_admissible("sess-h", "repair")
    assert allowed is True
    assert why == "governor_unavailable_local_reserved"
    # 冷却后半开：重试 governor，成功后熔断复位
    loop_budget._CHARGE_UNAVAILABLE_SINCE -= loop_budget._CHARGE_COOLDOWN + 1
    monkeypatch.setattr(budget, "retry_allowed", lambda *_a, **_k: (True, "allow"))
    allowed, why = loop_retry_admissible("sess-h", "repair")
    assert allowed is True
    assert why == "allow"
    assert loop_budget._CHARGE_UNAVAILABLE_SINCE is None
    monkeypatch.setattr(budget, "charge", real_charge)
    assert loop_charge("sess-h", "repair") is True


def test_local_table_full_of_active_sessions_rejects_new_key(fresh_local_state, monkeypatch):
    monkeypatch.setattr(loop_budget, "_retry_budget", lambda: None)
    now = loop_budget._now()
    for i in range(loop_budget._LOCAL_SESSIONS):
        loop_budget._LOCAL_ATTEMPTS[(f"sess-{i}", "replan")] = (1, now)
    allowed, why = loop_retry_admissible("sess-new", "replan")
    assert allowed is False
    assert why == "local_retry_capacity_exhausted"


def test_local_table_evicts_stale_sessions(fresh_local_state, monkeypatch):
    monkeypatch.setattr(loop_budget, "_retry_budget", lambda: None)
    now = loop_budget._now()
    for i in range(loop_budget._LOCAL_SESSIONS):
        loop_budget._LOCAL_ATTEMPTS[(f"sess-{i}", "replan")] = (
            1, now - loop_budget._LOCAL_ENTRY_TTL - 1)
    loop_budget._LOCAL_ATTEMPTS[("sess-fresh", "replan")] = (1, now)
    allowed, why = loop_retry_admissible("sess-new", "replan")
    assert allowed is True
    assert why == "governor_unavailable_local_reserved"


def test_unknown_loop_no_gate():
    assert loop_retry_admissible("sess-g", "nonexistent")[0] is True
    assert loop_charge("sess-g", "nonexistent") is False
