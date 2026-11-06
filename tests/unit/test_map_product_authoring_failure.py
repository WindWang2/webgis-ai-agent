"""Test GIS-07: webgis_map_product sets success=False on cartographic authoring failure.

Verifies that when authoring_failures is populated and no layers were bound (bound_layers empty):
1. out["cartographic_authoring_failed"] is True
2. out["success"] is False (previously incorrectly remained True)
3. out["error"] describes the failure reason.
"""
import shutil
from unittest.mock import AsyncMock, patch
import pytest

from app.services.session_data import session_data_manager
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.tools.registry import ToolRegistry
from app.services.gis_harness.tools import register_gis_harness_tools


def _point_fc(n: int = 15):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [104.06 + i * 0.001, 30.67 + i * 0.001]},
                "properties": {"name": f"pt_{i}", "val": i},
            }
            for i in range(n)
        ],
    }


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_gis_harness_tools(reg)
    return reg


@pytest.fixture
async def test_session():
    sid = "test-authoring-fail-session"
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)
    yield sid
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)


@pytest.mark.asyncio
async def test_map_product_fails_when_all_layers_fail_authoring(registry, test_session):
    """When layer authoring fails and zero layers are mounted, success must be False."""
    ref = await session_data_manager.store(test_session, _point_fc(15), prefix="geojson")

    # Mock layer_upsert to simulate failure on MapSpec layer commit
    fail_upsert = AsyncMock(return_value={"success": False, "message": "MapSpec write failed: database locked"})

    with patch("app.services.mapspec_store.mapspec_store.layer_upsert", fail_upsert):
        res = await registry.dispatch(
            "webgis_map_product",
            {
                "query": "成都小学分布",
                "session_id": test_session,
                "primary_ref": ref,
            },
            session_id=test_session,
        )

    assert res["cartographic_authoring_failed"] is True
    assert res["success"] is False, "Total cartographic failure must set success=False"
    assert "Failed to mount any map layers" in res.get("error", "")
    assert "MapSpec write failed" in res.get("error", "")


@pytest.mark.asyncio
async def test_map_product_succeeds_when_layers_bound(registry, test_session):
    """Normal authoring where layers mount successfully retains success=True."""
    ref = await session_data_manager.store(test_session, _point_fc(15), prefix="geojson")

    res = await registry.dispatch(
        "webgis_map_product",
        {
            "query": "成都小学分布",
            "session_id": test_session,
            "primary_ref": ref,
        },
        session_id=test_session,
    )

    assert res["success"] is True
    assert not res.get("cartographic_authoring_failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("gate_enabled", [True, False])
async def test_relocated_product_handler_retains_workflow_final_gate(
        registry, test_session, monkeypatch, gate_enabled):
    """The extracted handler keeps partial-ref refusal and its kill switch."""
    from app.core.config import settings
    from app.services.gis_harness.analysis_workflows.contracts import AnalysisArtifactEnvelope
    from app.services.gis_harness.analysis_workflows.state import WorkflowRunState, state_alias

    ref = await session_data_manager.store(test_session, _point_fc(), prefix="geojson")
    state = WorkflowRunState(
        workflow_id="product-partial", family="network_proximity", status="partial",
        envelopes={"nearest_facility": AnalysisArtifactEnvelope(
            step_id="nearest_facility", workflow_id="product-partial",
            tool_name="network_closest_facility", ref=ref)},
    )
    state_ref = await session_data_manager.store(test_session, state.model_dump(), prefix="awf-state")
    await session_data_manager.set_alias(test_session, state_ref, state_alias(state.workflow_id))
    monkeypatch.setattr(settings, "GIS_AWF_PRODUCT_GATE", gate_enabled)

    result = await registry.dispatch(
        "webgis_map_product", {"query": "成都小学分布", "primary_ref": ref},
        session_id=test_session,
    )

    if gate_enabled:
        assert result["success"] is False
        assert result["workflow_ref_gate"][0]["eligible"] is False
        assert result["workflow_ref_gate"][0]["ref"] == ref
        assert any("partial ≠ final" in warning for warning in result["warnings"])
    else:
        assert result["success"] is True
        assert not result.get("workflow_ref_gate")
