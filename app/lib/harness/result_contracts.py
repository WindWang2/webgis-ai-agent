"""Wire contracts for deterministic replay results and benchmark reports.

These are dictionary records, not runtime wrappers: recording, digest and JSON
formats stay unchanged. Arbitrary tool arguments and recorded provider payloads
remain untyped at their existing boundaries. Only an exact comparison's leaves
are intentionally ``Any``: that comparator accepts arbitrary Python values.
"""
from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class MetricRow(TypedDict):
    scene_id: str
    check_id: str
    value: float


class GateCheck(TypedDict):
    score: float
    target: float
    passed: bool
    evaluated: NotRequired[bool]
    reason: NotRequired[str]
    status: NotRequired[str]
    trusted: NotRequired[bool]


class GateResult(TypedDict, total=False):
    overall_passed: bool
    metrics: dict[str, float]
    thresholds: dict[str, float]
    checks: dict[str, GateCheck]
    run_id: str | None
    session_id: str | None


class GoalSatisfaction(TypedDict, total=False):
    status: Literal["pass", "fail", "not_evaluated"]
    reason: str
    dimensions: list[str | None]


class ExactDiff(TypedDict):
    path: str
    expected: Any
    actual: Any
    diff_class: Literal["exact", "nondeterministic_text"]
    reason: NotRequired[str]
    turn: NotRequired[int]


class MutationOutcome(TypedDict, total=False):
    op: str
    unknown_op: bool
    success: bool
    is_error: bool
    error_code: str | None
    is_compiled: bool
    spec_fingerprint: str | None
    layer_count: int | None
    finding_count: int
    superseded: bool


class DispatchDecision(TypedDict):
    call_id: str
    tool: str
    allowed: bool
    capability: NotRequired[str]
    reason: NotRequired[str]
    alternatives: NotRequired[list[str]]


class RederivedDecision(TypedDict):
    selected: str
    recorded: str
    rederived: bool


class SelectedDecision(TypedDict):
    selected: str


class ReceiptEntry(TypedDict):
    status: str
    ref_minted: bool
    error_code: str | None


class ReceiptProjection(TypedDict, total=False):
    receipt: dict[str, ReceiptEntry]
    receipt_repeat: dict[str, str]


class DispatchProjection(TypedDict, total=False):
    call_id: str
    allowed: bool
    capability: str


class CheckProjection(TypedDict, total=False):
    passed: bool | None
    evaluated: bool | None
    score: float | None
    reason: str | None


class GateProjection(TypedDict, total=False):
    overall_passed: bool | None
    checks: dict[str, CheckProjection]


class MutationProjection(TypedDict, total=False):
    op: str | None
    success: bool | None
    spec_fingerprint: str | None
    error_code: str | None


class ActualProjection(ReceiptProjection, total=False):
    gate: GateProjection
    goal: GoalSatisfaction
    mutations: list[MutationOutcome]
    dispatch: dict[str, DispatchProjection]
    decision_rederive: dict[str, SelectedDecision]


class TurnProjection(TypedDict, total=False):
    turn: int
    gate: GateProjection
    goal: GoalSatisfaction
    mutations: list[MutationProjection]
    dispatch: list[DispatchProjection]
    receipt: ReceiptProjection


class ScenarioProjection(TypedDict, total=False):
    turns: list[TurnProjection]


class RegistryDrift(TypedDict):
    kind: str
    recorded_digest: str
    current_digest: str
    hint: str


class DecisionDiff(TypedDict, total=False):
    decision_id: str
    kind: str
    type: str
    baseline: str
    current: str
    baseline_count: int
    current_count: int
    selected: str


class DriftAttributes(TypedDict, total=False):
    decision_count: int
    decisions_digest: str
    registry_drift: RegistryDrift
    decision_diffs: list[DecisionDiff]


class BenchEntry(DriftAttributes, total=False):
    scenario_id: str
    category: str
    ok: bool
    replay_digest: str
    levels_run: list[str]
    not_run: list[str]
    turns: int
    exact_diff_count: int
    metric_row_count: int
    projection: ScenarioProjection
    deferred_levels: list[str]
    duration_ms: float
    context_bytes_proxy: int


class BenchReport(TypedDict, total=False):
    suite_seed: int
    profile: str
    scenarios: int
    green: int
    red: int
    entries: list[BenchEntry]
    ratchet_rows: list[MetricRow]
    fault_contract_violations: list[str]


JourneyStage = Literal[
    "intent", "plan", "tool_dispatch", "data_result", "mutation",
    "render_observation", "export", "settlement",
]
JourneyStatus = Literal["pass", "fail", "blocked", "partial", "not_evaluated"]
JourneyMode = Literal[
    "offline_replay", "live_dispatch_stub_llm", "browser_renderer",
    "live_services_opt_in",
]


