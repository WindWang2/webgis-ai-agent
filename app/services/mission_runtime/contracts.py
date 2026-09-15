"""Mission Runtime contracts (ADR-0197) — pure types, no I/O.

Mission is the durable ownership/lifecycle envelope above SessionPlan,
Workflow Runtime, and Specialist Swarm. Vocabulary is closed and versioned.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "mission.v1"

#: Max bounded list sizes (Zero Big Data / projection discipline).
MAX_REFS = 64
MAX_SESSIONS = 16
MAX_CHECKPOINTS_RING = 8
MAX_FRONTIER = 64
MAX_GOAL_CHARS = 2000
MAX_SUMMARY_CHARS = 400
MAX_CHECKPOINT_BYTES = 16 * 1024
MAX_SWARM_TASKS = 48


class MissionState(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_DEPENDENCY = "waiting_dependency"
    PARTIALLY_COMPLETE = "partially_complete"
    SUSPENDED = "suspended"
    RECOVERING = "recovering"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[MissionState] = frozenset({
    MissionState.COMPLETE,
    MissionState.FAILED,
    MissionState.CANCELLED,
})

ACTIVE_STATES: frozenset[MissionState] = frozenset({
    MissionState.CREATED,
    MissionState.PLANNING,
    MissionState.RUNNING,
    MissionState.WAITING_DEPENDENCY,
    MissionState.PARTIALLY_COMPLETE,
    MissionState.SUSPENDED,
    MissionState.RECOVERING,
})

#: Whitelist transitions (key = from → allowed to).
_TRANSITION_MAP: Dict[MissionState, frozenset[MissionState]] = {
    MissionState.CREATED: frozenset({
        MissionState.PLANNING, MissionState.RUNNING, MissionState.CANCELLED,
        MissionState.RECOVERING,
    }),
    MissionState.PLANNING: frozenset({
        MissionState.RUNNING, MissionState.SUSPENDED, MissionState.FAILED,
        MissionState.CANCELLED, MissionState.RECOVERING,
    }),
    MissionState.RUNNING: frozenset({
        MissionState.WAITING_DEPENDENCY, MissionState.PARTIALLY_COMPLETE,
        MissionState.SUSPENDED, MissionState.RECOVERING, MissionState.COMPLETE,
        MissionState.FAILED, MissionState.CANCELLED,
    }),
    MissionState.WAITING_DEPENDENCY: frozenset({
        MissionState.RUNNING, MissionState.SUSPENDED, MissionState.RECOVERING,
        MissionState.FAILED, MissionState.CANCELLED,
    }),
    MissionState.PARTIALLY_COMPLETE: frozenset({
        MissionState.RUNNING, MissionState.SUSPENDED, MissionState.COMPLETE,
        MissionState.FAILED, MissionState.CANCELLED, MissionState.RECOVERING,
    }),
    MissionState.SUSPENDED: frozenset({
        MissionState.RUNNING, MissionState.RECOVERING, MissionState.CANCELLED,
        MissionState.FAILED,
    }),
    MissionState.RECOVERING: frozenset({
        MissionState.RUNNING, MissionState.PARTIALLY_COMPLETE,
        MissionState.WAITING_DEPENDENCY, MissionState.SUSPENDED,
        MissionState.COMPLETE, MissionState.FAILED, MissionState.CANCELLED,
    }),
    MissionState.COMPLETE: frozenset(),
    MissionState.FAILED: frozenset(),
    MissionState.CANCELLED: frozenset(),
}

TRANSITIONS: Dict[MissionState, frozenset[MissionState]] = {
    k: frozenset(v) for k, v in _TRANSITION_MAP.items()
}


def transition_allowed(current: MissionState | str, to: MissionState | str) -> bool:
    try:
        cur = MissionState(current)
        dest = MissionState(to)
    except ValueError:
        return False
    return dest in TRANSITIONS.get(cur, frozenset())


def is_terminal(state: MissionState | str) -> bool:
    try:
        return MissionState(state) in TERMINAL_STATES
    except ValueError:
        return False


class OperationClass(str, Enum):
    """Recovery / compensation class for mission-owned work units."""

    PURE = "pure"
    IDEMPOTENT = "idempotent"
    REPEATABLE = "repeatable"
    DESTRUCTIVE_AT_MOST_ONCE = "destructive_at_most_once"
    COMPENSATABLE = "compensatable"


def operation_class_from_side_effect(side_effect: str) -> OperationClass:
    """Map workflow/swarm side_effect vocabulary onto OperationClass."""
    se = (side_effect or "pure").strip().lower()
    if se == "destructive":
        return OperationClass.DESTRUCTIVE_AT_MOST_ONCE
    if se == "derived_external":
        return OperationClass.IDEMPOTENT
    if se in ("compensatable",):
        return OperationClass.COMPENSATABLE
    if se in ("repeatable", "idempotent"):
        return OperationClass(se) if se in OperationClass._value2member_map_ else OperationClass.IDEMPOTENT
    return OperationClass.PURE


class RecoveryClass(str, Enum):
    """Recovery coordinator classification for unfinished work."""

    COMPLETED = "completed"
    RUNNING_OWNER_DEAD = "running_but_owner_dead"
    RETRYABLE_FAILURE = "retryable_failure"
    NON_RETRYABLE_FAILURE = "non_retryable_failure"
    DESTRUCTIVE_UNKNOWN = "destructive_unknown_outcome"
    WAITING_EXTERNAL = "waiting_external_dependency"
    CANCELLED = "cancelled"


class SwarmTaskDurableState(str, Enum):
    """Reuse Workflow NodeState vocabulary for swarm task durability."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    UNRESOLVED = "UNRESOLVED"  # destructive unknown — never blind-rerun


