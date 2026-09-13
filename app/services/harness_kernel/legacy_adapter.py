"""CanonicalPlan → SessionPlan projection adapter (K4, ADR-0180 D-006).

One-way projection: the legacy ChatEngine keeps CanonicalPlan as its own
source of truth (``plan-current`` store); this adapter mirrors the
host-neutral semantics into the SAME SessionPlan envelope the Pi path uses,
so both hosts produce equivalent Harness plan evidence (parity KPI, frontend
session-plan panel on the legacy host).

It never writes CanonicalPlan, never imports Pi-domain modules, and every
entry point is exception-safe (projection is additive disclosure for the
legacy path — a failure must never break a legacy turn).

Steps map via ``step_status_from_canonical``; step ids are ``l{n}`` (legacy
plan steps are n-indexed) so they never collide with chapter-materialized
``step-<capability>`` ids on a shared session.
"""
from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

from app.services.distributed_lock import session_lock_registry
from app.services.harness_kernel import metrics as hk_metrics
from app.services.harness_kernel.models import (
    MAX_DECISIONS,
    MAX_STEPS,
    MAX_TURNS,
    PlanDecision,
    PlanStep,
    PlanTurnRecord,
    StepEvidence,
    push_bounded,
    step_status_from_canonical,
)
from app.services.session_plan import (
    SESSION_PLAN_STEP,
    SessionPlan,
    SessionPlanEvent,
    ensure_session_plan_slot,
    load_session_plan,
    save_session_plan,
)

logger = logging.getLogger(__name__)

LEGACY_STEP_ID_FMT = "l{}"


def _step_event(plan: SessionPlan, step: PlanStep) -> SessionPlanEvent:
    latest = step.latest_evidence()
    return SessionPlanEvent(
        event=SESSION_PLAN_STEP,
        data={
            "session_id": plan.session_id,
            "envelope_id": plan.envelope_id,
            "step_id": step.id,
            "goal": step.goal,
            "capability": step.capability,
            "tool": step.tool,
            "status": step.status,
            "attempts": step.attempts,
            "ref": latest.ref if latest else "",
            "host": step.host,
            "turn_id": step.turn_id,
        },
    )


def _journal(
    plan: SessionPlan, kind: str, *, note: str = "", detail: Optional[dict] = None
) -> None:
    push_bounded(
        plan.decisions,
        PlanDecision(kind=kind, at=time.time(), host="chatengine", note=note[:300],
                     detail=detail or {}),
        MAX_DECISIONS,
    )


async def project_orchestrator_plan(
    session_id: str,
    plan: Any,
    *,
    turn_id: str = "",
) -> List[SessionPlanEvent]:
    """Mirror an orchestrator ``Plan`` (post ``_maybe_plan``) into SessionPlan.

    Creates the envelope slot when absent and (re)builds the ``l{n}`` steps
    from the plan's steps. Existing step state survives by step id (a re-plan
    that keeps a step keeps its evidence).
    """
    if not session_id or plan is None:
        return []
    events: List[SessionPlanEvent] = []
    async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
        envelope = await ensure_session_plan_slot(session_id)
        now = time.time()
        if lock is not None and lock.lost:
            return []
        if not envelope.user_goal:
            envelope.user_goal = str(getattr(plan, "intent", "") or "")
            _journal(envelope, "legacy_projected", note="plan projected (new)")
        existing = {s.id: s for s in envelope.steps}
        steps: List[PlanStep] = []
        for s in getattr(plan, "steps", None) or []:
            sid = LEGACY_STEP_ID_FMT.format(int(getattr(s, "n", 0) or 0))
            prev = existing.get(sid)
            status = "succeeded" if bool(getattr(s, "done", False)) else "pending"
            if prev is not None:
                if status == "succeeded" and prev.status != "succeeded":
                    prev.status = "succeeded"
                    prev.attach_evidence(
                        StepEvidence(tool=str(getattr(s, "tool", "") or ""), at=now)
                    )
                    events.append(_step_event(envelope, prev))
                steps.append(prev)
                continue
            step = PlanStep(
                id=sid,
                goal=str(getattr(s, "goal", "") or "")[:300],
                tool=str(getattr(s, "tool", "") or ""),
                tool_binding=[
                    str(t) for t in (getattr(s, "tool_binding", None) or [])
                ],
                status=status,  # type: ignore[arg-type]
                depends_on=[],
                host="chatengine",
                turn_id=turn_id,
                created_at=now,
                updated_at=now,
            )
            steps.append(step)
        if envelope.steps != steps:
            envelope.steps = steps
            for st in steps:
                events.append(_step_event(envelope, st))
        hk_metrics.record("host_parity", host="chatengine")
        if lock is not None and lock.lost:
            return []
        await save_session_plan(envelope)
        return events


