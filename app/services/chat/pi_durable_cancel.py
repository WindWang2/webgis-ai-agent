"""Pi turn 的 durable job 撤销（G01-PI-NATIVE-RUNTIME / PI-05，ADR-0286 D5）。

M0 审计缺口 G8：dispatch 建 ``JobOrigin`` 关联（session/turn/tool_call）+
receipt 逐出披露已有，但「turn 取消 → 该 turn 名下排队中 durable job 撤销」
无实现无测试 —— 晚到的后台计算可能对着已取消的 turn 继续跑。

本模块只做**桥接调用**（jobs 服务的既有 API 是唯一事实源，不改它）：
按 ``session_id + turn_id`` 查非终态 job（``ACTIVE_STATUSES``），逐个
``DurableJobStore.request_cancel``；never-raise（撤销失败只记日志与计数，
绝不影响 settle 终态）；跨 turn/跨 session 的 job 不受影响。

撤销语义边界（有意）：仅非 clean 终态（cancelled/aborted/failed/
interrupted）触发；clean/refused 的后台产物按 SSE ``background_job_ids``
披露继续（前端期望完成）。job 级「late success 不能覆盖 cancelled」由
jobs lifecycle 的终态不可迁移规则保证（本模块不重复实现）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from sqlalchemy import and_, select

from app.models.db_model import AnalysisTask
from app.services.jobs.lifecycle import ACTIVE_STATUSES
from app.services.jobs.store import DurableJobStore

logger = logging.getLogger(__name__)

#: 单 turn 撤销扫描上限（防御性；正常 turn 的 durable job 数远低于此）。
_MAX_JOBS_PER_TURN = 64


async def revoke_turn_durable_jobs(
    session_id: str,
    turn_id: str,
    *,
    reason: str = "",
    db: Any = None,
) -> Dict[str, Any]:
    """撤销 ``turn_id`` 名下非终态 durable job（best-effort，never-raise）。

    返回有界摘要：``{"requested", "already_terminal", "missing",
    "skipped_other_turn", "error"}``（观测/测试用；无 payload、无凭证）。
    """
    summary: Dict[str, Any] = {
        "requested": 0,
        "already_terminal": 0,
        "missing": 0,
        "skipped_other_turn": 0,
        "error": "",
    }
    if not session_id or not turn_id:
        return summary
    import contextlib

    db_is_owned = db is None
    try:
        if db is not None:
            cm = contextlib.nullcontext(db)
        else:
            from app.core.database import AsyncSessionLocal

            cm = AsyncSessionLocal()
        async with cm as session_:
            stmt = (
                select(AnalysisTask)
                .where(
                    and_(
                        AnalysisTask.session_id == session_id,
                        AnalysisTask.turn_id == turn_id,
                    )
                )
                .order_by(AnalysisTask.id.desc())
                .limit(_MAX_JOBS_PER_TURN * 2)
            )
            rows = list((await session_.execute(stmt)).scalars().all())
            active_statuses = {s.value for s in ACTIVE_STATUSES}
            targets = []
            for row in rows:
                if row.turn_id != turn_id or row.session_id != session_id:
                    summary["skipped_other_turn"] += 1  # 防御：查询条件已限定
                    continue
                if str(row.status) not in active_statuses:
                    summary["already_terminal"] += 1
                    continue
                targets.append(row.id)
                if len(targets) >= _MAX_JOBS_PER_TURN:
                    break
            for job_id in targets:
                try:
                    ok, _status = await DurableJobStore.request_cancel(session_, job_id)
                    if ok:
                        summary["requested"] += 1
                    else:
                        summary["already_terminal"] += 1
                except Exception:  # noqa: BLE001 — 单 job 失败不阻断其余
                    summary["missing"] += 1
            if db_is_owned:
                await session_.commit()
    except Exception as exc:  # noqa: BLE001 — 撤销绝不影响 settle 终态
        summary["error"] = type(exc).__name__
        logger.warning(
            "[PiDurableCancel] revoke failed session=%s turn=%s: %s",
            session_id, turn_id, exc,
        )
        return summary
    if summary["requested"]:
        logger.info(
            "[PiDurableCancel] revoked %d durable job(s) for cancelled turn "
            "session=%s turn=%s reason=%s",
            summary["requested"], session_id, turn_id, reason or "turn_not_clean",
        )
    return summary
