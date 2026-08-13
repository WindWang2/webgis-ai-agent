"""Remote-request reliability primitives (Section 15/16).

- ``RetryPolicy``: bounded exponential backoff + full jitter, with an injectable
  sleep/clock seam so tests never wait on wall-clock time.
- ``is_transient``: the single classifier for retryable conditions. Only real
  transient errors (connection reset / timeout / 429 / 5xx) are retried; 4xx,
  schema/validation errors, and security decisions are NEVER retried.
- ``retry_call``: runs a callable under the policy. POST is retried only when the
  caller marks it idempotent.

These primitives are deliberately decoupled from any specific HTTP client so the
same contract backs both the requests-based adapters and future async paths.
"""
import random
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple, Type

from app.services.data_fabric.errors import (
    DataFabricError,
    TRANSIENT_HTTP_STATUS,
    SourceRateLimitedError,
    SourceBadResponseError,
    SourceTimeoutError,
    SourceUnreachableError,
    SecurityBlockedError,
    InvalidQueryError,
    UnsupportedSourceError,
)


# Errors that mean "the request itself was bad" — retrying cannot help.
PERMANENT_ERRORS: Tuple[Type[BaseException], ...] = (
    SecurityBlockedError,
    InvalidQueryError,
    UnsupportedSourceError,
    ValueError,
    TypeError,
)


def is_transient(exc: BaseException) -> bool:
    """True iff ``exc`` represents a retryable transient failure.

    Classifies:
    - typed DataFabric errors with transient codes (rate-limited / bad-response /
      timeout / unreachable);
    - requests' ConnectionError / Timeout families (imported lazily so this
      module has no hard requests dependency for non-requests callers);
    - any exception whose message indicates a transient HTTP status in
      ``TRANSIENT_HTTP_STATUS``.
    Security decisions, 4xx client errors, and programming errors are permanent.
    """
    if isinstance(exc, PERMANENT_ERRORS):
        return False
    if isinstance(exc, (SourceRateLimitedError, SourceBadResponseError, SourceTimeoutError, SourceUnreachableError)):
        return True
    if isinstance(exc, DataFabricError):
        # Other typed errors (auth, unsupported capability, result-too-large,
        # materialization, cancelled) are not transient.
        return False

    # requests families (lazy import).
    try:
        import requests.exceptions as rex

        if isinstance(exc, (rex.ConnectionError, rex.Timeout)):
            return True
        # HTTPError carries the response status on .response.status_code.
        if isinstance(exc, rex.HTTPError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None:
                return status in TRANSIENT_HTTP_STATUS
            return False
    except Exception:  # pragma: no cover - requests always present in this project
        pass

    msg = str(exc).lower()
    if any(tok in msg for tok in ("connection reset", "connection aborted", "timed out", "timeout", "temporarily unavailable")):
        return True
    # Look for an embedded transient HTTP status like "503" / "429" in the message.
    for code in TRANSIENT_HTTP_STATUS:
        if str(code) in msg:
            return True
    return False


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff with full jitter.

    Backoff: ``sleep = random.uniform(0, base * 2 ** attempt)`` (full jitter),
    capped at ``max_sleep``. ``max_attempts`` includes the first try, so 3 → at
    most 2 retries. ``retryable`` lets a caller narrow the classifier.
    """

    max_attempts: int = 3
    base_sleep: float = 0.2
    max_sleep: float = 5.0
    retryable: Optional[Callable[[BaseException], bool]] = None

    def __post_init__(self):
        if self.max_attempts < 1:
            object.__setattr__(self, "max_attempts", 1)
        if self.base_sleep < 0:
            object.__setattr__(self, "base_sleep", 0.0)

    def backoff_seconds(self, attempt: int, rng: Optional[random.Random] = None) -> float:
        """Full-jitter backoff for the given (0-indexed) attempt."""
        upper = min(self.max_sleep, self.base_sleep * (2 ** attempt))
        if upper <= 0:
            return 0.0
        if rng is None:
            return random.uniform(0.0, upper)
        return rng.uniform(0.0, upper)


DEFAULT_RETRY = RetryPolicy()


def retry_call(
    fn: Callable[..., Any],
    *args,
    policy: RetryPolicy = DEFAULT_RETRY,
    sleep: Callable[[float], None] = __import__("time").sleep,
    rng: Optional[random.Random] = None,
    idempotent: bool = False,
    on_retry: Optional[Callable[[int, BaseException], None]] = None,
    **kwargs,
) -> Any:
    """Run ``fn(*args, **kwargs)`` under ``policy``.

    - Transient failures are retried up to ``policy.max_attempts``.
    - Permanent failures raise immediately.
    - ``idempotent`` is a caller assertion; non-idempotent calls are still
      retried on read/connection-class transient errors (safe: no side effect
      reached the server), but NOT on HTTPError-class transient errors
      (the server may have acted). This is the conservative Post rule.
    """
    classifier = policy.retryable or is_transient
    last_exc: Optional[BaseException] = None
    for attempt in range(policy.max_attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            transient = classifier(exc)
            # Conservative POST rule: a server-side transient (5xx/429 on an
            # HTTPError) means the request may have been applied — only retry
            # when the caller asserts idempotency. Connection/timeout class is
            # always safe to retry (no response was processed).
            if transient and not idempotent and _is_http_error_class(exc):
                transient = False
            if not transient or attempt == policy.max_attempts - 1:
                raise
            delay = policy.backoff_seconds(attempt, rng=rng)
            if on_retry:
                on_retry(attempt + 1, exc)
            if delay > 0:
                sleep(delay)
    # Unreachable: the loop either returns or raises.
    raise last_exc  # pragma: no cover


def _is_http_error_class(exc: BaseException) -> bool:
    try:
        import requests.exceptions as rex

        return isinstance(exc, rex.HTTPError)
    except Exception:  # pragma: no cover
        return False


__all__ = [
    "RetryPolicy",
    "DEFAULT_RETRY",
    "is_transient",
    "retry_call",
    "PERMANENT_ERRORS",
]
