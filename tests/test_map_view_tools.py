"""地图视图操作工具测试"""
import pytest

from app.tools.registry import ToolRegistry
from app.tools.map_view import register_map_view_tools, _extract_bbox_from_geojson
from app.services.session_data import session_data_manager


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_map_view_tools(r)
    return r


def test_extract_bbox_from_featurecollection():
    gj = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10, 20]}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [30, 40]}},
        ],
    }
    assert _extract_bbox_from_geojson(gj) == [10, 20, 30, 40]


def test_extract_bbox_respects_existing_bbox_field():
    gj = {"type": "FeatureCollection", "features": [], "bbox": [1, 2, 3, 4]}
    assert _extract_bbox_from_geojson(gj) == [1, 2, 3, 4]


def test_extract_bbox_empty_returns_none():
    assert _extract_bbox_from_geojson({"type": "FeatureCollection", "features": []}) is None
    assert _extract_bbox_from_geojson(None) is None


@pytest.mark.asyncio
async def test_fly_to_location_emits_fly_to(registry):
    out = await registry.dispatch("fly_to_location", {"longitude": 116.4, "latitude": 39.9, "zoom": 11})
    assert out["success"] is True
    assert out["command"] == "fly_to"
    assert out["params"]["center"] == [116.4, 39.9]
    assert out["params"]["zoom"] == 11


@pytest.mark.asyncio
async def test_fly_to_location_rejects_out_of_range(registry):
    out = await registry.dispatch("fly_to_location", {"longitude": 200, "latitude": 0})
    assert "error" in out


@pytest.mark.asyncio
async def test_zoom_to_bbox_validates_order(registry):
    ok = await registry.dispatch("zoom_to_bbox", {"bbox": [0, 0, 1, 1]})
    assert ok["success"] is True
    assert ok["command"] == "zoom_to_bbox"
    bad = await registry.dispatch("zoom_to_bbox", {"bbox": [1, 1, 0, 0]})
    assert "error" in bad


@pytest.mark.asyncio
async def test_reset_map_view_returns_default(registry):
    out = await registry.dispatch("reset_map_view", {})
    assert out["command"] == "fly_to"
    assert out["params"]["zoom"] == 4
    assert out["params"]["pitch"] == 0


@pytest.mark.asyncio
async def test_set_map_view_requires_at_least_one_param(registry):
    bad = await registry.dispatch("set_map_view", {})
    assert "error" in bad
    ok = await registry.dispatch("set_map_view", {"pitch": 45})
    assert ok["success"] is True
    assert ok["params"] == {"pitch": 45}


@pytest.mark.asyncio
async def test_zoom_to_layer_resolves_alias(registry):
    sid = "test-map-view-session"
    gj = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [100.0, 30.0]}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [101.0, 31.0]}},
        ],
    }
    ref = await session_data_manager.store(sid, gj, prefix="t")
    await session_data_manager.set_alias(sid, ref, "测试层")

    out = await registry.dispatch("zoom_to_layer", {"layer_ref": "测试层"}, session_id=sid)
    assert out["success"] is True
    assert out["command"] == "zoom_to_bbox"
    assert out["params"]["bbox"] == [100.0, 30.0, 101.0, 31.0]

    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_zoom_to_layer_missing_session(registry):
    out = await registry.dispatch("zoom_to_layer", {"layer_ref": "x"})
    assert "error" in out
