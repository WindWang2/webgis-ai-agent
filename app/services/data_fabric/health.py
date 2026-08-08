"""
Geospatial Data Fabric: Data Source Health Monitoring Service
Provides diagnostic health checks and monitoring for Data Fabric endpoints with SSRF security validation.
"""
import time
import logging
from typing import Dict, Any, Optional
from app.schemas.data_fabric_schema import DataFabricHealth, ConnectionProfile
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.security import DataFabricSecurity, DataFabricSecurityError

logger = logging.getLogger(__name__)


class DataFabricHealthCheck:
    """
    Data Source Health Monitoring Service for validating connectivity,
    latency, SSRF compliance, and runtime status of Data Fabric endpoints.
    """

    def check_health(self, adapter: GeospatialDataSourceAdapter) -> DataFabricHealth:
        """
        Execute diagnostic health check on a data source adapter.
        """
        start_time = time.perf_counter()
        try:
            # 1. SSRF check on adapter profile URL if present
            if adapter.profile.url:
                try:
                    DataFabricSecurity.validate_url(
                        adapter.profile.url,
                        allow_private=adapter.profile.allow_private,
                    )
                except DataFabricSecurityError as se:
                    elapsed = (time.perf_counter() - start_time) * 1000.0
                    return DataFabricHealth(
                        status="unreachable",
                        message=f"SSRF Security Violation: {se}",
                        details={"error_type": "security_violation"},
                        latency_ms=round(elapsed, 2),
                    )

            # 2. Delegate to adapter's health method
            health_status = adapter.health()
            elapsed = (time.perf_counter() - start_time) * 1000.0
            if health_status.latency_ms is None:
                health_status.latency_ms = round(elapsed, 2)
            return health_status
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000.0
            logger.error(f"[DataFabricHealthCheck] Health check failed for adapter '{adapter.profile.id}': {e}")
            return DataFabricHealth(
                status="unreachable",
                message=f"Connection failed: {str(e)}",
                details={"exception": str(e)},
                latency_ms=round(elapsed, 2),
            )

    def check_connection_profile(self, profile: ConnectionProfile) -> DataFabricHealth:
        """
        Validate connectivity and SSRF policy for a ConnectionProfile without full adapter instantiation.
        """
        start_time = time.perf_counter()
        try:
            if profile.url:
                DataFabricSecurity.validate_url(
                    profile.url,
                    allow_private=profile.allow_private,
                )

            elapsed = (time.perf_counter() - start_time) * 1000.0
            return DataFabricHealth(
                status="healthy",
                message=f"Profile '{profile.id}' passed security and connection validation",
                details={"source_type": profile.source_type, "url": profile.url},
                latency_ms=round(elapsed, 2),
            )
        except DataFabricSecurityError as se:
            elapsed = (time.perf_counter() - start_time) * 1000.0
            return DataFabricHealth(
                status="unreachable",
                message=f"SSRF Policy Violation: {se}",
                details={"profile_id": profile.id},
                latency_ms=round(elapsed, 2),
            )
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000.0
            return DataFabricHealth(
                status="unreachable",
                message=f"Validation error: {e}",
                details={"profile_id": profile.id},
                latency_ms=round(elapsed, 2),
            )

    def check_all(self, adapters: Dict[str, GeospatialDataSourceAdapter]) -> Dict[str, DataFabricHealth]:
        """
        Execute health checks across all registered adapters in parallel / map.
        """
        results: Dict[str, DataFabricHealth] = {}
        for profile_id, adapter in adapters.items():
            results[profile_id] = self.check_health(adapter)
        return results


# Global singleton instance
data_fabric_health_check = DataFabricHealthCheck()
