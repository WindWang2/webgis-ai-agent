"""Pi hot-path assembly for the layered GIS context (ADR-0206 D5/D6).

One entry point — :func:`assemble_gis_context_card` — called from
``chat._build_cartography_turn_context`` after the existing verdict/memory
blocks. Contract:

- **Default ON, killable**: ``GIS_CONTEXT_SCOPES`` (default "1"); ``0``
  restores pre-ADR-0206 behavior exactly.
- **Graceful no-op**: no mission / no project / backend absent / any DB
  error → empty string + a receipt carrying the miss reason. Injection is
  fail-open; *invalidation is fail-closed* (applied and persisted before
  rendering — a render failure never undoes it).
- **Bounded**: one durable mission binding per session, working-context
  payload ≤16 KB, card ≤1600 chars, one bounded receipt log per turn.
- **Isolation**: org-mismatched contexts never load; project-scoped
  contexts never render outside their project; sessions never share
  bindings.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from app.services.gis_context.card import (
    ContextCardReceipt,
    render_gis_context_card,
)
from app.services.gis_context.flags import (
    COMBINED_BUDGET_CHARS,
    context_scopes_enabled,
)
from app.services.gis_context.observation import (
    diff_against,
    observe_session,
)
from app.services.gis_context.scope import mission_scope_ref
from app.services.gis_context.working_context import GISWorkingContext

logger = logging.getLogger(__name__)

MISSION_BINDING_KEY = "_mission_binding"
_LOG_EVERY_N = 1


def _store() -> Any:
    from app.services.gis_context.store import WorkingContextStore

    return WorkingContextStore()


def _resolve_binding(state: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Durable binding first (survives restarts), process-local fallback."""
    if isinstance(state, dict):
        raw = state.get(MISSION_BINDING_KEY)
        if isinstance(raw, dict) and raw.get("mission_id"):
            return {
                "mission_id": str(raw.get("mission_id") or "")[:64],
                "org_id": str(raw.get("org_id") or "")[:64],
            }
    return {}


async def _persist_binding(session_id: str, mission_id: str, org_id: str) -> bool:
    """Durable binding write. Returns False so the caller can observe the
    failure — without it, a restart/FIFO eviction would re-bind a second
    mission for the same session (the ≤1-per-session bound is only closed
    when this write lands)."""
    try:
        from app.services.session_data import session_data_manager

        await session_data_manager.set_map_state(
            session_id, MISSION_BINDING_KEY,
            {"mission_id": mission_id[:64], "org_id": str(org_id or "")[:64]},
        )
        return True
    except Exception:  # noqa: BLE001 — binding persistence is best-effort;
        return False   # in-process session_ctx still carries it this turn


def _has_gis_work(mapspec: Optional[Dict[str, Any]]) -> bool:
    """Evidence gate for auto-bind: a mission is bound only once actual map
    work exists (≥1 layer or ≥1 source) — chatty sessions never create rows.
    """
    if not isinstance(mapspec, dict):
        return False
    layers = mapspec.get("layers")
    sources = mapspec.get("sources")
    return bool((isinstance(layers, list) and layers) or (isinstance(sources, dict) and sources))


def _auto_bind_mission(
    *,
    session_id: str,
    org_id: str,
    user_id: str,
    project_id: str,
    root_goal: str,
) -> str:
    """Create-once-per-session mission bind. Gated by ``GIS_CONTEXT_SCOPES``
    (not ``GIS_MISSION_HOTPATH`` — that flag owns the swarm blast radius).
    Returns mission_id or "" (never raises)."""
    try:
        from app.services.mission_runtime.service import (
            get_mission_runtime,
            mission_runtime_enabled,
        )

        if not mission_runtime_enabled():
            return ""
        runtime = get_mission_runtime()
        rec = runtime.create(
            org_id=str(org_id or "0")[:64],
            user_id=str(user_id or "")[:64],
            project_id=project_id or None,
            root_goal=str(root_goal or "")[:2000],
            session_id=str(session_id or "")[:64],
        )
        return str(rec.mission_id)[:64]
    except Exception as exc:  # noqa: BLE001 — bind is additive, never a turn blocker
        logger.debug("[gis_context] auto-bind failed: %s", type(exc).__name__)
        return ""


