"""ads-v1 declarative fallback chains (DS4, ADR-0174, gap A4 + failure self-heal).

Executes an acquisition along a source's declared fallback chain
(``config/sources`` ``fallbacks:`` — bare ids or conditional ``on:`` rules),
recording every hop in the frozen D3 ``FallbackDecision`` contract and the
whole attempt in a D4 ``AcquisitionFact``.

Integration with the existing primitives (reuse, not replacement):

- **per-hop retry** — ``reliability.retry_call`` with the standard
  ``is_transient`` classifier (4xx/security errors never retried); the
  policy uses zero backoff by default (callers may inject their own);
- **circuit breaking** — fabric's thread-safe ``CircuitBreakerRegistry``
  keyed by source_id is the authoritative sync breaker; OPEN sources are
  skipped with a ``circuit_open`` decision record;
- **health surface** — attempts are mirrored into ``provider_health`` via
  ``fabric_health_bridge`` (``fabric:<source_id>`` keys), so the monitoring
  snapshot covers fabric sources.

Comparability rule (task book hard constraint): when the fallback target's
granularity / data-type / coverage differs from the origin source, the D3
decision records ``comparable=False`` and the result payload is flagged
``degraded`` — silent source switching is forbidden.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.data_fabric.circuit_breaker import CircuitBreakerRegistry, CircuitState
from app.services.data_fabric.contracts import AcquisitionFact, FallbackDecision
from app.services.data_fabric.reliability import RetryPolicy, retry_call
from app.services.provider_health import fabric_health_bridge

logger = logging.getLogger(__name__)

#: Default per-hop retry attempts (1 try + 1 retry), zero backoff — bounded.
_DEFAULT_MAX_ATTEMPTS = 2

#: Module-level breaker registry (same lifecycle as the fabric one).
breaker_registry = CircuitBreakerRegistry()

D3_TRIGGERS = {
    "timeout", "5xx", "429", "quota", "empty_result", "truncated",
    "schema_mismatch", "circuit_open", "probe_failed", "other",
}


def classify_failure(exc: Optional[BaseException], *, empty_result: bool = False) -> str:
    """Map a failure to the D3 trigger vocabulary (typed errors first)."""
    if empty_result:
        return "empty_result"
    if exc is None:
        return "other"
    from app.services.data_fabric.errors import (
        InvalidQueryError,
        SourceBadResponseError,
        SourceRateLimitedError,
        SourceTimeoutError,
        SourceUnreachableError,
    )

    if isinstance(exc, SourceTimeoutError):
        return "timeout"
    if isinstance(exc, SourceRateLimitedError):
        return "429"
    if isinstance(exc, InvalidQueryError):
        return "schema_mismatch"
    if isinstance(exc, SourceBadResponseError):
        msg = str(exc).lower()
        if "truncat" in msg or "unterminated" in msg or "expecting" in msg:
            # mid-stream body cut / malformed JSON → the response was truncated
            return "truncated"
        # adapters wrap raw HTTP errors here — scan the embedded status first
        # ("503 Server Error: …"), else the body/shape was wrong
        for token, trigger in (("429", "429"), ("503", "5xx"), ("502", "5xx"), ("500", "5xx"), ("timed out", "timeout")):
            if token in msg:
                return trigger
        return "schema_mismatch"
    if isinstance(exc, SourceUnreachableError):
        return "timeout"
    msg = str(exc).lower()
    for token, trigger in (("429", "429"), ("503", "5xx"), ("502", "5xx"), ("500", "5xx"), ("timed out", "timeout")):
        if token in msg:
            return trigger
    return "other"


def resolve_chain(
    source_id: str,
    *,
    max_hops: int = 5,
    service=None,
) -> List[Tuple[str, List[str], Optional[bool]]]:
    """Registry-declared chain → ordered hops after the primary.

    Each hop: ``(source_id, triggers, comparable_override)``; empty triggers
    means any failure activates it. Depth-limited, cycle-safe.
    """
    from app.services.data_fabric.source_registry import SourceRegistryError, source_registry_service

    service = service or source_registry_service
    hops: List[Tuple[str, List[str], Optional[bool]]] = []
    seen = {source_id}
    current = source_id
    while len(hops) < max_hops:
        try:
            definition = service.get(current)
        except SourceRegistryError:
            break
        advanced = False
        for rule in definition.normalized_fallbacks():
            if rule.source_id in seen:
                continue
            hops.append((rule.source_id, list(rule.on), rule.comparable))
            seen.add(rule.source_id)
            current = rule.source_id
            advanced = True
            break
        if not advanced:
            break
    return hops


def _comparable(from_facts: Any, to_facts: Any, override: Optional[bool]) -> bool:
    """Granularity/data-type/coverage agreement decides comparability.

    Unknown facts on either side → False (conservative; task book default).
    """
    if override is not None:
        return override
    if from_facts is None or to_facts is None:
        return False
    same_type = getattr(from_facts, "data_type", None) == getattr(to_facts, "data_type", None)
    g_from = getattr(from_facts, "granularity", None)
    g_to = getattr(to_facts, "granularity", None)
    same_granularity = g_from == g_to or g_from is None or g_to is None
    fb = getattr(from_facts, "bbox", None)
    tb = getattr(to_facts, "bbox", None)
    if fb is None and tb is None:
        same_coverage = True
    elif fb and tb:
        same_coverage = _bbox_contains(tb, fb)
    else:
        same_coverage = False
    return bool(same_type and same_granularity and same_coverage)


def _bbox_contains(outer: List[float], inner: List[float]) -> bool:
    try:
        return (
            outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3]
        )
    except (TypeError, IndexError):
        return False


class ChainResult:
    """Outcome of one chain execution (decisions + fact + payload)."""

    def __init__(
        self,
        *,
        source_used: Optional[str],
        features: List[Dict[str, Any]],
        decisions: List[FallbackDecision],
        fact: AcquisitionFact,
        error: Optional[BaseException],
    ):
        self.source_used = source_used
        self.features = features
        self.decisions = decisions
        self.fact = fact
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def non_comparable(self) -> bool:
        return any(not d.comparable for d in self.decisions)


def execute_fallback_chain(
    primary_source_id: str,
    runner: Callable[[str], Tuple[List[Dict[str, Any]], Dict[str, Any]]],
    *,
    chain: Optional[List[Tuple[str, List[str], Optional[bool]]]] = None,
    facts_by_source: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    dataset_key: str = "",
    version: str = "latest",
    wave: str = "",
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    max_hops: int = 5,
) -> ChainResult:
    """Run ``runner(source_id) -> (features, meta)`` along the declared chain.

    ``runner`` performs ONE acquisition against a source (the caller binds the
    adapter/plan); the executor owns retry, breaking, health mirroring and the
    D3/D4 records. ``chain`` overrides the registry resolution (tests inject
    fault scenarios; production leaves it None).
    """
    if chain is None:
        chain = resolve_chain(primary_source_id, max_hops=max_hops)
    facts_by_source = facts_by_source or {}

    started = time.perf_counter()
    decisions: List[FallbackDecision] = []
    retries_total = 0
    last_error: Optional[BaseException] = None

    features: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}
    source_used: Optional[str] = None
    outcome: str = "failed"

    current = primary_source_id
    while current is not None:
        # breaker gate (authoritative sync breaker, keyed by source_id)
        if breaker_registry.state(current) == CircuitState.OPEN:
            decisions.append(FallbackDecision(
                trigger="circuit_open",
                from_source=current,
                to_source=chain[0][0] if chain else "",
                reason=f"熔断打开，跳过 {current}",
                comparable=False,
                decided_at=_now_iso(),
            ))
            last_error = last_error or RuntimeError(f"circuit open for {current}")
            nxt = _match_hop(chain, "circuit_open")
            if nxt is None:
                break
            current, chain = _consume(nxt, chain)
            continue

        attempts = 0

        def _hop() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
            nonlocal attempts
            attempts += 1
            return runner(current)

        try:
            features, meta = retry_call(
                _hop,
                policy=RetryPolicy(max_attempts=max_attempts, base_sleep=0.0),
                sleep=lambda _s: None,
                idempotent=True,
            )
            retries_total += max(0, attempts - 1)
            fabric_health_bridge.record_success(f"fabric:{current}")
            breaker_registry.record_success(current)
            source_used = current
            last_error = None

            if meta.get("empty_result"):
                trigger = "empty_result"
                nxt = _match_hop(chain, trigger)
                if nxt is not None:
                    decisions.append(FallbackDecision(
                        trigger=trigger,
                        from_source=current,
                        to_source=nxt[0],
                        reason="源返回空结果（声明的 empty_result 触发）",
                        comparable=_comparable(
                            facts_by_source.get(current), facts_by_source.get(nxt[0]), nxt[2],
                        ),
                        decided_at=_now_iso(),
                    ))
                    current, chain = _consume(nxt, chain)
                    continue
            outcome = "success"
            break
        except Exception as exc:  # noqa: BLE001 — the chain exists to classify failures
            retries_total += max(0, attempts - 1)
            last_error = exc
            fabric_health_bridge.record_error(f"fabric:{current}", exc)
            breaker_registry.record_failure(current, exc)
            trigger = classify_failure(exc)
            nxt = _match_hop(chain, trigger)
            if nxt is None:
                outcome = "failed"
                break
            decisions.append(FallbackDecision(
                trigger=trigger,
                from_source=current,
                to_source=nxt[0],
                reason=f"{type(exc).__name__}: {exc}"[:200],
                comparable=_comparable(
                    facts_by_source.get(current), facts_by_source.get(nxt[0]), nxt[2],
                ),
                decided_at=_now_iso(),
            ))
            current, chain = _consume(nxt, chain)

    latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
    fact = AcquisitionFact(
        request_id=request_id or f"req-{int(time.time() * 1000)}",
        dataset_key=dataset_key or (source_used or primary_source_id),
        source_id=source_used,
        version=version,
        rows=len(features),
        bytes=int(meta.get("bytes", 0) or 0),
        latency_ms=latency_ms,
        retries=retries_total,
        degraded=bool(decisions),
        outcome=outcome if outcome == "success" or decisions or last_error else "failed",
        fallback=decisions[0] if decisions else None,
        wave=wave,
    )
    if last_error is not None and source_used is None:
        fact.outcome = "failed"
    elif decisions:
        fact.outcome = "degraded"
    elif outcome == "success":
        fact.outcome = "success"

    return ChainResult(
        source_used=source_used,
        features=features,
        decisions=decisions,
        fact=fact,
        error=last_error,
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _match_hop(
    chain: List[Tuple[str, List[str], Optional[bool]]],
    trigger: str,
) -> Optional[Tuple[str, List[str], Optional[bool]]]:
    """First hop whose triggers include ``trigger`` (empty triggers = any)."""
    for hop in chain:
        triggers = hop[1]
        if not triggers or trigger in triggers:
            return hop
    return None


def _consume(
    hop: Tuple[str, List[str], Optional[bool]],
    chain: List[Tuple[str, List[str], Optional[bool]]],
) -> Tuple[str, List[Tuple[str, List[str], Optional[bool]]]]:
    """Move to ``hop``; the remaining chain keeps hops after it (cycle-free by
    construction in resolve_chain; explicit chains are caller-owned)."""
    idx = chain.index(hop) if hop in chain else 0
    return hop[0], chain[idx + 1:]


__all__ = [
    "classify_failure",
    "resolve_chain",
    "execute_fallback_chain",
    "ChainResult",
    "breaker_registry",
    "D3_TRIGGERS",
]
