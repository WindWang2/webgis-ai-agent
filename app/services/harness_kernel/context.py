"""Canonical turn context builder (K2, ADR-0204).

A pure projection over the SessionPlan envelope + caller-injected cross-domain
refs: typed, bounded, ref-carrying (no GeoJSON / MapSpec / model payloads).
Downstream consumers read ``HarnessTurnContext`` instead of re-querying
Redis / session state / global singletons — the envelope stays the single
truth, this module never writes.

The builder imports ``models`` only (leaf discipline). Cross-domain facts
(mission ref, governor hints, knowledge refs) arrive as arguments because the
kernel must not import those services.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.harness_kernel.models import (
    MAX_CONTEXT_CAPABILITIES,
    MAX_CONTEXT_REFS,
    ContextCapabilityRow,
    HarnessTurnContext,
    PlanStep,
)

_STEP_OPEN = ("pending", "running")


def _capability_rows(plan: Any) -> List[ContextCapabilityRow]:
    """Capability status view (envelope progress rows are the truth)."""
    rows: List[ContextCapabilityRow] = []
    for row in getattr(plan, "progress", None) or []:
        cap = str(getattr(row, "capability", "") or "").strip()
        if not cap:
            continue
        rows.append(
            ContextCapabilityRow(
                capability=cap[:64],
                status=str(getattr(row, "status", "") or "pending"),
                ref=str(getattr(row, "bound_ref", "") or "")[:120],
            )
        )
    return rows[:MAX_CONTEXT_CAPABILITIES]


def _evidence_refs(steps: List[PlanStep]) -> List[str]:
    """Newest-first artifact refs from step evidence (deduped, bounded)."""
    refs: List[str] = []
    for step in reversed(steps):
        latest = step.latest_evidence()
        ref = str(latest.ref if latest else "") or ""
        if ref and ref not in refs:
            refs.append(ref)
        if len(refs) >= MAX_CONTEXT_REFS:
            break
    return refs


def _last_verdict(plan: Any) -> str:
    """The finalizer verdict if the chapter carries one (read-only)."""
    chapter = getattr(plan, "gis_chapter", None)
    if not isinstance(chapter, dict):
        return ""
    product = chapter.get("map_product")
    if isinstance(product, dict):
        verdict = str(product.get("verdict") or product.get("status") or "")
        if verdict:
            return verdict[:64]
    return ""


def build_turn_context(
    plan: Any,
    turn_id: str,
    *,
    mission_ref: str = "",
    governor_hints: Optional[Dict[str, Any]] = None,
    knowledge_refs: Optional[List[str]] = None,
) -> HarnessTurnContext:
    """Project one turn's canonical context (bounded; pure; never raises on
    well-formed envelopes — callers still wrap best-effort per repo policy).
    """
    turns = getattr(plan, "turns", None) or []
    record = next(
        (t for t in reversed(turns) if getattr(t, "turn_id", "") == turn_id),
        None,
    )
    turn_found = record is not None
    if record is None:
        # Unknown turn: echo the REQUESTED identity honestly (no silent
        # substitution) and project envelope-level facts against the newest
        # record for the summary blocks.
        record = turns[-1] if turns else None
    steps: List[PlanStep] = [
        s for s in (getattr(plan, "steps", None) or []) if isinstance(s, PlanStep)
    ]
    chapter = getattr(plan, "gis_chapter", None)
    chapter = chapter if isinstance(chapter, dict) else {}
    recovery = getattr(plan, "recovery", None)
    phase = str(getattr(record, "phase", "created") or "created") if record else ""
    status = str(getattr(record, "status", "running")) if record else "running"
    open_caps = [
        row.capability
        for row in (_capability_rows(plan))
        if row.status in ("pending", "voided", "failed")
    ]
    refused = sum(
        1
        for h in (getattr(record, "phase_history", None) or [])
        if str(getattr(h, "reason_code", "ok")) != "ok"
    ) if record else 0
    completion = status if status != "running" else ""
    return HarnessTurnContext(
        session_id=str(getattr(plan, "session_id", "") or ""),
        envelope_id=str(getattr(plan, "envelope_id", "") or ""),
        turn_id=str(turn_id or getattr(record, "turn_id", "") or ""),
        turn_found=turn_found,
        host=str(getattr(record, "host", "unknown") or "unknown") if record else "unknown",
        mission_ref=str(mission_ref or "")[:120],
        phase=phase,
        turn_status=status,
        phase_refused=refused,
        user_goal=str(getattr(plan, "user_goal", "") or "")[:300],
        query=str(chapter.get("query") or "")[:300],
        recipe_id=str(chapter.get("recipe_id") or ""),
        replaced=bool(getattr(plan, "replaced", False)),
        superseded=bool(getattr(plan, "superseded", False)),
        capabilities=_capability_rows(plan),
        open_capabilities=[c[:64] for c in open_caps[:MAX_CONTEXT_CAPABILITIES]],
        steps_total=len(steps),
        steps_open=sum(1 for s in steps if s.status in _STEP_OPEN),
        steps_succeeded=sum(1 for s in steps if s.status == "succeeded"),
        steps_failed=sum(1 for s in steps if s.status == "failed"),
        tool_calls=int(getattr(record, "tool_calls", 0) or 0) if record else 0,
        evidence_refs=_evidence_refs(steps),
        last_verdict=_last_verdict(plan),
        recovery_pending=bool(
            str(getattr(recovery, "resumed_from_turn_id", "") or "")
        ),
        resumed_from_turn_id=str(
            getattr(recovery, "resumed_from_turn_id", "") or ""
        )[:64],
        completion=completion,
    )
