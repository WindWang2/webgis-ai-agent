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
            events = apply_distribution_drift(
                db, project_id, _numeric_field_profiles(mapspec)
            )
            written = harvest_facts_from_review(db, project_id, mapspec, review)
            if recipe_id and _review_is_trustworthy(review):
                record_fact(
                    db, project_id, "recipe_outcome", recipe_id,
                    {"task": _recipe_task(mapspec)},
                    fingerprint=None,
                    validity_tier=_review_tier(review),
                )
                written += 1
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
    """Collect ``field -> field_profile`` for numeric thematic fields.

    Only fields a layer actually classifies on are drift-relevant: an unused
    column changing shape does not invalidate any cartographic prior. Reads
    the Spatial Meta Profile already embedded on MapSpec sources — no data
    materialization (metadata-first contract).
    """
    sources = mapspec.get("sources") if isinstance(mapspec.get("sources"), dict) else {}
    thematic_fields = set()
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        legend_spec = layer.get("legend_spec")
        if isinstance(legend_spec, dict) and isinstance(legend_spec.get("field"), str):
            thematic_fields.add(legend_spec["field"])
    if not thematic_fields:
        return {}
    collected: dict = {}
    for source in sources.values():
        if not isinstance(source, dict):
            continue
        profile = source.get("profile")
        fields = profile.get("fields") if isinstance(profile, dict) else None
        if not isinstance(fields, dict):
            continue
        for field, field_profile in fields.items():
            if str(field) in thematic_fields and isinstance(field_profile, dict):
                collected[str(field)] = field_profile
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
            _harvest_sync, project_id, mapspec, review, recipe_id,
        )
    except Exception as ex:  # noqa: BLE001 — best-effort by contract
        logger.warning(
            "[CartoMemory] harvest skipped for session=%s project=%s: %s",
            session_id, project_id, ex,
        )
        return empty


__all__ = ["harvest_project_memory", "set_session_local_factory"]
