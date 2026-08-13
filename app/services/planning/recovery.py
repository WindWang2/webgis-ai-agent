"""Failure classification and recovery policy (design-v3 §recovery).

Maps the dispatch codes that already exist in the repo — ``VALIDATION_ERROR`` /
``NOT_FOUND`` / ``UNKNOWN_TOOL`` / ``TOOL_ERROR`` (app/tools/registry.py:331-463,
app/services/tool_dispatch_service.py:142-163) plus message-level signals and
exceptions — onto ``FailureClass``, then picks the first recovery action from a
static policy. No blind retry anywhere: ``retry_transient`` appears only for
``transient_network`` failures; a failed call is never automatically replayed
for other classes.
"""
from typing import Optional

from app.services.jobs.cancellation import OperationCancelled

from .models import FailureClass, RecoveryAction

# Message-level signals, matched as lowercased substrings. Scan order inside a
# class is fixed so the outcome is deterministic (network → auth → resource →
# no_data → empty).
_NETWORK_MARKERS = (
    "timeout", "timed out", "connection", "network", "socket",
    "refused", "reset", "超时", "网络", "连接",
)
_AUTH_MARKERS = (
    "auth", "401", "403", "unauthorized", "forbidden", "permission",
    "授权", "权限", "凭证",
)
_RESOURCE_MARKERS = (
    "memory", "quota", "rate limit", "429", "resource", "配额",
    "内存不足", "超限", "too large",
)
_NO_DATA_MARKERS = (
    "no data", "无数据", "没有数据", "无任何", "未找到数据", "找不到数据", "no matching",
)
_EMPTY_MARKERS = (
    "empty", "未返回任何", "无要素", "0 条", "零要素", "no features",
)


def _has_any(markers: tuple[str, ...], text: str) -> bool:
    return any(marker in text for marker in markers)


def classify_error(
    *,
    status: Optional[str] = None,
    code: Optional[str] = None,
    error_type: Optional[str] = None,
    message: Optional[str] = None,
    exception: Optional[Exception] = None,
) -> FailureClass:
    """Classify a dispatch failure into a ``FailureClass`` (deterministic).

    Precedence (documented — first match wins):

    1. cancellation — ``OperationCancelled`` exception, or ``cancelled`` in
       status / code / error_type. Cancellation is never a tool fault
       (tool_pipeline.py:34, ADR-0052).
    2. exception type — ``TimeoutError``/``ConnectionError`` →
       ``transient_network``; ``KeyError``/``FileNotFoundError`` →
       ``missing_ref`` (registry maps these to NOT_FOUND); ``ValueError`` →
       ``validation``.
    3. dispatch code — ``VALIDATION_ERROR`` → validation; ``NOT_FOUND`` →
       missing_ref; ``UNKNOWN_TOOL`` → tool_unavailable.
    4. ``TOOL_ERROR`` (or absent code / ok / success) → message markers:
       network → transient_network, auth → auth, resource → resource_limit,
       no data → no_data, empty/suspicious → empty_result.
    5. default → ``internal``.

    ``status="ok"`` with empty-result markers still classifies as
    ``empty_result`` (suspicious results are successful dispatches whose
    payload contains no usable data — tool_dispatch_service.py:377-397).
    """
    code_l = (code or "").lower()
    type_l = (error_type or "").lower()
    status_l = (status or "").lower()
    msg_l = (message or "").lower()

    # 1. cancellation — never a tool fault
    if (
        isinstance(exception, OperationCancelled)
        or code_l == "cancelled"
        or status_l == "cancelled"
        or "operationcancelled" in type_l
        or "cancelled" in type_l
    ):
        return FailureClass.cancelled

    # 2. exception types
    if exception is not None:
        if isinstance(exception, (TimeoutError, ConnectionError)):
            return FailureClass.transient_network
        if isinstance(exception, (KeyError, FileNotFoundError)):
            return FailureClass.missing_ref
        if isinstance(exception, ValueError):
            return FailureClass.validation
        # other exception types fall through to code/message classification

    # 3. authoritative dispatch codes
    if code_l == "validation_error":
        return FailureClass.validation
    if code_l == "not_found":
        return FailureClass.missing_ref
    if code_l == "unknown_tool":
        return FailureClass.tool_unavailable

    # 4. TOOL_ERROR / ok / no code → message-level signals
    if code_l in ("tool_error", "ok", "success", ""):
        if _has_any(_NETWORK_MARKERS, msg_l):
            return FailureClass.transient_network
        if _has_any(_AUTH_MARKERS, msg_l):
            return FailureClass.auth
        if _has_any(_RESOURCE_MARKERS, msg_l):
            return FailureClass.resource_limit
        if _has_any(_NO_DATA_MARKERS, msg_l):
            return FailureClass.no_data
        if _has_any(_EMPTY_MARKERS, msg_l):
            return FailureClass.empty_result

    # 5. default
    return FailureClass.internal


# Static recovery policy: preferred action first. No blind retry — only
# transient_network may retry, and then only once (retry_transient is the sole
# action there).
RECOVERY_POLICY: dict[FailureClass, list[RecoveryAction]] = {
    FailureClass.validation: [RecoveryAction.correct_args],
    FailureClass.missing_ref: [RecoveryAction.reuse_ref, RecoveryAction.correct_args],
    FailureClass.empty_result: [
        RecoveryAction.alternate_tool,
        RecoveryAction.replan_remaining,
        RecoveryAction.stop,
    ],
    FailureClass.no_data: [
        RecoveryAction.alternate_tool,
        RecoveryAction.replan_remaining,
        RecoveryAction.stop,
    ],
    FailureClass.auth: [RecoveryAction.stop],
    FailureClass.tool_unavailable: [
        RecoveryAction.alternate_tool,
        RecoveryAction.replan_remaining,
    ],
    FailureClass.resource_limit: [RecoveryAction.replan_remaining, RecoveryAction.stop],
    FailureClass.transient_network: [RecoveryAction.retry_transient],
    FailureClass.cancelled: [RecoveryAction.stop],
    FailureClass.internal: [RecoveryAction.replan_remaining, RecoveryAction.stop],
}


def recovery_action_for(failure_class: FailureClass) -> RecoveryAction:
    """First (preferred) recovery action for a failure class."""
    return RECOVERY_POLICY[failure_class][0]
