"""Turn-end harvesting of project cartographic memory (ADR-0069 / spec P2).

The write seam: the chat route is the only layer that knows both the
``session_id`` (MapSpec/review owner) and the ``project_id`` (memory scope),
so harvesting happens here rather than in the session-scoped bridge or
lifecycle engine.

Harvesting is strictly best-effort and strictly **after** a verdict exists —
memory lags evidence by one step and can never short-circuit review
(ADR-0069 decision 2).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_session_local_factory: Optional[Callable] = None


def _get_session_local() -> Callable:
    """Return the sync session factory, honoring the test override."""
    if _session_local_factory is not None:
        return _session_local_factory
    from app.core.database import SessionLocal
    return SessionLocal


def set_session_local_factory(factory: Optional[Callable]) -> None:
    """Override the sync session factory (tests). Pass ``None`` to reset."""
    global _session_local_factory
    _session_local_factory = factory


def _harvest_sync(
    project_id: str, mapspec: dict, review: Optional[dict], recipe_id: str = "",
    session_id: str = "",
) -> dict:
    """Drift-then-harvest in one transaction.

    Order matters: distribution drift is applied FIRST so priors that the
    refreshed data invalidated are already ``stale`` before this turn's
    verdict gets a chance to re-establish them. A passing review then
    re-confirms whatever is still true (spec P3).

    ``recipe_id``（spec 开放问题 3）：本 session 计划所用的 recipe。评审
    通过时记一条 ``recipe_outcome`` 事实——"该方法在本项目产出过通过
    评审的图"，成为推荐排序的项目验证信号。
    """
    from app.services.cartography.project_memory import (
        _review_is_trustworthy,
        _review_tier,
        apply_distribution_drift,
        harvest_facts_from_review,
        record_fact,
    )

    SessionLocal = _get_session_local()
    with SessionLocal() as db:
        try:
            from sqlalchemy import select, update
            from app.models.project import CartoProjectFact, Project
            from app.lib.cartography.quality_loop import cartographic_fingerprint

            # Serialize the read/receipt/count transaction across workers.
            # A SQLite no-op write acquires its writer lock without changing
            # project activity; Postgres uses the stable project row lock.
            if db.get_bind().dialect.name == "sqlite":
                db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
            else:
                db.execute(select(Project.id).where(Project.id == project_id).with_for_update(nowait=True))
            events = apply_distribution_drift(
                db, project_id, _numeric_field_profiles(mapspec)
            )
            written = harvest_facts_from_review(db, project_id, mapspec, review)
            review_digest = hashlib.sha256(json.dumps(
                {"session": session_id, "map": cartographic_fingerprint(mapspec)},
                sort_keys=True, ensure_ascii=False, default=str,
            ).encode()).hexdigest()

            prior_outcome = db.execute(select(CartoProjectFact).where(
                CartoProjectFact.project_id == project_id,
                CartoProjectFact.kind == "recipe_outcome",
                CartoProjectFact.subject == recipe_id,
            ).with_for_update()).scalar_one_or_none() if recipe_id else None
            prior_payload = prior_outcome.payload if prior_outcome is not None and isinstance(prior_outcome.payload, dict) else {}
            receipts = prior_payload.get("outcome_receipts")
            receipts = [r for r in receipts if isinstance(r, str)] if isinstance(receipts, list) else []
            already_recorded = review_digest in receipts or (prior_outcome is not None and prior_outcome.evidence_digest == review_digest)
            if recipe_id and _review_is_trustworthy(review) and not already_recorded:
                outcome = record_fact(
                    db, project_id, "recipe_outcome", recipe_id,
                    {"task": _recipe_task(mapspec), "outcome_receipts": [*receipts[-127:], review_digest]},
                    fingerprint=None,
                    evidence_digest=review_digest,
                    validity_tier=_review_tier(review),
                )
                # Keep the last128 session/map receipts with the bounded fact;
                # review timing or repeated turns are not new outcome samples.
                if outcome is not None and outcome.status == "active" and outcome.evidence_digest == review_digest:
                    written += 1
                    from app.services.cartography.intent_learning import (
                        record_recipe_outcome,
                    )
                    record_recipe_outcome(db, recipe_id=recipe_id, success=True)
            if events or written:
                db.commit()
            else:
                db.rollback()
            return {"facts_written": written, "drift_events": len(events)}
        except Exception:
            db.rollback()
            raise


def _recipe_task(mapspec: dict) -> str:
    """从 MapSpec 侧可得的任务线索（plan 的 gis_intent 不在此处；诚实留空
    优于编造）。cartographic_profile 若携带 intent 轨迹则用它。"""
    profile = mapspec.get("cartographic_profile")
    if isinstance(profile, dict):
        task = profile.get("task")
        if isinstance(task, str) and task:
            return task
    return ""


def _numeric_field_profiles(mapspec: dict) -> dict:
    """Collect source/field identities for only the fields actually classified on each source.

    Only fields a layer actually classifies on are drift-relevant: an unused
    column changing shape does not invalidate any cartographic prior. Reads
    the Spatial Meta Profile already embedded on MapSpec sources — no data
    materialization (metadata-first contract).
    """
    sources = mapspec.get("sources") if isinstance(mapspec.get("sources"), dict) else {}
    from app.services.cartography.memory_subjects import field_subject
    thematic_fields = set()
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        legend_spec = layer.get("legend_spec")
        if isinstance(legend_spec, dict) and isinstance(legend_spec.get("field"), str):
            source_id = layer.get("source")
            if isinstance(source_id, str):
                thematic_fields.add((source_id, legend_spec["field"]))
    if not thematic_fields:
        return {}
    collected: dict = {}
    for source_id, source in sources.items():
        if not isinstance(source, dict):
            continue
        profile = source.get("profile")
        fields = profile.get("fields") if isinstance(profile, dict) else None
        if not isinstance(fields, dict):
            continue
        for field, field_profile in fields.items():
            if (str(source_id), str(field)) in thematic_fields and isinstance(field_profile, dict):
                collected[field_subject(str(source_id), str(field))] = field_profile
    return collected


async def harvest_project_memory(
    session_id: Optional[str], project_id: Optional[str]
) -> dict:
    """Apply distribution drift and harvest facts from this session's review.

    Returns ``{"facts_written": int, "drift_events": int}`` (zeros when there
    is no project context, no passing verdict, or the ledger is unavailable).
    Never raises: memory is additive, and a harvest failure must not fail the
    user's turn.
    """
    empty = {"facts_written": 0, "drift_events": 0}
    if not session_id or not project_id:
        return empty
    try:
        from app.services.mapspec.store import mapspec_store_instance
        from app.services.session_data import session_data_manager

        state, mapspec = await asyncio.gather(
            session_data_manager.get_map_state(session_id),
            mapspec_store_instance.get_mapspec(session_id),
        )
        if not isinstance(mapspec, dict) or not mapspec:
            return empty
        review = state.get("_cartographic_review") if isinstance(state, dict) else None
        # Session 计划携带本轮实际选用的 recipe（plan_orchestrator 在意图
        # 解析时写入）。读不到就没有可记的方法结论——留空，不猜。
        recipe_id = ""
        try:
            from app.services.chat.plan_orchestrator import get_plan

            plan = get_plan(session_id)
            recipe_id = str(getattr(plan, "recipe_id", "") or "")
        except Exception:  # noqa: BLE001 — plan 缺席不阻断收割
            recipe_id = ""
        return await asyncio.to_thread(
            _harvest_sync, project_id, mapspec, review, recipe_id, session_id,
        )
    except Exception as ex:  # noqa: BLE001 — best-effort by contract
        logger.warning(
            "[CartoMemory] harvest skipped for session=%s project=%s: %s",
            session_id, project_id, ex,
        )
        return empty


__all__ = ["harvest_project_memory", "set_session_local_factory"]
