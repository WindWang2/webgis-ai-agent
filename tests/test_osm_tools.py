"""OSM 工具测试"""
import pytest
import json
from unittest.mock import patch
from app.tools.registry import ToolRegistry
from app.tools.osm import register_osm_tools, _overpass_to_geojson


def test_osm_tools_registered():
    registry = ToolRegistry()
    register_osm_tools(registry)
    tools = registry.list_tools()
    assert "query_osm_poi" in tools
    assert "query_osm_roads" in tools
    assert "query_osm_buildings" in tools
    assert "query_osm_boundary" in tools


def test_overpass_to_geojson():
    overpass_data = json.dumps({
        "elements": [
            {"type": "node", "id": 1, "lat": 39.9, "lon": 116.4, "tags": {"name": "测试点"}},
            {"type": "way", "id": 2, "geometry": [{"lat": 39.9, "lon": 116.4}, {"lat": 39.91, "lon": 116.41}]},
        ]
    })
    geojson = _overpass_to_geojson(overpass_data)
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 2
    assert geojson["features"][0]["geometry"]["type"] == "Point"
    assert geojson["features"][1]["geometry"]["type"] == "LineString"


def test_overpass_to_geojson_empty():
    geojson = _overpass_to_geojson('{"elements": []}')
    assert geojson["features"] == []


@pytest.mark.asyncio
async def test_query_osm_poi_mock():
    registry = ToolRegistry()
    register_osm_tools(registry)

    # 经 ProviderHealthTracker 统一执行缝（#311）：Nominatim 地理编码 → Overpass 查询
    geo_data = [{"boundingbox": ["39.8","40.0","116.3","116.5"], "importance": 0.9, "lat": "39.9", "lon": "116.4"}]
    overpass_data = {"elements": [{"type": "node", "id": 1, "lat": 39.9, "lon": 116.4, "tags": {"name": "Test Restaurant"}}]}

    async def fake_tracked(name, url, params, **kwargs):
        if name == "overpass":
            return overpass_data
        return geo_data

    with patch("app.tools.osm.tracked_provider_get", side_effect=fake_tracked):
        result = await registry.dispatch("query_osm_poi", {"area": "北京", "category": "restaurant"})
        assert result["type"] == "poi_query"
        assert result["area"] == "北京"
        assert result["count"] == 1
        assert len(result["geojson"]["features"]) == 1