def _load_and_maybe_purge(
    mission_id: str, *, org_id: str
) -> tuple:
    """Load the working context; lazily purge when the mission is terminal.
    Returns (context, miss_reason, mission_goal_revision)."""
    store = _store()
    wc = store.load(mission_id, org_id=org_id)
    goal_revision = 0
    try:
        from app.services.mission_runtime.contracts import is_terminal
        from app.services.mission_runtime.service import get_mission_runtime

        rec = get_mission_runtime().store.get_mission(mission_id, org_id=org_id or None)
        if rec is not None:
            try:
                goal_revision = int(getattr(rec, "goal_revision", 0) or 0)
            except (TypeError, ValueError):
                goal_revision = 0
            state_val = rec.state.value if hasattr(rec.state, "value") else str(rec.state)
            if is_terminal(state_val):
                store.purge(mission_id)
                return None, "mission_terminal", goal_revision
    except Exception:  # noqa: BLE001 — mission lookup is a guard, not a gate
        pass
    if wc is None:
        return None, "no_context", goal_revision
    return wc, "", goal_revision


def _fetch_reuse_candidates(wc: GISWorkingContext, *, project_id: str, limit: int = 3):
    """Sync DB retrieval — runs inside ``asyncio.to_thread`` only."""
    try:
        from app.core.database import SessionLocal
        from app.services.project_knowledge.retrieval import (
            ReuseQuery,
            find_reuse_candidates,
        )

        with SessionLocal() as db:
            return find_reuse_candidates(
                db,
                org_id=wc.org_id,
                project_id=project_id,
                query=ReuseQuery(
                    bbox=wc.basis.aoi_bbox,
                    temporal_label=wc.basis.time_period or None,
                    limit=limit,
                ),
            )
    except Exception:  # noqa: BLE001 — reuse hints are additive
        return []


