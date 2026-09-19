"""#1407: harness kernel/state lifecycle — settle completed, fencing, phase order."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_end_turn_completed_settles_running_steps():
    from app.services.harness_kernel.models import PlanStep, PlanTurnRecord
    from app.services.harness_kernel.runtime import GISSessionRuntime
    from app.services.session_plan import SessionPlan

    plan = SessionPlan(
        session_id="s-1407",
        envelope_id="e1",
        turns=[PlanTurnRecord(turn_id="t1", status="running", host="pi")],
        steps=[
            PlanStep(
                id="step-1", goal="g", capability="cap", tool="tool_a",
                status="running", host="pi", turn_id="t1",
            )
        ],
    )

    async def _run():
        rt = GISSessionRuntime("s-1407")

        class _CM:
            async def __aenter__(self):
                return MagicMock(lost=False)

            async def __aexit__(self, *a):
                return False

        with patch(
            "app.services.harness_kernel.runtime.load_session_plan",
            new=AsyncMock(return_value=plan),
        ), patch(
            "app.services.harness_kernel.runtime.save_session_plan",
            new=AsyncMock(),
        ), patch(
            "app.services.harness_kernel.runtime._session_scope",
            return_value=_CM(),
        ):
            await rt.end_turn(
                "t1", host="pi", status="completed", checkpoint=False)
        assert plan.steps[0].status == "failed"
        assert plan.turns[0].status == "completed"

    asyncio.run(_run())


def test_settle_step_same_tool_call_id_no_attempts_inflation():
    from app.services.harness_kernel.models import PlanStep, StepEvidence
    from app.services.harness_kernel.runtime import _settle_step
    from app.services.session_plan import SessionPlan

    step = PlanStep(
        id="s1", goal="g", capability="cap_x", tool="t",
        status="succeeded", host="pi", turn_id="t1", attempts=1,
        evidence=[StepEvidence(tool="t", tool_call_id="tc-1", ref="", at=0.0)],
    )
    plan = SessionPlan(session_id="s", envelope_id="e", steps=[step])
    _settle_step(
        plan,
        capability_hits=["cap_x"],
        status="succeeded",
        tool_name="t",
        tool_call_id="tc-1",
        ref="",
        error="",
        host="pi",
        turn_id="t1",
    )
    assert step.attempts == 1
    assert len(step.evidence) == 1


def test_derive_phase_replan_pending_before_aborted():
    from app.services.gis_harness.durable_context import LOOP_BUDGETS
    from app.services.gis_harness.runtime_state_machine import (
        RuntimePhase,
        derive_runtime_phase,
    )

    chapter = {
        "plan_id": "p1",
        "query": "q",
        "plan_runtime": {"replan_pending": True},
        "rows": [{"id": "r1", "status": "pending"}],
    }
    exhausted = {k: int(v) for k, v in LOOP_BUDGETS.items()}
    phase = derive_runtime_phase(chapter, recovery_loops=exhausted)
    assert phase == RuntimePhase.REPLANNING.value


def test_transition_empty_owner_raises_fencing():
    from app.services.mission_runtime.store import FencingError, MissionStore

    store = MissionStore.__new__(MissionStore)
    with pytest.raises(FencingError, match="EMPTY_LEASE_OWNER"):
        store.transition("m1", to_state="running", lease_epoch=0, owner="")


def test_request_replan_charges_inside_lock_source():
    """Structural: update_recovery_state call site sits inside the lock block."""
    src = open("app/services/gis_harness/plan_runtime.py").read()
    # After fix: update_recovery_state appears before save_session_plan inside try
    idx_upd = src.find("await update_recovery_state(\n                session_id, loop=REPLAN_LOOP")
    idx_save = src.find("await save_session_plan(fresh)", idx_upd)
    idx_except = src.find("except Exception", idx_upd)
    assert idx_upd > 0 and idx_save > idx_upd
    assert idx_except > idx_save  # charge+save before except (still inside try/lock)
