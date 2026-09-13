"""Production Telemetry Digest REST API (/api/v1/metrics/digest)."""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.services.cartography_runtime import get_harness_telemetry_summary
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

    # ADR-0180 D5：Pi 工具面指标段（additive；invalid-name / validation /
    # proxy fallback / per-turn surface bytes —— T9 回归门观测面）。
    try:
        from app.services.chat.pi_surface_metrics import snapshot as pi_surface_snapshot

        pi_surface = pi_surface_snapshot()
    except Exception:  # noqa: BLE001 — 指标段缺席不影响既有 digest
        pi_surface = {}

    return {
        "success": True,
        "tool_metrics": snapshot,
        "spatial_cache": spatial_info,
        "harness_enabled": harness_metrics is not None,
        "harness_metrics": harness_metrics,
        "pi_surface": pi_surface,
    }
