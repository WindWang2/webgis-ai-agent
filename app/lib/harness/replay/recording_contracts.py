"""Closed recording projections; provider arguments remain an open boundary."""
from typing import Any, NotRequired, TypedDict

from app.lib.harness.result_contracts import ExactDiff


class PayloadDigest(TypedDict):
    digest: str
    bytes: int


class ToolResultReference(TypedDict, total=False):
    geojson_ref: str
    mapspec_fingerprint: str
    status: str
    digest: PayloadDigest


class RecordedToolCall(TypedDict, total=False):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    arg_bytes: int
    args_truncated: bool
    status: str
    result_ref: ToolResultReference
    duration_ms: float
    is_error: bool
    error_code: str
    error_msg: str


class RecordingMetadata(TypedDict, total=False):
    source: str
    schema: int
    degraded: bool
    budget_degraded: bool


class EnvironmentFingerprint(TypedDict, total=False):
    env_schema_version: int
    registry_digest: str
    python_version: str
    platform: str
    runtime_flags: dict[str, bool]
    policy_versions: dict[str, str]
    sources: dict[str, str]
    manifest: str
    budgets_digest: str


class EnvironmentDrift(TypedDict):
    kind: str
    behavioral: bool
    changed_keys: list[str]


class TurnTiming(TypedDict, total=False):
    total: float
    first_event: float | None
    llm_total: float | None
    llm_ttft: float | None
    context: float | None
    tool: float | None
    map_ack_wait_max: float | None


class TurnWork(TypedDict, total=False):
    llm_rounds: int
    tool_calls: int
    tool_retries: int
    deduped_tool_calls: int
    sse_events: int
    map_actions_issued: int
    map_actions_acked: int
    map_actions_unacked: int
    artifacts: int


class TokenUsage(TypedDict, total=False):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reports: int


class TurnWarning(TypedDict):
    code: str
    detail: str


class CalibrationDrop(TypedDict):
    path: str
    reason: str
    # The generic exact comparator accepts arbitrary leaves, not only JSON.
    recorded: NotRequired[Any]
    current: NotRequired[Any]
    turn: NotRequired[int]


class CalibrationSummary(TypedDict):
    dropped_count: int
    underfilled: bool
    dropped: list[CalibrationDrop]


class CalibrationReport(CalibrationSummary):
    calibrated: bool
    residual_diffs: list[ExactDiff]


class ReceiptPin(TypedDict, total=False):
    status: str
    ref_minted: bool
    error_code: str
