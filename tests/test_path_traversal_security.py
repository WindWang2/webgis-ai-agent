"""Security tests: path traversal in upload_tools and nature_resources."""
import pytest
from unittest.mock import MagicMock, patch
from app.utils.path import validate_data_path


class TestValidateDataPath:
    """Core path validation utility."""

    def test_normal_path_passes(self):
        result = validate_data_path("uploads/123/file.geojson", "/data")
        assert result.startswith("/data/")

    def test_traversal_rejected(self):
        with pytest.raises(ValueError, match="非法路径"):
            validate_data_path("../../etc/passwd", "/data")

    def test_absolute_escape_rejected(self):
        with pytest.raises(ValueError, match="非法路径"):
            validate_data_path("/etc/passwd", "/data")


class TestUploadToolsPathTraversal:
    """upload_tools must validate record.filename before file access."""

    def _make_record(self, filename):
        r = MagicMock()
        r.id = 1
        r.original_name = "test.geojson"
        r.file_type = "vector"
        r.format = "geojson"
        r.crs = "EPSG:4326"
        r.geometry_type = "Point"
        r.feature_count = 5
        r.bbox = [0, 0, 1, 1]
        r.file_size = 1024
        r.upload_time = None
        r.filename = filename
        return r

    @patch("app.tools.upload_tools.db_session")
    def test_traversal_filename_raises_key_error(self, mock_db_session):
        """record.filename with ../ must raise KeyError, not read file."""
        from app.tools.upload_tools import register_upload_tools
        from app.tools.registry import ToolRegistry

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = self._make_record(
            "../../etc/passwd"
        )
        mock_db_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        registry = ToolRegistry()
        register_upload_tools(registry)
        fn = registry._tools["get_upload_info"]

        with pytest.raises(KeyError, match="文件路径校验失败"):
            fn(upload_id=1)


class TestNatureResourcesPathTraversal:
    """nature_resources delete must validate path before os.remove."""

    @patch("app.tools.nature_resources.db_session")
    def test_traversal_delete_returns_error(self, mock_db_session):
        """Deleting with ../../etc/crontab must return error dict."""
        from app.tools.nature_resources import register_nature_resource_tools
        from app.tools.registry import ToolRegistry

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.original_name = "evil"
        mock_record.filename = "../../etc/crontab"
        mock_record.geometry_type = "raster_analysis"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_record
        mock_db_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        registry = ToolRegistry()
        register_nature_resource_tools(registry)
        fn = registry._tools["manage_analysis_asset"]

        result = fn(asset_id=1, action="delete")
        assert result.get("error"), "Path traversal delete was not blocked"
        assert "校验失败" in result["error"]
