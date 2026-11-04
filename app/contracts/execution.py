"""Bounded GeoCompute plan identity and terminal evidence wire contracts."""
from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class PlanRecord(TypedDict, total=False):
    execution_plan_version: int
    plan_id: str
    plan_fingerprint: str
    node_fingerprints: dict[str, str]
    runtime_manifest_fingerprint: str | None


class DriftRecord(TypedDict):
    state: str
    reason: str | None
    stored_plan_fingerprint: str | None
    current_plan_fingerprint: str | None
    stored_runtime_fingerprint: str | None
    current_runtime_fingerprint: str | None


class CrsRecord(TypedDict):
    output_crs: str | None
    allow_reproject: bool


class BundleNode(TypedDict):
    node_id: str
    category: str
    operation: str
    fingerprint: str
    deterministic: bool
    policy: str
    crs: CrsRecord | None
    dataset_fingerprints: dict[str, str]
    parameters_json: str
    status: str
    output_ref: str | None
    attempts: int


class ExecutionBundle(TypedDict):
    bundle_version: int
    reproducibility: str
    reproducibility_reason: str | None
    runtime_manifest_fingerprint: str | None
    plan_fingerprint: str
    plan_id: str
    execution_plan_version: int
    run_id: str
    run_status: str
    backend_variant: str
    dataset_versions: dict[str, str]
    nodes: list[BundleNode]
    materialized_refs: list[str]
    drift: DriftRecord


class ReproducibilityRecord(TypedDict):
    classification: str | None
    reason: str | None
    bundle_digest: str
    plan_fingerprint: str | None
    runtime_manifest_fingerprint: str | None
    backend_variant: str | None


class LineageInput(TypedDict):
    ref_id: str
    kind: str


class LineageRecord(TypedDict):
    node_id: str
    category: str
    fingerprint: str
    status: str
    inputs: list[str]
    input_lineage: list[LineageInput]
    dataset_fingerprints: dict[str, str]
    output_ref: str | None
    output_summary: dict[str, str | int | float | bool | None]


class RunExtras(TypedDict, total=False):
    reproducibility: ReproducibilityRecord
    lineage: list[LineageRecord]


class SnapshotRun(TypedDict):
    run_id: str
    plan_id: str
    plan_fingerprint: str
    status: str
    wall_time_s: float | None
    error_code: str | None
    error_message: str | None
    reproducibility: NotRequired[ReproducibilityRecord]


class EvidenceRecord(TypedDict):
    status: str
    attempts: int
    rows_emitted: int | None
    error_code: str | None
    output_ref: str | None
    duration_s: float | None
    checkpoint_verified: bool | None
    backend_variant: str | None
    reuse_source: str | None
    reuse_skipped_reason: str | None
    bytes_emitted: NotRequired[int | None]
    error_message: NotRequired[str | None]
    retry_safe: NotRequired[bool | None]
    fingerprint: NotRequired[str | None]
    policy: NotRequired[str | None]
    failure_codes: NotRequired[list[str]]
    # Operator-specific summary keys are intentionally open at this leaf.
    output_summary: NotRequired[dict[str, Any]]


class RunSnapshot(TypedDict):
    run: SnapshotRun
    evidence: dict[str, EvidenceRecord]
    lineage: NotRequired[list[LineageRecord]]
    evidence_detail: NotRequired[Literal["compact"]]
    evidence_truncated: NotRequired[bool]
    evidence_total: NotRequired[int]


class SnapshotSummary(TypedDict):
    run_id: str
    status: str
    source: Literal["snapshot"]
    created_at: str | None


class VectorPartitionRecord(TypedDict):
    index: int
    bbox: list[float]
    halo_bbox: list[float]


class PartitionSummary(TypedDict):
    scheme: str
    tiles: int
    crs: str | None
    adaptive_from: int | None


class SkewReport(TypedDict):
    ratio: float
    peak_partition: int
    peak_rows: int
    mean_rows: float
    counts: list[int]
