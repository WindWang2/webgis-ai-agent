"""PlanStore — per-session current-plan persistence over session_data_manager.

The store is a thin write-through facade in front of the existing
``session_data_manager`` (design-v3 §store). A process-local LRU (capacity 200)
serves as cache-aside in front of the store: ``save`` writes through to both,
``load_current`` checks cache → store → populates cache. The store survives
worker restarts when Redis is present and degrades to in-memory without it —
same semantics as the rest of the repo.

Persistence reuses plan_mode's mechanism (app/services/plan_mode.py:199-233):
plans live as ``ref:plan-*`` items. The *current* plan per session lives at a
deterministic per-session ref registered under the alias ``plan-current``, so
``overwrite()`` updates it in place — no new ref id is minted per save (the
``store()``-mints-new-ref trap documented at plan_mode.py:216-221). History
plans (superseded) are registered under a per-plan-id alias ``plan-id:<plan_id>``
so ``get_by_id`` can address them deterministically.

Concurrency (P1-B / planning-reviewer P2-4 / adversarial P2-8):
- The process-local cache has a short TTL (``L1_TTL_SECONDS``, mirrored from
  session_data_redis.py) — in the multi-replica deployment a worker never
  serves a cached plan older than ~2s, so a stale worker's turn-end flush
  cannot silently revert a newer plan written by another worker.
- ``save`` carries a revision guard: when the *persisted* current plan has a
  different ``plan_id`` and a newer-or-equal ``revision`` than the plan being
  saved, the write is refused (log warning) and the store's plan is re-read
  into the process cache instead of clobbering. Last-writer-wins still holds
  for same-``plan_id`` revisions. ``supersede`` promotes a brand-new plan by
  design, so it bypasses the guard (``_promote=True``).

Restart-continuity bound (persistence P3): plans persist only for the
underlying session DATA_TTL (2h, ``session_data_redis.DATA_TTL``) — a worker
restart within that window resumes the current plan; beyond it the plan is gone
(``load_current`` returns None). History (superseded) plans are subject to the
same DATA_TTL *and* to per-session LRU eviction like any other session ref:
long-retired plans may be evicted and ``get_by_id`` then returns None.
"""
import logging
import time
from collections import OrderedDict
from typing import Any, Optional

from app.services.session_data import session_data_manager

from .models import CanonicalPlan, PlanStatus

logger = logging.getLogger(__name__)

# Deterministic per-session ref alias for the "current" plan.
CURRENT_PLAN_ALIAS = "plan-current"

# Cache-aside TTL — mirrors session_data_redis.L1_TTL_SECONDS: short on purpose,
# so a second worker's writes surface here within ~2s instead of being masked
# by a long-lived process-local copy (P1-B cross-worker staleness).
L1_TTL_SECONDS = 2.0


def _plan_id_alias(plan_id: str) -> str:
    """Deterministic history key (alias) for a superseded plan."""
    return f"plan-id:{plan_id}"


