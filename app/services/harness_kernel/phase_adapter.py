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

from typing import Dict

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
