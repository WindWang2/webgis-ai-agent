"""地理编码工具测试"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
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
    
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=[
        {"display_name": "北京市", "lat": "39.9042", "lon": "116.4074", "type": "city", "importance": 0.9}
    ])
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        result = await registry.dispatch("geocode", {"query": "北京"})
        assert result["count"] == 1
        assert result["results"][0]["lat"] == 39.9042


@pytest.mark.asyncio
async def test_reverse_geocode_mock():
    registry = ToolRegistry()
    register_geocoding_tools(registry)
    
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={
        "display_name": "北京市东城区天安门",
        "lat": "39.9042",
        "lon": "116.4074",
        "address": {"city": "北京"}
    })
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        result = await registry.dispatch("reverse_geocode", {"lat": 39.9042, "lon": 116.4074})
        assert "北京市" in result["name"]
