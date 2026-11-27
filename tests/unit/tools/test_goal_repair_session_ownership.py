"""G42 工具面会话归属安全回归（review P1：app/tools/goal_repair_tools.py）。

缺陷（修复前）：``webgis_goal_repair_status`` / ``webgis_goal_repair_cancel``
的归属校验写成 ``if sid and ...`` —— sid 为空（调用方未传 session_id 且无
runtime 上下文）时整段校验被跳过：

* status：任意 attempt_id 可跨会话读他会话 attempt 回执/下载 URL（泄漏）；
* cancel：可按 attempt_id 取消他会话补全 attempt（破坏性），且 ctx 以
  "unknown" 会话落账。

修复：与同族 preview/start 同一语义 —— sid 缺席即
``SOURCE_UNAVAILABLE`` fail closed（零副作用），归属者/跨会话既有语义不变。
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.services.gis_harness.goal_repair_execution.contracts import (
    JOB_KIND,
    TASK_TYPE,
)

BOB = "session-BOB-g42-own"
ALICE = "session-ALICE-g42-own"


def _tool_fn(name: str):
    from app.tools.goal_repair_tools import register_goal_repair_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_goal_repair_tools(registry)
    return registry._tools[name]


def _sweep() -> None:
    """清埋本测试域的 attempt 行（测试间/跨运行隔离）。"""
    from sqlalchemy import delete, select

    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask

    with contextlib.closing(SessionLocal()) as db:
        stale = db.execute(select(AnalysisTask.id).where(
            AnalysisTask.task_type == TASK_TYPE,
            AnalysisTask.session_id.in_((BOB, ALICE)),
        )).scalars().all()
        if stale:
            db.execute(delete(AnalysisTask).where(
                AnalysisTask.id.in_(stale)))
            db.commit()


def _insert_attempt(session_id: str, tag: str) -> str:
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask

    with contextlib.closing(SessionLocal()) as db:
        row = AnalysisTask(
            task_type=TASK_TYPE,
            job_kind=JOB_KIND,
            session_id=session_id,
            status="pending",
            display_name=f"目标补全 · {tag}",
            parameters={
                "session_id": session_id,
                "goal_id": tag,
                "plan_version": "v1",
                "entry": "ui",
            },
            idempotency_key=f"goal_repair:test-own:{tag}",
        )
        db.add(row)
        db.commit()
        return str(row.id)


def _row(attempt_id: str):
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask

    with contextlib.closing(SessionLocal()) as db:
        return db.execute(select(AnalysisTask).where(
            AnalysisTask.id == int(attempt_id))).scalar_one()


@pytest.fixture
def foreign_attempt():
    """他人会话（BOB）的活跃 attempt 行（真实测试 DB，非 mock）。"""
    from app.core.database import Base, SessionLocal, get_engine
    from app.models.db_model import AnalysisTask

    Base.metadata.create_all(bind=get_engine())
    _sweep()
    attempt_id = _insert_attempt(BOB, "bob-g1")
    yield attempt_id
    with contextlib.closing(SessionLocal()) as db:
        row = db.get(AnalysisTask, int(attempt_id))
        if row is not None:
            db.delete(row)
            db.commit()


# ── webgis_goal_repair_status ─────────────────────────────────────────────


def test_status_attempt_id_without_session_context_rejected(foreign_attempt):
    """sid 缺席 + attempt_id：必须 fail closed，不得跨会话读他会话回执。"""
    result = asyncio.run(_tool_fn("webgis_goal_repair_status")(
        attempt_id=foreign_attempt))
    assert result["success"] is False
    assert result["code"] == "SOURCE_UNAVAILABLE"


def test_status_cross_session_with_sid_rejected(foreign_attempt):
    """带 sid 的跨会话 attempt：既有 404 语义拒绝不变（零副作用）。"""
    result = asyncio.run(_tool_fn("webgis_goal_repair_status")(
        attempt_id=foreign_attempt, session_id=ALICE))
    assert result["success"] is False
    assert result["code"] == "ATTEMPT_NOT_FOUND"
    assert _row(foreign_attempt).status == "pending"


def test_status_owner_session_still_allowed(foreign_attempt):
    """归属者带 sid：status 正常放行（修复不过度阻断合法轮询）。"""
    result = asyncio.run(_tool_fn("webgis_goal_repair_status")(
        attempt_id=foreign_attempt, session_id=BOB))
    assert result["success"] is True
    assert result["attempt_id"] == foreign_attempt
    assert result["session_id"] == BOB


# ── webgis_goal_repair_cancel ─────────────────────────────────────────────


def test_cancel_attempt_id_without_session_context_rejected(foreign_attempt):
    """sid 缺席 + attempt_id：取消必须 fail closed 且对他会话 attempt 零副作用。"""
    result = asyncio.run(_tool_fn("webgis_goal_repair_cancel")(
        attempt_id=foreign_attempt))
    assert result["success"] is False
    assert result["code"] == "SOURCE_UNAVAILABLE"
    row = _row(foreign_attempt)
    assert row.status == "pending"
    assert row.cancel_requested_at is None


def test_cancel_cross_session_with_sid_rejected(foreign_attempt):
    """带 sid 的跨会话取消：拒绝且不先执行后拒绝（行保持活跃）。"""
    result = asyncio.run(_tool_fn("webgis_goal_repair_cancel")(
        attempt_id=foreign_attempt, session_id=ALICE))
    assert result["success"] is False
    assert result["code"] == "ATTEMPT_NOT_FOUND"
    row = _row(foreign_attempt)
    assert row.status == "pending"
    assert row.cancel_requested_at is None


def test_cancel_owner_session_still_allowed():
    """归属者带 sid：取消本会话 attempt 正常放行并落终态。"""
    from app.core.database import Base, SessionLocal, get_engine
    from app.models.db_model import AnalysisTask

    Base.metadata.create_all(bind=get_engine())
    _sweep()
    attempt_id = _insert_attempt(ALICE, "alice-own")
    try:
        result = asyncio.run(_tool_fn("webgis_goal_repair_cancel")(
            attempt_id=attempt_id, session_id=ALICE))
        assert result["success"] is True
        assert result["cancel_requested"] is True
        assert _row(attempt_id).status == "cancelled"
    finally:
        with contextlib.closing(SessionLocal()) as db:
            row = db.get(AnalysisTask, int(attempt_id))
            if row is not None:
                db.delete(row)
                db.commit()