def _clamp_refs(refs: List[str]) -> List[str]:
    out: List[str] = []
    for r in refs or []:
        s = str(r or "").strip()
        if not s:
            continue
        if not s.startswith("ref:") and not s.startswith("artifact:") and ":" not in s:
            # allow bare ids used as opaque tickets (workflow instance ids etc.)
            pass
        if s not in out:
            out.append(s)
        if len(out) >= MAX_REFS:
            break
    return out


class MissionResourceBudget(BaseModel):
    """Durable mission quota + consumption (cumulative; no billing)."""

    quota: Dict[str, float] = Field(default_factory=dict)
    consumed: Dict[str, float] = Field(default_factory=dict)
    reserved: Dict[str, float] = Field(default_factory=dict)
    retry_cost: Dict[str, float] = Field(default_factory=dict)

    def charge(self, dim: str, amount: float, *, retry: bool = False) -> None:
        if amount <= 0:
            return
        key = str(dim)[:64]
        self.consumed[key] = float(self.consumed.get(key, 0.0)) + float(amount)
        if retry:
            self.retry_cost[key] = float(self.retry_cost.get(key, 0.0)) + float(amount)

    def release_reservation(self, dim: str, amount: float) -> None:
        key = str(dim)[:64]
        cur = float(self.reserved.get(key, 0.0))
        nxt = max(0.0, cur - float(amount))
        if nxt == 0.0:
            self.reserved.pop(key, None)
        else:
            self.reserved[key] = nxt

    def reserve(self, dim: str, amount: float) -> None:
        if amount <= 0:
            return
        key = str(dim)[:64]
        self.reserved[key] = float(self.reserved.get(key, 0.0)) + float(amount)

    def exhausted(self) -> List[str]:
        hits: List[str] = []
        for dim, limit in (self.quota or {}).items():
            if float(limit) <= 0:
                continue
            used = float(self.consumed.get(dim, 0.0)) + float(self.reserved.get(dim, 0.0))
            if used >= float(limit):
                hits.append(str(dim))
        return hits


class MissionFailureState(BaseModel):
    error_code: str = ""
    detail: str = ""
    recovery_class: str = ""
    at: float = 0.0


class MissionRecoveryState(BaseModel):
    attempt: int = 0
    last_checkpoint_id: str = ""
    last_checkpoint_at: float = 0.0
    last_recovery_at: float = 0.0
    blocked_reason: str = ""
    unresolved_ops: List[str] = Field(default_factory=list)


class MissionFrontier(BaseModel):
    """Completed vs remaining work tickets (refs / node ids / task ids)."""

    completed: List[str] = Field(default_factory=list)
    running: List[str] = Field(default_factory=list)
    pending: List[str] = Field(default_factory=list)
    failed: List[str] = Field(default_factory=list)
    blocked: List[str] = Field(default_factory=list)

    @field_validator("completed", "running", "pending", "failed", "blocked", mode="before")
    @classmethod
    def _bound(cls, v: Any) -> List[str]:
        if not v:
            return []
        return [str(x)[:96] for x in list(v)[:MAX_FRONTIER]]


