"""SpatialMemory 存储层（R2 写入 / R3 supersession / R7 GC）。

同步 SQLAlchemy 自由函数（仓库范式：model + migration + service 函数，
见 ADR-0069 ``project_memory``），调用方经 ``asyncio.to_thread`` 进入。

事务约定：与 ADR-0069 相同——**调用方负责 commit/rollback**；本模块只
flush。生产缝（harvest/工具钩子）用 :func:`safe_record_memory` 系列
fail-safe 包装，绝不把记忆写失败抬进用户 turn。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.spatial_memory import GISSpatialMemory
from app.services.gis_memory.contract import (
    SCOPE_PROJECT,
    SCOPE_SESSION,
    SCOPE_USER,
    STATUS_ACTIVE,
    STATUS_INVALIDATED,
    STATUS_SUPERSEDED,
    SOURCE_USER_CORRECTION,
    SOURCE_USER_DECISION,
    MemoryPolicyError,
    MemoryWriteRequest,
    SpatialMemoryRecord,
)
from app.services.gis_memory.policy import (
    ROUTE_CARTO_PROJECT_FACT,
    PolicyVerdict,
    evaluate,
)
from app.services.gis_memory.sanitizer import (
    assert_no_secrets,
    sanitize_refs,
    sanitize_subject,
    sanitize_value,
)

logger = logging.getLogger(__name__)

#: 每 (org, scope, scope_id) 行数预算（R7：无界增长即漏洞）。
SCOPE_BUDGET: Dict[str, int] = {
    SCOPE_SESSION: 80,
    SCOPE_PROJECT: 400,
    SCOPE_USER: 200,
}

#: dataset 语义类记忆（dataset_version 失效规则的作用面）。
_DATASET_KINDS = ("dataset_semantics", "field_role")


def _now_naive() -> datetime:
    # 仓库 DateTime 列统一 naive-UTC 语义（ADR-0069 评审结论：aware 在
    # Postgres 上不可移植）。
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_record(row: GISSpatialMemory) -> SpatialMemoryRecord:
    return SpatialMemoryRecord(
        id=row.id,
        kind=row.kind,
        scope=row.scope,
        scope_id=row.scope_id,
        org_id=row.org_id,
        subject=row.subject,
        value=row.value if isinstance(row.value, dict) else {},
        refs=[r for r in (row.refs or []) if isinstance(r, str)],
        evidence=row.evidence if isinstance(row.evidence, dict) else {},
        fingerprint=row.fingerprint or "",
        confidence=float(row.confidence or 0.0),
        status=row.status,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
        created_at=row.created_at.isoformat() if row.created_at else None,
        last_validated_at=(
            row.last_validated_at.isoformat() if row.last_validated_at else None
        ),
        version=int(row.version or 1),
        sensitive=bool(row.sensitive),
        invalidation_rule=row.invalidation_rule,
    )


def _expires_at(now: datetime, ttl_s: Optional[int]) -> Optional[datetime]:
    if ttl_s is None:
        return None
    return now + timedelta(seconds=int(ttl_s))


def record_memory(
    db: Session, req: MemoryWriteRequest, *, _retry_left: int = 1
) -> Optional[GISSpatialMemory]:
    """受控写入一条记忆（消毒 → R2 门 → R3 supersede → 预算）。

    返回落库行；策略拒绝/弱证据冲突失败返回 None（记日志，不抛）。
    同 fingerprint 的再验证 = 原行刷新（version+1）；不同 fingerprint =
    显式取代链（D6：新证据 active、旧证据 superseded；用户纠正必胜；
    learned-vs-learned 高置信者胜，弱的新证据**不落库**）。

    消毒先于策略门（review F7）：fingerprint/预算/落库都基于**消毒后**
    形状——「先裁剪再校验」的注释才为真。并发双写（review F6）由 active
    行 partial unique index 兜底：败方吃 IntegrityError → rollback 后
    基于胜者已提交的状态重试一次。
    """
    value = sanitize_value(req.value)
    refs = sanitize_refs(req.refs)
    try:
        assert_no_secrets(value)
    except MemoryPolicyError:
        logger.warning(
            "[GISMemory] write blocked (secret-shaped value) kind=%s subject=%s",
            req.kind, req.subject,
        )
        return None
    subject = sanitize_subject(req.subject)
    scope_id = req.scope_id
    if req.scope == SCOPE_SESSION:
        from app.services.gis_memory.sanitizer import sanitize_scope_id

        scope_id = sanitize_scope_id(SCOPE_SESSION, scope_id)

    effective = (
        req
        if (value == req.value and refs == list(req.refs) and subject == req.subject)
        else MemoryWriteRequest(
            kind=req.kind, scope=req.scope, scope_id=scope_id, subject=subject,
            value=value, evidence=req.evidence, confidence=req.confidence,
            org_id=req.org_id, refs=refs, user_id=req.user_id,
            sensitive=req.sensitive, invalidation_rule=req.invalidation_rule,
            ttl_s=req.ttl_s, fingerprint=req.fingerprint,
        )
    )
    verdict: PolicyVerdict = evaluate(effective)
    if not verdict.allowed:
        logger.info(
            "[GISMemory] write rejected kind=%s subject=%s: %s",
            req.kind, subject, verdict.reason,
        )
        return None

    if verdict.route == ROUTE_CARTO_PROJECT_FACT:
        # D4：项目制图偏好改道 ADR-0069 账本（单一 preference 真相）。
        return _route_to_carto_project_fact(db, effective, verdict)

    fingerprint = verdict.fingerprint
    now = _now_naive()
    expires = _expires_at(now, verdict.ttl_s)

    actives = list(db.execute(
        select(GISSpatialMemory).where(
            GISSpatialMemory.org_id == req.org_id,
            GISSpatialMemory.scope == req.scope,
            GISSpatialMemory.scope_id == scope_id,
            GISSpatialMemory.kind == req.kind,
            GISSpatialMemory.subject == subject,
            GISSpatialMemory.status == STATUS_ACTIVE,
        ).order_by(
            GISSpatialMemory.confidence.desc(), GISSpatialMemory.last_validated_at.desc()
        )
    ).scalars().all())

    if actives and actives[0].fingerprint == fingerprint:
        # 同语义再验证：原行刷新（非矛盾 → 不产生新版本行）
        row = actives[0]
        row.confidence = max(float(row.confidence or 0.0), float(req.confidence))
        row.last_validated_at = now
        row.version = int(row.version or 1) + 1
        row.expires_at = expires if expires is not None else row.expires_at
        if req.evidence.source == SOURCE_USER_CORRECTION:
            # 人的显式再确认升级证据（同一语义，来源更硬）
            row.evidence = req.evidence.to_dict()
            row.sensitive = bool(req.sensitive)
        if req.refs:
            merged = list(row.refs or [])
            for ref in refs:
                if ref not in merged:
                    merged.append(ref)
            row.refs = merged[:8]
        db.flush()
        return row

    if actives:
        held = actives[0]
        user_correction = req.evidence.source == SOURCE_USER_CORRECTION
        if not user_correction and float(req.confidence) <= float(held.confidence or 0.0):
            # 弱新证据 vs 已存事实：不落库、不覆盖（D6：高置信者胜）。
            logger.info(
                "[GISMemory] contradiction held kind=%s subject=%s "
                "held_conf=%.2f incoming_conf=%.2f (not written)",
                req.kind, subject, float(held.confidence or 0.0), req.confidence,
            )
            return None
        for row in actives:
            row.status = STATUS_SUPERSEDED
            row.last_validated_at = now
        db.flush()

    row = GISSpatialMemory(
        org_id=req.org_id,
        user_id=req.user_id,
        scope=req.scope,
        scope_id=scope_id,
        kind=req.kind,
        subject=subject,
        value=value,
        refs=refs,
        evidence=req.evidence.to_dict(),
        fingerprint=fingerprint,
        confidence=float(req.confidence),
        status=STATUS_ACTIVE,
        expires_at=expires,
        created_at=now,
        last_validated_at=now,
        version=1,
        supersedes_id=actives[0].id if actives else None,
        sensitive=bool(req.sensitive),
        invalidation_rule=verdict.invalidation_rule,
    )
    try:
        db.add(row)
        db.flush()
    except IntegrityError:
        # F6：并发双写吃 partial unique index（同 key 双 active 不可能落库）。
        # 回滚本事务（含本次 supersede 标记），基于胜者已提交状态重判一次。
        db.rollback()
        if _retry_left > 0:
            logger.info(
                "[GISMemory] supersede race on kind=%s subject=%s "
                "(org=%s scope=%s) — retrying after rollback",
                req.kind, subject, req.org_id, req.scope,
            )
            return record_memory(db, req, _retry_left=_retry_left - 1)
        logger.warning(
            "[GISMemory] supersede race unresolved after retry kind=%s subject=%s",
            req.kind, subject,
        )
        return None
    _apply_scope_budget(db, req.org_id, req.scope, scope_id)
    return row


def _route_to_carto_project_fact(
    db: Session, req: MemoryWriteRequest, verdict: PolicyVerdict
) -> Optional[GISSpatialMemory]:
    """偏好 → ADR-0069 ``record_fact(kind=preference)``（单一 preference 真相）。

    ``supersede`` 语义沿用 ADR-0069 自身的纪律（「调用方已确认」才显式
    升级）：显式用户来源（纠正/决策）= 用户已经改主意，新偏好落 active；
    其余来源冲突时保持既有 conflicted 挂起语义，绝不静默覆盖。
    """
    from app.services.cartography.project_memory import record_fact

    explicit = req.evidence.source in (SOURCE_USER_CORRECTION, SOURCE_USER_DECISION)
    fact = record_fact(
        db,
        req.scope_id,
        "preference",
        sanitize_subject(req.subject),
        sanitize_value(req.value),
        fingerprint=verdict.fingerprint,
        confidence=float(req.confidence),
        expires_at=_expires_at(_now_naive(), verdict.ttl_s),
        supersede=explicit,
    )
    if fact is None:
        return None
    logger.info(
        "[GISMemory] preference routed to carto_project_facts project=%s subject=%s",
        req.scope_id, req.subject,
    )
    return None


def safe_record_memory(db: Session, req: MemoryWriteRequest) -> bool:
    """生产缝的 fail-safe 包装：任何异常降级为 False + 日志（记忆是增值
    上下文，绝不抬进用户 turn —— ADR-0069 harvest 同纪律）。调用方 commit。"""
    try:
        return record_memory(db, req) is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[GISMemory] safe_record failed kind=%s subject=%s: %s",
            req.kind, req.subject, exc,
        )
        return False


def get_active_memories(
    db: Session,
    org_id: str,
    scope: str,
    scope_id: str,
    *,
    kinds: Optional[Sequence[str]] = None,
    include_sensitive: bool = False,
    limit: int = 64,
) -> List[SpatialMemoryRecord]:
    """某作用域的 active 记忆（未过期；superseded/invalidated 永不返回）。"""
    if not org_id or not scope_id:
        return []
    now = _now_naive()
    stmt = select(GISSpatialMemory).where(
        GISSpatialMemory.org_id == org_id,
        GISSpatialMemory.scope == scope,
        GISSpatialMemory.scope_id == scope_id,
        GISSpatialMemory.status == STATUS_ACTIVE,
        (GISSpatialMemory.expires_at.is_(None))
        | (GISSpatialMemory.expires_at > now),
    )
    if kinds:
        stmt = stmt.where(GISSpatialMemory.kind.in_(list(kinds)))
    if not include_sensitive:
        stmt = stmt.where(GISSpatialMemory.sensitive.is_(False))
    stmt = stmt.order_by(
        GISSpatialMemory.last_validated_at.desc()
    ).limit(max(1, min(int(limit), 200)))
    return [_to_record(row) for row in db.execute(stmt).scalars().all()]


def sweep_expired(
    db: Session,
    *,
    now: Optional[datetime] = None,
    org_id: Optional[str] = None,
    limit: int = 500,
) -> int:
    """过期记忆 → ``invalidated``（R7；留审计，预算淘汰最终物理回收）。

    只处理 ``invalidation_rule=ttl`` 与任何带 expires_at 的行；返回失效数。
    """
    current = _now_naive() if now is None else _naive(now)
    stmt = select(GISSpatialMemory).where(
        GISSpatialMemory.status == STATUS_ACTIVE,
        GISSpatialMemory.expires_at.is_not(None),
        GISSpatialMemory.expires_at <= current,
    )
    if org_id:
        stmt = stmt.where(GISSpatialMemory.org_id == org_id)
    rows = list(db.execute(
        stmt.order_by(GISSpatialMemory.expires_at.asc()).limit(max(1, limit))
    ).scalars().all())
    for row in rows:
        row.status = STATUS_INVALIDATED
    if rows:
        db.flush()
    return len(rows)


def invalidate_for_dataset(
    db: Session,
    *,
    org_id: str,
    dataset_key: str,
    version_token: Optional[str] = None,
) -> int:
    """dataset 版本漂移 → 该 key 的语义/字段角色记忆失效（R3/R7）。

    ``version_token`` 给定时只失效与之不匹配的行（同版本 = 无漂移）；
    缺省时全量失效（调用方明确知道数据换了但拿不到 token）。
    """
    count = 0
    for kind in _DATASET_KINDS:
        rows = list(db.execute(
            select(GISSpatialMemory).where(
                GISSpatialMemory.org_id == org_id,
                GISSpatialMemory.kind == kind,
                GISSpatialMemory.subject == dataset_key,
                GISSpatialMemory.status == STATUS_ACTIVE,
            )
        ).scalars().all())
        for row in rows:
            if version_token is not None:
                held = row.value.get("version_token") if isinstance(row.value, dict) else None
                if held == version_token:
                    continue
            row.status = STATUS_INVALIDATED
            row.invalidation_rule = "dataset_version"
            count += 1
    if count:
        db.flush()
    return count


def _apply_scope_budget(db: Session, org_id: str, scope: str, scope_id: str) -> int:
    """把 (org, scope, scope_id) 行数压回预算内（R7）。

    淘汰序：非 active（invalidated/superseded，最旧优先）→ active 按
    ``低置信 + 旧验证`` 优先。返回淘汰数。
    """
    cap = SCOPE_BUDGET.get(scope)
    if not cap:
        return 0
    total = db.execute(
        select(func.count())
        .select_from(GISSpatialMemory)
        .where(
            GISSpatialMemory.org_id == org_id,
            GISSpatialMemory.scope == scope,
            GISSpatialMemory.scope_id == scope_id,
        )
    ).scalar_one()
    overflow = int(total or 0) - cap
    if overflow <= 0:
        return 0
    removed = 0
    base = select(GISSpatialMemory).where(
        GISSpatialMemory.org_id == org_id,
        GISSpatialMemory.scope == scope,
        GISSpatialMemory.scope_id == scope_id,
    )
    for pass_status in (None, STATUS_ACTIVE):
        if overflow <= 0:
            break
        stmt = base
        if pass_status is None:
            stmt = stmt.where(GISSpatialMemory.status != STATUS_ACTIVE)
        else:
            stmt = stmt.where(GISSpatialMemory.status == STATUS_ACTIVE)
        victims = list(db.execute(
            stmt.order_by(
                GISSpatialMemory.confidence.asc(),
                GISSpatialMemory.last_validated_at.asc(),
            ).limit(overflow)
        ).scalars().all())
        for victim in victims:
            db.delete(victim)
            removed += 1
            overflow -= 1
    if removed:
        db.flush()
    return removed


def invalidate_for_scope(
    db: Session, *, org_id: str, scope: str, scope_id: str
) -> int:
    """作用域消失（项目删除/会话终结/访问撤销）→ 全部失效（R7/R8）。"""
    rows = list(db.execute(
        select(GISSpatialMemory).where(
            GISSpatialMemory.org_id == org_id,
            GISSpatialMemory.scope == scope,
            GISSpatialMemory.scope_id == scope_id,
            GISSpatialMemory.status == STATUS_ACTIVE,
        )
    ).scalars().all())
    for row in rows:
        row.status = STATUS_INVALIDATED
        row.invalidation_rule = "scope_gone"
    if rows:
        db.flush()
    return len(rows)


def retire_memory(db: Session, *, org_id: str, memory_id: str) -> bool:
    """手动撤销（审计面入口；跨 org 拒绝——tenancy 等值防线）。"""
    row = db.get(GISSpatialMemory, memory_id)
    if row is None or row.org_id != org_id:
        return False
    row.status = STATUS_INVALIDATED
    row.invalidation_rule = "manual"
    row.last_validated_at = _now_naive()
    db.flush()
    return True


def list_memories(
    db: Session,
    *,
    org_id: str,
    scope: Optional[str] = None,
    scope_id: Optional[str] = None,
    limit: int = 200,
) -> List[SpatialMemoryRecord]:
    """审计视图（含非 active；投影/检索走 get_active_memories/retrieve）。"""
    stmt = select(GISSpatialMemory).where(GISSpatialMemory.org_id == org_id)
    if scope:
        stmt = stmt.where(GISSpatialMemory.scope == scope)
    if scope_id:
        stmt = stmt.where(GISSpatialMemory.scope_id == scope_id)
    stmt = stmt.order_by(GISSpatialMemory.last_validated_at.desc()).limit(
        max(1, min(int(limit), 500))
    )
    return [_to_record(row) for row in db.execute(stmt).scalars().all()]


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def memory_stats(db: Session, *, org_id: str) -> Dict[str, Any]:
    """可观测面：按 (scope, status) 计数（无行内容泄漏）。"""
    rows = db.execute(
        select(
            GISSpatialMemory.scope,
            GISSpatialMemory.status,
            func.count(),
        ).where(GISSpatialMemory.org_id == org_id).group_by(
            GISSpatialMemory.scope, GISSpatialMemory.status
        )
    ).all()
    stats: Dict[str, Any] = {}
    for scope, status, count in rows:
        stats[f"{scope}.{status}"] = int(count or 0)
    return stats


__all__ = [
    "SCOPE_BUDGET",
    "record_memory",
    "safe_record_memory",
    "get_active_memories",
    "sweep_expired",
    "invalidate_for_dataset",
    "invalidate_for_scope",
    "retire_memory",
    "list_memories",
    "memory_stats",
]
