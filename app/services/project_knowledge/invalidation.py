"""ProjectKnowledge 失效接线：ref_lifecycle 观察者 + 显式失效入口。

纪律（与 ``ref_lifecycle`` R6 相同）：观察者**绝不影响失效权威** ——
hook 内任何异常只记日志；正确性从不依赖 hook 被调用（检索期 lazy
liveness 复核兜底）。注册幂等。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Iterator, List

from sqlalchemy import and_, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.project_knowledge import ProjectKnowledgeEntry
from app.services.project_knowledge import store as ks
from app.services.project_knowledge.contract import (
    AS_SESSION_REF,
    ST_ACTIVE,
)

logger = logging.getLogger(__name__)

_hook_registered = False
_hook_register_lock = threading.Lock()

#: ref-tag 兜底扫描的分页大小（有界内存）。
_REF_SCAN_PAGE = 200
#: 分页上限：org 级 active 行的诚实扫描上界（25 页 × 200 = 5000 行）。
#: 触顶记 warning 并停止 —— 绝不静默截断（#1740 修的正是静默漏降级）。
_MAX_REF_SCAN_PAGES = 25


def _matching_active_rows(
    db: Session, *, org_id: str, ref_id: str, session_id: str
) -> Iterator[ProjectKnowledgeEntry]:
    """Bounded candidate scan followed by exact authority and session checks.

    Legacy entries without a session remain subject to lazy liveness checks;
    a session event must never invalidate a different session's identical ref.
    The explicit org-only maintenance API still supports legacy entries.
    """
    direct = and_(ProjectKnowledgeEntry.authority_store == AS_SESSION_REF,
                  ProjectKnowledgeEntry.authority_id == ref_id)
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        tag = {"authority": AS_SESSION_REF, "id": ref_id}
        if session_id:
            tag["session_id"] = session_id
            direct = and_(direct,
                cast(ProjectKnowledgeEntry.evidence, JSONB)["session_id"].astext == session_id)
        tagged = cast(ProjectKnowledgeEntry.refs, JSONB).contains([tag])
    else:
        # SQLite JSON predicates preserve exact values (no substring scan of a
        # serialized blob, and no entire-org fetch/match loop in Python).
        tags = func.json_each(ProjectKnowledgeEntry.refs).table_valued("key", "value")
        match = [func.json_extract(tags.c.value, "$.authority") == AS_SESSION_REF,
                 func.json_extract(tags.c.value, "$.id") == ref_id]
        if session_id:
            match.append(func.json_extract(tags.c.value, "$.session_id") == session_id)
            direct = and_(direct, func.json_extract(
                ProjectKnowledgeEntry.evidence, "$.session_id") == session_id)
        tagged = exists(select(1).select_from(tags).where(*match))
    after = ""
    for _page in range(_MAX_REF_SCAN_PAGES):
        stmt = select(ProjectKnowledgeEntry).where(
            ProjectKnowledgeEntry.status == ST_ACTIVE,
            or_(direct, tagged),
        ).order_by(ProjectKnowledgeEntry.id).limit(_REF_SCAN_PAGE)
        if org_id:
            stmt = stmt.where(ProjectKnowledgeEntry.org_id == org_id)
        if after:
            stmt = stmt.where(ProjectKnowledgeEntry.id > after)
        rows: List[ProjectKnowledgeEntry] = list(db.execute(stmt).scalars().all())
        for row in rows:
            direct_row = (row.authority_store == AS_SESSION_REF and row.authority_id == ref_id
                      and (not session_id or (row.evidence or {}).get("session_id") == session_id))
            tagged_row = any(
                isinstance(tag, dict) and tag.get("authority") == AS_SESSION_REF
                and tag.get("id") == ref_id
                and (not session_id or tag.get("session_id") == session_id)
                for tag in (row.refs or [])
            )
            if direct_row or tagged_row:
                yield row
        if len(rows) < _REF_SCAN_PAGE:
            return
        after = str(rows[-1].id)
    logger.warning(
        "[ProjectKnowledge] ref scan hit cap (%d pages x %d rows) org=%s session=%s ref=%s",
        _MAX_REF_SCAN_PAGES, _REF_SCAN_PAGE, org_id, session_id, ref_id,
    )


def handle_ref_invalidation_for_session(
    db: Session, *, org_id: str = "", ref_id: str, session_id: str = ""
) -> int:
    """Demote exact session references; caller commits.

    The lifecycle hook always supplies session_id. Org-only scope is reserved
    for explicit maintenance of old projections whose session was not recorded.
    Direct and tagged dependencies are both invalidated in the same operation.
    """
    if not ref_id or not (org_id or session_id):
        return 0
    return ks.demote_entries(
        db, list(_matching_active_rows(db, org_id=org_id, ref_id=ref_id, session_id=session_id)),
        status="stale", rule="head_changed",
    )


def _invalidate_in_worker(session_id: str, ref_id: str) -> None:
    try:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            handle_ref_invalidation_for_session(db, session_id=session_id, ref_id=ref_id)
            db.commit()
    except Exception:  # noqa: BLE001 — projection observer cannot affect authority
        logger.warning("[ProjectKnowledge] ref invalidation failed session=%s ref=%s",
                       session_id, ref_id, exc_info=True)


_invalidation_tasks: set[asyncio.Task] = set()


def _ref_lifecycle_hook(session_id: str, ref_id: str, reason: object) -> None:
    """Best-effort projection update; never run sync database IO on the event loop."""
    from app.services.project_knowledge import project_knowledge_enabled

    if not project_knowledge_enabled() or not session_id or not ref_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _invalidate_in_worker(session_id, ref_id)
        return
    if len(_invalidation_tasks) >= 64:
        logger.warning("[ProjectKnowledge] invalidation queue full; lazy liveness check required")
        return
    task = loop.create_task(asyncio.to_thread(_invalidate_in_worker, session_id, ref_id))
    _invalidation_tasks.add(task)
    task.add_done_callback(_invalidation_tasks.discard)


def register_project_knowledge_hook() -> bool:
    """把投影失效观察者接入 ref_lifecycle（幂等；flag off 时不注册）。"""
    global _hook_registered
    with _hook_register_lock:
        if _hook_registered:
            return False
        from app.services.ref_lifecycle import register_ref_invalidation_hook

        register_ref_invalidation_hook(_ref_lifecycle_hook)
        _hook_registered = True
        return True


__all__ = [
    "handle_ref_invalidation_for_session",
    "register_project_knowledge_hook",
]
