"""ProjectKnowledge 存储层（投影 upsert / 失效 / 预算）。

同步 SQLAlchemy 自由函数（仓库范式：model + migration + service 函数，
见 ADR-0069 / gis_memory），调用方负责 commit/rollback；本模块只 flush。

事务约定与 gis_memory 相同：调用方 commit。并发双写由 active 行 partial
unique index 兜底：败方吃 IntegrityError → SAVEPOINT 回滚（begin_nested）
后基于胜者已提交状态重试一次。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.project_knowledge import ProjectKnowledgeEntry
from app.services.project_knowledge.contract import (
    ENTRY_STATUSES,
    KnowledgePolicyError,
    KnowledgeUpsert,
    PROJECT_ROW_BUDGET,
    RULE_NONE,
    RULE_SCOPE_GONE,
    REF_TOKEN_MAX,
    ST_ACTIVE,
    ST_INVALIDATED,
    ST_STALE,
    ST_SUPERSEDED,
    SUBJECT_CHAR_BUDGET,
    SUMMARY_CHAR_BUDGET,
    REFS_MAX,
    KnowledgeEntry,
    ref_tags_from,
    validate_bbox,
)

logger = logging.getLogger(__name__)


def _now_naive() -> datetime:
    # 仓库 DateTime 列统一 naive-UTC 语义（ADR-0069 评审结论）。
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean_summary(summary: str) -> str:
    return str(summary or "")[:SUMMARY_CHAR_BUDGET]


def _to_entry(row: ProjectKnowledgeEntry) -> KnowledgeEntry:
    return KnowledgeEntry(
        id=row.id,
        org_id=row.org_id,
        project_id=row.project_id,
        entity_kind=row.entity_kind,
        authority_store=row.authority_store,
        authority_id=row.authority_id,
        subject=row.subject,
        version_token=row.version_token or "",
        summary=row.summary or "",
        bbox=validate_bbox(row.bbox),
        temporal_label=row.temporal_label,
        method_key=row.method_key,
        refs=ref_tags_from(row.refs),
        status=row.status,
        invalidation_rule=row.invalidation_rule or "",
        version=int(row.version or 1),
        weight=float(row.weight or 0.0),
        created_at=row.created_at.isoformat() if row.created_at else None,
        last_validated_at=(
            row.last_validated_at.isoformat() if row.last_validated_at else None
        ),
    )


def upsert_entry(
    db: Session, req: KnowledgeUpsert, *, _retry_left: int = 1
) -> Optional[ProjectKnowledgeEntry]:
    """受控投影写入（消毒 → 门 → 同 key 再验证/取代 → 预算）。

    返回落库行；写入门拒绝返回 None（记日志，不抛 —— 投影是增值读模型，
    绝不抬进用户路径）。同 natural key 同 token = 原行再验证刷新；
    不同 token = 旧行 superseded + 新行 active（D6 取代链）。
    """
    try:
        req.validate()
    except KnowledgePolicyError as exc:
        logger.info("[ProjectKnowledge] upsert rejected: %s", exc)
        return None

    subject = str(req.subject)[:SUBJECT_CHAR_BUDGET]
    summary = _clean_summary(req.summary)
    bbox = validate_bbox(req.bbox)
    refs = [t.to_dict() for t in list(req.refs)[:REFS_MAX]]
    token = str(req.version_token or "")[:REF_TOKEN_MAX]

    actives = list(db.execute(
        select(ProjectKnowledgeEntry).where(
            ProjectKnowledgeEntry.org_id == req.org_id,
            ProjectKnowledgeEntry.project_id == req.project_id,
            ProjectKnowledgeEntry.entity_kind == req.entity_kind,
            ProjectKnowledgeEntry.authority_store == req.authority_store,
            ProjectKnowledgeEntry.authority_id == req.authority_id,
            ProjectKnowledgeEntry.status == ST_ACTIVE,
        )
    ).scalars().all())

    now = _now_naive()
    if actives and actives[0].version_token == token:
        # 同版本再验证：原行刷新（权威状态未变 → 不产生新版本行）
        row = actives[0]
        row.subject = subject
        row.summary = summary
        row.bbox = list(bbox) if bbox else None
        row.temporal_label = req.temporal_label
        row.method_key = req.method_key
        row.refs = refs
        row.last_validated_at = now
        row.version = int(row.version or 1) + 1
        db.flush()
        return row

    row = ProjectKnowledgeEntry(
        org_id=req.org_id,
        project_id=req.project_id,
        entity_kind=req.entity_kind,
        subject=subject,
        authority_store=req.authority_store,
        authority_id=req.authority_id,
        version_token=token,
        summary=summary,
        bbox=list(bbox) if bbox else None,
        temporal_label=req.temporal_label,
        method_key=req.method_key,
        refs=refs,
        evidence={"seam": "indexer", "observed_at": now.isoformat()},
        status=ST_ACTIVE,
        invalidation_rule=RULE_NONE,
        created_at=now,
        last_validated_at=now,
        version=1,
        supersedes_id=actives[0].id if actives else None,
        weight=0.0,
    )
    try:
        # SAVEPOINT：并发 IntegrityError 只撤销本次 supersede+insert。
        with db.begin_nested():
            if actives:
                for prev in actives:
                    prev.status = ST_SUPERSEDED
                    prev.last_validated_at = now
                db.flush()
            db.add(row)
            db.flush()
    except IntegrityError:
        if _retry_left > 0:
            logger.info(
                "[ProjectKnowledge] supersede race kind=%s authority=%s/%s "
                "(org=%s project=%s) — retrying after savepoint rollback",
                req.entity_kind, req.authority_store, req.authority_id,
                req.org_id, req.project_id,
            )
            return upsert_entry(db, req, _retry_left=_retry_left - 1)
        logger.warning(
            "[ProjectKnowledge] supersede race unresolved after retry "
            "kind=%s authority=%s/%s",
            req.entity_kind, req.authority_store, req.authority_id,
        )
        return None
    _apply_budget(db, req.org_id, req.project_id)
    return row


def get_active_entries(
    db: Session,
    *,
    org_id: str,
    project_id: str,
    kinds: Optional[Sequence[str]] = None,
    limit: int = 200,
) -> List[KnowledgeEntry]:
    """某项目的 active 知识条目（跨 org/跨项目读空集 —— 恒等值过滤）。"""
    if not org_id or not project_id:
        return []
    stmt = select(ProjectKnowledgeEntry).where(
        ProjectKnowledgeEntry.org_id == org_id,
        ProjectKnowledgeEntry.project_id == project_id,
        ProjectKnowledgeEntry.status == ST_ACTIVE,
    )
    if kinds:
        stmt = stmt.where(ProjectKnowledgeEntry.entity_kind.in_(list(kinds)))
    stmt = stmt.order_by(
        ProjectKnowledgeEntry.weight.desc(),
        ProjectKnowledgeEntry.last_validated_at.desc(),
    ).limit(max(1, min(int(limit), 500)))
    return [_to_entry(row) for row in db.execute(stmt).scalars().all()]


def get_entries_by_authority(
    db: Session,
    *,
    org_id: str,
    project_id: str,
    authority_store: str,
    authority_id: str,
    statuses: Optional[Sequence[str]] = None,
) -> List[KnowledgeEntry]:
    """按权威回指取条目（失效/复核路径用）。"""
    stmt = select(ProjectKnowledgeEntry).where(
        ProjectKnowledgeEntry.org_id == org_id,
        ProjectKnowledgeEntry.project_id == project_id,
        ProjectKnowledgeEntry.authority_store == authority_store,
        ProjectKnowledgeEntry.authority_id == authority_id,
    )
    if statuses:
        stmt = stmt.where(ProjectKnowledgeEntry.status.in_(list(statuses)))
    stmt = stmt.limit(64)
    return [_to_entry(row) for row in db.execute(stmt).scalars().all()]


def demote_entries(
    db: Session,
    entries: Sequence[ProjectKnowledgeEntry],
    *,
    status: str,
    rule: str,
) -> int:
    """把给定行降级到 status（写回 demote；幂等 —— 已是目标状态不重复计）。"""
    if status not in ENTRY_STATUSES or rule not in ("", "version_bump", "head_changed", "claim_lost", "manual", "scope_gone"):
        return 0
    now = _now_naive()
    count = 0
    for row in entries:
        if row.status == status and row.invalidation_rule == rule:
            continue
        row.status = status
        row.invalidation_rule = rule
        row.last_validated_at = now
        count += 1
    if count:
        db.flush()
    return count


def demote_stale_by_token(
    db: Session,
    *,
    org_id: str,
    project_id: str,
    authority_store: str,
    authority_id: str,
    live_token: Optional[str],
) -> int:
    """权威 token 漂移 → 该权威的 active 行降级。

    ``live_token`` 为 None = 权威行消失 → ``invalidated``（scope_gone）；
    与行 token 不一致 → ``stale``（version_bump）。一致 → 不动。
    返回降级行数。
    """
    rows = list(db.execute(
        select(ProjectKnowledgeEntry).where(
            ProjectKnowledgeEntry.org_id == org_id,
            ProjectKnowledgeEntry.project_id == project_id,
            ProjectKnowledgeEntry.authority_store == authority_store,
            ProjectKnowledgeEntry.authority_id == authority_id,
            ProjectKnowledgeEntry.status == ST_ACTIVE,
        )
    ).scalars().all())
    if not rows:
        return 0
    if live_token is None:
        return demote_entries(db, rows, status=ST_INVALIDATED, rule=RULE_SCOPE_GONE)
    victims = [r for r in rows if (r.version_token or "") != str(live_token)[:REF_TOKEN_MAX]]
    return demote_entries(db, victims, status=ST_STALE, rule="version_bump")


def invalidate_for_authority(
    db: Session,
    *,
    org_id: str,
    project_id: str,
    authority_store: str,
    authority_id: str,
    rule: str = RULE_SCOPE_GONE,
) -> int:
    """显式失效某权威回指的全部 active 行（seam/observer 入口）。"""
    rows = list(db.execute(
        select(ProjectKnowledgeEntry).where(
            ProjectKnowledgeEntry.org_id == org_id,
            ProjectKnowledgeEntry.project_id == project_id,
            ProjectKnowledgeEntry.authority_store == authority_store,
            ProjectKnowledgeEntry.authority_id == authority_id,
            ProjectKnowledgeEntry.status == ST_ACTIVE,
        )
    ).scalars().all())
    return demote_entries(db, rows, status=ST_INVALIDATED, rule=rule)


def invalidate_for_project(db: Session, *, org_id: str, project_id: str) -> int:
    """项目终结/访问撤销 → 全部失效（scope_gone）。"""
    rows = list(db.execute(
        select(ProjectKnowledgeEntry).where(
            ProjectKnowledgeEntry.org_id == org_id,
            ProjectKnowledgeEntry.project_id == project_id,
            ProjectKnowledgeEntry.status == ST_ACTIVE,
        )
    ).scalars().all())
    return demote_entries(db, rows, status=ST_INVALIDATED, rule=RULE_SCOPE_GONE)


def retire_entry(db: Session, *, org_id: str, entry_id: str) -> bool:
    """手动撤销（审计面入口；跨 org 拒绝 —— tenancy 等值防线）。"""
    row = db.get(ProjectKnowledgeEntry, entry_id)
    if row is None or row.org_id != org_id:
        return False
    if row.status != ST_ACTIVE:
        return False
    row.status = ST_INVALIDATED
    row.invalidation_rule = "manual"
    row.last_validated_at = _now_naive()
    db.flush()
    return True


def _apply_budget(db: Session, org_id: str, project_id: str) -> int:
    """把 (org, project) 行数压回预算内（无界增长即漏洞）。

    淘汰序：非 active（invalidated/superseded/stale，最旧优先）→ active 按
    ``低权重 + 旧验证`` 优先。返回淘汰数。
    """
    total = db.execute(
        select(func.count())
        .select_from(ProjectKnowledgeEntry)
        .where(
            ProjectKnowledgeEntry.org_id == org_id,
            ProjectKnowledgeEntry.project_id == project_id,
        )
    ).scalar_one()
    overflow = int(total or 0) - PROJECT_ROW_BUDGET
    if overflow <= 0:
        return 0
    removed = 0
    base = select(ProjectKnowledgeEntry).where(
        ProjectKnowledgeEntry.org_id == org_id,
        ProjectKnowledgeEntry.project_id == project_id,
    )
    for pass_status in (None, ST_ACTIVE):
        if overflow <= 0:
            break
        stmt = base
        if pass_status is None:
            stmt = stmt.where(ProjectKnowledgeEntry.status != ST_ACTIVE)
        else:
            stmt = stmt.where(ProjectKnowledgeEntry.status == ST_ACTIVE)
        victims = list(db.execute(
            stmt.order_by(
                ProjectKnowledgeEntry.weight.asc(),
                ProjectKnowledgeEntry.last_validated_at.asc(),
            ).limit(overflow)
        ).scalars().all())
        for victim in victims:
            db.delete(victim)
            removed += 1
            overflow -= 1
    if removed:
        db.flush()
    return removed


def project_knowledge_stats(db: Session, *, org_id: str) -> Dict[str, Any]:
    """可观测面：按 (project, status) 计数（无行内容泄漏）。"""
    rows = db.execute(
        select(
            ProjectKnowledgeEntry.project_id,
            ProjectKnowledgeEntry.status,
            func.count(),
        ).where(ProjectKnowledgeEntry.org_id == org_id).group_by(
            ProjectKnowledgeEntry.project_id, ProjectKnowledgeEntry.status
        )
    ).all()
    stats: Dict[str, Any] = {}
    for project_id, status, count in rows:
        stats[f"{project_id}.{status}"] = int(count or 0)
    return stats


__all__ = [
    "upsert_entry",
    "get_active_entries",
    "get_entries_by_authority",
    "demote_entries",
    "demote_stale_by_token",
    "invalidate_for_authority",
    "invalidate_for_project",
    "retire_entry",
    "project_knowledge_stats",
]
