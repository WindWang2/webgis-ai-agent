"""Canonical phase ↔ V7 RuntimePhase projection adapter (ADR-0208 K1).

The kernel turn phase is the OWNED turn-level lifecycle; V7's
``RuntimePhase`` (``app/services/gis_harness/runtime_state_machine``) stays a
DERIVED task-level projection over chapter facts (its own ADR-0134 D1
discipline — not migrated here, by design). This module is the string-level
mapping that lets observability/tests speak both dialects WITHOUT the kernel
importing gis_harness (leaf-direction rule preserved).

Parity is approximate by nature: task-level ≠ turn-level. The mapping is
total (every canonical phase has a V7 rendering) and V7-legal by
construction — the parity test walks V7's own LEGAL_TRANSITIONS.
"""
from __future__ import annotations

from typing import Dict, Optional

from app.services.harness_kernel.models import TERMINAL_PHASES

#: Terminal canonical phases as a set (parity checks iterate this).
TERMINAL_SET = frozenset(TERMINAL_PHASES)

#: canonical turn phase → V7 task-phase rendering.
CANONICAL_TO_RUNTIME_PHASE: Dict[str, str] = {
    "created": "idle",
    "understanding": "intent_resolved",
    "planning": "plan_ready",
    "qualifying": "plan_ready",
    "executing": "executing",
    "observing": "observing",
    "verifying": "critiquing",
    "repairing": "repairing",
    "replanning": "replanning",
    # terminals
    "completed": "committed",
    "failed": "aborted",
    "cancelled": "aborted",
    "aborted": "aborted",
    "refused": "aborted",
    "interrupted": "aborted",
}

#: reverse view (V7 phase → the canonical phases that project onto it) —
#: advisory only; the canonical side is the owned truth.
RUNTIME_TO_CANONICAL: Dict[str, tuple] = {}
for _c, _v in CANONICAL_TO_RUNTIME_PHASE.items():
    RUNTIME_TO_CANONICAL.setdefault(_v, []).append(_c)


def project_runtime_phase(canonical_phase: str) -> str:
    """Canonical turn phase → V7 RuntimePhase value (unknown → idle)."""
    return CANONICAL_TO_RUNTIME_PHASE.get(str(canonical_phase or ""), "idle")


# ── F03: same-domain vocabulary renderings (ADR-0204-f03 D5) ────────────────
#
# StageState (workflow_instance) and GoalNodeStatus (goal_graph) stay DERIVED
# projections owned by their modules; these renderings are the single
# string-level answer to "how would the canonical terminal truth show up in
# that dialect" — used by the settle-time parity line, never as a second
# write path. Terminal statuses render into each vocabulary's collapse
# semantics; running statuses render into their in-flight values.

#: canonical TurnStatus → StageState-family rendering (total).
STATUS_TO_STAGE_VIEW: Dict[str, str] = {
    "running": "active",
    "completed": "satisfied",
    "failed": "failed",
    "cancelled": "skipped",
    "aborted": "blocked",
    "refused": "blocked",
    "interrupted": "stale",
}

#: canonical TurnStatus → GoalNodeStatus-family rendering (total).
STATUS_TO_GOAL_VIEW: Dict[str, str] = {
    "running": "ready",
    "completed": "satisfied",
    "failed": "failed",
    "cancelled": "skipped",
    "aborted": "blocked",
    "refused": "skipped",
    "interrupted": "stale",
}


def render_stage_view(canonical_status: str) -> str:
    """Canonical TurnStatus → StageState-family value (unknown → pending)."""
    return STATUS_TO_STAGE_VIEW.get(str(canonical_status or ""), "pending")


def render_goal_view(canonical_status: str) -> str:
    """Canonical TurnStatus → GoalNodeStatus-family value (unknown → pending)."""
    return STATUS_TO_GOAL_VIEW.get(str(canonical_status or ""), "pending")


def terminal_parity(canonical_phase: str, v7_phase: str) -> Optional[str]:
    """Settle-time parity verdict (F03): None = consistent, else reason.

    A settled canonical turn must render into the V7 phase the task-level
    projection would show for that outcome: ``completed`` → ``committed``,
    every other terminal → ``aborted``. While the canonical turn is still
    running no verdict is claimed (task-level ≠ turn-level mid-flight).
    Unknown/empty V7 phases (feature off / no chapter) are not parity
    failures — they are reported as ``v7_absent`` for observability only.
    """
    phase = str(canonical_phase or "")
    observed = str(v7_phase or "")
    if phase not in TERMINAL_SET:
        return None
    if not observed:
        return "v7_absent"
    expected = CANONICAL_TO_RUNTIME_PHASE.get(phase, "aborted")
    if observed == expected:
        return None
    if observed == "aborted" and expected == "committed":
        # task-level collapse wins: later turns may already have re-opened
        # the task while THIS turn's record says completed — report, don't
        # treat as drift (the canonical side stays authoritative).
        return "task_level_advanced"
    return f"expected_{expected}_got_{observed}"
