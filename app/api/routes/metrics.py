"""Production Telemetry Digest REST API (/api/v1/metrics/digest)."""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.agent_pi_bridge import get_harness_telemetry_summary
from app.core.auth import require_admin
from app.services import tool_metrics
from app.services.spatial_analyzer import SpatialAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/metrics/digest")
async def get_metrics_digest(_user: dict = Depends(require_admin)) -> Dict[str, Any]:
    """Return production tool call metrics, SpatialAnalyzer cache stats, and harness info.

    #792 (F-A-4): harness telemetry is aggregated across the per-session
    harness registry (mean of non-null per-session rates), no longer the
    last-touched session's harness presented as service-level."""
    snapshot = tool_metrics.aggregator_snapshot()
    spatial_info = SpatialAnalyzer.get_st_dbscan_cache_info()
    harness_metrics = get_harness_telemetry_summary()

    return {
        "success": True,
        "tool_metrics": snapshot,
        "spatial_cache": spatial_info,
        "harness_enabled": harness_metrics is not None,
        "harness_metrics": harness_metrics,
    }
