"""
Geospatial Data Fabric: Data Source Health Monitoring Service.

Truthfulness contract (Section 33/34/35):
- A validated URL is NOT "healthy". ``check_connection_profile`` now reports
  ``valid_profile`` when the URL merely passes SSRF validation — only an actual
  adapter probe reports ``healthy`` / ``reachable``.
- Health is cached with a bounded TTL (healthy longer than failure) so repeated
  API calls do not hammer the remote source.
- The per-source circuit breaker is consulted first: an open breaker yields a
  ``circuit_open`` result without touching the network (fail fast).
"""
import logging
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from app.schemas.data_fabric_schema import ConnectionProfile, DataFabricHealth
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import SourceUnreachableError
from app.services.data_fabric.security import DataFabricSecurity, DataFabricSecurityError

logger = logging.getLogger(__name__)

# Health status vocabulary (Section 33). Free string on the schema, but these
# are the canonical values the agent/tools rely on.
STATUS_VALID_PROFILE = "valid_profile"
STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_UNREACHABLE = "unreachable"
STATUS_TIMEOUT = "timeout"
STATUS_MISCONFIGURED = "misconfigured"
STATUS_CIRCUIT_OPEN = "circuit_open"
STATUS_AUTH_FAILED = "auth_failed"

# Failure statuses that count as evidence the source is down (trip the breaker).
_FAILURE_STATUSES = frozenset({STATUS_UNREACHABLE, STATUS_DEGRADED, STATUS_TIMEOUT})


class SourceDownError(SourceUnreachableError):
    """Raised internally to feed health-check failures into the circuit breaker.

    Subclasses ``SourceUnreachableError`` so ``is_transient`` classifies it as
    transient (a health failure IS evidence the source may be down).
    """

    def __init__(self, source_key: str):
        super().__init__(f"source '{source_key}' health check failed")


@dataclass
class _CachedHealth:
    health: DataFabricHealth
    cached_at: float


