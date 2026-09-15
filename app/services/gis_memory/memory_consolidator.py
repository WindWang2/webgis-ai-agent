"""会话 Settle 记忆整合（ADR-0190）：晋升 / 固化 / 审计。

位点纪律（与 ADR-0183 harvest 同源——「记忆永远滞后证据一个身位」）：
``consolidate_session`` 在 chat 路由 turn 结束的 ``harvest_spatial_memory``
**之后**异步执行，绝不阻断 turn：

- **晋升**：session 记忆被检索命中 ≥ ``MIN_HITS`` 次（热索引 hit_count，
  重温不丢）且证据强度 ∈ 晋升白名单，才沉淀到长效作用域——习惯类
  （CRS 偏好 / 成功策略）→ user 作用域；实体类（地名 / 边界 / 数据集
  语义）→ project 作用域（无 project 不晋升，不造孤儿作用域）；
- **固化（pin）**：显式用户来源（决策/纠正）的记忆清空 ``expires_at``——
  人的显式表达永不过期；失败避坑类（tool_failure 证据）天然不在晋升
  白名单，维持短命（R2：失败记忆必须过期）；
- **晋升非旁路**：晋升写 = 以新作用域构造 :class:`MemoryWriteRequest` 走
  既有写入门（policy.evaluate），弱证据照样拒；value 带
  ``consolidated_from`` 审计标记；原 session 行保留到 TTL 自然失效。

全程 fail-safe：任何异常降级为计数返回，绝不抬进用户 turn。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from app.services.gis_memory import store as s
from app.services.gis_memory.contract import (
    KIND_BOUNDARY_REF,
    KIND_CRS_RESOLUTION,
    KIND_DATASET_SEMANTICS,
    KIND_FIELD_ROLE,
    KIND_RESOLVED_PLACE,
    KIND_SUCCESSFUL_STRATEGY,
    KIND_USER_CARTO_PREF,
    SCOPE_PROJECT,
    SCOPE_SESSION,
    SCOPE_USER,
    SOURCE_INTENT_RESOLUTION,
    SOURCE_REVIEW_PASSED,
    SOURCE_TOOL_RESULT,
    SOURCE_USER_CORRECTION,
    SOURCE_USER_DECISION,
    MemoryEvidence,
    MemoryWriteRequest,
    SpatialMemoryRecord,
)

logger = logging.getLogger(__name__)

#: 晋升所需的最小检索命中次数（进程内热索引计数，重温不丢）。
MIN_HITS = 2

#: 晋升白名单证据源（弱证据/短命证据不沉淀为长效记忆）。
PROMOTABLE_EVIDENCE = (
    SOURCE_REVIEW_PASSED,
    SOURCE_TOOL_RESULT,
    SOURCE_INTENT_RESOLUTION,
    SOURCE_USER_DECISION,
    SOURCE_USER_CORRECTION,
)

#: 显式偏好固化来源（永不过期）。
PIN_SOURCES = (SOURCE_USER_DECISION, SOURCE_USER_CORRECTION)

#: 习惯类 kind → user 作用域（跨会话个人习惯）。
HABIT_KINDS = (
    KIND_CRS_RESOLUTION,
    KIND_SUCCESSFUL_STRATEGY,
    KIND_USER_CARTO_PREF,
)

#: 实体类 kind → project 作用域（项目内共享事实）。
ENTITY_KINDS = (
    KIND_RESOLVED_PLACE,
    KIND_BOUNDARY_REF,
    KIND_DATASET_SEMANTICS,
    KIND_FIELD_ROLE,
)

_session_local_factory = None


def set_session_local_factory(factory) -> None:
    """测试 seam：与 harvest/queries 同款覆盖。"""
    global _session_local_factory
    _session_local_factory = factory


def _get_session_local():
    if _session_local_factory is not None:
        return _session_local_factory
    from app.core.database import SessionLocal

    return SessionLocal


def _target_scope(kind: str) -> Optional[str]:
    if kind in HABIT_KINDS:
        return SCOPE_USER
    if kind in ENTITY_KINDS:
        return SCOPE_PROJECT
    return None


def _hit_count(retriever, org_id: str, record: SpatialMemoryRecord) -> int:
    """命中计数：热索引优先，回退记录内上次持久化值。"""
    if retriever is not None:
        try:
            entry = retriever.index.get(org_id, record.id)
            if entry is not None:
                return int(entry.hit_count)
        except Exception:  # noqa: BLE001 — 计数缺席按 0（保守不晋升）
            pass
    value = record.value if isinstance(record.value, dict) else {}
    try:
        return int(value.get("hit_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def consolidate_session_sync(
    session_id: str,
    *,
    org_id: str,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    db=None,
    retriever=None,
) -> Dict[str, int]:
    """同步整合：扫描 session 记忆，执行固化与晋升（返回计数）。"""
    out: Dict[str, int] = {"promoted": 0, "pinned": 0, "considered": 0}
    if not session_id or not org_id or db is None:
        return out
    rows = s.get_active_memories(
        db, org_id, SCOPE_SESSION, session_id, limit=200
    )
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    for record in rows:
        out["considered"] += 1
        evidence = record.evidence if isinstance(record.evidence, dict) else {}
        source = str(evidence.get("source", ""))

        # 1) 显式偏好固化：清过期时间（人的显式表达永不过期）。
        if source in PIN_SOURCES and record.expires_at:
            row = db.get(_memory_model(), record.id)
            if row is not None:
                row.expires_at = None
                out["pinned"] += 1

        # 2) 晋升：白名单证据 + 足量命中 + 目标作用域身份可用。
        if source not in PROMOTABLE_EVIDENCE:
            continue
        if _hit_count(retriever, org_id, record) < MIN_HITS:
            continue
        target_scope = _target_scope(record.kind)
        if target_scope == SCOPE_USER:
            target_scope_id = user_id
        elif target_scope == SCOPE_PROJECT:
            target_scope_id = project_id
        else:
            continue
        if not target_scope_id:
            continue
        value = dict(record.value or {})
        # 注意键名：消毒层把 "session_id" 视为凭证形键剥除——审计键用
        # "from_session"（不在黑名单，可落库）。
        value["consolidated_from"] = {
            "from_session": session_id,
            "at": now_iso,
        }
        promoted = s.record_memory(db, MemoryWriteRequest(
            kind=record.kind,
            scope=target_scope,
            scope_id=target_scope_id,
            subject=record.subject,
            value=value,
            evidence=MemoryEvidence(source=source, method="consolidation"),
            confidence=float(record.confidence),
            org_id=org_id,
            user_id=user_id,
            refs=tuple(record.refs or ()),
        ))
        if promoted is not None:
            out["promoted"] += 1
    db.commit()
    return out


def _memory_model():
    from app.models.spatial_memory import GISSpatialMemory

    return GISSpatialMemory


async def consolidate_session(
    session_id: Optional[str],
    *,
    org_id: str,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    retriever=None,
) -> Dict[str, int]:
    """异步门（事件循环不碰同步 DB）；fail-safe，绝不抛。"""
    empty = {"promoted": 0, "pinned": 0, "considered": 0}
    if not session_id or not org_id:
        return empty
    try:
        factory = _get_session_local()
        if factory is None:
            return empty

        def _run() -> Dict[str, int]:
            db = factory()
            try:
                return consolidate_session_sync(
                    session_id, org_id=org_id, user_id=user_id,
                    project_id=project_id, db=db, retriever=retriever,
                )
            finally:
                try:
                    db.close()
                except Exception:  # noqa: BLE001
                    pass

        result = await asyncio.to_thread(_run)
        if result.get("promoted") or result.get("pinned"):
            logger.info(
                "[GISMemory] consolidated session=%s org=%s promoted=%s "
                "pinned=%s", session_id, org_id, result["promoted"],
                result["pinned"],
            )
        return result
    except Exception as exc:  # noqa: BLE001 — 整合是增值路径，绝不阻断 turn
        logger.warning(
            "[GISMemory] consolidation skipped session=%s: %s",
            session_id, exc,
        )
        return empty


async def consolidate_after_harvest(
    session_id: Optional[str],
    project_id: Optional[str],
    *,
    org_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, int]:
    """chat 路由 settle 位点的薄封装（harvest 之后调用；默认检索器计数）。"""
    from app.services.gis_memory.proactive_retriever import (
        default_proactive_retriever,
    )

    return await consolidate_session(
        session_id, org_id=org_id, user_id=user_id, project_id=project_id,
        retriever=default_proactive_retriever,
    )


__all__ = [
    "MIN_HITS",
    "PROMOTABLE_EVIDENCE",
    "PIN_SOURCES",
    "HABIT_KINDS",
    "ENTITY_KINDS",
    "consolidate_session_sync",
    "consolidate_session",
    "consolidate_after_harvest",
    "set_session_local_factory",
]
