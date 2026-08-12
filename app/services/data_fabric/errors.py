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
    # classification
    "TRANSIENT_HTTP_STATUS",
    "PERMANENT_HTTP_STATUS",
    "classify_http_status",
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
]