class JourneyIdentity(TypedDict):
    session_id: str
    turn_id: str
    goal_id: str
    operation_id: str
    correlation_id: str
    request_id: str
    run_id: str


class JourneyStageEvidence(TypedDict):
    stage: JourneyStage
    ordinal: int
    status: JourneyStatus
    observed_ordinal: int | None
    evidence_refs: list[str]
    source_fingerprint: str
    reason_code: str


class JourneyFinding(TypedDict):
    code: str
    stage: JourneyStage
    detail: str


class JourneyCompletionClaims(TypedDict):
    command_accepted: bool
    tool_dispatched: bool
    tool_result_validated: bool
    artifact_persisted: bool
    mapspec_applied: bool
    map_rendered_and_observed: bool
    goal_satisfied: bool
    publication_export_verified: bool


class EndToEndJourneyEvidence(TypedDict):
    schema_version: str
    mode: JourneyMode
    identity: JourneyIdentity
    stages: list[JourneyStageEvidence]
    completion_claims: JourneyCompletionClaims
    findings: list[JourneyFinding]
    last_valid_stage: JourneyStage | None
    overall: JourneyStatus
    sequence_basis: str
    runtime_fingerprint: dict[str, Any]


class ResumeState(TypedDict, total=False):
    seed: int
    profile: str
    entries: dict[str, BenchEntry]


class ProjectionDelta(TypedDict):
    turn: int | str
    kind: str
    aspect: str
    key: str | None
    baseline: CheckProjection | MutationProjection | DispatchProjection | ReceiptEntry | str | None
    current: CheckProjection | MutationProjection | DispatchProjection | ReceiptEntry | str | None


class ScenarioDrift(TypedDict):
    scenario_id: str
    kind: Literal["new", "missing", "digest_drift"]
    baseline_ok: NotRequired[bool | None]
    current_ok: NotRequired[bool | None]
    decisions_digest_drift: NotRequired[bool]
    projection_absent: NotRequired[bool]
    delta: NotRequired[list[ProjectionDelta]]
    delta_count: NotRequired[int]


class ComparisonReport(TypedDict):
    baseline: str
    drifts: list[ScenarioDrift]
    green_baseline: int | None
    green_current: int | None


class DiffSummary(TypedDict):
    scenarios_compared: int
    drifted: int
    new: int
    missing: int
    unchanged: int


class DiffReport(TypedDict):
    baseline: str
    current: str
    green_baseline: int | None
    green_current: int | None
    summary: DiffSummary
    deltas: list[ScenarioDrift]


class BaselineEntry(TypedDict):
    scenario_id: str | None
    ok: bool | None
    replay_digest: str | None
    decisions_digest: NotRequired[str]


class BenchBaseline(TypedDict):
    kind: str
    suite_seed: int | None
    profile: str | None
    green: int | None
    red: int | None
    entries: list[BaselineEntry]
    corpus_version: NotRequired[int]


class DigestTurn(TypedDict, total=False):
    gate_passed: bool | None
    checks: dict[str, CheckProjection]
    goal: GoalSatisfaction
    mutations: list[MutationOutcome]
    dispatch: list[DispatchProjection]
    decision_rederive: dict[str, str]
    receipt: ReceiptProjection


class VisualCritiqueRecord(TypedDict):
    dimension: str
    severity: str
    suggestion: str
    evidence_class: str
    confidence: float | None
    evidence: str


class VisualJudgeSummary(TypedDict):
    evidence_class: str
    source: str
    status: str
    reason: str
    mode: str
    critiques: list[VisualCritiqueRecord]
    error_count: int
    warning_count: int
    fingerprint: str
    screenshot_digest: str
    duration_ms: int
    model: str


def project_dispatch(entry: DispatchDecision) -> DispatchProjection:
    """Keep the deterministic fields, omitting absent optional capability."""
    out: DispatchProjection = {}
    if entry.get("call_id") is not None:
        out["call_id"] = entry["call_id"]
    if entry.get("allowed") is not None:
        out["allowed"] = entry["allowed"]
    if entry.get("capability") is not None:
        out["capability"] = entry["capability"]
    return out


def project_mutation(entry: MutationOutcome) -> MutationProjection:
    """A missing mutation field is explicitly null in benchmark projections."""
    return {
        "op": entry.get("op"), "success": entry.get("success"),
        "spec_fingerprint": entry.get("spec_fingerprint"),
        "error_code": entry.get("error_code"),
    }


def baseline_entry(entry: BenchEntry) -> BaselineEntry:
    out: BaselineEntry = {
        "scenario_id": entry.get("scenario_id"), "ok": entry.get("ok"),
        "replay_digest": entry.get("replay_digest"),
    }
    if entry.get("decisions_digest"):
        out["decisions_digest"] = entry["decisions_digest"]
    return out
