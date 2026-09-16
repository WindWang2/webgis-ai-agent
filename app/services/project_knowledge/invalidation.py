"""ProjectKnowledge 失效接线：ref_lifecycle 观察者 + 显式失效入口。

纪律（与 ``ref_lifecycle`` R6 相同）：观察者**绝不影响失效权威** ——
hook 内任何异常只记日志；正确性从不依赖 hook 被调用（检索期 lazy
liveness 复核兜底）。注册幂等。
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select
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


def handle_ref_invalidation_for_session(
    db: Session, *, org_id: str, ref_id: str
) -> int:
    """ref 失效 → 本投影中回指该 ref 的 active 行降级（session_ref 权威或
    ref-tag 引用）。返回降级行数。caller commit。

    匹配有界（active 行 ≤ 预算），ref-tag 匹配在 Python 侧做（无跨后端
    JSON 谓词）。
    """
    if not org_id or not ref_id:
        return 0
    rows = list(db.execute(
        select(ProjectKnowledgeEntry).where(
            ProjectKnowledgeEntry.org_id == org_id,
            ProjectKnowledgeEntry.status == ST_ACTIVE,
            ProjectKnowledgeEntry.authority_store == AS_SESSION_REF,
            ProjectKnowledgeEntry.authority_id == ref_id,
        )
    ).scalars().all())
    direct = [r for r in rows if r.authority_id == ref_id]
    count = ks.demote_entries(
        db,
        [r for r in direct if r.authority_store == AS_SESSION_REF] or direct,
        status="stale",
        rule="head_changed",
    )
    if count == 0:
        # ref-tag 引用兜底：authority_id 不是该 ref，但 refs 里引用了它。
        tagged = list(db.execute(
            select(ProjectKnowledgeEntry).where(
                ProjectKnowledgeEntry.org_id == org_id,
                ProjectKnowledgeEntry.status == ST_ACTIVE,
            ).limit(400)
        ).scalars().all())
        victims = [
            r for r in tagged
            if any(
                isinstance(t, dict) and t.get("id") == ref_id
                for t in (r.refs or [])
            )
        ]
        count = ks.demote_entries(db, victims, status="stale", rule="head_changed")
    return count


def _ref_lifecycle_hook(session_id: str, ref_id: str, reason: object) -> None:
    """``register_ref_invalidation_hook`` 观察者（fail-safe）。"""
    try:
        from app.core.database import SessionLocal
        from app.core import tenancy

        org_id = tenancy.effective_org_in_thread(None)
        if not org_id:
            return
        with SessionLocal() as db:
            handle_ref_invalidation_for_session(db, org_id=org_id, ref_id=ref_id)
            db.commit()
    except Exception:  # noqa: BLE001 — 观察者绝不影响失效权威
        logger.debug(
            "[ProjectKnowledge] ref invalidation hook failed session=%s ref=%s",
            session_id, ref_id, exc_info=True,
        )


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
