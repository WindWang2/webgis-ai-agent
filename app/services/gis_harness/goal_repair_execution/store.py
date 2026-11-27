"""G42 WP03 —— 修复 attempt 持久化（durable job 行 + 单 claim + 读模型）。

语义：

- **attempt = AnalysisTask 行**（task_type ``goal_repair_attempt``，kind
  ``goal_repair``）—— 幂等键 ``goal_repair:{session}:{goal}:{plan_version}``
  兑现跨 API worker 的单 claim（双击/重发 = 同一活跃 attempt，R11）；
- **显式重试 = 新 attempt**：同 goal 上一 attempt 已终态后，新提交以
  ``:r{n}`` 后缀建新键，``parent_attempt_id`` 保留父链与原失败事实；
- **完整快照落 dispatch_spec**（重跑描述符；绝不经 API 返回）；有界视图
  从行 + result_summary 构造（浏览器刷新后的回看权威）；
- 本模块是执行**读模型**，不是新调度引擎 —— 执行体由 runner 经
  ``run_async_job`` 驱动（G23/G30 同族）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .contracts import (
    ATTEMPT_SCHEMA,
    JOB_KIND,
    TASK_TYPE,
    GoalRepairErrorCode,
    ERROR_ATTEMPT_NOT_FOUND,
)

logger = logging.getLogger(__name__)


def attempt_idempotency_key(
    session_id: str, goal_id: str, plan_version: str, *, retry_no: int = 0,
) -> str:
    key = f"goal_repair:{session_id}:{goal_id}:{plan_version}"
    if retry_no > 0:
        key = f"{key}:r{retry_no}"
    return key[:255]


def _row_to_view(row: Any) -> Dict[str, Any]:
    """AnalysisTask 行 → 有界 attempt 视图（零副作用；不含 dispatch_spec）。"""
    from app.services.jobs.lifecycle import (
        coerce_status,
        is_active,
        is_cancellable,
    )

    status = coerce_status(row.status or "pending")
    params = row.parameters if isinstance(row.parameters, dict) else {}
    summary = row.result_summary if isinstance(row.result_summary, dict) else {}
    view: Dict[str, Any] = {
        "schema_version": ATTEMPT_SCHEMA,
        "attempt_id": str(row.id),
        "status": status.value,
        "active": is_active(status),
        "cancellable": is_cancellable(status),
        "session_id": str(row.session_id or params.get("session_id") or ""),
        "goal_id": str(params.get("goal_id") or "")[:128],
        "plan_version": str(params.get("plan_version") or "")[:16],
        "entry": str(params.get("entry") or "")[:12],
        "parent_attempt_id": str(params.get("parent_attempt_id") or "")[:16],
        "turn_id": str(params.get("turn_id") or "")[:48],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "heartbeat_at":
            row.heartbeat_at.isoformat() if row.heartbeat_at else None,
        "completed_at":
            row.completed_at.isoformat() if row.completed_at else None,
        "error": ((row.error_trace or "").splitlines()[-1][:200]
                  if row.error_trace else None),
        "actions": list(summary.get("actions") or [])[:8],
        "outcome": str(summary.get("outcome") or "")[:24] or None,
        "final_verdict_before":
            str(summary.get("final_verdict_before") or "")[:24] or None,
        "final_verdict_after":
            str(summary.get("final_verdict_after") or "")[:24] or None,
        "final_changed": bool(summary.get("final_changed")),
        "blockers": [str(b)[:64] for b in (summary.get("blockers") or [])[:6]],
    }
    return view


def _sync_read_attempt(attempt_id: int) -> Optional[Any]:
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select
    import contextlib

    with contextlib.closing(SessionLocal()) as db:
        return db.execute(
            select(AnalysisTask).where(AnalysisTask.id == attempt_id)
        ).scalar_one_or_none()


def get_attempt_view(attempt_id: int) -> Optional[Dict[str, Any]]:
    row = _sync_read_attempt(attempt_id)
    if row is None:
        return None
    return _row_to_view(row)


def find_attempts(
    session_id: str, goal_id: str = "", *, active_only: bool = False,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    """会话（+goal）的 attempt 视图列表（新→旧）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select
    import contextlib

    with contextlib.closing(SessionLocal()) as db:
        stmt = select(AnalysisTask).where(
            AnalysisTask.task_type == TASK_TYPE,
            AnalysisTask.session_id == session_id,
        )
        if goal_id:
            # goal 过滤下推 SQL：先 limit 再 Python 过滤时，同会话更新的
            # 其它 goal attempt 会把本 goal 的活跃/历史行挤出 → 单 claim
            # 去重与 no_progress 防护漏判。
            stmt = stmt.where(
                AnalysisTask.parameters["goal_id"].as_string() == goal_id)
        stmt = stmt.order_by(
            AnalysisTask.created_at.desc()).limit(max(1, limit))
        rows = db.execute(stmt).scalars().all()
    views = []
    for row in rows:
        params = row.parameters if isinstance(row.parameters, dict) else {}
        if goal_id and str(params.get("goal_id") or "") != goal_id:
            continue
        view = _row_to_view(row)
        if active_only and not view.get("active"):
            continue
        views.append(view)
    return views


