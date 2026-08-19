"""ck_report_status 与报告 saga 写入的中间态必须一致。

线上 SQLite 的 CHECK 是 pending/processing/completed/failed。
saga 若写入 generating，INSERT 会 IntegrityError，报告永远落不下来。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.exc import IntegrityError

from app.models.db_model import Conversation
from app.models.report import Report
from app.services.report_service import ReportService

# 与 app.models.report.ck_report_status / 线上 SQLite 一致。
_ALLOWED_STATUSES = {"pending", "processing", "completed", "failed"}


def _create_reports_table():
    engine = create_engine("sqlite://")
    Report.__table__.create(engine)
    return engine


def test_ck_report_status_rejects_generating():
    engine = _create_reports_table()
    with engine.begin() as conn:
        with pytest.raises(IntegrityError, match="ck_report_status"):
            conn.execute(
                insert(Report.__table__),
                {
                    "id": "r-generating",
                    "session_id": "s1",
                    "title": "成都市高等院校分布空间分析报告",
                    "format": "html",
                    "status": "generating",
                },
            )


def test_in_progress_status_is_allowed_by_check():
    engine = _create_reports_table()
    with engine.begin() as conn:
        conn.execute(
            insert(Report.__table__),
            {
                "id": "r-ok",
                "session_id": "s1",
                "title": "ok",
                "format": "html",
                "status": "processing",
            },
        )
    assert "processing" in _ALLOWED_STATUSES
    assert "generating" not in _ALLOWED_STATUSES


@pytest.mark.asyncio
async def test_create_and_generate_writes_check_legal_in_progress_status():
    conversation = MagicMock()
    conversation.title = "成都市高等院校分布空间分析报告"
    message = MagicMock()
    message.role = "user"
    message.content = "生成报告"
    message.tool_calls = None
    message.tool_result = None

    created: dict = {}
    db = AsyncMock()

    async def _get(model, pk):
        if model is Conversation:
            return conversation
        return None

    db.get = AsyncMock(side_effect=_get)
    exec_result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = [message]
    exec_result.scalars.return_value = scalars
    db.execute = AsyncMock(return_value=exec_result)

    def _add(row):
        created["report"] = row
        created["insert_status"] = row.status

    db.add = MagicMock(side_effect=_add)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.expunge = MagicMock()

    svc = ReportService()
    svc.generate_report = AsyncMock(return_value=False)

    await svc.create_and_generate(db, "sess-1", format="html", title="成都市高等院校分布空间分析报告")

    assert created["insert_status"] in _ALLOWED_STATUSES, (
        f"INSERT status {created['insert_status']!r} is rejected by ck_report_status"
    )
