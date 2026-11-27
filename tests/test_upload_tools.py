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
        with patch("app.tools.upload_tools.db_session") as mock_ctx:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
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
        # #2156 P3：_resolve_session_id 无 runtime context 时 fail closed ——
        # 测试以已验证上下文扮演正常用户（绑定值与调用一致）。
        from app.lib.runtime.context import bind_runtime_context
        with patch("app.tools.upload_tools.db_session") as mock_ctx, \
                bind_runtime_context(session_id="sess-1"):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            register_upload_tools(registry)
            result = registry._tools["list_uploaded_data"](session_id="sess-1")
        assert result["success"] is True
        assert result["count"] == 1
        assert result["uploads"][0]["original_name"] == "test.geojson"

    def _run_list(self, registry, records, session_id="sess-1"):
        """Drive list_uploaded_data against a mocked db_session returning
        `records`; caller supplies the runtime-verified session via context."""
        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.filter.return_value = \
            mock_db.query.return_value.order_by.return_value
        mock_db.query.return_value.order_by.return_value.limit.return_value.all.return_value = records
        from app.lib.runtime.context import bind_runtime_context
        with patch("app.tools.upload_tools.db_session") as mock_ctx, \
                bind_runtime_context(session_id=session_id):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            register_upload_tools(registry)
            return registry._tools["list_uploaded_data"](session_id=session_id)

    @staticmethod
    def _mock_record(i):
        r = MagicMock()
        r.id = i
        r.original_name = f"file-{i}.geojson"
        r.file_type = "vector"
        r.format = "geojson"
        r.crs = "EPSG:4326"
        r.geometry_type = "Point"
        r.feature_count = 1
        r.bbox = [0, 0, 1, 1]
        r.file_size = 1024
        return r

    def test_list_uploaded_data_flags_truncation_beyond_limit(self, registry):
        """P3 api-contract：会话有 51+ 条上传时必须回 truncated=True 且只列 50 条，
        使 LLM 能区分「正好 50」与「还有更多」（修复前静默截断无标志）。"""
        result = self._run_list(registry, [self._mock_record(i) for i in range(51)])
        assert result["success"] is True
        assert result["count"] == 50
        assert len(result["uploads"]) == 50
        assert result["truncated"] is True

    def test_list_uploaded_data_not_truncated_at_exactly_limit(self, registry):
        """P3 api-contract：正好 50 条时 truncated 必须为 False，不能误报截断。"""
        result = self._run_list(registry, [self._mock_record(i) for i in range(50)])
        assert result["success"] is True
        assert result["count"] == 50
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_get_upload_info_not_found(self, registry):
        """通过 dispatch 调用以走完标准错误包装契约（V3.x "Exception As Thought"）。"""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        with patch("app.tools.upload_tools.db_session") as mock_ctx:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            register_upload_tools(registry)
            result = await registry.dispatch("get_upload_info", {"upload_id": 99999})
        assert result.get("success") is False
        assert result.get("code") == "NOT_FOUND"

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
        mock_record.filename = "uploads/1/test.geojson"
        mock_record.session_id = "sess-1"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_record
        # #2156 P3：无 runtime context 时 fail closed —— 测试扮演正常用户：
        # 绑定已验证会话（与记录 session_id 一致）。
        from app.lib.runtime.context import bind_runtime_context
        with patch("app.tools.upload_tools.db_session") as mock_ctx, \
             patch("pathlib.Path.exists", return_value=False), \
             bind_runtime_context(session_id="sess-1"):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            register_upload_tools(registry)
            result = registry._tools["get_upload_info"](upload_id=1, session_id="sess-1")
        assert result["success"] is True
        assert result["original_name"] == "test.geojson"

    def test_get_upload_info_cross_session_denied(self, registry):
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.session_id = "victim-session"
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_record
        with patch("app.tools.upload_tools.db_session") as mock_ctx:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            register_upload_tools(registry)
            with pytest.raises(KeyError):
                registry._tools["get_upload_info"](upload_id=1, session_id="attacker-session")
