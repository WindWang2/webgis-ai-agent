"""统一异常处理模块测试"""
import pytest
from unittest.mock import MagicMock

from app.core.exception import (
    sanitize_traceback,
    format_error_response,
    global_exception_handler,
    PRODUCTION_ERROR_MESSAGE,
)


class TestSanitizeTraceback:
    def test_removes_project_root(self):
        from app.core.exception import PRODUCTION_ERROR_MESSAGE
        # Use actual project root for realistic test
        from pathlib import Path
        project_root = str(Path(__file__).parent.parent)
        tb = f"File {project_root}/app/main.py, line 42"
        result = sanitize_traceback(tb)
        assert project_root not in result
        assert "<REDACTED>" in result

    def test_redacts_file_path(self):
        tb = 'File "/path/to/file.py", line 123'
        result = sanitize_traceback(tb)
        assert "/path/to/file.py" not in result
        assert "<REDACTED_PATH>" in result
        assert "line 123" in result

    def test_empty_string(self):
        assert sanitize_traceback("") == ""


class TestFormatErrorResponse:
    def test_production_mode(self):
        request = MagicMock()
        request.url.path = "/api/test"
        request.method = "GET"
        exc = ValueError("test error")
        resp = format_error_response(exc, request, include_details=False)
        assert resp["code"] == "SERVER_ERROR"
        assert resp["success"] is False
        assert resp["message"] == PRODUCTION_ERROR_MESSAGE
        assert resp["data"] is None
        assert "error_type" not in resp

    def test_development_mode(self):
        request = MagicMock()
        request.url.path = "/api/test"
        request.method = "POST"
        exc = RuntimeError("dev error")
        resp = format_error_response(exc, request, include_details=True)
        assert resp["code"] == "SERVER_ERROR"
        assert resp["success"] is False
        assert resp["error_type"] == "RuntimeError"
        assert resp["error_detail"] == "dev error"
        assert resp["path"] == "/api/test"
        assert resp["method"] == "POST"
        assert "traceback" in resp

    def test_error_message_truncation(self):
        request = MagicMock()
        request.url.path = "/"
        request.method = "GET"
        long_msg = "x" * 500
        exc = ValueError(long_msg)
        resp = format_error_response(exc, request, include_details=True)
        assert len(resp["error_detail"]) <= 200
