"""OSM 工具测试"""
import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
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

    # Mock for _geocode_bbox (session.get)
    geo_data = [{"boundingbox": ["39.8","40.0","116.3","116.5"], "importance": 0.9, "lat": "39.9", "lon": "116.4"}]
    
    # Mock for Overpass query (session.post)
    overpass_data = '{"elements": [{"type": "node", "id": 1, "lat": 39.9, "lon": 116.4, "tags": {"name": "Test Restaurant"}}]}'

    mock_session = MagicMock()
    
    # Setup for _geocode_bbox
    mock_geo_resp = AsyncMock()
    mock_geo_resp.status = 200
    mock_geo_resp.json.return_value = geo_data
    mock_geo_resp.__aenter__.return_value = mock_geo_resp
    
    # Setup for _query_overpass
    mock_overpass_resp = AsyncMock()
    mock_overpass_resp.status = 200
    mock_overpass_resp.text.return_value = overpass_data
    mock_overpass_resp.__aenter__.return_value = mock_overpass_resp

    mock_session.get.return_value = mock_geo_resp
    mock_session.post.return_value = mock_overpass_resp
    mock_session.__aenter__.return_value = mock_session

    with patch("aiohttp.ClientSession", return_value=mock_session):
        result = await registry.dispatch("query_osm_poi", {"area": "北京", "category": "restaurant"})
        assert result["type"] == "poi_query"
        assert result["area"] == "北京"
        assert result["count"] == 1
        assert len(result["geojson"]["features"]) == 1
