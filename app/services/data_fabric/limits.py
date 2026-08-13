"""Bounded resource guards for Data Fabric remote results (Section 22 / 70).

A remote vector query must not be able to OOM the process. The guard enforces
at least one hard bound on feature count, response bytes, and page count — it
does NOT trust the remote `limit` parameter (servers may ignore it).

Limits are sourced from settings but clamped to non-zero floors, so an operator
cannot accidentally disable protection by setting ``DATA_FABRIC_MAX_FEATURES=0``.
"""
import json
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.data_fabric.errors import ResultTooLargeError


# Hard floors — values below these are raised to them so protection cannot be
# disabled via config. (Generous enough never to obstruct legitimate use.)
_MIN_MAX_FEATURES = 1_000
_MIN_MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # 16 MiB
_MIN_MAX_PAGES = 10
_MIN_QUERY_TIMEOUT = 5.0


def max_features() -> int:
    return max(int(settings.DATA_FABRIC_MAX_FEATURES), _MIN_MAX_FEATURES)


def max_response_bytes() -> int:
    return max(int(settings.DATA_FABRIC_MAX_RESPONSE_BYTES), _MIN_MAX_RESPONSE_BYTES)


def max_pages() -> int:
    return max(int(settings.DATA_FABRIC_MAX_PAGES), _MIN_MAX_PAGES)


def query_timeout() -> float:
    return max(float(settings.DATA_FABRIC_QUERY_TIMEOUT), _MIN_QUERY_TIMEOUT)


def total_query_timeout() -> float:
    return max(float(settings.DATA_FABRIC_TOTAL_QUERY_TIMEOUT), query_timeout())


def _estimate_features_bytes(features: List[Dict[str, Any]]) -> int:
    """Cheap upper-bound byte estimate for a feature list (no exact serialization).

    json.dumps is the real size; for a guard we only need a deterministic
    over-approximation that is cheap on large lists.
    """
    # Sampling: exact serialization of a 100k-feature list is wasteful on the
    # hot path. Sample up to 256 features, average, multiply. Falls back to a
    # per-feature floor so degenerate empty-feature lists still accrue.
    if not features:
        return 0
    sample = features[:256]
    try:
        sample_bytes = len(json.dumps(sample, ensure_ascii=False))
    except (TypeError, ValueError):
        sample_bytes = len(sample) * 512
    avg = max(sample_bytes / len(sample), 64.0)
    return int(avg * len(features))


def enforce_result_bounds(
    features: List[Dict[str, Any]],
    *,
    page_count: Optional[int] = None,
    max_feat: Optional[int] = None,
    max_bytes: Optional[int] = None,
) -> None:
    """Raise ``ResultTooLargeError`` if a result exceeds the hard bounds.

    Called at materialization / query choke points BEFORE the payload is stored,
    so an oversized remote response is rejected rather than blowing up memory.

    The actionable hint tells the caller how to shrink the request (bbox/filter/
    smaller limit / async materialize).
    """
    feat_limit = max_feat if max_feat is not None else max_features()
    byte_limit = max_bytes if max_bytes is not None else max_response_bytes()

    count = len(features)
    if count > feat_limit:
        raise ResultTooLargeError(
            f"query returned {count} features (limit {feat_limit})",
            details={
                "feature_count": count,
                "limit": feat_limit,
                "hint": "narrow bbox, add attribute filters, lower limit, or materialize asynchronously",
            },
        )

    est_bytes = _estimate_features_bytes(features)
    if est_bytes > byte_limit:
        raise ResultTooLargeError(
            f"query result ~{est_bytes} bytes exceeds limit {byte_limit}",
            details={
                "estimated_bytes": est_bytes,
                "limit": byte_limit,
                "hint": "narrow bbox, add attribute filters, lower limit, or materialize asynchronously",
            },
        )

    if page_count is not None and page_count > max_pages():
        raise ResultTooLargeError(
            f"pagination exceeded {max_pages()} pages",
            details={"page_count": page_count, "limit": max_pages()},
        )


def enforce_page_bound(page_index: int) -> None:
    """Per-page guard inside pagination loops: raises before fetching page N+1."""
    if page_index >= max_pages():
        raise ResultTooLargeError(
            f"pagination would exceed {max_pages()} page limit",
            details={"page_index": page_index, "limit": max_pages()},
        )


__all__ = [
    "max_features",
    "max_response_bytes",
    "max_pages",
    "query_timeout",
    "total_query_timeout",
    "enforce_result_bounds",
    "enforce_page_bound",
]
