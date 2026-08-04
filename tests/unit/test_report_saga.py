"""Unit tests for ReportService.create_and_generate status saga (ADR-0023)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.models.api_response import ErrCode
from app.models.db_model import Conversation
from app.services.report_service import ReportService


@pytest.mark.asyncio
async def test_report_saga_invalid_format():
    svc = ReportService()
    db = AsyncMock()
    res = await svc.create_and_generate(db, "session-123", format="invalid_format")
    assert not res.success
    assert res.err_code == ErrCode.VALIDATE_ERROR
    assert "不支持的格式" in res.message


@pytest.mark.asyncio
async def test_report_saga_session_not_found():
    svc = ReportService()
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    res = await svc.create_and_generate(db, "nonexistent-session", format="pdf")
    assert not res.success
    assert res.err_code == ErrCode.NOT_FOUND
    assert res.message == "会话不存在"


@pytest.mark.asyncio
async def test_report_saga_no_messages():
    svc = ReportService()
    db = AsyncMock()

    conv = Conversation(id="sess-1", title="Test Session")
    db.get = AsyncMock(return_value=conv)

    exec_result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    exec_result.scalars.return_value = scalars_mock
    db.execute = AsyncMock(return_value=exec_result)

    res = await svc.create_and_generate(db, "sess-1", format="markdown")
    assert not res.success
    assert res.err_code == ErrCode.VALIDATE_ERROR
    assert res.message == "会话中暂无消息，无法生成报告"
