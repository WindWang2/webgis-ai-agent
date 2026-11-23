"""Turn-context receipt lifecycle + session source policy store (G43, ADR-0295).

Three durable faces over the two G43 tables (no second knowledge/memory
store — policy rows hold only server-minted source ids; receipt rows hold
only bounded projections):

- **Receipt lifecycle** — the bridge records what actually happened to one
  Pi turn's assembled context: ``record_assembly`` (typed/legacy assembly
  done) → ``record_not_sent`` / ``record_submitted`` (disposition) →
  ``record_turn_outcome``. All updates are idempotent on ``turn_id`` and
  monotonic (a later, weaker observation can never rewrite a stronger one —
  e.g. a duplicate ``assembled`` event after ``submitted`` is a no-op).
- **Policy CAS** — ``load_policy`` / ``update_policy`` with optimistic
  ``policy_version``; concurrent writers get ``PolicyConflict`` carrying the
  current row.
- **Source catalog** — the authorized optional-source list for one session
  (project knowledge entries + cartography memory facts + the gis_memory
  block), each with its authoritative version token. Used for policy
  validation (server-minted ids only; cross-tenant/unauthorized ids are
  rejected without existence leaks) and the UI workbench.

Discipline: every function is fail-open for the *turn path* (a receipt
write failure must never break a live turn) and fail-closed for the *policy
path* (an unauthorized/unknown source id is rejected).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


from app.services.context_assembly.source_policy import (
    AUTO_POLICY,
    SourceKind,
    SourcePolicy,
    validate_policy_payload,
)

logger = logging.getLogger(__name__)

#: closed not_sent reason codes (G37 model gate / transport failures)
NOT_SENT_REASONS = (
    "model_budget_rejected",
    "model_apply_error",
    "pi_send_error",
    "image_gate_rejected",
    "assembly_rejected",
)

#: monotonic rank for receipt status (a stronger terminal state wins)
_STATUS_RANK = {"assembled": 1, "not_sent": 2, "unknown": 2, "submitted": 3}


class PolicyConflict(Exception):
    """CAS conflict — carries the current policy for the caller to merge."""

    def __init__(self, current: SourcePolicy) -> None:
        super().__init__("policy_version conflict")
        self.current = current


class SourceRejected(Exception):
    """A submitted source id is unknown/unauthorized for this session."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_policy(row) -> SourcePolicy:
    preferred = tuple(str(x) for x in (row.preferred or []) if isinstance(x, str))
    excluded = tuple(str(x) for x in (row.excluded or []) if isinstance(x, str))
    return SourcePolicy(
        session_id=str(row.session_id),
        mode=str(row.mode or "auto"),
        preferred=preferred,
        excluded=excluded,
        policy_version=int(row.policy_version or 1),
    )


async def load_policy(session_id: str, db=None) -> SourcePolicy:
    """Effective policy for a session (missing row ⇒ AUTO, never raises
    beyond a caller-supplied session's own DB errors)."""
    if not session_id:
        return AUTO_POLICY
    owns_session = db is None
    if owns_session:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await load_policy(session_id, db=db)
    row = await db.get(_policy_row_cls(), session_id)
    if row is None:
        return SourcePolicy(session_id=session_id, mode="auto", policy_version=0)
    return _row_to_policy(row)


def _policy_row_cls():
    from app.models.db_model import SessionSourcePolicyRow

    return SessionSourcePolicyRow


def _receipt_row_cls():
    from app.models.db_model import TurnContextReceiptRow

    return TurnContextReceiptRow


