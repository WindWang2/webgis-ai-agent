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


@pytest.mark.asyncio
async def test_query_osm_poi_enforces_limit_contract():
    """limit 必须同时作用于 Overpass 查询与最终返回（大 bbox 否则返回数万要素）。"""
    registry = ToolRegistry()
    register_osm_tools(registry)

    geo_data = [{"boundingbox": ["39.8", "40.0", "116.3", "116.5"], "importance": 0.9, "lat": "39.9", "lon": "116.4"}]
    # Overpass 返回 1000 个点（模拟 limit 失效/服务端语义差异）
    overpass_data = {"elements": [
        {"type": "node", "id": i, "lat": 39.9, "lon": 116.4, "tags": {"name": f"p{i}"}}
        for i in range(1000)
    ]}

    sent_queries = []

    async def fake_tracked(name, url, params, **kwargs):
        if name == "overpass":
            sent_queries.append(kwargs["data"]["data"])
            return overpass_data
        return geo_data

    with patch("app.tools.osm.tracked_provider_get", side_effect=fake_tracked):
        result = await registry.dispatch("query_osm_poi", {"area": "北京", "limit": 50})

    assert result["count"] == 50
    assert len(result["geojson"]["features"]) == 50
    # Overpass 查询里必须带输出上限
    assert "out body geom 50;" in sent_queries[0]


@pytest.mark.asyncio
async def test_query_osm_roads_enforces_limit_contract():
    """query_osm_roads 同样截断（大区域 + 低等级路极易超量）。"""
    registry = ToolRegistry()
    register_osm_tools(registry)

    geo_data = [{"boundingbox": ["39.8", "40.0", "116.3", "116.5"], "importance": 0.9, "lat": "39.9", "lon": "116.4"}]
    overpass_data = {"elements": [
        {"type": "way", "id": i, "geometry": [{"lat": 39.9, "lon": 116.4}, {"lat": 39.91, "lon": 116.41}],
         "tags": {"highway": "residential"}}
        for i in range(500)
    ]}

    async def fake_tracked(name, url, params, **kwargs):
        if name == "overpass":
            return overpass_data
        return geo_data

    with patch("app.tools.osm.tracked_provider_get", side_effect=fake_tracked):
        result = await registry.dispatch("query_osm_roads", {"area": "北京", "limit": 100})

    assert result["count"] == 100
    assert len(result["geojson"]["features"]) == 100


@pytest.mark.asyncio
async def test_query_osm_poi_fallback_empty_search_terms():
    registry = ToolRegistry()
    register_osm_tools(registry)

    geo_data = [{"boundingbox": ["39.8", "40.0", "116.3", "116.5"], "importance": 0.9, "lat": "39.9", "lon": "116.4"}]
    # Overpass returns empty features, triggering fallback to Nominatim search
    overpass_empty = {"elements": []}
    nom_poi_data = [{"lat": "39.91", "lon": "116.41", "name": "Custom Place", "type": "custom"}]

    async def fake_tracked(name, url, params, **kwargs):
        if name == "overpass":
            return overpass_empty
        if name == "nominatim":
            if "q" in params and params["q"] in ("北京", "custom_cat"):
                if params["q"] == "custom_cat":
                    return nom_poi_data
                return geo_data
        return []

    with patch("app.tools.osm.tracked_provider_get", side_effect=fake_tracked):
        result = await registry.dispatch("query_osm_poi", {"area": "北京", "category": "custom_cat"})

    assert result["count"] == 1
    assert result["geojson"]["features"][0]["properties"]["name"] == "Custom Place"

