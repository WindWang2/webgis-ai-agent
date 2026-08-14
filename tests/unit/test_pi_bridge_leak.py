"""Regression: Pi-bridge per-turn state must not leak across long-lived sessions.

Simulates 50 long chat sessions, each with tool dispatches (the hardened
callback contract requires the verified turn sessionId — SEC fix — so every
dispatch lands in that session's bucket of _session_executed_sets) and a
client that disconnects mid-stream. Asserts the
module-level dedup sets and dispatch-result cache return to baseline after
every turn, and that gc/tracemalloc don't accumulate retained tool results.

RED on the pre-fix code: neither _session_executed_sets[""] nor
_dispatch_result_cache is cleaned at turn end (only at the *next* turn start,
keyed by the real session id), so both grow monotonically across sessions.
"""
import asyncio
import gc
import tracemalloc
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge, PiToolRequest, dispatch_tool
from app.services.tool_dispatch_service import ToolDispatchResult
from tests.fixtures.pi_mocks import make_token_event


class _StubRegistry:
    def list_tools(self):
        return ["query_map_features"]

    def metadata(self, name):
        return {"tier": 1}


def _make_event_rpc(events):
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.process_died = False

    async def _seed(cmd, data=None):
        for ev in events:
            await rpc.events.put(ev)

    rpc.request = AsyncMock(side_effect=_seed)
    return rpc


@pytest.fixture(autouse=True)
def _clean_state():
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    yield
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()


async def _fake_dispatch(self, tc, session_id, executed):
    """Stand-in for ToolDispatchService.dispatch: adds to the dedup set, returns ok."""
    executed.add((tc["function"]["name"], str(tc["function"]["arguments"])))
    return ToolDispatchResult(
        status="ok",
        llm_payload="ok",
        slim_event={"type": "step_result"},
        geojson_ref=None,
        raw_result={"data": "x" * 1024},  # MB-scale in the real world
        error_msg=None,
    )


async def _run_one_session(i, monkeypatch):
    """One chat session: two Pi tool dispatches + a stream the client abandons."""
    for j in range(2):
        await dispatch_tool(PiToolRequest(
            name="query_map_features",
            toolCallId=f"tc-{i}-{j}",
            arguments={"bbox": [116.0, 39.0, 116.1, 39.1]},
            # The signed-token HTTP route always stamps the verified turn
            # session; the bridge rejects sessionId-less callbacks since the
            # cross-session mutation fix.
            sessionId=f"session-{i}",
        ))
    rpc = _make_event_rpc([make_token_event("hello")])
    bridge = PiBridge(rpc=rpc)
    agen = bridge.stream_prompt(f"msg {i}", session_id=f"session-{i}")
    ev = await agen.__anext__()
    assert ev.startswith("event: task_start")
    await agen.aclose()  # client disconnect -> GeneratorExit -> finally


@pytest.mark.asyncio
async def test_turn_state_returns_to_baseline_across_many_sessions(monkeypatch):
    monkeypatch.setattr(bridge_mod, "get_tool_registry", lambda: _StubRegistry())
    monkeypatch.setattr(bridge_mod.ToolDispatchService, "dispatch", _fake_dispatch)

    for i in range(50):
        await _run_one_session(i, monkeypatch)
        assert bridge_mod._session_executed_sets == {}, (
            f"turn {i}: executed sets leaked {len(bridge_mod._session_executed_sets)} "
            f"bucket(s) ("" bucket size={len(bridge_mod._session_executed_sets.get('', ()))})"
        )
        assert bridge_mod._dispatch_result_cache == {}, (
            f"turn {i}: dispatch cache leaked {len(bridge_mod._dispatch_result_cache)} entries"
        )


@pytest.mark.asyncio
async def test_no_retained_objects_or_memory_growth(monkeypatch):
    monkeypatch.setattr(bridge_mod, "get_tool_registry", lambda: _StubRegistry())
    monkeypatch.setattr(bridge_mod.ToolDispatchService, "dispatch", _fake_dispatch)

    tracemalloc.start()
    try:
        for i in range(50):
            await _run_one_session(i, monkeypatch)
        current, _peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    gc.collect()
    retained = [o for o in gc.get_objects() if isinstance(o, ToolDispatchResult)]
    assert len(retained) <= 5, f"{len(retained)} ToolDispatchResult objects retained after 50 sessions"
    assert current < 2_000_000, f"tracemalloc current={current} bytes after 50 sessions"