async def update_policy(
    *,
    session_id: str,
    org_id: str,
    project_id: str,
    user_id: str,
    mode: str,
    preferred: List[str],
    excluded: List[str],
    expected_version: int,
    authorized_source_ids: Optional[List[str]] = None,
    db=None,
) -> SourcePolicy:
    """CAS policy write.

    ``authorized_source_ids`` is the server-side catalog (already
    authorization-checked by the caller). Any id outside it is rejected with
    a unified error (no existence leak across tenants/projects).
    """
    preferred, excluded, err = validate_policy_payload(
        mode=mode, preferred=preferred, excluded=excluded
    )
    if err:
        raise SourceRejected(err)
    if mode == "auto" and (preferred or excluded):
        # auto 行必须干净：不接受携带选择的 auto（回读语义会漂移）。
        raise SourceRejected("auto_with_selection")
    if authorized_source_ids is not None:
        allowed = set(authorized_source_ids)
        unknown = [s for s in (*preferred, *excluded) if s not in allowed]
        if unknown:
            raise SourceRejected("source_not_authorized")

    owns_session = db is None
    if owns_session:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await update_policy(
                session_id=session_id, org_id=org_id, project_id=project_id,
                user_id=user_id, mode=mode, preferred=preferred,
                excluded=excluded, expected_version=expected_version,
                authorized_source_ids=None, db=db,
            )
    # review P2：CAS 必须原子 —— 条件 UPDATE（WHERE policy_version = expected）
    # 的 rowcount 即裁决；两个并发 PUT 不会都胜出（丢失更新不可能）。
    Row = _policy_row_cls()
    row = await db.get(Row, session_id)
    if row is None:
        if expected_version not in (0, 1):
            raise PolicyConflict(SourcePolicy(session_id=session_id))
        row = Row(
            session_id=session_id, org_id=org_id or "", project_id=project_id or "",
            user_id=user_id or "", mode=mode, preferred=list(preferred),
            excluded=list(excluded), policy_version=1,
            updated_by_user_id=user_id or None,
        )
        db.add(row)
        try:
            await db.commit()
        except Exception as exc:  # noqa: BLE001 — 并发首插竞态 → 冲突
            await db.rollback()
            current = await db.get(Row, session_id)
            if current is not None:
                raise PolicyConflict(_row_to_policy(current)) from exc
            raise
        return _row_to_policy(row)

    from sqlalchemy import update as _update

    result = await db.execute(
        _update(Row)
        .where(Row.session_id == session_id, Row.policy_version == int(expected_version))
        .values(
            org_id=org_id or row.org_id,
            project_id=project_id or row.project_id,
            user_id=user_id or row.user_id,
            mode=mode,
            preferred=list(preferred),
            excluded=list(excluded),
            policy_version=int(expected_version) + 1,
            updated_by_user_id=user_id or None,
        )
    )
    if result.rowcount != 1:
        await db.rollback()
        current = await db.get(Row, session_id)
        raise PolicyConflict(
            _row_to_policy(current) if current is not None
            else SourcePolicy(session_id=session_id)
        )
    await db.commit()
    refreshed = await db.get(Row, session_id)
    return _row_to_policy(refreshed if refreshed is not None else row)


# ---------------------------------------------------------------------------
# Receipt lifecycle (idempotent on turn_id, monotonic status)
# ---------------------------------------------------------------------------


async def record_assembly(
    *,
    session_id: str,
    turn_id: str,
    run_id: str = "",
    org_id: str = "",
    project_id: str = "",
    user_id: str = "",
    assembly_mode: str,
    policy_version: int,
    digest: str,
    payload: Dict[str, Any],
    db=None,
) -> Optional[int]:
    """Insert the receipt row for one turn's completed assembly.

    Idempotent: an existing row for the turn is left untouched (late
    duplicate assembly events never rewrite a submission state). Returns
    the row id, or None when persistence failed (caller keeps the in-memory
    receipt — read model then honestly reports unknown/missing).
    """
    if not turn_id:
        return None
    owns_session = db is None
    if owns_session:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await record_assembly(
                session_id=session_id, turn_id=turn_id, run_id=run_id,
                org_id=org_id, project_id=project_id, user_id=user_id,
                assembly_mode=assembly_mode, policy_version=policy_version,
                digest=digest, payload=payload, db=db,
            )
    try:
        Row = _receipt_row_cls()
        existing = (
            await db.execute(_select_row(Row, turn_id))
        ).scalar_one_or_none()
        if existing is not None:
            return int(existing.id)
        row = Row(
            session_id=session_id or "",
            turn_id=turn_id,
            run_id=run_id or None,
            org_id=(org_id or "")[:64],
            project_id=(project_id or "")[:255],
            user_id=(user_id or "")[:255],
            assembly_mode=assembly_mode[:16],
            policy_version=int(policy_version or 0),
            digest=(digest or "")[:64],
            status="assembled",
            payload=payload,
        )
        db.add(row)
        await db.commit()
        return int(row.id)
    except Exception as exc:  # noqa: BLE001 — 回执落盘失败不阻断 turn（C22）
        logger.warning(
            "[turn-receipt] record_assembly failed turn=%s: %s", turn_id, exc
        )
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


