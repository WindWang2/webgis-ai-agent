"""Production Telemetry Digest REST API (/api/v1/metrics/digest)."""
import logging
from typing import Any, Dict

from fastapi import APIRouter

from app.agent_pi_bridge import get_harness
from app.services import tool_metrics
from app.services.spatial_analyzer import SpatialAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/metrics/digest")
async def get_metrics_digest() -> Dict[str, Any]:
    """Return production tool call metrics, SpatialAnalyzer cache stats, and harness info."""
    snapshot = tool_metrics.aggregator_snapshot()
    spatial_info = SpatialAnalyzer.get_st_dbscan_cache_info()
    harness = get_harness()

    harness_metrics = None
    if harness is not None:
        harness_metrics = harness.get_telemetry_summary()

    return {
        "success": True,
        "tool_metrics": snapshot,
        "spatial_cache": spatial_info,
        "harness_enabled": harness is not None,
        "harness_metrics": harness_metrics,
    }