async def assemble_gis_context_card(
    session_id: str,
    *,
    org_id: str = "",
    project_id: str = "",
    user_id: str = "",
    turn_id: str = "",
    query_text: str = "",
    state: Optional[Dict[str, Any]] = None,
    mapspec: Optional[Dict[str, Any]] = None,
    claim_store: Any = None,
    budget_used: int = 0,
    include_reuse: bool = True,
) -> Tuple[str, ContextCardReceipt]:
    """Assemble the bounded ``[GIS_CONTEXT]`` block for the next Pi turn.

    Returns ``(text, receipt)``; ``text`` is "" whenever nothing qualified.
    ``include_reuse=False`` skips the reuse section entirely (the caller
    already injected a ``<project_knowledge>`` block — M5 no-duplicate
    injection).
    """
    receipt = ContextCardReceipt()
    if not session_id or not context_scopes_enabled():
        receipt.skipped_reason = "flag_off" if not context_scopes_enabled() else "no_session"
        return "", receipt

    # 1) binding (durable → process-local) or evidence-gated auto-bind
    binding = _resolve_binding(state)
    mission_id = binding.get("mission_id", "")
    if not mission_id:
        try:
            from app.services.gis_harness.hotpath_convergence.session_ctx import (
                get_turn_context,
            )

            ctx = get_turn_context(session_id, tenant_id=org_id)
            mission_id = str(getattr(ctx, "mission_id", "") or "")[:64]
            if claim_store is None:
                # Existing store only — never create one on the read path.
                claim_store = getattr(ctx, "claim_store", None)
        except Exception:  # noqa: BLE001
            mission_id = ""
    if not mission_id and _has_gis_work(mapspec):
        mission_id = await asyncio.to_thread(
            _auto_bind_mission,
            session_id=session_id,
            org_id=org_id,
            user_id=user_id,
            project_id=project_id,
            root_goal=query_text,
        )
        if mission_id:
            try:
                from app.services.gis_harness.hotpath_convergence.session_ctx import (
                    set_mission_id,
                )

                set_mission_id(session_id, mission_id, tenant_id=org_id)
            except Exception:  # noqa: BLE001
                pass
            persisted = await _persist_binding(session_id, mission_id, org_id)
            receipt.notes.append(
                "mission_bound" if persisted else "mission_bound_persist_failed"
            )
    if not mission_id:
        receipt.miss_reason = "no_mission"
        return "", receipt
    if binding and org_id and binding.get("org_id") and binding["org_id"] != org_id:
        receipt.miss_reason = "org_mismatch"
        return "", receipt

    # 2) load (+ lazy terminal purge) — off the event loop
    created = False
    try:
        wc, miss, goal_revision = await asyncio.to_thread(
            _load_and_maybe_purge, mission_id, org_id=org_id)
    except Exception as exc:  # noqa: BLE001
        receipt.miss_reason = f"load_failed:{type(exc).__name__}"
        return "", receipt
    if wc is None:
        receipt.miss_reason = miss or "no_context"
        if miss != "mission_terminal" and mapspec:
            # First observation for this mission — establish the basis.
            created = True
            wc = GISWorkingContext(
                mission_id=mission_id,
                org_id=str(org_id or "")[:64],
                project_id=str(project_id or "")[:64],
                user_id=str(user_id or "")[:64],
                goal_revision_mirror=int(goal_revision or 0),
            )
        else:
            return "", receipt
    # Scope guards via the scope contract (ADR-0206 D2): a project-scoped
    # context never renders outside its project; org-carrying contexts never
    # render outside their org.
    scope = wc.scope_ref()
    if not scope.renderable_in(org_id=org_id, project_id=project_id):
        receipt.miss_reason = (
            "project_mismatch"
            if scope.project_id and scope.project_id != project_id
            else "org_mismatch"
        )
        return "", receipt

    # 3) observe → invalidate (fail-closed, persisted before render)
    obs = observe_session(state, mapspec)
    snapshot = None
    try:
        from app.services.gis_situation.diff import load_snapshot

        snapshot = await load_snapshot(session_id)
    except Exception:  # noqa: BLE001 — snapshot facts are additive
        snapshot = None
    if snapshot is not None:
        fresh = observe_session(state, mapspec, situation_snapshot=snapshot)
        obs.time_period = fresh.time_period or obs.time_period
        obs.recipe_id = fresh.recipe_id or obs.recipe_id
        obs.aoi_name = fresh.aoi_name or obs.aoi_name

    from app.services.gis_context.invalidation import apply_changes

    if goal_revision and wc.goal_revision_mirror != goal_revision:
        # Mission goal moved (revise_goal): mirror it so the next card can
        # flag decisions accepted under an older goal revision.
        wc.goal_revision_mirror = int(goal_revision)
        wc.mark_stale("goal", f"goal_revision={goal_revision}")
        receipt.notes.append("goal_revision_bumped")

    changes = diff_against(wc, obs)
    outcome = apply_changes(wc, changes, obs=obs, claim_store=claim_store, turn_id=turn_id)
    # Write only on real transitions — read-mostly turns never touch the DB.
    # expected = the on-disk revision we observed (pre-bump), for every
    # loaded context — revision 1 included (review P2-3).
    if outcome.changed or created:
        expected = wc.revision - 1 if not created else None
        try:
            await asyncio.to_thread(_store().save, wc, expected_revision=expected)
        except Exception as exc:  # noqa: BLE001 — persistence failure logged via receipt
            receipt.notes.append(f"save_failed:{type(exc).__name__}"[:48])

    # 5) render (budget-yielding — checked before the reuse fetch so oversized
    #    turns never pay for retrieval; the invalidation save above already
    #    happened and is fail-closed)
    if budget_used > COMBINED_BUDGET_CHARS:
        receipt.skipped_reason = "budget_skipped"
        _log_receipt(session_id, mission_id, receipt)
        return "", receipt

    # 4) project reuse candidates (read-only, additive; skipped when the
    #    caller already renders a project_knowledge block this turn)
    reuse = []
    if project_id and include_reuse:
        try:
            from app.services.project_knowledge import project_knowledge_enabled

            if project_knowledge_enabled():
                reuse = await asyncio.to_thread(
                    _fetch_reuse_candidates, wc, project_id=project_id
                ) or []
        except Exception:  # noqa: BLE001
            reuse = []

    text = render_gis_context_card(wc, reuse_candidates=reuse, receipt=receipt)
    if not text and not receipt.miss_reason:
        receipt.miss_reason = "empty_context"
    _log_receipt(session_id, mission_id, receipt)
    return text, receipt


_log_counter = {"n": 0}


def _log_receipt(session_id: str, mission_id: str, receipt: ContextCardReceipt) -> None:
    """One bounded info line per turn (hit/miss/reuse/stale observability)."""
    try:
        _log_counter["n"] += 1
        if _log_counter["n"] % _LOG_EVERY_N:
            return
        payload = receipt.to_bounded_dict()
        logger.info(
            "[gis_context] session=%s mission=%s hit=%s miss=%s stale=%s "
            "reuse=%d/%d/%d chars=%s notes=%s",
            str(session_id)[:24], str(mission_id)[:24],
            payload["hit"], payload["miss_reason"] or "-",
            payload["stale_fields"], payload["reuse_exact"],
            payload["reuse_partial"], payload["reuse_rejected"],
            payload["chars"], ",".join(payload["notes"]) or "-",
        )
    except Exception:  # noqa: BLE001 — logging must never break a turn
        pass


__all__ = [
    "MISSION_BINDING_KEY",
    "assemble_gis_context_card",
    "mission_scope_ref",
]