class MissionRefs(BaseModel):
    active_session_ids: List[str] = Field(default_factory=list)
    session_plan_refs: List[str] = Field(default_factory=list)
    workflow_instance_refs: List[str] = Field(default_factory=list)
    swarm_run_refs: List[str] = Field(default_factory=list)
    artifact_refs: List[str] = Field(default_factory=list)
    map_product_refs: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)

    def add_session(self, session_id: str) -> None:
        sid = str(session_id or "").strip()
        if sid and sid not in self.active_session_ids:
            self.active_session_ids = (self.active_session_ids + [sid])[:MAX_SESSIONS]

    def add_ref(self, bucket: str, ref: str) -> None:
        attr = getattr(self, bucket, None)
        if not isinstance(attr, list):
            return
        r = str(ref or "").strip()
        if not r or r in attr:
            return
        setattr(self, bucket, (attr + [r])[:MAX_REFS])


class MissionRecord(BaseModel):
    """In-memory / projection form of a durable Mission row."""

    schema_version: str = SCHEMA_VERSION
    mission_id: str
    org_id: str
    user_id: str = ""
    project_id: Optional[str] = None
    owner_scope: str = ""
    root_goal: str = ""
    goal_revision: int = 1
    state: MissionState = MissionState.CREATED
    revision: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0

    refs: MissionRefs = Field(default_factory=MissionRefs)
    frontier: MissionFrontier = Field(default_factory=MissionFrontier)
    resource_budget: MissionResourceBudget = Field(default_factory=MissionResourceBudget)
    failure: MissionFailureState = Field(default_factory=MissionFailureState)
    recovery: MissionRecoveryState = Field(default_factory=MissionRecoveryState)

    lease_owner: str = ""
    lease_epoch: int = 0
    lease_expires_at: float = 0.0

    @field_validator("root_goal", mode="before")
    @classmethod
    def _goal_bound(cls, v: Any) -> str:
        return str(v or "")[:MAX_GOAL_CHARS]


class MissionCheckpoint(BaseModel):
    checkpoint_id: str
    mission_id: str
    mission_revision: int
    goal_revision: int
    state: str
    frontier: MissionFrontier = Field(default_factory=MissionFrontier)
    refs: MissionRefs = Field(default_factory=MissionRefs)
    resource_budget: MissionResourceBudget = Field(default_factory=MissionResourceBudget)
    swarm_run_id: str = ""
    session_plan_ref: str = ""
    workflow_instance_refs: List[str] = Field(default_factory=list)
    note: str = ""
    created_at: float = 0.0

    def to_bounded_dict(self) -> Dict[str, Any]:
        raw = self.model_dump()
        # ensure serialized size stays bounded
        import json
        blob = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        if len(blob.encode("utf-8")) <= MAX_CHECKPOINT_BYTES:
            return raw
        # strip note / truncate lists
        raw["note"] = (raw.get("note") or "")[:80]
        for key in ("workflow_instance_refs",):
            raw[key] = list(raw.get(key) or [])[:8]
        fr = raw.get("frontier") or {}
        for k in ("completed", "running", "pending", "failed", "blocked"):
            fr[k] = list(fr.get(k) or [])[:16]
        raw["frontier"] = fr
        return raw


class SwarmTaskReceipt(BaseModel):
    task_id: str
    assignment_id: str = ""
    state: SwarmTaskDurableState = SwarmTaskDurableState.PENDING
    operation_class: OperationClass = OperationClass.PURE
    produced_refs: List[str] = Field(default_factory=list)
    summary: str = ""
    error_code: str = ""
    attempt: int = 0
    idempotency_key: str = ""
    settled_at: float = 0.0

    @field_validator("produced_refs", mode="before")
    @classmethod
    def _refs(cls, v: Any) -> List[str]:
        return _clamp_refs(list(v or []))[:12]

    @field_validator("summary", mode="before")
    @classmethod
    def _sum(cls, v: Any) -> str:
        return str(v or "")[:MAX_SUMMARY_CHARS]


class SwarmRunDurable(BaseModel):
    swarm_run_id: str
    mission_id: str
    goal_slice: str = ""
    state: str = "running"  # running|succeeded|failed|cancelled|partial
    tasks: Dict[str, SwarmTaskReceipt] = Field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def task_states(self) -> Dict[str, str]:
        return {tid: t.state.value for tid, t in self.tasks.items()}


class MissionDiagnostics(BaseModel):
    mission_id: str
    goal: str = ""
    state: str = ""
    revision: int = 0
    goal_revision: int = 0
    current_frontier: MissionFrontier = Field(default_factory=MissionFrontier)
    blocked_reason: str = ""
    artifact_count: int = 0
    swarm_status: str = ""
    resource_use: Dict[str, float] = Field(default_factory=dict)
    last_checkpoint: str = ""
    lease_owner: str = ""
    lease_epoch: int = 0
    recovery_count: int = 0
