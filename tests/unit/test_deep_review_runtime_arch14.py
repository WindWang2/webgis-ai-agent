"""ARCH-14: the legacy host must forward SessionPlan SSE events.

``legacy_adapter.project_orchestrator_plan`` returns ``SessionPlanEvent``s
for newly projected step rows, but ``execution_engine._maybe_plan`` discarded
them — the SessionPlan panel only caught up on next hydration. The streaming
legacy path now yields ``events_to_sse(...)`` for them (Pi parity).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.chat_engine import ChatEngine
from app.services.session_plan import SESSION_PLAN_STEP, SessionPlanEvent
from app.tools.registry import ToolRegistry


def _make_registry() -> ToolRegistry:
    r = ToolRegistry()

    @r.tool(name="echo_tool", description="echo", tier=1)
    def echo_tool(text: str = "") -> dict:
        return {"echo": text, "success": True}

    return r


@pytest.mark.asyncio
async def test_maybe_plan_stashes_projection_events(monkeypatch):
    from app.services.chat import plan_orchestrator as po_mod
    from app.services.planning import followup as followup_mod
    from app.services.planning.followup import FollowUpKind
    from app.services.harness_kernel import legacy_adapter

    eng = ChatEngine(_make_registry())
    monkeypatch.setattr(eng, "_get_map_state_summary", AsyncMock(return_value=""))
    monkeypatch.setattr(eng, "_planner_llm_config", lambda: MagicMock())

    fake_plan = MagicMock()
    fake_plan.domains = []
    orch = po_mod.plan_orchestrator
    monkeypatch.setattr(orch, "restore_plan", AsyncMock())
    monkeypatch.setattr(orch, "get_plan", MagicMock(return_value=None))
    monkeypatch.setattr(orch, "orchestrate_plan", AsyncMock(return_value=fake_plan))
    monkeypatch.setattr(
        followup_mod,
        "classify_followup",
        lambda *a, **k: FollowUpKind.new_goal,
    )
    projected = SessionPlanEvent(
        event=SESSION_PLAN_STEP,
        data={"session_id": "s-arch14", "step_id": "l1", "status": "pending"},
    )
    monkeypatch.setattr(
        legacy_adapter,
        "project_orchestrator_plan",
        AsyncMock(return_value=[projected]),
    )

    plan = await eng._maybe_plan("s-arch14", "分析成都市医院分布", [])

    assert plan is fake_plan
    stashed = eng._pop_plan_projection_events("s-arch14")
    assert len(stashed) == 1
    assert stashed[0].event == SESSION_PLAN_STEP
    # popped once — no replay on the next turn
    assert eng._pop_plan_projection_events("s-arch14") == []


@pytest.mark.asyncio
async def test_stream_forwards_plan_projection_events(monkeypatch):
    eng = ChatEngine(_make_registry())

    async def fake_maybe_plan(session_id, message, messages):
        eng._stash_plan_projection_events(session_id, [
            SessionPlanEvent(
                event=SESSION_PLAN_STEP,
                data={"session_id": session_id, "step_id": "l1", "status": "pending"},
            )
        ])
        return None

    monkeypatch.setattr(eng, "_maybe_plan", fake_maybe_plan)
    monkeypatch.setattr(
        eng,
        "_get_or_create_session",
        AsyncMock(return_value=[{"role": "system", "content": "sys"}]),
    )
    monkeypatch.setattr(eng, "_save_msg_async", AsyncMock(return_value=None))
    monkeypatch.setattr(eng, "_generate_title", AsyncMock(return_value=None))

    async def fake_stream(*a, **k):
        yield ("done", {"message": {"content": "answer", "tool_calls": None}})

    monkeypatch.setattr(eng, "_call_llm_stream", fake_stream)

    events = [
        e
        async for e in eng.chat_stream("hi", session_id="s-arch14-stream")
    ]

    assert any("session_plan_step" in e for e in events), (
        "ARCH-14: projected SessionPlan step events must reach the SSE stream"
    )