class _PlanLRU:
    """Tiny OrderedDict LRU with TTL — the process-local cache-aside.

    Entries expire after ``L1_TTL_SECONDS``; an expired entry is dropped and
    ``get`` returns None so the caller re-reads from the store (P1-B: a stale
    worker never serves a long-stale plan).
    """

    def __init__(self, capacity: int = 200):
        # key -> (value, expires_at_monotonic)
        self._data: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self.capacity = max(1, capacity)

    def get(self, key: str) -> Optional[Any]:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def put(self, key: str, value: Any) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = (value, time.monotonic() + L1_TTL_SECONDS)
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)

    def pop(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


class PlanStore:
    """Per-session plan persistence with a process-local cache in front."""

    def __init__(self, session_store: Any = None, capacity: int = 200):
        # Defaults to the repo singleton (memory backend when USE_REDIS=false);
        # injectable for tests.
        self._session_store = session_store if session_store is not None else session_data_manager
        self._cache = _PlanLRU(capacity=capacity)

    # ── cache helpers (worker-local cache management / tests) ──────────────

    def clear_cache(self) -> None:
        """Drop the process-local cache (worker-restart simulation)."""
        self._cache.clear()

    def peek(self, session_id: str) -> Optional[CanonicalPlan]:
        """Synchronous cache-aside peek — NEVER touches the session store.

        For sync callers (orchestrator ``get_plan``, decision/metrics logging)
        that only need the in-process canonical when one is already loaded.
        Returns None on a cold cache; use ``load_current`` for the async
        store read.
        """
        return self._cache.get(session_id)

    def cache_put(self, plan: CanonicalPlan) -> None:
        """Synchronous cache-aside write (mirrors the ``save`` write-through).

        For sync callers (``AgentPlanOrchestrator.set_plan``) that update the
        canonical before the async ``save`` lands. The persisted session-store
        copy is refreshed on the next ``save`` / ``supersede``.
        """
        self._cache.put(plan.session_id, plan)

    def forget(self, session_id: str) -> None:
        """Synchronous cache-only drop (mirrors ``clear`` without the tombstone).

        Used by the orchestrator's ``clear_plan``; the session-store tombstone
        is handled by the session clear path.
        """
        self._cache.pop(session_id)

    # ── persistence API ────────────────────────────────────────────────────

    async def load_current(self, session_id: str) -> Optional[CanonicalPlan]:
        """Load the session's current plan; None when absent. Never raises."""
        cached = self._cache.get(session_id)
        if cached is not None:
            return cached
        data = await self._safe_get(session_id, CURRENT_PLAN_ALIAS)
        if data is None:
            return None
        plan = self._safe_validate(data)
        if plan is None:
            return None
        self._cache.put(session_id, plan)
        return plan

    async def save(self, plan: CanonicalPlan, *, _promote: bool = False) -> None:
        """Persist ``plan`` as the session's current plan (write-through).

        Refreshes ``updated_at`` in place, then writes the LRU cache and the
        session store. The current ref key is deterministic per session (alias
        ``plan-current``): the first save mints it via ``store()``, every later
        save ``overwrite()``s the same ref in place.

        Revision guard (P1-B): when the *persisted* current plan belongs to a
        different ``plan_id`` and carries a newer-or-equal ``revision`` than
        ``plan``, this save refuses to clobber it — a stale worker must not
        revert a newer plan. It logs a warning and re-reads the store's plan
        into the process cache (converging the worker instead of fighting it).
        Same-``plan_id`` saves stay last-writer-wins regardless of revision.

        ``_promote`` (internal, used by ``supersede``) bypasses the guard: the
        caller deliberately replaced the current plan with a brand-new one.
        """
        plan.updated_at = time.time()
        self._cache.put(plan.session_id, plan)
        payload = plan.model_dump()

        ref_id = await self._session_store.resolve_alias(plan.session_id, CURRENT_PLAN_ALIAS)
        if ref_id != CURRENT_PLAN_ALIAS:
            if not _promote:
                persisted = await self._safe_get(plan.session_id, ref_id)
                current = self._safe_validate(persisted) if persisted is not None else None
                if (
                    current is not None
                    and current.plan_id != plan.plan_id
                    and current.revision >= plan.revision
                ):
                    logger.warning(
                        "PlanStore.save refused to clobber newer plan: session=%s "
                        "persisted plan_id=%s rev=%d vs saved plan_id=%s rev=%d "
                        "— re-reading persisted plan",
                        plan.session_id, current.plan_id, current.revision,
                        plan.plan_id, plan.revision,
                    )
                    self._cache.put(plan.session_id, current)
                    return
            if await self._session_store.overwrite(plan.session_id, ref_id, payload):
                return
            # overwrite failed (ref evicted / backend degraded) → re-mint below
        new_ref = await self._session_store.store(plan.session_id, payload, prefix="plan")
        await self._session_store.set_alias(plan.session_id, new_ref, CURRENT_PLAN_ALIAS)

    async def supersede(self, session_id: str, new_plan: CanonicalPlan) -> Optional[CanonicalPlan]:
        """Mark the current plan superseded and promote ``new_plan``.

        The old plan gets status ``superseded`` and is persisted under its
        plan-id history key (alias ``plan-id:<plan_id>``, deterministic per
        plan_id so re-superseding updates in place); ``new_plan`` becomes the
        session's current plan. Returns the superseded plan, or None when there
        was no current plan.
        """
        current = await self.load_current(session_id)
        if current is not None:
            current.status = PlanStatus.superseded
            current.updated_at = time.time()
            await self._save_history(session_id, current)
        # _promote=True: 新计划取代旧计划是 supersede 的显式意图，不走
        # save() 的跨 plan_id revision guard（否则会误拒绝自己的提升）。
        await self.save(new_plan, _promote=True)
        return current

    async def get_by_id(self, session_id: str, plan_id: str) -> Optional[CanonicalPlan]:
        """Load a plan by id: the current plan, or a superseded history plan."""
        if plan_id in (CURRENT_PLAN_ALIAS, "current"):
            return await self.load_current(session_id)
        current = await self.load_current(session_id)
        if current is not None and current.plan_id == plan_id:
            return current
        alias = _plan_id_alias(plan_id)
        ref_id = await self._session_store.resolve_alias(session_id, alias)
        if ref_id != alias:
            data = await self._safe_get(session_id, ref_id)
            if data is not None:
                plan = self._safe_validate(data)
                if plan is not None:
                    return plan
        # Fallback: a history plan placed directly at ref:plan-<plan_id>.
        data = await self._safe_get(session_id, f"ref:plan-{plan_id}")
        return self._safe_validate(data)

    async def clear(self, session_id: str) -> None:
        """Drop the current plan for the session (cache + store).

        ``session_data_manager`` has no single-key delete, so the current ref is
        tombstoned with None — a later ``load_current`` returns None, and a
        later ``save`` overwrites the tombstone in place. Superseded history
        plans remain addressable via ``get_by_id``.
        """
        self._cache.pop(session_id)
        ref_id = await self._session_store.resolve_alias(session_id, CURRENT_PLAN_ALIAS)
        if ref_id != CURRENT_PLAN_ALIAS:
            await self._session_store.overwrite(session_id, ref_id, None)

    # ── internals ──────────────────────────────────────────────────────────

    async def _save_history(self, session_id: str, plan: CanonicalPlan) -> None:
        """Persist a superseded plan under its deterministic plan-id alias."""
        payload = plan.model_dump()
        alias = _plan_id_alias(plan.plan_id)
        ref_id = await self._session_store.resolve_alias(session_id, alias)
        if ref_id != alias:
            if await self._session_store.overwrite(session_id, ref_id, payload):
                return
        new_ref = await self._session_store.store(session_id, payload, prefix="plan")
        await self._session_store.set_alias(session_id, new_ref, alias)

    async def _safe_get(self, session_id: str, key: str) -> Optional[Any]:
        """Store read that never raises (store miss / backend error → None)."""
        try:
            return await self._session_store.get(session_id, key)
        except Exception as e:  # noqa: BLE001
            logger.warning("PlanStore.get failed session=%s key=%s: %s", session_id, key, e)
            return None

    def _safe_validate(self, data: Any) -> Optional[CanonicalPlan]:
        """Deserialize a stored payload; corrupt/missing payloads → None."""
        if not isinstance(data, dict):
            return None
        try:
            return CanonicalPlan.model_validate(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("PlanStore: stored payload is not a valid CanonicalPlan: %s", e)
            return None


# Module-level singleton, mirroring plan_orchestrator's pattern.
plan_store = PlanStore()