def find_active_attempt(
    session_id: str, goal_id: str,
) -> Optional[Dict[str, Any]]:
    """同 goal 的活跃 attempt（单执行语义；无 = None）。"""
    for view in find_attempts(session_id, goal_id, active_only=True, limit=8):
        return view
    return None


def _next_retry_no(session_id: str, goal_id: str, plan_version: str) -> int:
    """显式重试序号（同键既有**终态**行数；上限钳制）。

    只数终态行：并发双提交窗口内先建行仍活跃 → 后到者计算同一
    retry_no → 落同一幂等键（DB 兑现单 claim）；先建行已终态 → 后到者
    是合法显式重试（新 attempt，父链保留）。
    """
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import func, or_, select
    import contextlib

    base = attempt_idempotency_key(
        session_id, goal_id, plan_version)
    # LIKE 通配符转义（review P3：session/goal id 含 %/_ 时防过计）。
    escaped = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    terminal = ("completed", "failed", "cancelled", "stale")
    with contextlib.closing(SessionLocal()) as db:
        count = db.execute(
            select(func.count()).select_from(AnalysisTask).where(
                AnalysisTask.task_type == TASK_TYPE,
                # 精确 base 或显式重试键（base:r{n}）——裸前缀 LIKE 会把
                # plan_version/含冒号 goal_id 的前缀邻居算进来（retry_no
                # 虚高 → 新键绕过单 claim）。
                or_(
                    AnalysisTask.idempotency_key == base,
                    AnalysisTask.idempotency_key.like(
                        f"{escaped}:r%", escape="\\"),
                ),
                AnalysisTask.status.in_(terminal),
            )
        ).scalar_one()
    return min(int(count or 0), 999)


def submit_attempt_row(
    *,
    session_id: str,
    goal_id: str,
    plan_version: str,
    plan: Dict[str, Any],
    bindings: List[Dict[str, Any]],
    prerequisites: Dict[str, Any],
    selected_action_ids: List[str],
    acknowledged_losses: List[str],
    owner: Dict[str, Any],
    parent_attempt_id: str = "",
) -> Dict[str, Any]:
    """幂等创建 attempt 行；返回 ``(attempt view, reused, job_id)`` 装配面。

    活跃去重前置检查由调用方（service.start）完成；此处幂等键兜底并发
    竞态（两个 worker 同时通过前置检查 → DB 唯一键只留一行，后者复用）。
    """
    from app.services.data_operations.runner import submit_async_job

    retry_no = _next_retry_no(session_id, goal_id, plan_version)
    key = attempt_idempotency_key(
        session_id, goal_id, plan_version, retry_no=retry_no)
    params = {
        "session_id": session_id[:128],
        "goal_id": goal_id[:128],
        "plan_version": plan_version[:16],
        "entry": str(owner.get("entry") or "")[:12],
        "parent_attempt_id": str(parent_attempt_id)[:16],
        "turn_id": str(owner.get("turn_id") or "")[:48],
        "selected_action_ids": [
            str(a)[:16] for a in selected_action_ids[:8]],
    }
    job_id, _status, reused = submit_async_job(
        task_type=TASK_TYPE,
        display_name=f"目标补全 · {goal_id[:40]}",
        params=params,
        idempotency_key=key,
        kind=JOB_KIND,
        owner_id=owner.get("user_id") or None,
        owner_token=owner.get("owner_token") or None,
        org_id=owner.get("org_id"),
        session_id=session_id,
        project_id=owner.get("project_id") or None,
    )
    if not reused:
        _write_dispatch_spec(
            job_id, plan=plan, bindings=bindings,
            prerequisites=prerequisites, owner=owner,
            selected_action_ids=selected_action_ids,
            acknowledged_losses=acknowledged_losses)
    view = get_attempt_view(job_id) or {
        "attempt_id": str(job_id), "status": "pending"}
    view["idempotent_reuse"] = bool(reused)
    return {"view": view, "reused": bool(reused), "job_id": job_id}


