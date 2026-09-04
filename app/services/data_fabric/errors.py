"""Structured error taxonomy for the Geospatial Data Fabric.

Every remote operation resolves to either a success or one of these typed
errors. Tools, routes and the agent translate the stable ``code`` strings
into actionable decisions instead of guessing from free-form messages.

Design (Data Fabric V3 / ADR-0053):
- ``DataFabricError`` carries a stable ``code`` plus optional ``details``.
- ``classify_http_status`` maps an HTTP response to the right code so the
  reliability layer (retry/circuit-breaker) can decide transient vs permanent
  without re-implementing the mapping per adapter.
- The transient/permanent sets are the single source of truth for the retry
  policy (see reliability.py): only ``TRANSIENT_HTTP_STATUS`` is retried.
"""
from typing import Any, Dict, Optional


# ── Canonical error code strings (stable public contract) ───────────────────
UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
INVALID_QUERY = "INVALID_QUERY"
SOURCE_UNREACHABLE = "SOURCE_UNREACHABLE"
SOURCE_TIMEOUT = "SOURCE_TIMEOUT"
SOURCE_AUTH_FAILED = "SOURCE_AUTH_FAILED"
SOURCE_RATE_LIMITED = "SOURCE_RATE_LIMITED"
SOURCE_BAD_RESPONSE = "SOURCE_BAD_RESPONSE"
RESULT_TOO_LARGE = "RESULT_TOO_LARGE"
MATERIALIZATION_FAILED = "MATERIALIZATION_FAILED"
CANCELLED = "CANCELLED"
SECURITY_BLOCKED = "SECURITY_BLOCKED"
DATASET_NOT_FOUND = "DATASET_NOT_FOUND"
# ── V2 (ADR-0094) additions ──────────────────────────────────────────────────
QUERY_BUDGET_EXCEEDED = "QUERY_BUDGET_EXCEEDED"
CRS_INVALID = "CRS_INVALID"
QUERY_UNSUPPORTED = "QUERY_UNSUPPORTED"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


# ── HTTP status classification (single source of truth for retry policy) ─────
# Genuinely transient: a bounded retry with backoff may succeed.
TRANSIENT_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
# Permanent client errors / security decisions: MUST NOT be retried.
PERMANENT_HTTP_STATUS = frozenset({400, 401, 403, 404, 405, 409, 422})


class DataFabricError(Exception):
    """Base for typed Data Fabric failures.

    ``code`` is the stable machine-readable contract; ``details`` carries
    optional structured context (e.g. hint, retry_after, status_code).
    """

    code: str = "DATA_FABRIC_ERROR"

    def __init__(
        self,
        message: str = "",
        *,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message or self.code)
        if code:
            self.code = code
        self.details: Dict[str, Any] = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"error_type": self.code, "error": str(self), "details": self.details}


class UnsupportedSourceError(DataFabricError):
    code = UNSUPPORTED_SOURCE


class UnsupportedCapabilityError(DataFabricError):
    code = UNSUPPORTED_CAPABILITY


class InvalidQueryError(DataFabricError):
    code = INVALID_QUERY


class SourceUnreachableError(DataFabricError):
    code = SOURCE_UNREACHABLE


class SourceTimeoutError(DataFabricError):
    code = SOURCE_TIMEOUT


class SourceAuthFailedError(DataFabricError):
    code = SOURCE_AUTH_FAILED


class SourceRateLimitedError(DataFabricError):
    code = SOURCE_RATE_LIMITED


class SourceBadResponseError(DataFabricError):
    code = SOURCE_BAD_RESPONSE


class ResultTooLargeError(DataFabricError):
    code = RESULT_TOO_LARGE


class MaterializationFailedError(DataFabricError):
    code = MATERIALIZATION_FAILED


class DataFabricCancelledError(DataFabricError):
    code = CANCELLED


class SecurityBlockedError(DataFabricError):
    code = SECURITY_BLOCKED


class DatasetNotFoundError(DataFabricError):
    code = DATASET_NOT_FOUND


class QueryBudgetExceededError(DataFabricError):
    code = QUERY_BUDGET_EXCEEDED


class CrsInvalidError(DataFabricError):
    code = CRS_INVALID


class QueryUnsupportedError(DataFabricError):
    code = QUERY_UNSUPPORTED


class SourceUnavailableError(DataFabricError):
    code = SOURCE_UNAVAILABLE


def classify_http_status(status: int) -> str:
    """Map a remote HTTP status to a canonical Data Fabric error code.

    The mapping is deliberately conservative: unknown 4xx are treated as bad
    responses (not retried), and only ``TRANSIENT_HTTP_STATUS`` is retryable.
    """
    if status in (401, 403):
        return SOURCE_AUTH_FAILED
    if status == 404:
        return SOURCE_UNREACHABLE
    if status == 429:
        return SOURCE_RATE_LIMITED
    if 500 <= status < 600:
        return SOURCE_BAD_RESPONSE
    if 400 <= status < 500:
        return SOURCE_BAD_RESPONSE
    return SOURCE_BAD_RESPONSE


