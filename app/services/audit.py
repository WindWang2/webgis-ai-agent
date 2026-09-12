"""组织审计事件服务（ADR-0139 P7）——单一写入入口，fail-open 纪律。

- 词表：``ACTIONS``（封闭前缀：admin. / quota. / auth.）；词表外 action
  写入时按前缀归类为 ``uncategorized.<given>``（不静默丢事实，也不
  放任词表漂移）。
- **fail-open**：审计写入失败只记结构化日志（含 action/actor/trace_id
  的最小重建信息），绝不阻断主流程——安全观测永远不倒灌业务路径。
- trace_id 取 W3C traceparent 的 trace-id 分量（32 hex），与请求关联
  中间件同一键；请求对象不便传递时允许显式传入。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_model import AuditEvent

logger = logging.getLogger(__name__)

#: 封闭词表前缀（append-only）。
ACTION_PREFIXES = ("admin.", "quota.", "auth.")

ACTION_ADMIN_QUOTA_SET = "admin.quota.set"
ACTION_ADMIN_AUDIT_QUERY = "admin.audit.query"
ACTION_QUOTA_EXCEEDED = "quota.exceeded"
ACTION_AUTH_REFRESH_REUSE = "auth.refresh.reuse_detected"


def normalize_action(action: str) -> str:
    """词表外 action 归类（不丢事实、不漂移词表）。"""
    if any(action.startswith(p) for p in ACTION_PREFIXES):
        return action[:64]
    return f"uncategorized.{action}"[:64]


def trace_id_from_header(traceparent: Optional[str]) -> Optional[str]:
    """W3C traceparent 头 → trace-id 分量（00-<32hex>-<16hex>-01）。"""
    if not traceparent:
        return None
    parts = str(traceparent).strip().split("-")
    if len(parts) >= 3 and len(parts[1]) == 32:
        return parts[1].lower()
    return None


async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    actor_id: Optional[str] = None,
    org_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> None:
    """落一条审计事件（fail-open：任何失败仅结构化日志）。"""
    try:
        db.add(AuditEvent(
            org_id=(str(org_id)[:255] if org_id else None),
            actor_id=(str(actor_id)[:255] if actor_id else None),
            action=normalize_action(action),
            target_type=(str(target_type)[:40] if target_type else None),
            target_id=(str(target_id)[:255] if target_id else None),
            detail=detail,
            trace_id=(str(trace_id)[:32] if trace_id else None),
        ))
        await db.commit()
    except Exception:  # noqa: BLE001 - fail-open：审计绝不阻断主流程
        logger.warning(
            "[audit] write failed (fail-open): action=%s actor=%s org=%s "
            "target=%s trace=%s",
            action, actor_id, org_id, target_type, trace_id, exc_info=True,
        )
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass


async def query_audit(
    db: AsyncSession,
    *,
    org_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
    before_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """审计查询（admin 面；id 降序 + 有界分页）。"""
    limit = max(1, min(int(limit), 500))
    stmt = select(AuditEvent)
    conds = []
    if org_id:
        conds.append(AuditEvent.org_id == str(org_id)[:255])
    if actor_id:
        conds.append(AuditEvent.actor_id == str(actor_id)[:255])
    if action:
        conds.append(AuditEvent.action == normalize_action(action))
    if before_id:
        conds.append(AuditEvent.id < int(before_id))
    if conds:
        stmt = stmt.where(*conds)
    rows = (await db.execute(
        stmt.order_by(AuditEvent.id.desc()).limit(limit)
    )).scalars().all()
    return [
        {
            "id": r.id,
            "org_id": r.org_id,
            "actor_id": r.actor_id,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "detail": r.detail,
            "trace_id": r.trace_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
