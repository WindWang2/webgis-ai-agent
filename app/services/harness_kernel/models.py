"""Harness Kernel contracts — host-neutral session/plan types (ADR-0180).

Leaf module: no imports from ``app.services`` (only stdlib/pydantic) so both
``app.services.session_plan`` (store layer) and
``app.services.harness_kernel.runtime`` (lifecycle layer) can depend on it
without cycles.

These types are the *additive* extension of the SessionPlan envelope
(ADR-0076): turn identity, host-neutral steps with evidence, a bounded
decision journal, and recovery metadata. Every field carries a default so an
old persisted envelope deserializes with zero drift; ``SCHEMA_VERSION``
guards the upgrade rule (v1 envelopes load as v2 with the new fields
defaulted — no migration step, no rewrite-on-read).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

#: Envelope schema generation. v1 = pre-kernel envelope (ADR-0076 waves);
#: v2 = kernel extension (this module). Absent field on a stored payload → v1.
SCHEMA_VERSION = 2

#: Host-neutral step status. ``invalidated`` is the K5 patch semantics
#: (a follow-up scope/subject change voids the step's evidence but keeps the
#: record for direction-5 subgraph replay); ``skipped`` covers user-cancel and
#: plan-finalize with the step never attempted.
StepStatus = Literal[
    "pending", "running", "succeeded", "failed", "skipped", "invalidated"
]

#: Host label on steps/turns/decisions — parity observability (K8), never a
#: behavioral switch inside the kernel.
PlanHost = Literal["pi", "chatengine", "unknown"]

#: Turn outcome at settle time. ``interrupted`` = the turn never settled
#: (server restart / process death discovered on the next hydrate).
TurnStatus = Literal[
    "running", "completed", "cancelled", "failed", "interrupted"
]


def step_status_from_canonical(raw: str) -> StepStatus:
    """CanonicalPlan StepStatus → kernel StepStatus (legacy adapter)."""
    return {
        "pending": "pending",
        "running": "running",
        "completed": "succeeded",
        "failed": "failed",
        "skipped": "skipped",
        "cancelled": "skipped",
    }.get(str(raw or "").strip(), "pending")


class StepEvidence(BaseModel):
    """One bounded evidence entry attached to a step (K0 contract).

    ``ref`` is a session_data artifact ref (``ref:...``) — the plan never
    inlines MapSpec/GeoJSON payloads (K2 rule); callers resolve via the
    existing ref inventory.
    """

    tool: str = ""
    tool_call_id: str = ""
    ref: str = ""
    summary: str = Field(default="", max_length=300)
    error: str = Field(default="", max_length=300)
    at: float = 0.0


class PlanStep(BaseModel):
    """Host-neutral plan step (single layer; deps are semantic annotations).

    ``capability`` links the step to the envelope's CapabilityProgress rows so
    the existing capability projection/SSE stays the single progress truth —
    the kernel never forks a second progress model (D-003).
    """

    id: str = Field(min_length=1, max_length=64)
    goal: str = Field(default="", max_length=300)
    capability: str = ""
    tool: str = ""
    tool_binding: List[str] = Field(default_factory=list)
    status: StepStatus = "pending"
    depends_on: List[str] = Field(default_factory=list)
    evidence: List[StepEvidence] = Field(default_factory=list)
    attempts: int = 0
    host: PlanHost = "unknown"
    turn_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def latest_evidence(self) -> Optional[StepEvidence]:
        return self.evidence[-1] if self.evidence else None

    def attach_evidence(self, entry: StepEvidence, *, keep: int = 4) -> None:
        """Append evidence FIFO-bounded (oldest dropped past ``keep``)."""
        self.evidence.append(entry)
        if len(self.evidence) > keep:
            del self.evidence[: len(self.evidence) - keep]
        self.updated_at = time.time()


class PlanTurnRecord(BaseModel):
    """One host turn's identity + outcome, FIFO-bounded on the envelope."""

    turn_id: str
    host: PlanHost = "unknown"
    started_at: float = 0.0
    ended_at: float = 0.0
    status: TurnStatus = "running"
    tool_calls: int = 0


class PlanDecision(BaseModel):
    """Bounded decision/evidence journal row (K0 contract).

    ``kind`` uses a small closed vocabulary so the journal is queryable; free
    context goes in ``note`` (bounded) / ``detail`` (small dict).
    """

    kind: str
    at: float = 0.0
    host: PlanHost = "unknown"
    turn_id: str = ""
    note: str = Field(default="", max_length=300)
    detail: Dict[str, Any] = Field(default_factory=dict)


DECISION_KINDS = (
    "plan_created",
    "plan_replaced",
    "plan_superseded",
    "plan_patched",
    "step_marked",
    "turn_started",
    "turn_ended",
    "turn_resumed",
    "duplicate_turn",
    "checkpoint",
    "stale_write_refused",
    "legacy_projected",
)


class PlanRecoveryMetadata(BaseModel):
    """Checkpoint/resume facts (K7). Persisted on the envelope, not the store."""

    last_turn_id: str = ""
    last_host: PlanHost = "unknown"
    last_turn_status: TurnStatus = "running"
    interrupted_at: float = 0.0
    #: Set when ``begin_turn`` found an older still-running turn (restart/
    #: crash resume). Cleared on the next cleanly-completed turn — this is
    #: what keeps the resume hint visible DURING the resume turn itself
    #: (``last_turn_status`` flips to "running" the moment it begins).
    resumed_from_turn_id: str = ""
    checkpoint_id: str = ""
    checkpoint_at: float = 0.0
    resume_count: int = 0


class PlanPatchKind:
    """K5 follow-up patch vocabulary (direction-5 stable protocol)."""

    SCOPE_CHANGE = "scope_change"
    SUBJECT_CHANGE = "subject_change"
    OUTPUT_REQUIREMENT = "output_requirement"
    CANCEL_SUBGOAL = "cancel_subgoal"
    ADD_COMPARISON = "add_comparison"


class PlanPatch(BaseModel):
    """One follow-up-driven plan patch request (K5).

    Direction 5 consumes ``PatchResult.invalidated_step_ids`` to schedule the
    affected-subgraph re-execution; this direction only marks and journals.
    """

    kind: str
    turn_id: str = ""
    host: PlanHost = "unknown"
    note: str = Field(default="", max_length=300)
    target_step_ids: List[str] = Field(default_factory=list)
    detail: Dict[str, Any] = Field(default_factory=dict)


class PatchResult(BaseModel):
    invalidated_step_ids: List[str] = Field(default_factory=list)
    revision: int = 0
    applied: bool = False
    #: session_plan_step payloads for the invalidated steps — callers with a
    #: live stream can flush them; programmatic patchers may drop them.
    events: List[Dict[str, Any]] = Field(default_factory=list)


# ── bounds (envelope stays KB-scale; refs never inline payloads) ─────────────
MAX_TURNS = 8
MAX_STEPS = 48
MAX_DECISIONS = 24
MAX_EVIDENCE_PER_STEP = 4
MAX_CHECKPOINT_SLOTS = 3


def push_bounded(items: List[Any], item: Any, limit: int) -> None:
    """Append ``item`` keeping at most ``limit`` newest entries."""
    items.append(item)
    if len(items) > limit:
        del items[: len(items) - limit]
