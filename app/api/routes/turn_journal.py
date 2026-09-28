"""Harness Turn Journal 只读诊断面（H04 / ADR-0216）。

纪律：
- 全端点 ``require_owned_session``（与 chat /sessions/{id}/plan 同款会话
  所有权校验——账本含执行因果链，不对外开放）。
- 只读：无任何写端点；journal 的写入只在 kernel seam 旁路与 sweep 任务。
- 响应有界（MAX_JOURNAL_QUERY 行帽 + per-turn 聚合），时间一律 aware-UTC ISO。
- 账本关闭（GIS_TURN_JOURNAL=off）不影响本面——账本已落行仍可查
  （诚实诊断：报告里带 pending/sink 状态）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core.auth import require_owned_session
from app.lib.runtime.clock import iso_utc
from app.models.harness_journal import TurnEventRow
from app.models.db_model import Conversation

router = APIRouter(prefix="/harness", tags=["Harness Turn Journal"])


def _get_ledger() -> Any:
    from app.services.turn_journal.ledger import TurnEventLedger

    return TurnEventLedger()


@router.get("/turn-journal/{session_id}")
async def get_turn_journal(
    session_id: str,
    turn_id: str = Query(default="", max_length=80),
    include_tree: bool = Query(default=True),
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """会话账本报告：因果树 + 未终局 turn + 恢复建议（只读）。

    ``turn_id`` 提供时因果树只含该 turn；``include_tree=false`` 只拿
    汇总/恢复面（大 session 的轻量探针）。
    """
    from app.services.turn_journal.diagnostics import (
        build_causal_tree_sync,
        session_journal_report_sync,
    )

    ledger = _get_ledger()
    if include_tree:
        report = await asyncio.to_thread(
            session_journal_report_sync, ledger, session_id, turn_id=turn_id,
        )
    else:
        # 轻量面（P3-3）：跳过树构建；单 turn 查询时按需补一棵。
        report = await asyncio.to_thread(
            session_journal_report_sync, ledger, session_id,
            turn_id=turn_id, with_tree=False,
        )
        if turn_id:
            report["causal_tree"] = await asyncio.to_thread(
                build_causal_tree_sync, ledger, session_id, turn_id=turn_id)
    from app.services.turn_journal.sink import get_turn_journal_sink

    report["sink_pending"] = get_turn_journal_sink().pending
    return report


@router.get("/turn-journal/{session_id}/events")
async def list_turn_events(
    session_id: str,
    turn_id: str = Query(default="", max_length=80),
    kind: str = Query(default="", max_length=64),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """分页原始事件流（诊断导出用；因果序 = id 升序）。"""
    ledger = _get_ledger()
    kinds = [kind] if kind else None
    events = await ledger.list_events(
        session_id, turn_id=turn_id, kinds=kinds,
        after_id=after_id, limit=limit,
    )
    return {
        "session_id": session_id,
        "count": len(events),
        "after_id": after_id,
        "limit": limit,
        "events": events,
    }


@router.get("/turn-journal/{session_id}/stats")
async def turn_journal_stats(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """账本体量速览（retention 容量面：行数/最老行/最老 turn）。"""
    ledger = _get_ledger()

    def _sync() -> Dict[str, Any]:
        with ledger._factory() as db:  # noqa: SLF001 — 同族内部访问
            total = db.execute(
                select(func.count(TurnEventRow.id)).where(
                    TurnEventRow.session_id == session_id)
            ).scalar() or 0
            oldest = db.execute(
                select(func.min(TurnEventRow.occurred_at)).where(
                    TurnEventRow.session_id == session_id)
            ).scalar()
            compacted = db.execute(
                select(func.count(TurnEventRow.id)).where(
                    TurnEventRow.session_id == session_id,
                    TurnEventRow.status == "compacted",
                )
            ).scalar() or 0
        return {
            "session_id": session_id,
            "total_events": int(total),
            "compacted_rows": int(compacted),
            "oldest_occurred_at": iso_utc(oldest),
        }

    return await asyncio.to_thread(_sync)
