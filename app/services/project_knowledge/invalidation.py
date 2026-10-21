"""ProjectKnowledge 失效接线：ref_lifecycle 观察者 + 显式失效入口。

纪律（与 ``ref_lifecycle`` R6 相同）：观察者**绝不影响失效权威** ——
hook 内任何异常只记日志；正确性从不依赖 hook 被调用（检索期 lazy
liveness 复核兜底）。注册幂等。
"""
from __future__ import annotations

import logging
import threading
from typing import Iterator, List

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

#: ref-tag 兜底扫描的分页大小（有界内存）。
_REF_SCAN_PAGE = 200
#: 分页上限：org 级 active 行的诚实扫描上界（25 页 × 200 = 5000 行）。
#: 触顶记 warning 并停止 —— 绝不静默截断（#1740 修的正是静默漏降级）。
_MAX_REF_SCAN_PAGES = 25


def _ref_tagged_active_rows(
    db: Session, *, org_id: str, ref_id: str
) -> Iterator[ProjectKnowledgeEntry]:
    """org 级 active 行里 refs 引用 ``ref_id`` 的行（确定性主键游标分页）。

    #1740: 修复前是无 ``ORDER BY`` 的 ``limit(400)`` —— 数据库返回的行序
    不确定，超过首页 400 条 active 行所持的 ref 引用被静默漏掉、不降级
    （每 (org, project) 行预算已是 400，多项目 org 必然漏）。改为按主键
    升序游标翻页：首页不再有特权，全部 active 行都被看见；ref-tag 匹配仍在
    Python 侧做（无跨后端 JSON 谓词，与原实现同一约束）。
    """
    after = ""
    for _page in range(_MAX_REF_SCAN_PAGES):
        stmt = select(ProjectKnowledgeEntry).where(
            ProjectKnowledgeEntry.org_id == org_id,
            ProjectKnowledgeEntry.status == ST_ACTIVE,
        ).order_by(ProjectKnowledgeEntry.id).limit(_REF_SCAN_PAGE)
        if after:
            stmt = stmt.where(ProjectKnowledgeEntry.id > after)
        rows: List[ProjectKnowledgeEntry] = list(
            db.execute(stmt).scalars().all())
        for row in rows:
            if any(
                isinstance(t, dict) and t.get("id") == ref_id
                for t in (row.refs or [])
            ):
                yield row
        if len(rows) < _REF_SCAN_PAGE:
            return
        last = rows[-1].id
        if not last:
            return
        after = str(last)
    logger.warning(
        "[ProjectKnowledge] ref-tag scan hit the page cap (%d pages x %d rows) "
        "org=%s ref=%s — remaining active rows not scanned",
        _MAX_REF_SCAN_PAGES, _REF_SCAN_PAGE, org_id, ref_id,
    )


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
        # #1740: 确定性游标分页扫描全部 active 行（原 limit(400) 无序查询会
        # 静默漏掉首页之后的行）。
        victims = list(_ref_tagged_active_rows(db, org_id=org_id, ref_id=ref_id))
        count = ks.demote_entries(db, victims, status="stale", rule="head_changed")
    return count


def _ref_lifecycle_hook(session_id: str, ref_id: str, reason: object) -> None:
    """``register_ref_invalidation_hook`` 观察者（fail-safe + flag 双重门：
    flag 关闭时零行为 —— 注册残留也不产生任何读写）。"""
    try:
        from app.services.project_knowledge import project_knowledge_enabled

        if not project_knowledge_enabled():
            return
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
