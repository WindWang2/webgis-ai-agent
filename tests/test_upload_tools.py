"""Upload tools tests — via registry pattern"""
import pytest
from unittest.mock import MagicMock, patch
from app.tools.upload_tools import register_upload_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    return ToolRegistry()


class TestUploadTools:
    def test_list_uploaded_data_empty(self, registry):
        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
        with patch("app.tools.upload_tools.SessionLocal", return_value=mock_db):
            register_upload_tools(registry)
            result = registry._tools["list_uploaded_data"]()
        assert result["success"] is True
        assert result["count"] == 0

    def test_list_uploaded_data_with_records(self, registry):
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.original_name = "test.geojson"
        mock_record.file_type = "vector"
        mock_record.format = "geojson"
        mock_record.crs = "EPSG:4326"
        mock_record.geometry_type = "Point"
        mock_record.feature_count = 10
        mock_record.bbox = [0, 0, 1, 1]
        mock_record.file_size = 1024

        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.filter.return_value = mock_db.query.return_value.order_by.return_value
        mock_db.query.return_value.order_by.return_value.limit.return_value.all.return_value = [mock_record]
        with patch("app.tools.upload_tools.SessionLocal", return_value=mock_db):
            register_upload_tools(registry)
            result = registry._tools["list_uploaded_data"](session_id="sess-1")
        assert result["success"] is True
        assert result["count"] == 1
        assert result["uploads"][0]["original_name"] == "test.geojson"

    def test_get_upload_info_not_found(self, registry):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        with patch("app.tools.upload_tools.SessionLocal", return_value=mock_db):
            register_upload_tools(registry)
            result = registry._tools["get_upload_info"](upload_id=99999)
        assert "error" in result

    def test_get_upload_info_found(self, registry):
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.original_name = "test.geojson"
        mock_record.file_type = "vector"
        mock_record.format = "geojson"
        mock_record.crs = "EPSG:4326"
        mock_record.geometry_type = "Point"
        mock_record.feature_count = 10
        mock_record.bbox = [0, 0, 1, 1]
        mock_record.file_size = 1024
        mock_record.upload_time = MagicMock()
        mock_record.upload_time.isoformat.return_value = "2026-01-01T00:00:00"
        mock_record.filename = "/data/test.geojson"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_record
        with patch("app.tools.upload_tools.SessionLocal", return_value=mock_db), \
             patch("pathlib.Path.exists", return_value=False):
            register_upload_tools(registry)
            result = registry._tools["get_upload_info"](upload_id=1)
        assert result["success"] is True
        assert result["original_name"] == "test.geojson"
