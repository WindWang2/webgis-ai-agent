"""
Unit tests for Production Telemetry Digest REST API (/api/v1/metrics/digest).
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import tool_metrics
from app.services.spatial_analyzer import SpatialAnalyzer

client = TestClient(app)


from app.core.auth import require_admin


@pytest.fixture(autouse=True)
def cleanup_metrics():
    """Reset global metrics and cache state before and after each test."""
    tool_metrics._reset_for_tests()
    SpatialAnalyzer.clear_st_dbscan_cache()
    app.dependency_overrides[require_admin] = lambda: {"user_id": "admin_test", "role": "admin"}
    yield
    tool_metrics._reset_for_tests()
    SpatialAnalyzer.clear_st_dbscan_cache()
    app.dependency_overrides.pop(require_admin, None)


def test_metrics_digest_endpoint():
    """Verify /api/v1/metrics/digest returns tool_metrics, spatial_cache, and harness info."""

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


def test_metrics_digest_harness_telemetry_aggregates_sessions():
    """#792 (F-A-4): /metrics/digest harness telemetry must aggregate across
    ALL per-session harnesses (mean of non-null per-session rates) instead of
    presenting only the last-touched session's window as service-level."""
    import app.services.cartography_runtime as bridge

    saved_harnesses = dict(bridge._harnesses)
    saved_harness = bridge._harness
    bridge._harnesses.clear()
    try:
        ha = bridge._get_session_harness("digest-a", create=True)
        hb = bridge._get_session_harness("digest-b", create=True)
        assert ha is not None and hb is not None
        # A: mutation with real semantic-validity evidence -> MapSpecValidity 100
        ha.record_tool_call("c1", "webgis_layer_upsert", {})
        ha.record_tool_result(
            "c1", "webgis_layer_upsert", {"success": True, "is_compiled": True}
        )
        # B: mutation WITHOUT semantic evidence -> MapSpecValidity 0
        hb.record_tool_call("c1", "webgis_layer_upsert", {})
        hb.record_tool_result("c1", "webgis_layer_upsert", {"success": True})

        response = client.get("/api/v1/metrics/digest")
        assert response.status_code == 200
        metrics = response.json()["harness_metrics"]
        assert metrics["harness_sessions"] == 2
        assert metrics["counts"]["HarnessSessions"] == 2.0
        # mean(100, 0) == 50: the digest reflects BOTH sessions — the old
        # last-touched behavior reported only session B's 0.0.
        assert metrics["rates"]["MapSpecValidity"] == 50.0
        assert metrics["counts"]["ToolCallsCount"] == 2.0

        # Nulls stay null when NO session has evidence for a rate.
        assert metrics["rates"]["CursorResolutionRate"] is None
        assert metrics["evaluated"]["CursorResolutionRate"] is False
    finally:
        for sid in ("digest-a", "digest-b"):
            bridge._discard_session_harness(sid)
        bridge._harnesses.clear()
        bridge._harnesses.update(saved_harnesses)
        bridge._harness = saved_harness
