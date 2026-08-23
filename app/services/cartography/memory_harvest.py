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


def _harvest_sync(project_id: str, mapspec: dict, review: Optional[dict]) -> int:
    from app.services.cartography.project_memory import harvest_facts_from_review

    SessionLocal = _get_session_local()
    with SessionLocal() as db:
        try:
            written = harvest_facts_from_review(db, project_id, mapspec, review)
            if written:
                db.commit()
            else:
                db.rollback()
            return written
        except Exception:
            db.rollback()
            raise


async def harvest_project_memory(
    session_id: Optional[str], project_id: Optional[str]
) -> int:
    """Harvest shared-classification facts from this session's passing review.

    Returns the number of facts written (0 when there is no project context,
    no passing verdict, or the ledger is unavailable). Never raises: memory
    is additive, and a harvest failure must not fail the user's turn.
    """
    if not session_id or not project_id:
        return 0
    try:
        from app.services.mapspec.store import mapspec_store_instance
        from app.services.session_data import session_data_manager

        state, mapspec = await asyncio.gather(
            session_data_manager.get_map_state(session_id),
            mapspec_store_instance.get_mapspec(session_id),
        )
        if not isinstance(mapspec, dict) or not mapspec:
            return 0
        review = state.get("_cartographic_review") if isinstance(state, dict) else None
        return await asyncio.to_thread(_harvest_sync, project_id, mapspec, review)
    except Exception as ex:  # noqa: BLE001 — best-effort by contract
        logger.warning(
            "[CartoMemory] harvest skipped for session=%s project=%s: %s",
            session_id, project_id, ex,
        )
        return 0


__all__ = ["harvest_project_memory", "set_session_local_factory"]
