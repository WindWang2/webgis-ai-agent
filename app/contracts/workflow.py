"""Dictionary contracts at the compiler, persistence, and runtime boundaries.

These describe the existing wire keys, not a second validation layer. Optional
keys account for legacy sparse records and empty/not-yet-produced evidence.
Tool-specific parameters remain open at their explicit extension boundary.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, NotRequired, TypedDict


class PortRecord(TypedDict, total=False):
    name: str
    artifact_type: str
    geometry_kind: str
    crs_requirement: str
    unit_requirement: str
    required: bool


class ParameterReference(TypedDict):
    name: str


class NodeResources(TypedDict, total=False):
    profile: str
    cpu: int
    mem_mb: int
    gpu: int
    io_mbps: int
    gpu_required: bool
    memory_mb: int


class WorkflowNode(TypedDict, total=False):
    node_id: str
    kind: str
    capability: str
    algorithm_id: str
    method_id: str
    role: str
    subworkflow_package_id: str
    inputs: list[PortRecord]
    outputs: list[PortRecord]
    depends_on: list[str]
    conditional: str
    fallback_of: str
    parallel_safe: bool
    optional: bool
    parameters: list[ParameterReference]
    side_effect: str
    params: dict[str, Any]
    resources: NodeResources
    priority: int
    title: str


WorkflowEdge = TypedDict("WorkflowEdge", {"from": str, "to": str})


class WorkflowDAG(TypedDict, total=False):
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    primary_output: str
    validation_violations: list[str]
    compiler_version: str
    methodology_fingerprint: str


TransitionRecord = TypedDict(
    "TransitionRecord", {"rev": int, "from": str, "to": str, "reason": str, "event": str}
)


class ViolationRecord(TypedDict):
    port: str
    code: str
    detail: str


class BindingRecord(TypedDict, total=False):
    node_id: str
    ok: bool
    action: str
    violations: list[ViolationRecord]
    disclosures: list[str]
    disclosure: str


class ReuseRecord(TypedDict, total=False):
    reused: bool
    reuse_fingerprint: str
    artifact_ref: str
    source_instance_id: str
    verified_inputs: list[str]
    fingerprint_level: str
    skipped_reason: str


class AttemptRecord(TypedDict):
    attempt: int
    status: str
    error_code: str
    error_message: str
    duration_ms: int | None
    backend: str
    output_ref: str


class ChangeRecord(TypedDict):
    dimension: str
    target_kind: str
    target: str
    detail: str
    source: str


class DecisionRecord(TypedDict):
    seq: int
    changes_fp: str
    dimensions: list[str]
    marked_stale: list[str]
    reuse_resolved: list[str]
    recomputed: list[str]
    reused: list[str]
    style_only: bool
    explanations: list[str]


class NodeRecord(TypedDict, total=False):
    instance_id: str
    node_id: str
    state: str
    state_revision: int
    claimed_by: str
    attempts: int
    error_code: str
    bound_ref: str
    output_ref: str
    output_fingerprint: str
    binding: BindingRecord
    reuse: ReuseRecord
    attempts_log: list[AttemptRecord]
    transitions: list[TransitionRecord]
    lease_expires_at: str
    heartbeat_at: str
    cancel_requested: bool
    next_ready_at: str


class InstanceRecord(TypedDict, total=False):
    instance_id: str
    package_id: str
    package_version: str
    package_fingerprint: str
    status: str
    revision: int
    owner_scope: str
    org_id: str
    session_id: str
    project_id: str
    parent_instance_id: str
    parent_node_id: str
    turn_id: str
    run_id: str
    run_lease_owner: str
    run_lease_expires_at: str
    cancel_requested: bool
    pending_changes: list[ChangeRecord]
    decisions: list[DecisionRecord]
    visited_packages: list[str]
    error_code: str
    error_detail: str
    created_at: str
    terminal_at: str


class InstancePatch(TypedDict, total=False):
    status: str
    cancel_requested: bool
    pending_changes: list[ChangeRecord]
    decisions: list[DecisionRecord]
    visited_packages: list[str]
    error_code: str
    error_detail: str
    started_at: datetime | None
    terminal_at: datetime | None
    run_lease_owner: str
    run_lease_expires_at: datetime | None


class NodeSpec(TypedDict):
    node_id: str
    optional: NotRequired[bool]


class NodeProjection(TypedDict):
    node_id: str
    state: str
    attempts: int
    error_code: str
    bound_ref: str
    output_ref: str
    reused: bool
    reuse_evidence: ReuseRecord
    binding_violations: list[str]
    reuse: NotRequired[ReuseRecord]


class InstanceProjection(TypedDict):
    instance_id: str
    package_id: str
    package_version: str
    package_fingerprint: str
    status: str
    revision: int
    cancel_requested: bool
    nodes: list[NodeProjection]
    counts: dict[str, int]
    decisions: list[DecisionRecord]
    pending_changes: list[ChangeRecord]
    error_code: str
    error_detail: str
    methodology_family: NotRequired[str]
    compiler_version: NotRequired[str]


class BlockedExplanation(TypedDict):
    node: str
    codes: list[str]


class Explanation(TypedDict):
    why_recomputed: list[str]
    why_reused: list[str]
    blocked: list[BlockedExplanation]


class ChangePlan(TypedDict):
    recompute: list[str]
    reuse: list[str]


class ChangeResult(TypedDict):
    applied: bool
    deferred: bool
    reason: NotRequired[str]
    pending_count: NotRequired[int]
    decision: NotRequired[DecisionRecord]
    plan: NotRequired[ChangePlan]


class RecoveryReport(TypedDict):
    scanned: int
    cancelled: int
    orphan_reset: int
    finalized: int
    errors: int
    workers_stale: list[str]


class RunSummary(TypedDict):
    plan_admission: NotRequired[PlanAdmission]
    status: str
    states: NotRequired[dict[str, str]]
    reason: NotRequired[str]
    error_code: NotRequired[str]


class CancelResult(TypedDict):
    cancelled: bool
    status: str


class CancelNodesResult(TypedDict):
    requested: list[str]
    flagged: list[str]


class RetryResult(TypedDict):
    requeued: list[str]
    exhausted: list[str]


class RetryNodeResult(TypedDict):
    node: str
    state: str
    attempts: int
    force: bool


class CloneResult(TypedDict):
    instance: InstanceRecord
    only_nodes: list[str]
    skip_nodes: list[str]


class RunResult(TypedDict):
    run: RunSummary
    instance: InstanceRecord


class RecomputePreview(TypedDict):
    style_only: bool
    recompute: list[str]
    reuse: list[str]
    would_mark_stale: list[str]
    explanations: list[str]
    changed_dimensions: list[str]


class PlanAdmission(TypedDict):
    mode: str
    violations: list[str]
    limits: dict[str, float]
    node_count: int
    parallel_waves: int
    truncated: bool
    critical_path: list[str]
    wall_exp_s: float | None
    peak_mem_exp_bytes: float | None


class PackageRecord(TypedDict):
    package_id: str
    version: str
    schema_version: str
    compiler_version: str
    methodology_family: str
    recipe_fingerprint: str
    methodology_fingerprint: str
    environment_fingerprint: str
    fingerprint: str
    status: str
    owner_scope: str
    project_id: str
    created_at: str
    published_at: str
    # The compiler package includes several versioned subcontracts; only DAG
    # consumers narrow their individual section, leaving extension keys intact.
    compiled_form: NotRequired[dict[str, Any]]


class WorkerCapabilities(TypedDict):
    cpu: int
    mem_mb: int
    gpu: int
    profiles: dict[str, int]
    io_mbps: int
    backends: list[str]


class WorkerRecord(TypedDict):
    worker_id: str
    role: str
    runtime: str
    capabilities: WorkerCapabilities
    load: dict[str, int]
    locality: dict[str, Any]
    last_heartbeat_at: str
