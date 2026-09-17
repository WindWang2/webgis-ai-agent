"""ProjectKnowledge REST — 项目级知识投影 / 复用检索（additive, thin）。

全部端点：
- ``GIS_PROJECT_KNOWLEDGE``（默认 OFF）off 时 503 ``project_knowledge_disabled``；
- 先过 ``ProjectService.get_project_with_auth``（IDOR 门）再动投影；
- 投影读写都带 effective org 恒等值过滤（fail-closed）；
- 只返回 ids/摘要/ref 指针/计数 —— 绝无 payload。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.services.project_knowledge import project_knowledge_enabled
from app.services.project_service import ProjectService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/knowledge", tags=["Project Knowledge"])


def _org(user: Dict[str, Any]) -> str:
    from app.core import tenancy

    return tenancy.effective_org_in_thread(user)


def _uid(user: Dict[str, Any]) -> str:
    # get_current_user 返回 {"user_id", "role", "org_id", "scopes"}；
    # id/sub 兜底为其他 auth profile 形状。
    return str(user.get("user_id") or user.get("id") or user.get("sub") or "")


def _gate(project_id: str, user: Dict[str, Any], db: Session):
    """flag + IDOR 门：off → 503；越权 → 404（不区分缺失与拒绝）。"""
    if not project_knowledge_enabled():
        raise HTTPException(status_code=503, detail="project_knowledge_disabled")
    _ensure_hook_registered()
    org_id = _org(user)
    project = ProjectService.get_project_with_auth(
        db, project_id, user_id=_uid(user), org_id=_org_int(user)
    )
    if project is None:
        raise HTTPException(status_code=404, detail="project_not_found")
    return project, org_id


def _ensure_hook_registered() -> None:
    """flag on 的首次使用时懒注册 ref_lifecycle 观察者（幂等、无 import
    副作用；review P2-4 —— 否则失效观察者是死代码）。注册失败绝不阻断
    请求（lazy 复核兜底失效正确性）。"""
    try:
        from app.services.project_knowledge.invalidation import (
            register_project_knowledge_hook,
        )

        register_project_knowledge_hook()
    except Exception:  # noqa: BLE001 — 观察者绝不影响主路径
        logger.debug("[ProjectKnowledge] hook registration failed", exc_info=True)


def _org_int(user: Dict[str, Any]) -> Optional[int]:
    """project auth 门需要 int org（projects.org_id 是 Integer FK）。"""
    raw = user.get("org_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


class RebuildResponse(BaseModel):
    project_id: str
    upserted: Dict[str, int] = Field(default_factory=dict)
    rejected: int = 0
    errors: int = 0
    failed_sources: List[str] = Field(default_factory=list)


class CardResponse(BaseModel):
    project_id: str
    text: str = ""
    items: int = 0
    truncated: bool = False
    omitted: int = 0
    chars: int = 0
    char_budget: int = 1600
    empty: bool = True


@router.get("/card", response_model=CardResponse)
def get_knowledge_card(
    project_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CardResponse:
    project, org_id = _gate(project_id, user, db)
    from app.services.project_knowledge import store as ks
    from app.services.project_knowledge.card import render_project_knowledge_card

    entries = ks.get_active_entries(
        db, org_id=org_id, project_id=project.id, limit=200
    )
    card = render_project_knowledge_card(
        entries, project_id=project.id, project_name=str(project.name or ""),
    )
    return CardResponse(
        project_id=project.id,
        text=card.text,
        items=card.items,
        truncated=card.truncated,
        omitted=card.omitted,
        chars=card.chars,
        empty=card.text == "",
    )


@router.get("/search")
def search_knowledge(
    project_id: str,
    q: str = Query(default="", max_length=200),
    kind: Optional[str] = Query(default=None, max_length=32),
    limit: int = Query(default=20, ge=1, le=100),
    user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project, org_id = _gate(project_id, user, db)
    from app.services.project_knowledge import store as ks

    kinds = [kind] if kind else None
    entries = ks.get_active_entries(
        db, org_id=org_id, project_id=project.id, kinds=kinds,
        limit=max(1, min(int(limit), 100)),
    )
    needle = (q or "").strip().lower()
    if needle:
        # 文本过滤只影响**展示**命中（search 是浏览面）；复用判定永远不
        # 看 subject 文本（见 retrieval）。
        entries = [
            e for e in entries
            if needle in e.subject.lower() or needle in (e.summary or "").lower()
        ]
    return {
        "project_id": project.id,
        "count": len(entries),
        "entries": [e.to_bounded_dict() for e in entries],
    }


@router.get("/reuse-candidates")
def get_reuse_candidates(
    project_id: str,
    bbox: Optional[str] = Query(default=None, max_length=100,
                                description="minx,miny,maxx,maxy"),
    temporal_label: Optional[str] = Query(default=None, max_length=64),
    method_key: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=8, ge=1, le=64),
    user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    project, org_id = _gate(project_id, user, db)
    from app.services.project_knowledge.contract import ReuseQuery, validate_bbox
    from app.services.project_knowledge.retrieval import find_reuse_candidates

    query = ReuseQuery(
        bbox=validate_bbox(bbox.split(",") if bbox else None),
        temporal_label=temporal_label,
        method_key=method_key,
        limit=limit,
    )
    candidates = find_reuse_candidates(
        db, org_id=org_id, project_id=project.id, query=query,
    )
    db.commit()   # lazy 降级写回
    return {
        "project_id": project.id,
        "count": len(candidates),
        "candidates": [c.to_bounded_dict() for c in candidates],
    }


@router.post("/rebuild", response_model=RebuildResponse)
def rebuild_knowledge(
    project_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RebuildResponse:
    project, org_id = _gate(project_id, user, db)
    from app.services.project_knowledge.indexer import rebuild_project_knowledge

    report = rebuild_project_knowledge(db, project=project, org_id=org_id)
    db.commit()
    return RebuildResponse(
        project_id=project.id,
        upserted=report.upserted,
        rejected=report.rejected,
        errors=report.errors,
        failed_sources=report.failed_sources,
    )


__all__ = ["router"]
