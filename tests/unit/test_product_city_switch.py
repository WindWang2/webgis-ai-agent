"""#2001: a city switch must not keep the previous city's extent."""
import shutil

import pytest

from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager
from app.services.gis_harness.tools import register_gis_harness_tools
from app.tools.registry import ToolRegistry

BEIJING = [115.4, 39.4, 117.5, 41.1]


def _points(lon: float, lat: float, **props):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        }],
    }


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_gis_harness_tools(reg)
    return reg


@pytest.fixture
async def test_session():
    sid = "test-city-switch-2001"
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)
    yield sid
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)


def _patch_admin(monkeypatch):
    monkeypatch.setattr(
        "app.services.local_first.admin_bbox_wgs84",
        lambda _name: list(BEIJING),
    )


@pytest.mark.asyncio
async def test_beijing_label_over_chengdu_extent_is_refused(registry, test_session, monkeypatch):
    _patch_admin(monkeypatch)
    payload = _points(104.06, 30.67, name="学校")
    payload["district"] = "北京市"
    ref = await session_data_manager.store(test_session, payload, prefix="geojson")
    result = await registry.dispatch(
        "webgis_map_product",
        {
            "query": "换成北京，其他样式保持不变",
            "primary_ref": ref,
        },
        session_id=test_session,
    )
    assert result["success"] is False
    assert result["code"] == "scope_bbox_disjoint"
    assert result["scope_subject"] == "primary_ref"
    assert result["scope_geography"]["evidence"] == "bbox"


@pytest.mark.asyncio
async def test_overlay_and_bound_layer_of_the_old_city_are_refused(
        registry, test_session, monkeypatch):
    _patch_admin(monkeypatch)
    beijing = await session_data_manager.store(
        test_session, _points(116.4, 39.9, cityname="北京市"), prefix="geojson")
    chengdu = await session_data_manager.store(
        test_session, _points(104.06, 30.67, cityname="成都市"), prefix="geojson")
    overlay = await registry.dispatch(
        "webgis_map_product",
        {
            "query": "换成北京，其他样式保持不变",
            "primary_ref": beijing,
            "overlay_refs": [chengdu],
        },
        session_id=test_session,
    )
    assert overlay["success"] is False
    assert overlay["code"] == "scope_bbox_disjoint"
    assert overlay["scope_subject"] == "overlay_ref"

    await mapspec_store.layer_upsert(
        test_session,
        {"id": "old-schools", "type": "circle", "source": "src-old"},
        {"type": "geojson", "ref_id": chengdu},
    )
    bound = await registry.dispatch(
        "webgis_map_product",
        {
            "query": "换成北京，其他样式保持不变",
            "layer_ids": ["old-schools"],
        },
        session_id=test_session,
    )
    assert bound["success"] is False
    assert bound["scope_subject"] == "layer_id"
    spec = await mapspec_store.get_mapspec(test_session) or {}
    assert all(layer.get("id") != "old-schools" for layer in spec.get("layers") or [])
