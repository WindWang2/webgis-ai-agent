"""Tests for standardizing tool error responses."""
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.tools.osm import register_osm_tools
from app.tools.geocoding import register_geocoding_tools
from app.tools.upload_tools import register_upload_tools

@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_spatial_tools(reg)
    register_osm_tools(reg)
    register_geocoding_tools(reg)
    register_upload_tools(reg)
    return reg

async def test_buffer_analysis_standard_error_on_failure(registry):
    # Pass invalid geojson to force failure
    result = await registry.dispatch("buffer_analysis", {"geojson": "invalid", "distance": 100})
    
    assert result.get("success") is False
    assert "code" in result
    assert "message" in result

async def test_query_osm_poi_standard_error_on_geocoding_failure(registry):
    # Mock _geocode_bbox to return None
    with patch("app.tools.osm._geocode_bbox", return_value=None):
        result = await registry.dispatch("query_osm_poi", {"area": "nonexistent_place"})
        
        assert result.get("success") is False
        assert "code" in result
        assert "message" in result

async def test_geocode_standard_error_on_api_failure(registry):
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.__aenter__.return_value = mock_resp
    
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp
    mock_session.__aenter__.return_value = mock_session

    with patch("aiohttp.ClientSession", return_value=mock_session):
        result = await registry.dispatch("geocode", {"query": "北京"})
        
        assert result.get("success") is False
        assert "code" in result
        assert "message" in result

async def test_get_upload_info_standard_error_on_missing_record(registry):
    # Mock db_session to return empty query result
    with patch("app.tools.upload_tools.db_session") as mock_db_sess:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db_sess.return_value.__enter__.return_value = mock_db
        
        result = await registry.dispatch("get_upload_info", {"upload_id": 999})
        
        assert result.get("success") is False
        assert "code" in result
        assert "message" in result
