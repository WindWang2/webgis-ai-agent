"""SpatialMemory 检索（R4）：谓词过滤 + 有界 top-k + 理由。

检索纪律：
- 必过滤先行（租户/作用域/active/未过期/敏感剔除/读侧置信下限），任何
  不过滤的「整库进 prompt」都是漏洞；
- 打分项全部产出**可读理由**（不透明排序不可接受——复核与评测都要知道
  「为什么召回它」）；
- 确定性：同状态同输入恒同序（score, scope 优先级, subject, id 字典序）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.services.gis_memory.contract import (
    KIND_RESOLVED_PLACE,
    SCOPE_PROJECT,
    SCOPE_SESSION,
    SCOPE_USER,
    RetrievedMemory,
    kind_base_weight,
)
from app.services.gis_memory.store import get_active_memories

logger = logging.getLogger(__name__)

#: 读侧置信下限（比写门槛低——写入门已把过低置信拒之门外，这里只兜底
#: 历史遗留行；低于它的记忆宁可沉默）。
READ_CONFIDENCE_FLOOR = 0.5

#: 默认/硬上限的 top-k。
DEFAULT_TOP_K = 8
HARD_TOP_K_CAP = 16

_SCOPE_PRIORITY = {SCOPE_SESSION: 2.0, SCOPE_PROJECT: 1.0, SCOPE_USER: 0.5}


@dataclass(frozen=True)
class MemoryQueryContext:
    """一次检索的情境（当前 Situation/Goal 的有界投影）。"""

    org_id: str
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    query_text: str = ""
    #: 显式语义键（规范地名 / dataset_key / 工具名 …）——精确匹配优先。
    subjects: Sequence[str] = ()
    kinds: Sequence[str] = ()
    #: 调用方已知的数据集当前版本（key → token）；给定时对
    #: dataset_semantics/field_role 记忆做版本兼容过滤（stale 不召回）。
    dataset_versions: Dict[str, str] = field(default_factory=dict)
    now: Optional[datetime] = None
    limit: int = DEFAULT_TOP_K
    include_sensitive: bool = False


def _naive_now(ctx_now: Optional[datetime]) -> datetime:
    if ctx_now is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    return ctx_now.replace(tzinfo=None) if ctx_now.tzinfo else ctx_now


def _freshness_bonus(last_validated_at: Optional[str], now: datetime) -> float:
    if not last_validated_at:
        return 0.0
    try:
        validated = datetime.fromisoformat(str(last_validated_at))
    except ValueError:
        return 0.0
    if validated.tzinfo:
        validated = validated.replace(tzinfo=None)
    age_days = max((now - validated).total_seconds(), 0.0) / 86400.0
    if age_days <= 1.0:
        return 1.0
    if age_days <= 7.0:
        return 0.6
    if age_days <= 30.0:
        return 0.3
    return 0.0


def _subject_bonus(record_subject: str, ctx: MemoryQueryContext) -> tuple[float, List[str]]:
    lowered = record_subject.lower()
    reasons: List[str] = []
    bonus = 0.0
    for subject in ctx.subjects:
        probe = str(subject).lower().strip()
        if not probe:
            continue
        if lowered == probe:
            bonus = max(bonus, 3.0)
            reasons.append(f"subject_exact:{subject}")
        elif probe in lowered or lowered in probe:
            bonus = max(bonus, 1.5)
            reasons.append(f"subject_partial:{subject}")
    text = ctx.query_text.strip().lower()
    if text and lowered and lowered in text:
        bonus = max(bonus, 2.0)
        reasons.append("query_mention")
    return bonus, reasons


def _dataset_version_ok(record_value: Dict, ctx: MemoryQueryContext) -> bool:
    token = record_value.get("version_token")
    subject_key = str(record_value.get("dataset_key") or "")
    if not ctx.dataset_versions or not subject_key:
        return True
    current = ctx.dataset_versions.get(subject_key)
    if current is None or token is None:
        return True
    return str(token) == str(current)


def retrieve_memories(db: Session, ctx: MemoryQueryContext) -> List[RetrievedMemory]:
    """按情境检索（R4）：有界 top-k，每条带理由。绝不整库返回。"""
    if not ctx.org_id:
        return []
    now = _naive_now(ctx.now)
    scope_targets: List[tuple[str, Optional[str]]] = []
    if ctx.session_id:
        scope_targets.append((SCOPE_SESSION, ctx.session_id))
    if ctx.project_id:
        scope_targets.append((SCOPE_PROJECT, ctx.project_id))
    if ctx.user_id:
        scope_targets.append((SCOPE_USER, ctx.user_id))

    candidates = []
    for scope, scope_id in scope_targets:
        candidates.extend(get_active_memories(
            db, ctx.org_id, scope, scope_id,
            kinds=ctx.kinds or None,
            include_sensitive=ctx.include_sensitive,
            limit=64,
        ))

    scored: List[RetrievedMemory] = []
    for record in candidates:
        if float(record.confidence) < READ_CONFIDENCE_FLOOR:
            continue
        if record.kind in ("dataset_semantics", "field_role") and (
            not _dataset_version_ok(record.value, ctx)
        ):
            continue
        reasons: List[str] = []
        subject_bonus, subject_reasons = _subject_bonus(record.subject, ctx)
        reasons.extend(subject_reasons)
        base = kind_base_weight(record.kind)
        confidence = float(record.confidence)
        freshness = _freshness_bonus(record.last_validated_at, now)
        priority = _SCOPE_PRIORITY.get(record.scope, 0.0)
        score = subject_bonus + base + confidence + freshness + priority
        reasons.append(f"kind_weight:{base:.1f}")
        reasons.append(f"confidence:{confidence:.2f}")
        if freshness:
            reasons.append(f"fresh:{freshness:.1f}")
        reasons.append(f"scope:{record.scope}")
        scored.append(RetrievedMemory(
            record=record, score=score,
            reasons=reasons[:6],
        ))

    scored.sort(
        key=lambda hit: (
            -hit.score,
            -_SCOPE_PRIORITY.get(hit.record.scope, 0.0),
            hit.record.subject,
            hit.record.id,
        )
    )
    cap = max(1, min(int(ctx.limit or DEFAULT_TOP_K), HARD_TOP_K_CAP))
    return scored[:cap]


def latest_resolved_place(
    db: Session,
    *,
    org_id: str,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[RetrievedMemory]:
    """R6 复用口：最近仍可信的 resolved_place（session → project → user）。"""
    hits = retrieve_memories(
        db,
        MemoryQueryContext(
            org_id=org_id,
            session_id=session_id,
            project_id=project_id,
            user_id=user_id,
            kinds=(KIND_RESOLVED_PLACE,),
            limit=1,
        ),
    )
    return hits[0] if hits else None


def lookup_dataset_memory(
    db: Session,
    *,
    org_id: str,
    project_id: Optional[str],
    dataset_key: str,
    version_token: Optional[str] = None,
) -> Dict[str, object]:
    """R6 复用口：某 dataset 的已学语义/字段角色（版本兼容校验内置）。

    返回 ``{"semantics": record|None, "field_roles": [record...]}``；
    ``version_token`` 给定时只返回兼容行（stale 行不召回，由 harvest 失效）。
    """
    ctx = MemoryQueryContext(
        org_id=org_id,
        project_id=project_id,
        subjects=(dataset_key,),
        limit=HARD_TOP_K_CAP,
    )
    if version_token is not None:
        ctx = MemoryQueryContext(
            org_id=org_id,
            project_id=project_id,
            subjects=(dataset_key,),
            dataset_versions={dataset_key: version_token},
            limit=HARD_TOP_K_CAP,
        )
    hits = retrieve_memories(db, ctx)
    semantics = next(
        (h.record for h in hits if h.record.kind == "dataset_semantics"), None
    )
    roles = [h.record for h in hits if h.record.kind == "field_role"]
    return {"semantics": semantics, "field_roles": roles}


__all__ = [
    "READ_CONFIDENCE_FLOOR",
    "DEFAULT_TOP_K",
    "HARD_TOP_K_CAP",
    "MemoryQueryContext",
    "retrieve_memories",
    "latest_resolved_place",
    "lookup_dataset_memory",
]