# code → typed exception (inverse mapping for in-band QueryResult markers).
_ERROR_CLASS_BY_CODE: Dict[str, type] = {
    SOURCE_UNREACHABLE: SourceUnreachableError,
    SOURCE_TIMEOUT: SourceTimeoutError,
    SOURCE_RATE_LIMITED: SourceRateLimitedError,
    SOURCE_AUTH_FAILED: SourceAuthFailedError,
    SOURCE_BAD_RESPONSE: SourceBadResponseError,
    RESULT_TOO_LARGE: ResultTooLargeError,
    MATERIALIZATION_FAILED: MaterializationFailedError,
    CANCELLED: DataFabricCancelledError,
    SECURITY_BLOCKED: SecurityBlockedError,
    # V2: previously these in-band codes degraded to SourceUnreachableError.
    INVALID_QUERY: InvalidQueryError,
    UNSUPPORTED_SOURCE: UnsupportedSourceError,
    UNSUPPORTED_CAPABILITY: UnsupportedCapabilityError,
    DATASET_NOT_FOUND: DatasetNotFoundError,
    QUERY_BUDGET_EXCEEDED: QueryBudgetExceededError,
    CRS_INVALID: CrsInvalidError,
    QUERY_UNSUPPORTED: QueryUnsupportedError,
    SOURCE_UNAVAILABLE: SourceUnavailableError,
}


def error_from_query_result(result: Any) -> Optional[DataFabricError]:
    """Interpret the in-band failure markers of an adapter ``QueryResult``.

    #766: adapters catch every exception and return an empty-but-"successful"
    ``QueryResult`` whose failure signal lives only in
    ``schema_info["error"]`` / ``metadata["error_type"]`` (or
    ``metadata["error"]`` / ``metadata["error_hint"]``). Consumers used to
    treat those as genuinely empty datasets. This helper converts the markers
    back into a typed ``DataFabricError`` so callers can distinguish "fetch
    failed" from "empty dataset". Returns ``None`` when no marker is present
    (a real, possibly empty, success).

    Duck-typed on ``.metadata`` / ``.schema_info`` / ``.dataset_id`` to avoid
    an import cycle with the pydantic schema module.
    """
    metadata = getattr(result, "metadata", None) or {}
    schema_info = getattr(result, "schema_info", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    if not isinstance(schema_info, dict):
        schema_info = {}

    err_type = metadata.get("error_type")
    err_msg = metadata.get("error") or schema_info.get("error")
    if not err_type and not err_msg:
        return None

    code = err_type or SOURCE_UNREACHABLE
    cls = _ERROR_CLASS_BY_CODE.get(code, SourceUnreachableError)
    details: Optional[Dict[str, Any]] = None
    dataset_id = getattr(result, "dataset_id", None)
    if dataset_id:
        details = {"dataset_id": dataset_id}
    return cls(err_msg or code, code=code, details=details)


__all__ = [
    # codes
    "UNSUPPORTED_SOURCE",
    "UNSUPPORTED_CAPABILITY",
    "INVALID_QUERY",
    "SOURCE_UNREACHABLE",
    "SOURCE_TIMEOUT",
    "SOURCE_AUTH_FAILED",
    "SOURCE_RATE_LIMITED",
    "SOURCE_BAD_RESPONSE",
    "RESULT_TOO_LARGE",
    "MATERIALIZATION_FAILED",
    "CANCELLED",
    "SECURITY_BLOCKED",
    "DATASET_NOT_FOUND",
    "QUERY_BUDGET_EXCEEDED",
    "CRS_INVALID",
    "QUERY_UNSUPPORTED",
    "SOURCE_UNAVAILABLE",
    # classification
    "TRANSIENT_HTTP_STATUS",
    "PERMANENT_HTTP_STATUS",
    "classify_http_status",
    "error_from_query_result",
    # exceptions
    "DataFabricError",
    "UnsupportedSourceError",
    "UnsupportedCapabilityError",
    "InvalidQueryError",
    "SourceUnreachableError",
    "SourceTimeoutError",
    "SourceAuthFailedError",
    "SourceRateLimitedError",
    "SourceBadResponseError",
    "ResultTooLargeError",
    "MaterializationFailedError",
    "DataFabricCancelledError",
    "SecurityBlockedError",
    "DatasetNotFoundError",
    "QueryBudgetExceededError",
    "CrsInvalidError",
    "QueryUnsupportedError",
    "SourceUnavailableError",
]
