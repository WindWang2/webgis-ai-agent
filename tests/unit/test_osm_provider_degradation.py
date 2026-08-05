"""OSM 家族降级契约测试（issue #310）。

执行缝（tracked_provider_get）返回 {"error": ...}（熔断/限流/故障）时，各调用方
必须保持既有契约：osm 空 FC/None、geocoding RuntimeError、viewport None。
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.tools.osm import _query_overpass, _geocode_bbox, _nominatim_search_poi
from app.tools.registry import ToolRegistry
from app.tools.geocoding import register_geocoding_tools
from app.services.viewport_naming import _fetch_nominatim

# osm.py / geocoding.py 在模块级 import tracked_provider_get → patch 模块内引用；
# viewport_naming 在函数内 import → patch provider_health 源。


@pytest.mark.asyncio
async def test_query_overpass_degraded():
    """熔断时 _query_overpass 返回空 FeatureCollection + error（现有契约）。"""
    with patch("app.tools.osm.tracked_provider_get", new=AsyncMock(return_value={"error": "Overpass 暂时不可用（频率限制或服务故障），请稍后重试"})):
        res = await _query_overpass("node(1);")
    assert res["type"] == "FeatureCollection"
    assert res["features"] == []
    assert "error" in res


@pytest.mark.asyncio
async def test_query_overpass_success_parses_dict():
    """seam 返回已解析 dict 时，_overpass_to_geojson 正确转 GeoJSON。"""
    with patch(
        "app.tools.osm.tracked_provider_get",
        new=AsyncMock(return_value={"elements": [{"type": "node", "lat": 1.0, "lon": 2.0, "tags": {"name": "X"}}]}),
    ):
        res = await _query_overpass("node(1);")
    assert res["type"] == "FeatureCollection"
    assert len(res["features"]) == 1
    assert res["features"][0]["geometry"]["coordinates"] == [2.0, 1.0]


@pytest.mark.asyncio
async def test_geocode_bbox_degraded_returns_none():
    with patch("app.tools.osm.tracked_provider_get", new=AsyncMock(return_value={"error": "x"})):
        assert await _geocode_bbox("Beijing") is None


@pytest.mark.asyncio
async def test_nominatim_search_poi_degraded_returns_empty_fc():
    with patch("app.tools.osm.tracked_provider_get", new=AsyncMock(return_value={"error": "x"})):
        res = await _nominatim_search_poi("restaurant", "10,20,30,40", 5)
    assert res == {"type": "FeatureCollection", "features": []}


def _geocoding_tools():
    reg = ToolRegistry()
    register_geocoding_tools(reg)
    return reg._tools  # registry 存的是原始 callable（绕过 dispatch 的引用解析）


@pytest.mark.asyncio
async def test_geocode_degraded_raises_runtime_error():
    tools = _geocoding_tools()
    with patch("app.tools.geocoding.tracked_provider_get", new=AsyncMock(return_value={"error": "Nominatim 暂时不可用"})):
        with pytest.raises(RuntimeError, match="Nominatim"):
            await tools["geocode"]("Beijing")


@pytest.mark.asyncio
async def test_geocode_success():
    tools = _geocoding_tools()
    with patch(
        "app.tools.geocoding.tracked_provider_get",
        new=AsyncMock(return_value=[{"display_name": "Beijing, China", "lat": "39.9", "lon": "116.4", "type": "city", "importance": "0.8"}]),
    ):
        res = await tools["geocode"]("Beijing")
    assert res["count"] == 1
    assert res["results"][0]["name"] == "Beijing, China"


@pytest.mark.asyncio
async def test_reverse_geocode_degraded_raises_runtime_error():
    tools = _geocoding_tools()
    with patch("app.tools.geocoding.tracked_provider_get", new=AsyncMock(return_value={"error": "Nominatim 暂时不可用"})):
        with pytest.raises(RuntimeError, match="Nominatim"):
            await tools["reverse_geocode"](39.9, 116.4)


@pytest.mark.asyncio
async def test_reverse_geocode_success():
    tools = _geocoding_tools()
    with patch(
        "app.tools.geocoding.tracked_provider_get",
        new=AsyncMock(return_value={"display_name": "Beijing", "lat": "39.9", "lon": "116.4", "address": {"city": "Beijing"}}),
    ):
        res = await tools["reverse_geocode"](39.9, 116.4)
    assert res["name"] == "Beijing"
    assert res["address"]["city"] == "Beijing"


@pytest.mark.asyncio
async def test_viewport_fetch_nominatim_degraded_returns_none():
    with patch("app.services.provider_health.tracked_provider_get", new=AsyncMock(return_value={"error": "x"})):
        assert await _fetch_nominatim(116.4, 39.9) is None


@pytest.mark.asyncio
async def test_viewport_fetch_nominatim_success():
    with patch(
        "app.services.provider_health.tracked_provider_get",
        new=AsyncMock(return_value={"display_name": "Beijing, China", "address": {}}),
    ):
        label = await _fetch_nominatim(116.4, 39.9)
    assert label == "Beijing, China"