async def project_canonical_flush(session_id: str, canonical: Any) -> None:
    """Mirror step-level truth at ``_flush_plan`` time (canonical is truth).

    Marks kernel steps ``l{n}`` succeeded per CanonicalStep.status and
    journals the flush — this is what keeps the projection convergent when
    ``advance_step`` ticked steps mid-turn.
    """
    if not session_id or canonical is None:
        return
    async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
        envelope = await load_session_plan(session_id)
        if envelope is None or (lock is not None and lock.lost):
            return
        now = time.time()
        changed = False
        for cs in getattr(canonical, "steps", None) or []:
            sid = LEGACY_STEP_ID_FMT.format(int(getattr(cs, "n", 0) or 0))
            step = next((s for s in envelope.steps if s.id == sid), None)
            status = step_status_from_canonical(str(getattr(cs.status, "value", cs.status) or "pending"))
            if step is None:
                step = PlanStep(
                    id=sid,
                    goal=str(getattr(cs, "goal", "") or "")[:300],
                    tool=str(getattr(cs, "tool", "") or ""),
                    status=status,  # type: ignore[arg-type]
                    host="chatengine",
                    created_at=now,
                    updated_at=now,
                )
                push_bounded(envelope.steps, step, MAX_STEPS)
                changed = True
                continue
            if step.status != status:
                if status == "succeeded" and step.status != "succeeded":
                    step.attach_evidence(
                        StepEvidence(tool=str(getattr(cs, "tool", "") or ""), at=now)
                    )
                step.status = status  # type: ignore[assignment]
                step.updated_at = now
                changed = True
        if changed:
            _journal(envelope, "legacy_projected", note="canonical flush mirrored")
            if lock is not None and not lock.lost:
                await save_session_plan(envelope)


async def begin_turn(session_id: str, turn_id: str, *, message: str = "") -> None:
    """Legacy turn start — same kernel journal the Pi path uses."""
    try:
        from app.services.harness_kernel.runtime import get_runtime

        await get_runtime(session_id).begin_turn(turn_id, host="chatengine", message=message)
    except Exception:  # noqa: BLE001 — 投影绝不破坏 legacy 回合
        logger.warning("[LegacyAdapter] begin_turn failed session=%s", session_id, exc_info=True)


def status_from_outcome(rt_ev: Any) -> str:
    """TurnEvidence outcome → kernel TurnStatus (unsettled → interrupted)."""
    try:
        outcome = rt_ev.outcome.outcome
    except Exception:  # noqa: BLE001
        return "interrupted"
    name = str(getattr(outcome, "name", "") or "")
    return {
        "SUCCEEDED": "completed",
        "CANCELLED": "cancelled",
        "FAILED": "failed",
    }.get(name, "interrupted")


async def end_turn(session_id: str, turn_id: str, *, status: str) -> None:
    """Legacy turn settle — checkpoint like the Pi path."""
    try:
        from app.services.harness_kernel.runtime import get_runtime

        await get_runtime(session_id).end_turn(
            turn_id, host="chatengine", status=status,  # type: ignore[arg-type]
        )
    except Exception:  # noqa: BLE001
        logger.warning("[LegacyAdapter] end_turn failed session=%s", session_id, exc_info=True)
