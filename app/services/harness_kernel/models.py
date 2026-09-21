"""Harness Kernel contracts — host-neutral session/plan types (ADR-0180/0204).

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
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

#: Envelope schema generation. v1 = pre-kernel envelope (ADR-0076 waves);
#: v2 = kernel extension (this module). Absent field on a stored payload → v1.
#: ADR-0208 additions (turn phase, event seq/id fields) are v2-additive: they
#: load with defaults under the same rule, so the generation does not move.
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
#: ADR-0208 terminal distinctions: ``refused`` = the turn ended before any
#: execution (agent declined / clarification required — nothing was at
#: stake); ``aborted`` = policy/system-initiated stop (governor budget,
#: fencing) as opposed to ``failed`` (a tool/execution error, retryable) and
#: ``cancelled`` (user-initiated stop after execution started).
TurnStatus = Literal[
    "running", "completed", "cancelled", "failed", "interrupted",
    "refused", "aborted",
]


# ── K1: canonical turn phase vocabulary (ADR-0208) ──────────────────────────
#
# One lifecycle vocabulary for a single turn on ANY host. The phase is OWNED
# by the kernel (unlike the V7 ``RuntimePhase``, which is a derived
# task-level projection over chapter facts): kernel lifecycle methods drive
# it at their existing seams, ``end_turn`` is the ONLY terminalizer
# (mirrors ADR-0100 "agent_settled is the sole turn finalizer"), and
# out-of-table transitions are refused + journalled (fail-closed, observable,
# never fatal).

#: Terminal phases — names align 1:1 with the terminal ``TurnStatus`` values
#: so a settled turn carries ONE terminal truth, not two.
TERMINAL_PHASES: Tuple[str, ...] = (
    "completed", "failed", "cancelled", "aborted", "refused", "interrupted",
)

TurnPhase = Literal[
    "created", "understanding", "planning", "qualifying", "executing",
    "observing", "verifying", "repairing", "replanning",
    "completed", "failed", "cancelled", "aborted", "refused", "interrupted",
]

#: Closed trigger vocabulary (what drove the transition).
PhaseTrigger = str  # advisory; values in _PHASE_TRANSITIONS keys

#: (from_phase, trigger) → to_phase. Both terminals and the replan/repair
#: loops are explicit; anything outside the table is refused (journalled,
#: counted, never raised) — the phase is authoritative, so silent drift is
#: worse than a refused advance.
_PHASE_TRANSITIONS: Dict[Tuple[str, str], str] = {
    # 主线：turn 打开 → 理解 → 规划 → 资格判定 → 执行 → 观察 → 终验
    ("created", "user_message"): "understanding",
    ("understanding", "plan_compiled"): "planning",
    ("planning", "plan_compiled"): "planning",
    ("understanding", "qualification_started"): "qualifying",
    ("planning", "qualification_started"): "qualifying",
    # 重复派发时 turn 已在执行/观察相位：资格自环（审计仍记录历史行）
    ("executing", "qualification_started"): "executing",
    ("observing", "qualification_started"): "observing",
    ("qualifying", "dispatch_started"): "executing",
    ("executing", "dispatch_started"): "executing",
    ("executing", "evidence_attached"): "observing",
    ("observing", "dispatch_started"): "executing",
    ("observing", "evidence_attached"): "observing",
    # 回路：步级失败不换 phase（turn 继续，步记账承担）；回路边显式列出
    ("executing", "tool_failed"): "executing",
    ("observing", "tool_failed"): "observing",
    # 修复 / 重规划（replan_pending 的 turn 侧投影：等待/执行重规划的
    # turn 处于 replanning；任务级 debt 旗标仍归 plan_runtime 所有）
    ("executing", "repair_requested"): "repairing",
    ("observing", "repair_requested"): "repairing",
    ("verifying", "repair_requested"): "repairing",
    ("repairing", "repair_requested"): "repairing",
    ("repairing", "repair_applied"): "executing",
    ("executing", "replan_requested"): "replanning",
    ("observing", "replan_requested"): "replanning",
    ("verifying", "replan_requested"): "replanning",
    ("repairing", "replan_requested"): "replanning",
    ("replanning", "plan_compiled"): "planning",
    ("repairing", "plan_compiled"): "planning",
    # 执行中重意图（用户 mid-turn 收窄/扩展）：重新规划是合法主线回退
    ("executing", "plan_compiled"): "planning",
    ("observing", "plan_compiled"): "planning",
    # 收尾：任一运行态 phase 可进终验；end_turn 是唯一终态写者
    ("created", "settle_started"): "verifying",
    ("understanding", "settle_started"): "verifying",
    ("planning", "settle_started"): "verifying",
    ("qualifying", "settle_started"): "verifying",
    ("executing", "settle_started"): "verifying",
    ("observing", "settle_started"): "verifying",
    ("repairing", "settle_started"): "verifying",
    ("replanning", "settle_started"): "verifying",
    # 终态（只从 verifying 出；status→trigger 映射见 runtime）
    ("verifying", "verdict_ready"): "completed",
    ("verifying", "settle_failed"): "failed",
    ("verifying", "user_cancel"): "cancelled",
    ("verifying", "policy_abort"): "aborted",
    ("verifying", "refused"): "refused",
    ("verifying", "host_interrupted"): "interrupted",
}

#: Public read-only view (tests + adapters iterate this).
PHASE_TRANSITIONS: Dict[Tuple[str, str], str] = dict(_PHASE_TRANSITIONS)

#: end_turn status → terminal trigger (single mapping; no second table).
STATUS_TO_TRIGGER: Dict[str, str] = {
    "completed": "verdict_ready",
    "failed": "settle_failed",
    "cancelled": "user_cancel",
    "aborted": "policy_abort",
    "refused": "refused",
    "interrupted": "host_interrupted",
}

#: Settle transition (running phase → verifying) — used by end_turn before
#: the terminal edge; from-phase is whatever the turn is in.
SETTLE_TRIGGER = "settle_started"

#: Dispatch transition used by begin_step (qualification is implied by the
#: dispatch gate: eligibility was just resolved at the bind seam).
DISPATCH_TRIGGER = "dispatch_started"
QUALIFY_TRIGGER = "qualification_started"
EVIDENCE_TRIGGER = "evidence_attached"
PLAN_TRIGGER = "plan_compiled"
USER_MESSAGE_TRIGGER = "user_message"


def next_phase(from_phase: str, trigger: str) -> Optional[str]:
    """Table lookup; ``None`` when the (from, trigger) pair is outside the
    legal table (caller journals + counts the refusal — never raises)."""
    return _PHASE_TRANSITIONS.get((str(from_phase), str(trigger)))


class PhaseTransition(BaseModel):
    """One canonical phase advance (bounded ring on the turn record)."""

    to_phase: str
    trigger: str = ""
    from_phase: str = ""
    at: float = 0.0
    #: ``ok`` = table-legal; otherwise the refusal reason code (the phase
    #: itself never changes on a refusal — the row is the audit trail).
    reason_code: str = "ok"

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "to": self.to_phase[:24],
            "trigger": self.trigger[:32],
            "from": self.from_phase[:24],
            "at": self.at,
            "code": self.reason_code[:32],
        }


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
    """One host turn's identity + outcome, FIFO-bounded on the envelope.

    ADR-0208: ``phase`` is the canonical lifecycle position; ``phase_history``
    is a bounded ring of advances (table-legal or refused-with-reason). Rows
    persisted before ADR-0208 load with ``phase="created"`` — for those rows
    ``status`` stays the authoritative terminal truth.
    """

    turn_id: str
    host: PlanHost = "unknown"
    started_at: float = 0.0
    ended_at: float = 0.0
    status: TurnStatus = "running"
    tool_calls: int = 0
    phase: TurnPhase = "created"
    phase_history: List[PhaseTransition] = Field(default_factory=list)

    def record_phase(self, transition: PhaseTransition, *, keep: int = 0) -> None:
        limit = keep or MAX_PHASE_HISTORY
        self.phase = transition.to_phase  # type: ignore[assignment]
        push_bounded(self.phase_history, transition, limit)


class PlanDecision(BaseModel):
    """Bounded decision/evidence journal row (K0/K4 contract).

    ADR-0208 versions the journal into the canonical event record: ``seq``
    is envelope-monotonic, ``event_id`` is the idempotency key (deterministic
    for causal events — ``kind:turn_id:causal_id`` — so a lock-retry or
    duplicate callback appends nothing), and ``causal_id`` carries the
    producing identity (tool_call_id / trigger id) for trace+replay. Old rows
    (seq=0, empty ids) stay readable — the vocabulary is advisory, unknown
    kinds pass through.
    """

    kind: str
    at: float = 0.0
    host: PlanHost = "unknown"
    turn_id: str = ""
    note: str = Field(default="", max_length=300)
    detail: Dict[str, Any] = Field(default_factory=dict)
    seq: int = 0
    event_id: str = ""
    causal_id: str = ""


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

#: Canonical event vocabulary (K4) — a closed superset of DECISION_KINDS.
#: New rows use the fine-grained tool_* kinds (``step_marked`` remains only
#: as the legacy row shape); observation/qualification/goal/repair kinds are
#: emitted at the kernel seam that owns the fact, and direction-5 consumers
#: (repair_requested/applied) read them from here — no second event bus.
EVENT_KINDS = DECISION_KINDS + (
    "phase_changed",
    "phase_refused",
    "intent_resolved",
    "tool_started",
    "tool_succeeded",
    "tool_failed",
    "tool_late",
    "qualification_changed",
    "observation_received",
    "goal_evaluated",
    "repair_requested",
    "repair_applied",
)

#: Events reserved for seams that do not exist inside the kernel yet
#: (capability-qualification denial outside begin_step, finalizer verdicts
#: beyond the product milestone, MapSpec mutation facts owned by the store).
#: Listed so producers/consumers agree on names before the first emitter.
RESERVED_EVENT_KINDS = (
    "map_mutated",
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
#: Journal ring stays 24 (hk1 bound): the versioned event rows are denser
#: than the old step_marked rows, but the checkpoint ring preserves fuller
#: snapshots, and raising the bound would raise the on-disk validation cap
#: that ROLLING-DEPLOY old pods enforce (a >32-row envelope fails
#: model_validate on an old pod → envelope silently rebuilt empty). ADR-0208
#: keeps the bound; replay depth trades against fleet-safety.
MAX_DECISIONS = 24
MAX_EVIDENCE_PER_STEP = 4
MAX_CHECKPOINT_SLOTS = 3
MAX_PHASE_HISTORY = 16


def push_bounded(items: List[Any], item: Any, limit: int) -> None:
    """Append ``item`` keeping at most ``limit`` newest entries."""
    items.append(item)
    if len(items) > limit:
        del items[: len(items) - limit]


# ── K2: canonical turn context (bounded projection, refs not payloads) ──────

MAX_CONTEXT_CAPABILITIES = 16
MAX_CONTEXT_REFS = 8


class ContextCapabilityRow(BaseModel):
    """One capability in the turn context projection (status view only —
    the progress row in the envelope stays the single truth)."""

    capability: str
    status: str = "pending"
    ref: str = ""


class HarnessTurnContext(BaseModel):
    """Typed, bounded, host-neutral snapshot of one turn (K2, ADR-0208).

    Built by ``app.services.harness_kernel.context.build_turn_context`` as a
    pure projection over the envelope + caller-injected cross-domain refs
    (mission/governor/knowledge are passed in — the kernel never imports
    those services, keeping models leaf). Everything is bounded and ref-
    carrying: no GeoJSON, no MapSpec payloads, no model output. Downstream
    consumers read THIS instead of re-querying Redis/session/global state.
    """

    context_schema: str = "hk.ctx.v1"
    # identity
    session_id: str = ""
    envelope_id: str = ""
    turn_id: str = ""
    #: False when the requested turn_id has no record on the envelope (the
    #: identity block then echoes the REQUESTED id with host="unknown") —
    #: callers can tell a projection miss from a real turn.
    turn_found: bool = False
    host: PlanHost = "unknown"
    mission_ref: str = ""
    # lifecycle
    phase: str = "created"
    turn_status: str = "running"
    phase_refused: int = 0
    # request / goal
    user_goal: str = Field(default="", max_length=300)
    query: str = Field(default="", max_length=300)
    recipe_id: str = ""
    replaced: bool = False
    superseded: bool = False
    # capability view (names + status + bound ref, ≤16)
    capabilities: List[ContextCapabilityRow] = Field(default_factory=list)
    open_capabilities: List[str] = Field(default_factory=list)
    # execution summary
    steps_total: int = 0
    steps_open: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0
    tool_calls: int = 0
    # observation / evidence summary (refs only, newest first, ≤8)
    evidence_refs: List[str] = Field(default_factory=list)
    last_verdict: str = Field(default="", max_length=64)
    # recovery / completion
    recovery_pending: bool = False
    resumed_from_turn_id: str = ""
    completion: str = ""  # completed/failed/... once settled; "" while running

    def summary_line(self) -> str:
        """One bounded structured line for settle-time observability."""
        caps = len(self.capabilities)
        return (
            f"[HarnessTurnContext] schema={self.context_schema} "
            f"session={self.session_id} turn={self.turn_id} host={self.host} "
            f"phase={self.phase} status={self.turn_status} "
            f"steps={self.steps_total}/open={self.steps_open} "
            f"ok={self.steps_succeeded} fail={self.steps_failed} "
            f"tool_calls={self.tool_calls} caps={caps} "
            f"open_caps={','.join(self.open_capabilities[:6]) or 'none'} "
            f"refs={len(self.evidence_refs)} "
            f"recipe={self.recipe_id or 'none'} "
            f"recovery={'pending' if self.recovery_pending else 'none'}"
        )
