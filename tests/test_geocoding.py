"""地理编码工具测试"""
import pytest
from unittest.mock import patch
from app.core.config import settings
from app.tools.registry import ToolRegistry
from app.tools.geocoding import register_geocoding_tools


def test_geocoding_tools_registered():
    registry = ToolRegistry()
    register_geocoding_tools(registry)
    tools = registry.list_tools()
    assert "geocode" in tools
    assert "reverse_geocode" in tools


def test_geocoding_schema():
    registry = ToolRegistry()
    register_geocoding_tools(registry)
    schemas = registry.get_schemas()
    geocode_schema = next(s for s in schemas if s["function"]["name"] == "geocode")
    assert "query" in geocode_schema["function"]["parameters"]["properties"]
    assert geocode_schema["function"]["parameters"]["required"] == ["query"]


@pytest.mark.asyncio
async def test_geocode_mock():
    registry = ToolRegistry()
    register_geocoding_tools(registry)

    async def fake_tracked(name, url, params, **kwargs):
        return [
            {"display_name": "北京市", "lat": "39.9042", "lon": "116.4074", "type": "city", "importance": 0.9}
        ]

    with patch("app.tools.geocoding.tracked_provider_get", side_effect=fake_tracked):
        result = await registry.dispatch("geocode", {"query": "北京"})
        assert result["count"] == 1
        assert result["results"][0]["lat"] == 39.9042


@pytest.mark.asyncio
async def test_reverse_geocode_mock():
    registry = ToolRegistry()
    register_geocoding_tools(registry)

    async def fake_tracked(name, url, params, **kwargs):
        return {
            "display_name": "北京市东城区天安门",
            "lat": "39.9042",
            "lon": "116.4074",
            "address": {"city": "北京"},
        }

    with patch("app.tools.geocoding.tracked_provider_get", side_effect=fake_tracked):
        result = await registry.dispatch("reverse_geocode", {"lat": 39.9042, "lon": 116.4074})
        assert "北京市" in result["name"]


@pytest.mark.asyncio
async def test_reverse_geocode_hits_reverse_endpoint(monkeypatch):
    """反查端点由 NOMINATIM_URL 派生：含 /search 时替换为 /reverse（含 query 保留）。"""
    registry = ToolRegistry()
    register_geocoding_tools(registry)

    captured = {}

    async def fake_tracked(name, url, params, **kwargs):
        captured["url"] = url
        return {
            "display_name": "北京市东城区天安门",
            "lat": "39.9042",
            "lon": "116.4074",
            "address": {"city": "北京"},
        }

    monkeypatch.setattr(settings, "NOMINATIM_URL", "https://nominatim.internal/search?token=x")
    with patch("app.tools.geocoding.tracked_provider_get", side_effect=fake_tracked):
        await registry.dispatch("reverse_geocode", {"lat": 39.9042, "lon": 116.4074})

    assert captured["url"] == "https://nominatim.internal/reverse?token=x"


@pytest.mark.asyncio
async def test_reverse_geocode_url_without_search_suffix(monkeypatch):
    """NOMINATIM_URL 不含 /search（自建 Nominatim 根/子路径）时，反查必须显式
    补 /reverse，而不是让 replace 空操作后静默打到 search 端点。"""
    registry = ToolRegistry()
    register_geocoding_tools(registry)

    captured = {}

    async def fake_tracked(name, url, params, **kwargs):
        captured["url"] = url
        return {
            "display_name": "北京市东城区天安门",
            "lat": "39.9042",
            "lon": "116.4074",
            "address": {"city": "北京"},
        }

    monkeypatch.setattr(settings, "NOMINATIM_URL", "https://nominatim.internal/nominatim")
    with patch("app.tools.geocoding.tracked_provider_get", side_effect=fake_tracked):
        result = await registry.dispatch("reverse_geocode", {"lat": 39.9042, "lon": 116.4074})

    assert captured["url"] == "https://nominatim.internal/nominatim/reverse"
    assert "/search" not in captured["url"]
    assert "北京市" in result["name"]