def _write_dispatch_spec(
    job_id: int, *, plan: Dict[str, Any], bindings: List[Dict[str, Any]],
    prerequisites: Dict[str, Any], owner: Dict[str, Any],
    selected_action_ids: List[str], acknowledged_losses: List[str],
) -> None:
    """完整执行快照落 dispatch_spec（重跑描述符；绝不经 API 返回）。

    红线（review P1）：**不落原始 owner_token** —— 平台不变量是 owner token
    仅以 SHA-256 摘要入库（OwnerTokenColumn）；G30 skill job 的 dispatch_spec
    同样刻意省略。持久行只留 user_id/org/project 等非凭据身份；对账需要
    actor 时由服务层按 Conversation 行重新权威派生。
    """
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select
    import contextlib

    safe_owner = {k: (v if v is not None else "")
                  for k, v in owner.items() if k != "owner_token"}

    with contextlib.closing(SessionLocal()) as db:
        row = db.execute(
            select(AnalysisTask).where(AnalysisTask.id == job_id)
        ).scalar_one_or_none()
        if row is None:
            return
        row.dispatch_spec = {
            "plan": plan,
            "bindings": bindings,
            "prerequisites": prerequisites,
            "owner": safe_owner,
            "selected_action_ids": [
                str(a)[:16] for a in selected_action_ids[:8]],
            "acknowledged_losses": [
                str(a)[:48] for a in acknowledged_losses[:8]],
        }
        db.commit()


def read_dispatch_spec(job_id: int) -> Optional[Dict[str, Any]]:
    row = _sync_read_attempt(job_id)
    if row is None:
        return None
    spec = row.dispatch_spec if isinstance(row.dispatch_spec, dict) else None
    return spec


def read_summary(job_id: int) -> Dict[str, Any]:
    row = _sync_read_attempt(job_id)
    if row is None:
        return {}
    return row.result_summary if isinstance(row.result_summary, dict) else {}


def write_summary_patch(job_id: int, patch: Dict[str, Any]) -> bool:
    """终态后的小幅对账补写（waiting_delivery 惰性收口；不改状态机）。

    必须构造**新 dict** 再赋值 —— JSON 列同对象就地修改 + 原引用回赋
    不会触发 SQLAlchemy 变更检测（历史无净变化 → UPDATE 缺席）。
    """
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select
    import contextlib

    with contextlib.closing(SessionLocal()) as db:
        row = db.execute(
            select(AnalysisTask).where(AnalysisTask.id == job_id)
        ).scalar_one_or_none()
        if row is None:
            return False
        summary = dict(row.result_summary) if isinstance(
            row.result_summary, dict) else {}
        summary.update({str(k): v for k, v in patch.items()})
        row.result_summary = summary
        db.commit()
        return True


def ensure_attempt(attempt_id: Any) -> Dict[str, Any]:
    """attempt_id → 视图（typed NOT_FOUND）。"""
    try:
        aid = int(attempt_id)
    except (TypeError, ValueError):
        raise GoalRepairErrorCode(
            ERROR_ATTEMPT_NOT_FOUND, f"attempt id 非法: {attempt_id!r}")
    view = get_attempt_view(aid)
    if view is None:
        raise GoalRepairErrorCode(
            ERROR_ATTEMPT_NOT_FOUND, f"attempt {attempt_id} 不存在")
    return view


__all__ = [
    "attempt_idempotency_key", "submit_attempt_row", "get_attempt_view",
    "find_attempts", "find_active_attempt", "read_dispatch_spec",
    "read_summary", "write_summary_patch", "ensure_attempt",
]
