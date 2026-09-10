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
