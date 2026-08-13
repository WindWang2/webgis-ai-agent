"""Reliability, circuit-breaker, and health-cache tests.

All deterministic: injectable clock/sleep, no real wall-clock waits, no network.
"""
import pytest

from app.services.data_fabric.errors import (
    SourceBadResponseError,
    SourceRateLimitedError,
    SecurityBlockedError,
    InvalidQueryError,
)
from app.services.data_fabric.reliability import RetryPolicy, is_transient, retry_call
from app.services.data_fabric.circuit_breaker import (
    CircuitBreakerRegistry,
    CircuitState,
)


# ── is_transient classification ─────────────────────────────────────────────


def test_transient_typed_errors_retryable():
    assert is_transient(SourceBadResponseError("503")) is True
    assert is_transient(SourceRateLimitedError("429")) is True


def test_permanent_errors_not_retryable():
    assert is_transient(SecurityBlockedError("ssrf")) is False
    assert is_transient(InvalidQueryError("bad")) is False
    assert is_transient(ValueError("bad")) is False


def test_transient_message_tokens():
    assert is_transient(RuntimeError("Connection reset by peer")) is True
    assert is_transient(RuntimeError("read timed out")) is True
    assert is_transient(RuntimeError("server returned 503")) is True
    assert is_transient(RuntimeError("not found")) is False


# ── retry_call ───────────────────────────────────────────────────────────────


def test_retry_succeeds_after_transient(monkeypatch):
    """Transient failures are retried; success on attempt 3."""
    sleeps = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise SourceBadResponseError("503")
        return "ok"

    out = retry_call(flaky, policy=RetryPolicy(max_attempts=5, base_sleep=0), sleep=sleeps.append)
    assert out == "ok"
    assert calls["n"] == 3


def test_retry_does_not_retry_permanent():
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise InvalidQueryError("bad query")

    with pytest.raises(InvalidQueryError):
        retry_call(bad, policy=RetryPolicy(max_attempts=5), sleep=lambda _s: None)
    assert calls["n"] == 1  # no retry


def test_retry_bounded_attempts():
    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        raise SourceBadResponseError("503")

    policy = RetryPolicy(max_attempts=3, base_sleep=0)
    with pytest.raises(SourceBadResponseError):
        retry_call(always_fail, policy=policy, sleep=lambda _s: None)
    assert calls["n"] == 3  # exactly max_attempts


def test_retry_backoff_is_jittered_and_bounded():
    """Backoff stays within [0, base*2^attempt] and is capped at max_sleep."""
    p = RetryPolicy(max_attempts=4, base_sleep=1.0, max_sleep=3.0)
    for attempt in range(5):
        d = p.backoff_seconds(attempt)
        assert 0.0 <= d <= 3.0


# ── circuit breaker (fake clock) ─────────────────────────────────────────────


class _FakeClock:
    def __init__(self, t0=0.0):
        self.t = t0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _registry(threshold=3, cool_down=30.0, clock=None):
    from app.services.data_fabric.circuit_breaker import CircuitBreaker

    return CircuitBreakerRegistry(
        breaker=CircuitBreaker(failure_threshold=threshold, cool_down=cool_down, clock=clock or _FakeClock()),
    )


def test_breaker_opens_after_threshold_failures():
    reg = _registry(threshold=3, cool_down=30.0, clock=_FakeClock())
    for _ in range(3):
        reg.record_failure("src", SourceBadResponseError("503"))
    assert reg.state("src") == CircuitState.OPEN
    assert reg.allow("src") is False  # fail fast


def test_breaker_half_open_after_cooldown():
    clk = _FakeClock()
    reg = _registry(threshold=2, cool_down=30.0, clock=clk)
    reg.record_failure("src", SourceBadResponseError("503"))
    reg.record_failure("src", SourceBadResponseError("503"))
    assert reg.state("src") == CircuitState.OPEN

    clk.advance(31.0)
    assert reg.state("src") == CircuitState.HALF_OPEN
    assert reg.allow("src") is True  # one trial allowed


