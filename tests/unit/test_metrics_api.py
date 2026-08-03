"""
Unit tests for Production Telemetry Digest REST API (/api/v1/metrics/digest).
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import tool_metrics
from app.services.spatial_analyzer import SpatialAnalyzer

client = TestClient(app)


def test_metrics_digest_endpoint():
    """Verify /api/v1/metrics/digest returns tool_metrics, spatial_cache, and harness info."""
    tool_metrics._reset_for_tests()
    SpatialAnalyzer.clear_st_dbscan_cache()

    # Record sample tool call
    tool_metrics.record_tool_call(
        tool="webgis_layer_upsert",
        arg_bytes=128,
        result_bytes=256,
        duration_ms=45,
        cache_hit=False,
        error=None,
        session_id="test_sess",
    )

    response = client.get("/api/v1/metrics/digest")
    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert "tool_metrics" in data
    assert "webgis_layer_upsert" in data["tool_metrics"]
    assert data["tool_metrics"]["webgis_layer_upsert"]["count"] == 1
    assert data["tool_metrics"]["webgis_layer_upsert"]["total_ms"] == 45

    assert "spatial_cache" in data
    assert "hits" in data["spatial_cache"]
    assert "misses" in data["spatial_cache"]

    assert "harness_enabled" in data
