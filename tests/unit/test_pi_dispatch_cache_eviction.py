"""Tests for #1039: dispatch dedup-cache & result cache eviction hardening.

Validates:
1. Cache entries belonging to the active turn are protected from eviction under load.
2. _session_executed_sets does not evict the active session under high session load.
3. _cleanup_turn_state cleans up only the specified session's state rather than wiping unrelated sessions.
4. Inactive stale entries are correctly evicted when cache exceeds capacity.
"""
import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import (
    _cleanup_turn_state,
    _dispatch_result_cache,
    _session_executed_sets,
    _session_plan_sse_cache,
    cache_dispatch_result,
    cache_session_plan_sse,
    get_cached_dispatch_result,
    take_session_plan_sse,
)
from app.services.tool_dispatch_service import ToolDispatchResult


@pytest.fixture(autouse=True)
def clean_caches():
    _dispatch_result_cache.clear()
    _session_plan_sse_cache.clear()
    _session_executed_sets.clear()
    bridge_mod._active_turn_context = None
    yield
    _dispatch_result_cache.clear()
    _session_plan_sse_cache.clear()
    _session_executed_sets.clear()
    bridge_mod._active_turn_context = None


def test_active_turn_dispatch_result_not_evicted_under_load(monkeypatch):
    """Active session's dispatch results are protected when cache exceeds 128."""
    active_session = "sess-active-protect"
    active_turn = "turn-001"
    monkeypatch.setattr(bridge_mod, "_active_turn_context", (active_session, active_turn))

    dummy_result = ToolDispatchResult(
        status="ok",
        llm_payload="ok",
        slim_event={"ok": True},
        geojson_ref=None,
        raw_result={"ok": True},
        error_msg=None,
    )

    # 1. Insert active session's tool result first (at the head of the FIFO)
    cache_dispatch_result("call-active-1", dummy_result, session_id=active_session)
    cache_dispatch_result("call-active-2", dummy_result, session_id=active_session)

    # 2. Flood cache with 150 other inactive sessions
    for i in range(150):
        cache_dispatch_result(f"call-flood-{i}", dummy_result, session_id=f"sess-inactive-{i}")

    # Active session entries MUST NOT be evicted
    res1 = get_cached_dispatch_result("call-active-1", session_id=active_session)
    res2 = get_cached_dispatch_result("call-active-2", session_id=active_session)
    assert res1 is not None, "Active session result call-active-1 was prematurely evicted"
    assert res2 is not None, "Active session result call-active-2 was prematurely evicted"


def test_active_turn_session_plan_sse_not_evicted_under_load(monkeypatch):
    """Active session's SessionPlan SSE blobs are protected when cache exceeds 128."""
    active_session = "sess-active-sse"
    active_turn = "turn-001"
    monkeypatch.setattr(bridge_mod, "_active_turn_context", (active_session, active_turn))

    # 1. Insert active session's SSE blob first
    cache_session_plan_sse("call-sse-1", "event: session_plan_updated\ndata: {}\n\n", session_id=active_session)

    # 2. Flood cache with 150 other inactive sessions
    for i in range(150):
        cache_session_plan_sse(f"call-flood-{i}", "event: test\ndata: {}\n\n", session_id=f"sess-flood-{i}")

    # Active session SSE blob MUST NOT be evicted
    sse = take_session_plan_sse("call-sse-1", session_id=active_session)
    assert sse != "", "Active session SessionPlan SSE was prematurely evicted"
    assert "session_plan_updated" in sse


def test_active_session_executed_sets_not_evicted_under_load(monkeypatch):
    """Active session's executed tool dedup set is protected when _session_executed_sets exceeds 128."""
    active_session = "sess-active-dedup"
    active_turn = "turn-001"
    monkeypatch.setattr(bridge_mod, "_active_turn_context", (active_session, active_turn))

    # 1. Register active session with a recorded tool
    active_set = _session_executed_sets.setdefault(active_session, set())
    active_set.add(("query_local_poi", '{"district": "wuhou"}'))

    # 2. Simulate dispatch_tool recording executions for 150 other sessions
    for i in range(150):
        s_id = f"sess-other-{i}"
        s_set = _session_executed_sets.setdefault(s_id, set())
        s_set.add(("some_tool", f'{{"i": {i}}}'))
        if len(_session_executed_sets) > 128:
            # We call the eviction logic or let helper handle it
            from app.agent_pi_bridge import _evict_session_executed_set
            _evict_session_executed_set()

    # Active session executed set MUST still be present and intact
    assert active_session in _session_executed_sets
    assert ("query_local_poi", '{"district": "wuhou"}') in _session_executed_sets[active_session]


