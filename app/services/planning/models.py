"""Canonical planning model — the source of truth for plan state.

This is the foundation of the planning-architecture convergence (design-v3
§"New package app/services/planning/"): CanonicalPlan is the persisted source
of truth via ``app.services.planning.store``, while the orchestrator ``Plan``
dataclass and plan_mode ``PlanProposal`` become compatibility projections of it
in a later integration slice.

Pydantic v2 models (repo pins ``pydantic>=2.13.4``) — serialize / deserialize
with ``.model_dump()`` / ``.model_validate()`` so plans round-trip through the
session_data JSON store.
"""
import time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PlanStatus(str, Enum):
    """Lifecycle status of a plan (state machine in design-v3 §models).

    ``proposed → validated → running → (partially_completed | completed | failed)``
    plus ``cancelled`` and ``superseded``. ``partially_completed`` is NOT a
    terminal state on purpose: ``execute_plan`` resumes from it (completed
    steps' result refs are reused, only remaining steps re-plan/execute).
    """

    proposed = "proposed"
    validated = "validated"
    running = "running"
    partially_completed = "partially_completed"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    superseded = "superseded"

    def is_terminal(self) -> bool:
        """True for states nothing can transition out of."""
        return self in TERMINAL_STATUSES


# Terminal plan states: completed / failed / cancelled / superseded.
TERMINAL_STATUSES: frozenset[PlanStatus] = frozenset(
    {
        PlanStatus.completed,
        PlanStatus.failed,
        PlanStatus.cancelled,
        PlanStatus.superseded,
    }
)


class StepStatus(str, Enum):
    """Per-step execution status (canonical replacement for plan_mode's
    per-plan ``__status__`` + orchestrator's boolean ``done`` flag)."""

    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"


class FailureClass(str, Enum):
    """Classified tool-failure taxonomy (design-v3 §recovery).

    Mirrors the dispatch codes that already exist in the repo
    (VALIDATION_ERROR / NOT_FOUND / UNKNOWN_TOOL / TOOL_ERROR in
    ``app/tools/registry.py``) plus message-level signals — each class maps to
    a deterministic recovery policy (see ``app.services.planning.recovery``).
    """

    validation = "validation"
    missing_ref = "missing_ref"
    empty_result = "empty_result"
    no_data = "no_data"
    auth = "auth"
    tool_unavailable = "tool_unavailable"
    resource_limit = "resource_limit"
    transient_network = "transient_network"
    cancelled = "cancelled"
    internal = "internal"


class RecoveryAction(str, Enum):
    """Structured recovery hint for the LLM / engine (no blind retry anywhere).

    ``retry_transient`` is reserved for transient-network failures only; a
    failed tool call is never automatically replayed for other classes.
    """

    correct_args = "correct_args"
    reuse_ref = "reuse_ref"
    alternate_tool = "alternate_tool"
    replan_remaining = "replan_remaining"
    retry_transient = "retry_transient"
    stop = "stop"


class StepError(BaseModel):
    """Error payload attached to a failed step."""

    failure_class: FailureClass
    message: str
    recovery_action: Optional[RecoveryAction] = None
    tool_call_id: Optional[str] = None


class CanonicalStep(BaseModel):
    """One step of a canonical plan.

    ``tool_family`` is the advisory domain the step belongs to (planner-side);
    ``tool`` is the concrete registered tool name once the step is executable.
    Args may carry ``${stepId}`` / ``${stepId.path.to.field}`` placeholders that
    reference earlier steps' results (same grammar as plan_mode.py).
    """

    id: str = Field(..., min_length=1, max_length=64, description="短步骤 ID（如 s1）")
    n: int = Field(..., description="1-based 步骤序号（与 orchestrator PlanStep.n 对齐）")
    goal: str = Field(..., description="该步的自然语言意图")
    tool_family: Optional[str] = Field(None, description="所属领域（tool_catalog domain）")
    tool: Optional[str] = Field(None, description="具体调用的已注册工具名")
    args: dict = Field(default_factory=dict, description="传给工具的参数（可含 ${} 占位符）")
    depends_on: list[str] = Field(default_factory=list, description="前置步骤 ID 列表")
    status: StepStatus = StepStatus.pending
    result_ref: Optional[str] = Field(None, description="完成时产出的 session_data 引用")
    error: Optional[StepError] = None


class CanonicalPlan(BaseModel):
    """The canonical, persisted plan — source of truth for plan state.

    Persisted per session via ``app.services.planning.store`` (session_data
    backend, deterministic ``plan-current`` key). ``created_at`` / ``updated_at``
    are float epoch timestamps.
    """

    plan_id: str
    session_id: str
    intent: str
    domains: list[str] = Field(default_factory=list)
    steps: list[CanonicalStep] = Field(default_factory=list)
    status: PlanStatus = PlanStatus.proposed
    revision: int = 1
    failure: Optional[StepError] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def step_by_id(self, step_id: str) -> Optional[CanonicalStep]:
        """Return the step with ``step_id``, or None when absent."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def next_pending_steps(self) -> list[CanonicalStep]:
        """Steps ready to run now: pending and every ``depends_on`` completed.

        Steps blocked on a failed / skipped / cancelled dependency are excluded
        (they can never become ready on this revision). Order is plan order.
        """
        ready: list[CanonicalStep] = []
        for step in self.steps:
            if step.status != StepStatus.pending:
                continue
            deps_ok = True
            for dep in step.depends_on:
                dep_step = self.step_by_id(dep)
                if dep_step is None or dep_step.status != StepStatus.completed:
                    deps_ok = False
                    break
            if deps_ok:
                ready.append(step)
        return ready

    def recompute_status(self) -> PlanStatus:
        """Derive the plan status from step statuses (design-v3 state machine).

        Pure: returns the derived status without mutating ``self.status`` so
        callers can inspect before assigning. Rules, in priority order:

        - no steps                  → keep current status
        - all steps completed       → completed
        - any running               → running (in-flight beats derived terminal states)
        - completed mixed with (failed | skipped), none pending/running
                                    → partially_completed (partial success is
                                      resumable — NOT terminal; P3 #5)
        - any failed, none completed/pending/running → failed
        - otherwise                 → keep current status

        ``partially_completed`` intentionally beats plain ``failed`` whenever at
        least one step completed: some-done-some-failed is a *partial success*
        (``execute_plan`` resumes from it, reusing completed refs), while
        ``failed`` is reserved for runs with no completed steps. Matches
        plan_mode's ``_failure_status()`` semantics.
        """
        if not self.steps:
            return self.status
        statuses = [s.status for s in self.steps]
        if all(s == StepStatus.completed for s in statuses):
            return PlanStatus.completed
        has_pending_or_running = any(
            s in (StepStatus.pending, StepStatus.running) for s in statuses
        )
        if any(s == StepStatus.running for s in statuses):
            return PlanStatus.running
        has_completed = any(s == StepStatus.completed for s in statuses)
        has_failed_or_skipped = any(
            s in (StepStatus.failed, StepStatus.skipped) for s in statuses
        )
        if has_completed and has_failed_or_skipped and not has_pending_or_running:
            return PlanStatus.partially_completed
        if any(s == StepStatus.failed for s in statuses) and not has_pending_or_running:
            return PlanStatus.failed
        return self.status

    def bump_revision(self) -> int:
        """Increment ``revision`` (and refresh ``updated_at``); returns the new value."""
        self.revision += 1
        self.updated_at = time.time()
        return self.revision
