"""Governor link for the LLM-context resource dimension (F04).

Wires context assembly into ADR-0182's governor on the already-contracted
``Subsystem.LLM_CONTEXT`` × ``Dimension.CONTEXT_TOKENS`` axis — the piece
that was missing between the contract (rg.v1) and any caller:

- **plan** — before the turn ships, the allocator's planned estimate
  becomes a ``ResourceDemand`` (adjudged ``CONTEXT_TOKENS``). Default is
  observe-first: even a REJECT never blocks the turn (context is
  non-deferrable), it is recorded in the receipt.
- **settle** — after render, the actual prompt estimate settles the
  reservation via ``governor.complete`` (estimate-vs-actual observation)
  and lands in the session ledger via ``record_context_tokens`` — the
  same actuals call the legacy assembler's R12 hook makes (one actuals
  truth).

Planned and actual use the *same* CJK-aware estimator, so the reconcile
ratio is a real estimator/allocator error, not a semantics mismatch.
Governor absence must never break a turn — every failure mode degrades to
a receipt note (fail-open with reasons, never silent).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.services.chat.context.history_compression import _estimate_tokens
from app.services.context_assembly.flags import governor_link_enabled

logger = logging.getLogger(__name__)

#: receipt keys
GK_DECISION = "decision"
GK_RECONCILE = "reconcile"
GK_SKIPPED = "skipped_reason"


@dataclass
class ContextGovernorPlan:
    """Handle carried from plan to settle (opaque to the assembly)."""

    session_id: str = ""
    turn_id: str = ""
    planned_tokens: int = 0
    decision: str = ""
    reservation: Optional[Any] = None
    ticket: Optional[Any] = None
    skipped_reason: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_receipt_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            GK_DECISION: self.decision or ("skipped" if self.skipped_reason else ""),
            GK_SKIPPED: self.skipped_reason,
        }
        reconcile = self.extra.get(GK_RECONCILE)
        if reconcile is not None:
            out[GK_RECONCILE] = reconcile
        for key, value in self.extra.items():
            if key != GK_RECONCILE:
                out[key] = value
        return out


def _build_demand(session_id: str, turn_id: str, planned_tokens: int):
    from app.services.governor.contract import (
        Certainty,
        DimValue,
        Dimension,
        ExecutionPriority,
        ResourceDemand,
        ResourceEstimate,
        Subsystem,
    )

    estimate = ResourceEstimate(
        subsystem=Subsystem.LLM_CONTEXT,
        dims={
            Dimension.CONTEXT_TOKENS: DimValue(
                certainty=Certainty.ESTIMATED,
                expected=float(planned_tokens),
                confidence=0.8,
                source="ctx_assembly.allocator",
                reason="deterministic_cjk_aware_estimate",
            ),
        },
        confidence=0.8,
        source="context_assembly",
        reason="planned_context_budget",
    )
    return ResourceDemand(
        session_id=session_id,
        turn_id=turn_id,
        subsystem=Subsystem.LLM_CONTEXT,
        estimate=estimate,
        priority=ExecutionPriority.INTERACTIVE,
    )


async def plan_turn_context(
    *, session_id: str, turn_id: str, planned_tokens: int
) -> ContextGovernorPlan:
    """Admission (observe-first) for the planned context budget."""
    plan = ContextGovernorPlan(
        session_id=session_id, turn_id=turn_id, planned_tokens=planned_tokens
    )
    if not governor_link_enabled():
        plan.skipped_reason = "disabled"
        return plan
    if not session_id or planned_tokens <= 0:
        plan.skipped_reason = "no_tokens"
        return plan
    try:
        governor = _governor()
        demand = _build_demand(session_id, turn_id, planned_tokens)
        decision, reservation, ticket = await governor.admit_and_reserve(demand)
        plan.decision = str(decision.decision.value)
        plan.reservation = reservation
        plan.ticket = ticket
        if plan.decision == "reject":
            # Context is non-deferrable: record, never block the turn.
            plan.extra["reject_reasons"] = [str(r) for r in decision.reasons][:4]
    except Exception as exc:  # noqa: BLE001 — governor 缺席绝不阻断 turn
        logger.debug("[ctx_governor] plan skipped: %s", type(exc).__name__)
        plan.skipped_reason = f"error:{type(exc).__name__}"
        plan.reservation = None
        plan.ticket = None
    return plan


def _governor():
    from app.services.governor.governor import get_governor

    return get_governor()


async def settle_turn_context(
    plan: Optional[ContextGovernorPlan],
    *,
    session_id: str,
    turn_id: str,
    actual_prompt: str,
) -> Dict[str, Any]:
    """Settle actual usage + ledger recording + estimate/actual reconcile.

    Returns the reconcile dict embedded into the receipt. Never raises.
    """
    actual_tokens = _estimate_tokens(actual_prompt) if actual_prompt else 0
    planned = plan.planned_tokens if plan else 0
    reconcile: Dict[str, Any] = {
        "planned_tokens": planned,
        "actual_tokens": actual_tokens,
        "delta_tokens": actual_tokens - planned,
    }
    if planned > 0:
        reconcile["actual_over_planned"] = round(actual_tokens / planned, 4)
    try:
        governor = _governor()
        if plan is not None and plan.reservation is not None:
            from app.services.governor.contract import (
                Dimension,
                ResourceUsage,
                Subsystem,
            )

            await governor.complete(
                plan.reservation,
                plan.ticket,
                usage=ResourceUsage(
                    session_id=session_id,
                    turn_id=turn_id,
                    subsystem=Subsystem.LLM_CONTEXT,
                    dims={Dimension.CONTEXT_TOKENS: float(actual_tokens)},
                    status="completed",
                ),
            )
        # Session ledger — the same actuals call the legacy assembler's R12
        # hook makes via context_link.record_context_report.
        governor.ledger.record_context_tokens(session_id, turn_id, actual_tokens)
        reconcile["ledger_recorded"] = True
    except Exception as exc:  # noqa: BLE001 — settle 失败只留痕，绝不阻断
        logger.debug("[ctx_governor] settle degraded: %s", type(exc).__name__)
        reconcile["ledger_recorded"] = False
        reconcile["settle_error"] = type(exc).__name__
    if plan is not None:
        plan.extra[GK_RECONCILE] = reconcile
    return reconcile


__all__ = [
    "ContextGovernorPlan",
    "plan_turn_context",
    "settle_turn_context",
]