async def _update_receipt(
    turn_id: str, *, fields: Dict[str, Any], require_rank: Optional[int] = None, db=None
) -> bool:
    """Monotonic field update.

    Status changes carry ``require_rank``: a row already at a stronger rank
    rejects the weaker late observation (never downgrades). Non-status
    enrichment (turn_outcome / user_message_id) passes ``require_rank=None``
    and applies at any status.
    """
    if not turn_id:
        return False
    owns_session = db is None
    if owns_session:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await _update_receipt(
                turn_id, fields=fields, require_rank=require_rank, db=db
            )
    try:
        Row = _receipt_row_cls()
        from sqlalchemy import update as _update

        # review P2：rank 守卫必须原子 —— 条件 UPDATE 把单调校验放进 WHERE
        # （单语句裁决）；先 SELECT 再提交会让并发弱写者双双通过检查，后
        # 提交者以弱状态覆盖强状态。
        stmt = _update(Row).where(Row.turn_id == turn_id)
        if require_rank is not None:
            new_rank = _STATUS_RANK.get(str(fields.get("status", "")), 0)
            weaker = tuple(s for s, rank in _STATUS_RANK.items() if rank < new_rank)
            stmt = stmt.where(Row.status.in_(weaker))
        result = await db.execute(stmt.values(**fields))
        if result.rowcount != 1:
            return False  # no row, or a stronger/equal state — never rewrite
        await db.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[turn-receipt] update failed turn=%s: %s", turn_id, exc)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False


def _select_row(Row, turn_id: str):
    from sqlalchemy import select as _select

    return _select(Row).where(Row.turn_id == turn_id)


async def record_not_sent(
    turn_id: str, *, reason: str, db=None
) -> bool:
    if reason not in NOT_SENT_REASONS:
        reason = "pi_send_error"
    return await _update_receipt(
        turn_id,
        fields={"status": "not_sent", "not_sent_reason": reason},
        require_rank=_STATUS_RANK["not_sent"],
        db=db,
    )


async def record_submitted(
    turn_id: str, *, disposition: str, db=None
) -> bool:
    # review P3：Pi 未来若新增 disposition 词表，收敛为 started（提交事实
    # 已确证，不因词表演进丢失转移）。
    if disposition not in ("started", "queued", "handled"):
        disposition = "started"
    return await _update_receipt(
        turn_id,
        fields={"status": "submitted", "disposition": disposition,
                "not_sent_reason": None},
        require_rank=_STATUS_RANK["submitted"],
        db=db,
    )


async def record_unknown(
    turn_id: str, *, reason: str = "", db=None
) -> bool:
    """Honest unknown (process died / socket lost mid-turn). Only applies to
    rows still in ``assembled`` — a confirmed submitted/not_sent state is
    never rewritten by a weaker observation."""
    return await _update_receipt(
        turn_id,
        fields={"status": "unknown"},
        require_rank=_STATUS_RANK["unknown"],
        db=db,
    )


async def record_turn_outcome(
    turn_id: str, *, outcome: str, db=None
) -> bool:
    if outcome not in ("completed", "failed", "cancelled"):
        return False
    return await _update_receipt(
        turn_id,
        fields={"turn_outcome": outcome},
        require_rank=None,  # 独立维度：任何状态都可补充终态
        db=db,
    )


async def link_user_message(turn_id: str, message_id: int, db=None) -> bool:
    return await _update_receipt(
        turn_id, fields={"user_message_id": int(message_id)}, require_rank=None, db=db
    )


async def get_receipt(
    session_id: str, turn_id: str, *, db=None
) -> Optional[Dict[str, Any]]:
    """Bounded read model for one turn's receipt (None = no durable record)."""
    owns_session = db is None
    if owns_session:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await get_receipt(session_id, turn_id, db=db)
    Row = _receipt_row_cls()
    row = (
        await db.execute(_select_row(Row, turn_id))
    ).scalar_one_or_none()
    if row is None or (session_id and str(row.session_id) != str(session_id)):
        return None
    return _receipt_read_model(row)


def _receipt_read_model(row) -> Dict[str, Any]:
    payload = dict(row.payload or {})
    return {
        "receipt_id": int(row.id),
        "session_id": str(row.session_id),
        "turn_id": str(row.turn_id),
        "run_id": str(row.run_id or ""),
        "assembly_mode": str(row.assembly_mode),
        "policy_version": int(row.policy_version or 0),
        "digest": str(row.digest or ""),
        "status": str(row.status),
        "not_sent_reason": row.not_sent_reason,
        "disposition": row.disposition,
        "turn_outcome": row.turn_outcome,
        "user_message_id": row.user_message_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "payload": payload,
    }


async def list_receipts(
    session_id: str, *, limit: int = 20, db=None
) -> List[Dict[str, Any]]:
    """Newest-first bounded receipt list for the session panel."""
    owns_session = db is None
    if owns_session:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await list_receipts(session_id, limit=limit, db=db)
    Row = _receipt_row_cls()
    stmt = (
        _select_all(Row)
        .where(Row.session_id == session_id)
        .order_by(Row.created_at.desc(), Row.id.desc())
        .limit(max(1, min(limit, 100)))
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_receipt_read_model(r) for r in rows]