def test_cleanup_turn_state_is_session_scoped():
    """_cleanup_turn_state cleans up only the target session and leaves other sessions untouched."""
    dummy_result = ToolDispatchResult(
        status="ok",
        llm_payload="ok",
        slim_event={"ok": True},
        geojson_ref=None,
        raw_result={"ok": True},
        error_msg=None,
    )

    # Seed data for session A and session B
    cache_dispatch_result("call-a", dummy_result, session_id="sess-A")
    cache_dispatch_result("call-b", dummy_result, session_id="sess-B")

    cache_session_plan_sse("call-a", "sse-data-A", session_id="sess-A")
    cache_session_plan_sse("call-b", "sse-data-B", session_id="sess-B")

    _session_executed_sets["sess-A"] = {("tool_a", "{}")}
    _session_executed_sets["sess-B"] = {("tool_b", "{}")}

    # Cleanup session A only
    _cleanup_turn_state("sess-A")

    # Session A is cleaned
    assert get_cached_dispatch_result("call-a", session_id="sess-A") is None
    assert take_session_plan_sse("call-a", session_id="sess-A") == ""
    assert "sess-A" not in _session_executed_sets

    # Session B is preserved
    assert get_cached_dispatch_result("call-b", session_id="sess-B") is not None
    assert take_session_plan_sse("call-b", session_id="sess-B") == "sse-data-B"
    assert "sess-B" in _session_executed_sets


def test_inactive_entries_evicted_when_exceeding_capacity():
    """When cache exceeds 128 without an active turn, oldest inactive entries are evicted."""
    dummy_result = ToolDispatchResult(
        status="ok",
        llm_payload="ok",
        slim_event={"ok": True},
        geojson_ref=None,
        raw_result={"ok": True},
        error_msg=None,
    )

    # Insert 150 entries
    for i in range(150):
        cache_dispatch_result(f"call-{i}", dummy_result, session_id=f"sess-{i}")
        cache_session_plan_sse(f"call-{i}", f"data-{i}", session_id=f"sess-{i}")

    # Total cache sizes must not exceed 128
    assert len(_dispatch_result_cache) <= 128
    assert len(_session_plan_sse_cache) <= 128

    # Oldest entries (e.g. call-0 .. call-21) were evicted
    assert get_cached_dispatch_result("call-0", session_id="sess-0") is None
    assert take_session_plan_sse("call-0", session_id="sess-0") == ""

    # Newest entries (e.g. call-149) are retained
    assert get_cached_dispatch_result("call-149", session_id="sess-149") is not None
    assert take_session_plan_sse("call-149", session_id="sess-149") == "data-149"


def test_clear_dispatch_cache_is_session_scoped():
    """_clear_dispatch_cache(session_id) clears only the specified session."""
    from app.agent_pi_bridge import _clear_dispatch_cache

    dummy_result = ToolDispatchResult(
        status="ok",
        llm_payload="ok",
        slim_event={"ok": True},
        geojson_ref=None,
        raw_result={"ok": True},
        error_msg=None,
    )

    cache_dispatch_result("call-x", dummy_result, session_id="sess-X")
    cache_dispatch_result("call-y", dummy_result, session_id="sess-Y")
    cache_session_plan_sse("call-x", "sse-X", session_id="sess-X")
    cache_session_plan_sse("call-y", "sse-Y", session_id="sess-Y")

    _clear_dispatch_cache("sess-X")

    # sess-X is cleared
    assert get_cached_dispatch_result("call-x", session_id="sess-X") is None
    assert take_session_plan_sse("call-x", session_id="sess-X") == ""

    # sess-Y is preserved
    assert get_cached_dispatch_result("call-y", session_id="sess-Y") is not None
    assert take_session_plan_sse("call-y", session_id="sess-Y") == "sse-Y"