def test_breaker_closes_on_success():
    clk = _FakeClock()
    reg = _registry(threshold=2, cool_down=30.0, clock=clk)
    reg.record_failure("src", SourceBadResponseError("503"))
    reg.record_failure("src", SourceBadResponseError("503"))
    clk.advance(31.0)
    reg.record_success("src")  # trial succeeded
    assert reg.state("src") == CircuitState.CLOSED


def test_breaker_call_fail_fast_when_open():
    clk = _FakeClock()
    reg = _registry(threshold=1, cool_down=30.0, clock=clk)
    reg.record_failure("src", SourceBadResponseError("503"))
    assert reg.state("src") == CircuitState.OPEN

    from app.services.data_fabric.errors import SourceUnreachableError

    def would_hit_network():  # pragma: no cover - must not be called
        raise AssertionError("must fail fast, not call network")

    with pytest.raises(SourceUnreachableError) as ei:
        reg.call("src", would_hit_network)
    assert "circuit breaker" in str(ei.value)


def test_breaker_permanent_error_does_not_trip():
    """Security/validation errors are not evidence the source is down."""
    reg = _registry(threshold=1, cool_down=30.0, clock=_FakeClock())
    reg.record_failure("src", SecurityBlockedError("ssrf"))
    assert reg.state("src") == CircuitState.CLOSED  # not tripped
    assert reg.allow("src") is True


# ── health cache (fake clock) ───────────────────────────────────────────────


def test_health_cache_avoids_repeat_probe():
    from app.schemas.data_fabric_schema import ConnectionProfile
    from app.services.data_fabric.health import DataFabricHealthCheck

    clk = _FakeClock()
    hc = DataFabricHealthCheck(healthy_ttl=30.0, failure_ttl=5.0, clock=clk)

    probes = {"n": 0}

    class _Adapter:
        def __init__(self):
            self.profile = ConnectionProfile(id="src1", source_type="ogc_api")

        def health(self):
            probes["n"] += 1
            from app.schemas.data_fabric_schema import DataFabricHealth

            return DataFabricHealth(status="healthy", latency_ms=1.0)

    a = _Adapter()
    # Need the breaker to allow; use a fresh registry that won't trip on healthy.
    from app.services.data_fabric.circuit_breaker import CircuitBreakerRegistry, set_breaker_registry

    set_breaker_registry(_registry(threshold=99, cool_down=30.0, clock=clk))
    try:
        hc.check_health(a, use_cache=False)
        first = probes["n"]
        hc.check_health(a)  # populates cache
        cached_before = probes["n"]
        hc.check_health(a)  # should hit cache
        assert probes["n"] == cached_before  # no new probe
        assert probes["n"] >= first
    finally:
        # restore default registry
        set_breaker_registry(CircuitBreakerRegistry())


def test_health_failure_ttl_is_shorter():
    from app.schemas.data_fabric_schema import DataFabricHealth
    from app.services.data_fabric.health import DataFabricHealthCheck

    clk = _FakeClock()
    hc = DataFabricHealthCheck(healthy_ttl=30.0, failure_ttl=5.0, clock=clk)
    from app.services.data_fabric.circuit_breaker import _BreakerEntry  # noqa: F401
    # verify TTL selection logic directly via the private cache
    h_ok = DataFabricHealth(status="healthy")
    h_bad = DataFabricHealth(status="unreachable", reachable=False)
    hc._cache_put("ok", h_ok)
    hc._cache_put("bad", h_bad)
    # within both TTLs → both hit
    assert hc._cache_get("ok") is not None
    assert hc._cache_get("bad") is not None
    # after 6s (> failure_ttl, < healthy_ttl) → only healthy survives
    clk.advance(6.0)
    assert hc._cache_get("ok") is not None
    assert hc._cache_get("bad") is None