def _select_all(Row):
    from sqlalchemy import select as _select

    return _select(Row)


# ---------------------------------------------------------------------------
# Authorized optional-source catalog (single authority for policy validation)
# ---------------------------------------------------------------------------


async def list_authorized_sources(
    *,
    session_id: str,
    org_id: str,
    project_id: str,
    user_id: str,
    db=None,
) -> List[Dict[str, Any]]:
    """当前会话用户可选资料目录（有界：ids + 版本 + 摘要，无正文）。

    粒度：pk 条目 / cm 事实为条目级；gis_memory 为整块（gm:block）。
    授权：项目级 ``get_project_with_auth`` 在此执行（与 memory/knowledge
    builder 同一授权边界）—— 跨租户/跨项目一律空目录（不泄漏存在性）。
    """
    sources: List[Dict[str, Any]] = []
    if not (project_id and org_id):
        return sources
    import asyncio

    def _project_authorized() -> bool:
        from app.core.database import SessionLocal
        from app.services.project_service import ProjectService

        try:
            org_int: Optional[int]
            try:
                org_int = int(org_id)
            except (TypeError, ValueError):
                org_int = None
            with SessionLocal() as db_sync:
                project = ProjectService.get_project_with_auth(
                    db_sync, project_id, user_id=user_id or None,
                    org_id=org_int,
                )
                return project is not None
        except Exception:  # noqa: BLE001 — 授权面失败 = 拒绝（fail-closed）
            return False

    if not await asyncio.to_thread(_project_authorized):
        return sources
    sources.extend(await _project_knowledge_sources(
        org_id=org_id, project_id=project_id))
    sources.extend(await _carto_memory_sources(project_id=project_id))
    sources.append({
        "source_id": "gm:block",
        "kind": SourceKind.GIS_MEMORY_BLOCK.value,
        "label": "GIS 空间记忆（整块）",
        "version_token": "",
        "selectable": True,
        "note": "org 级投影文本，块级粒度",
    })
    return sources[:200]


async def _project_knowledge_sources(*, org_id: str, project_id: str) -> List[Dict[str, Any]]:
    try:
        from app.services.project_knowledge.store import get_active_entries

        def _sync() -> List[Dict[str, Any]]:
            from app.core.database import SessionLocal

            with SessionLocal() as db:
                entries = get_active_entries(
                    db, org_id=str(org_id), project_id=str(project_id), limit=100
                )
                return [
                    {
                        "source_id": f"pk:{e.id}",
                        "kind": SourceKind.PROJECT_KNOWLEDGE_ENTRY.value,
                        "label": str(e.subject)[:64] or str(e.id)[:12],
                        "entity_kind": str(e.entity_kind),
                        "version_token": str(e.version_token)[:32],
                        "authority": f"{e.authority_store}:{e.authority_id}",
                        "selectable": True,
                    }
                    for e in entries
                ]

        import asyncio

        return await asyncio.to_thread(_sync)
    except Exception as exc:  # noqa: BLE001 — 目录缺席 = 空清单（不虚构）
        logger.debug("[turn-receipt] knowledge catalog unavailable: %s", exc)
        return []


async def _carto_memory_sources(*, project_id: str) -> List[Dict[str, Any]]:
    try:
        import asyncio

        def _sync() -> List[Dict[str, Any]]:
            from app.core.database import SessionLocal
            from app.services.cartography.project_memory import get_active_facts

            with SessionLocal() as db:
                facts = get_active_facts(db, str(project_id), limit=100)
                return [
                    {
                        "source_id": f"cm:{fact.id}",
                        "kind": SourceKind.CARTO_MEMORY_FACT.value,
                        "label": (
                            f"{fact.kind} · {fact.subject}"[:64]
                            if fact.subject else str(fact.kind)[:32]
                        ),
                        "version_token": (
                            f"lv:{fact.last_verified_at.isoformat()[:19]}"
                            if fact.last_verified_at else ""
                        ),
                        "selectable": True,
                    }
                    for fact in facts
                ]

        return await asyncio.to_thread(_sync)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[turn-receipt] memory catalog unavailable: %s", exc)
        return []


__all__ = [
    "NOT_SENT_REASONS",
    "PolicyConflict",
    "SourceRejected",
    "get_receipt",
    "link_user_message",
    "list_authorized_sources",
    "list_receipts",
    "load_policy",
    "record_assembly",
    "record_not_sent",
    "record_submitted",
    "record_turn_outcome",
    "record_unknown",
    "update_policy",
]
