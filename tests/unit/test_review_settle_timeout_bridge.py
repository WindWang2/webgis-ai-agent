"""A real coordinator timeout must remain a failure in both Pi host paths."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app import agent_pi_bridge as bridge_module
from app.lib.runtime.evidence import Outcome
from app.services import harness_kernel
from app.services.harness_kernel import runtime as runtime_module, settle, settle_claim
from app.services.session_data import MemorySessionStore
from app.services.session_plan import load_session_plan


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_coordinator_timeout_fails_pi_tracker_and_turn_evidence(stream, monkeypatch):
    sid = f"settle-timeout-pi-{stream}"
    backend = MemorySessionStore()
    runtime = runtime_module.GISSessionRuntime(sid, store=backend)
    # Preserve the package's lazy export after teardown (setattr would restore
    # a concrete function and hide later patches to its defining module).
    monkeypatch.setitem(harness_kernel.__dict__, "get_runtime", lambda session_id: runtime)
    monkeypatch.setattr(runtime_module, "get_runtime", lambda session_id: runtime)
    monkeypatch.setattr(settle_claim, "SETTLE_EFFECT_BUDGET_S", 0.02)
    # The host pre-run is separate from coordinator ownership. Keep it cheap,
    # then stall the real coordinator before its terminal authority write.
    monkeypatch.setattr(settle, "run_settle_effects", AsyncMock(return_value=([], None)))
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def stalled_refusal(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(settle, "resolve_turn_refusal", stalled_refusal)
    monkeypatch.setattr("app.services.chat.pi_turn_context.bind_turn_prompt", AsyncMock(return_value="hi"))
    tracker = Mock()
    tracker.create.return_value = SimpleNamespace(id=f"{sid}-task")
    monkeypatch.setattr("app.services.chat.engine_instance.try_get_chat_engine", lambda: SimpleNamespace(tracker=tracker))
    summaries = []
    monkeypatch.setattr(bridge_module, "emit_turn_summary", summaries.append)
    settle.reset_for_tests()
    rpc = SimpleNamespace(events=asyncio.Queue(), process_died_event=asyncio.Event())

    async def request(command, data=None):
        if command == "prompt":
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=request)
    bridge = bridge_module.PiBridge(rpc=rpc)
    turn_results = []
    try:
        if stream:
            async for _ in bridge.stream_prompt("hi", session_id=sid, on_turn_result=turn_results.append):
                pass
        else:
            await bridge.prompt("hi", session_id=sid)
        plan = await load_session_plan(sid, store=backend)
        assert entered.is_set() and cancelled.is_set()
        assert plan.turns[-1].status == "interrupted"
        assert plan.turns[-1].settle_finished_at > 0
        tracker.fail_task.assert_called_once()
        tracker.complete_task.assert_not_called()
        tracker.cancel.assert_not_called()
        assert summaries[-1].outcome.outcome == Outcome.FAILED
        assert summaries[-1].outcome.failure_class == "pi_settle_interrupted"
        if stream:
            assert turn_results[-1]["completed"] is False
    finally:
        settle.reset_for_tests()