class DataFabricHealthCheck:
    """Health monitoring with SSRF validation, TTL caching, and breaker gating."""

    def __init__(
        self,
        healthy_ttl: float = 30.0,
        failure_ttl: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.healthy_ttl = healthy_ttl
        self.failure_ttl = failure_ttl
        self._clock = clock
        self._cache: Dict[str, _CachedHealth] = {}

    # ── cache helpers ───────────────────────────────────────────────────────
    def _cache_get(self, key: str) -> Optional[DataFabricHealth]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        ttl = self.healthy_ttl if entry.health.status == STATUS_HEALTHY else self.failure_ttl
        if self._clock() - entry.cached_at < ttl:
            return entry.health
        return None

    def _cache_put(self, key: str, health: DataFabricHealth) -> None:
        self._cache[key] = _CachedHealth(health=health, cached_at=self._clock())

    def clear_cache(self) -> None:
        self._cache.clear()

    # ── adapter health (real probe) ──────────────────────────────────────────
    def check_health(
        self,
        adapter: GeospatialDataSourceAdapter,
        *,
        use_cache: bool = True,
        source_key: Optional[str] = None,
    ) -> DataFabricHealth:
        """Run (or return cached) diagnostic health for an adapter.

        Honors the circuit breaker: an open breaker short-circuits to
        ``circuit_open`` without a remote probe. Caches by ``source_key`` (or
        ``adapter.profile.id``) so repeated calls within TTL skip the probe.
        """
        key = source_key or (adapter.profile.id if adapter and adapter.profile else "unknown")

        # 缓存先行（M1 修复）：allow() 在 HALF_OPEN 下会占用唯一 trial 名额；
        # 若缓存命中在 allow 之后提前返回，名额永不释放 → 源被永久 fail-fast。
        # 顺序改为：缓存未命中才请求 breaker 名额。
        if use_cache:
            cached = self._cache_get(key)
            if cached is not None:
                return cached

        # Circuit-breaker fast-fail.
        try:
            from app.services.data_fabric.circuit_breaker import get_breaker_registry

            if not get_breaker_registry().allow(key):
                health = DataFabricHealth(
                    status=STATUS_CIRCUIT_OPEN,
                    message=f"source '{key}' circuit breaker open; failing fast",
                    details={"circuit_state": "open"},
                    reachable=False,
                )
                self._cache_put(key, health)
                return health
        except Exception:  # pragma: no cover - defensive
            pass

        start_time = time.perf_counter()
        try:
            if adapter.profile.url:
                try:
                    DataFabricSecurity.validate_url(
                        adapter.profile.url,
                        allow_private=adapter.profile.allow_private,
                    )
                except DataFabricSecurityError as se:
                    elapsed = (time.perf_counter() - start_time) * 1000.0
                    # M1：该路径未走到 record_*（永久性配置错误不计入熔断），
                    # 显式释放 allow() 占用的 half-open trial 名额。
                    try:
                        from app.services.data_fabric.circuit_breaker import get_breaker_registry

                        get_breaker_registry().release_trial(key)
                    except Exception:  # pragma: no cover - defensive
                        pass
                    health = DataFabricHealth(
                        status=STATUS_UNREACHABLE,
                        message=f"SSRF Security Violation: {se}",
                        details={"error_type": "security_violation"},
                        latency_ms=round(elapsed, 2),
                        reachable=False,
                    )
                    self._cache_put(key, health)
                    return health

            health_status = adapter.health()
            elapsed = (time.perf_counter() - start_time) * 1000.0
            if health_status.latency_ms is None or health_status.latency_ms == 0:
                health_status.latency_ms = round(elapsed, 2)
            self._record(key, health_status)
            self._cache_put(key, health_status)
            return health_status
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000.0
            logger.error(
                "[DataFabricHealthCheck] Health check failed for adapter '%s': %s",
                key, e,
            )
            health = DataFabricHealth(
                status=STATUS_UNREACHABLE,
                message=f"Connection failed: {e}",
                details={"exception": type(e).__name__},
                latency_ms=round(elapsed, 2),
                reachable=False,
            )
            self._record(key, health)
            self._cache_put(key, health)
            return health

    @staticmethod
    def _record(source_key: str, health: DataFabricHealth) -> None:
        """Feed success/failure into the per-source circuit breaker."""
        try:
            from app.services.data_fabric.circuit_breaker import get_breaker_registry

            reg = get_breaker_registry()
            if health.status == STATUS_HEALTHY:
                reg.record_success(source_key)
            elif health.status in _FAILURE_STATUSES:
                reg.record_failure(source_key, SourceDownError(source_key))
        except Exception:  # pragma: no cover - defensive
            pass

    # ── profile validation (truthful: NOT a health probe) ────────────────────
    def check_connection_profile(self, profile: ConnectionProfile) -> DataFabricHealth:
        """Validate SSRF policy + shape for a ConnectionProfile.

        This does NOT probe the remote endpoint. A passing result is reported as
        ``valid_profile`` — it means "URL is well-formed and SSRF-safe", NOT
        "the source is reachable/healthy". Previously it returned ``healthy``
        with "passed connection validation", which conflated the two.
        """
        start_time = time.perf_counter()
        if not profile.url:
            elapsed = (time.perf_counter() - start_time) * 1000.0
            return DataFabricHealth(
                status=STATUS_MISCONFIGURED,
                message=f"Profile '{profile.id}' has no endpoint URL",
                details={"source_type": profile.source_type},
                latency_ms=round(elapsed, 2),
                reachable=False,
            )
        try:
            DataFabricSecurity.validate_url(profile.url, allow_private=profile.allow_private)
            elapsed = (time.perf_counter() - start_time) * 1000.0
            return DataFabricHealth(
                status=STATUS_VALID_PROFILE,
                message=f"Profile '{profile.id}' passed SSRF validation (not yet probed)",
                details={"source_type": profile.source_type, "url": DataFabricSecurity.redact_url(profile.url)},
                latency_ms=round(elapsed, 2),
                reachable=False,
            )
        except DataFabricSecurityError as se:
            elapsed = (time.perf_counter() - start_time) * 1000.0
            return DataFabricHealth(
                status=STATUS_UNREACHABLE,
                message=f"SSRF Policy Violation: {se}",
                details={"profile_id": profile.id},
                latency_ms=round(elapsed, 2),
                reachable=False,
            )

    def check_all(self, adapters: Dict[str, GeospatialDataSourceAdapter]) -> Dict[str, DataFabricHealth]:
        results: Dict[str, DataFabricHealth] = {}
        for profile_id, adapter in adapters.items():
            results[profile_id] = self.check_health(adapter, source_key=profile_id)
        return results


# Global singleton instance
data_fabric_health_check = DataFabricHealthCheck()
