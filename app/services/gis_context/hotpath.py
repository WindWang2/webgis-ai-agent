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
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_context.card import (
    ContextCardReceipt,
    render_gis_context_card,
)
from app.services.gis_context.flags import (
    COMBINED_BUDGET_CHARS,
    context_scopes_enabled,
    revalidation_enabled,
)
from app.services.gis_context.observation import (
    ContextChange,
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


def _reconcile_step(wc: GISWorkingContext, *, project_id: str) -> tuple:
    """Dataset fingerprint reconciliation — runs inside ``asyncio.to_thread``
    with its own short DB session (bounded scalar reads, ADR-0215 D10).

    Returns ``(change_dicts, mutated, fingerprint_map)``: authority-drift
    events to fold into the invalidation pass, whether the basis was
    mutated (token learning / drift adoption → the caller persists), and
    the resolved authority-id → token map for the reuse query.
    """
    if not project_id:
        return [], False, None
    try:
        from app.core.database import SessionLocal
        from app.services.gis_context.reuse_identity import (
            reconcile_dataset_fingerprints,
        )

        with SessionLocal() as db:
            result = reconcile_dataset_fingerprints(wc, db, project_id=project_id)
        return (
            list(result.events),
            bool(result.mutated),
            dict(result.dataset_fingerprints) if result.dataset_fingerprints else None,
        )
    except Exception:  # noqa: BLE001 — reconciliation is additive
        return [], False, None


def _fetch_reuse_candidates(
    wc: GISWorkingContext,
    *,
    project_id: str,
    dataset_fingerprints: Any = None,
    limit: int = 3,
):
    """Sync DB retrieval — runs inside ``asyncio.to_thread`` only.

    ``dataset_fingerprints`` is the turn's resolved authority map; ``None``
    falls back to the basis-recorded tokens (post-reconciliation contexts).
    An explicit empty dict disables fingerprints (kill-switch parity with
    the pre-ADR-0215 query)."""
    try:
        from app.core.database import SessionLocal
        from app.services.project_knowledge.retrieval import (
            find_reuse_candidates,
        )
        from app.services.gis_context.reuse_identity import reuse_query_from_context

        # None → derive from basis records; {} → explicitly fingerprint-less
        # (kill-switch parity); non-empty → the turn's resolved map.
        fp_arg = None if dataset_fingerprints is None else dict(dataset_fingerprints)
        query = reuse_query_from_context(
            wc, dataset_fingerprints=fp_arg, limit=limit,
        )
        with SessionLocal() as db:
            return find_reuse_candidates(
                db,
                org_id=wc.org_id,
                project_id=project_id,
                query=query,
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

    # 3) observe → reconcile fingerprints → invalidate (fail-closed,
    #    persisted before render). ``disk_revision`` is the CAS token we
    #    observed on load; every mutation below (invalidation bump,
    #    fingerprint learning, marker reconfirmation) folds into ONE save.
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

    disk_revision = int(wc.revision)
    changes = diff_against(wc, obs)

    # 3a) fingerprint reconciliation (ADR-0215 D10): learn authority tokens
    #     and surface authority-side drift into the same invalidation pass.
    resolved_fingerprints = None
    basis_mutated = False
    if project_id and revalidation_enabled():
        try:
            events, basis_mutated, resolved_fingerprints = await asyncio.to_thread(
                _reconcile_step, wc, project_id=project_id)
            if events:
                changes.extend(
                    ContextChange(kind=e["kind"], detail=e.get("detail", ""),
                                  ref_id=e.get("ref_id", ""))
                    for e in events
                )
            if basis_mutated:
                receipt.notes.append("fingerprints_reconciled")
        except Exception:  # noqa: BLE001 — reconciliation is additive
            basis_mutated = False
    elif project_id:
        # Kill-switch parity with the pre-ADR-0215 query: no fingerprints.
        resolved_fingerprints = {}

    outcome = apply_changes(wc, changes, obs=obs, claim_store=claim_store, turn_id=turn_id)

    # 3b) passive revalidation (ADR-0215 D1): markers whose attributed facts
    #     are all re-verified and whose field the observation confirms clear
    #     with a persisted receipt — never a timer. Passive *rejections*
    #     stay in-memory only (counted for observability, never persisted):
    #     a persistent blocker must not flood the receipt ring or turn every
    #     read-mostly turn into a write.
    rtv_restored = 0
    rtv_rejected = 0
    if revalidation_enabled():
        try:
            from app.services.gis_context.revalidation import reconfirm_markers

            rtv_receipts = reconfirm_markers(
                wc, obs, turn_id=turn_id, record_rejections=False) or []
            rtv_restored = sum(1 for r in rtv_receipts if r.verdict == "restored")
            rtv_rejected = len(rtv_receipts) - rtv_restored
            receipt.rtv_restored = rtv_restored
            receipt.rtv_rejected = rtv_rejected
            if rtv_restored:
                receipt.notes.append(f"rtv_restored={rtv_restored}")
        except Exception:  # noqa: BLE001 — reconfirmation is additive
            pass

    # Write only on real transitions — read-mostly turns never touch the DB.
    # expected = the on-disk revision observed at load, for every loaded
    # context (revision 1 included); None only for a context created here.
    if outcome.changed or created or basis_mutated or rtv_restored:
        expected = disk_revision if not created else None
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
                    _fetch_reuse_candidates, wc,
                    project_id=project_id,
                    dataset_fingerprints=resolved_fingerprints,
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
    """One bounded info line per turn (hit/miss/stale-reasons/reuse-reject
    reasons/revalidation — reason-grade observability, ADR-0215 D8)."""
    try:
        _log_counter["n"] += 1
        if _log_counter["n"] % _LOG_EVERY_N:
            return
        payload = receipt.to_bounded_dict()
        logger.info(
            "[gis_context] session=%s mission=%s hit=%s miss=%s stale=%s(%s) "
            "reuse=%d/%d/%d reject=%s rtv=%d/%d chars=%s notes=%s",
            str(session_id)[:24], str(mission_id)[:24],
            payload["hit"], payload["miss_reason"] or "-",
            payload["stale_fields"],
            ",".join(payload["stale_reason_kinds"]) or "-",
            payload["reuse_exact"],
            payload["reuse_partial"],
            payload["reuse_rejected"],
            ",".join(payload["reuse_reject_reasons"]) or "-",
            payload["rtv_restored"], payload["rtv_rejected"],
            payload["chars"], ",".join(payload["notes"]) or "-",
        )
    except Exception:  # noqa: BLE001 — logging must never break a turn
        pass


# ── Cross-session continuation (ADR-0215 D7) ─────────────────────────────

async def bind_session_mission(
    session_id: str,
    mission_id: str,
    *,
    org_id: str = "",
    project_id: str = "",
) -> Tuple[bool, str]:
    """Explicitly attach ``session_id`` to an existing mission's working
    context (the entry a new session needs to resume the same mission —
    durable bindings live in the *original* session's map_state).

    Guards, in order: non-empty args; context exists under the caller's org
    (org-mismatched rows are invisible — no existence leak); mission not
    terminal (a terminal mission is lazily purged and refused); scope
    renderable in the caller's org/project. User identity is attribution
    only — authorization is the mission runtime's (org + project here).

    Returns ``(ok, reason_code)`` — reason codes are stable for tools.
    """
    sid = str(session_id or "")[:64]
    mid = str(mission_id or "")[:64]
    if not sid or not mid:
        return False, "invalid_args"
    org = await _resolve_caller_org(sid, explicit_org=str(org_id or "")[:64])
    if not org:
        # Caller org unresolvable from any server-side source — refuse
        # rather than wild-card across tenants.
        return False, "no_org_context"
    wc, miss, _ = await asyncio.to_thread(_load_and_maybe_purge, mid, org_id=org)
    if wc is None:
        # Covers unknown mission, foreign-org mission (invisible) and
        # terminal mission (purged on load) — one honest code each.
        return False, (miss or "no_context")
    if not scope_renderable(wc, org_id=org_id, project_id=project_id):
        return False, "scope_mismatch"
    persisted = await _persist_binding(sid, mid, org_id)
    if not persisted:
        return False, "binding_persist_failed"
    return True, ""


def scope_renderable(
    wc: GISWorkingContext, *, org_id: str = "", project_id: str = ""
) -> bool:
    """Scope gate shared by the card path and the bind path (ADR-0206 D2)."""
    return wc.scope_ref().renderable_in(org_id=org_id, project_id=project_id)


async def _resolve_caller_org(session_id: str, *, explicit_org: str = "") -> str:
    """Caller-org resolution for session-only entry points (tool path).

    Order: explicit arg (chat callers) → durable binding org → server-side
    session turn-context tenant. Never model-supplied; "" = unresolvable
    (callers refuse rather than wild-card).
    """
    if explicit_org:
        return str(explicit_org)[:64]
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id) or {}
        binding = _resolve_binding(state if isinstance(state, dict) else None)
        if binding.get("org_id"):
            return str(binding["org_id"])[:64]
    except Exception:  # noqa: BLE001 — fall through to the tenant scan
        pass
    return _tenant_scan(session_id)


def _tenant_scan(session_id: str) -> str:
    try:
        from app.services.gis_harness.hotpath_convergence.session_ctx import (
            find_session_tenant,
        )

        return find_session_tenant(session_id)
    except Exception:  # noqa: BLE001
        return ""


# ── Active revalidation entry (ADR-0215 D1) ──────────────────────────────

def _db_token_resolver(project_id: str):
    """Token resolver bound to the project_dataset authority (own short
    session per call — revalidation runs at tool frequency, not per turn)."""
    def resolve(ref_id: str):
        from app.core.database import SessionLocal
        from app.services.project_knowledge.liveness import live_version_token

        with SessionLocal() as db:
            return live_version_token(
                db, project_id=project_id,
                authority_store="project_dataset",
                authority_id=str(ref_id or "")[:64],
            )
    return resolve


def _find_claim_store(claim_id: str):
    """Locate the process-local ClaimStore holding ``claim_id``."""
    try:
        from app.services.gis_harness.hotpath_convergence.session_ctx import (
            find_claim_store,
        )

        return find_claim_store(claim_id)
    except Exception:  # noqa: BLE001 — lookup failure is not an upgrade
        return None


async def request_revalidation(
    session_id: str,
    *,
    claim_ids: Optional[List[str]] = None,
    reaffirm_texts: Optional[List[str]] = None,
    org_id: str = "",
    project_id: str = "",
    turn_id: str = "",
) -> Dict[str, Any]:
    """Evidence-checked stale→current restoration (tool-facing).

    The caller may only *name* claims/decisions; every check re-reads live
    state inside the engine. Returns a bounded summary of receipts with
    closed reason codes. Fail-closed: any resolution failure yields
    ``{"ok": False, "reason": ...}`` with no state change.
    """
    out: Dict[str, Any] = {"ok": False, "reason": "", "receipts": [],
                           "restored": 0, "rejected": 0}
    sid = str(session_id or "")[:64]
    if not sid:
        out["reason"] = "invalid_args"
        return out
    claim_ids = [str(c or "")[:64] for c in (claim_ids or []) if str(c or "").strip()][:8]
    reaffirm_texts = [str(t or "").strip()[:200] for t in (reaffirm_texts or []) if str(t or "").strip()][:4]
    if not claim_ids and not reaffirm_texts:
        out["reason"] = "invalid_args"
        return out

    # Binding resolution mirrors the card path (durable binding first),
    # falling back to the server-side session tenant for sessions whose
    # binding has not been established yet.
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(sid) or {}
    except Exception:  # noqa: BLE001
        state = {}
    binding = _resolve_binding(state if isinstance(state, dict) else None)
    mission_id = binding.get("mission_id", "")
    org = str(org_id or "")[:64] or binding.get("org_id", "") or _tenant_scan(sid)
    if not org:
        out["reason"] = "no_org_context"
        return out
    if not mission_id:
        out["reason"] = "no_mission"
        return out

    wc, miss, _ = await asyncio.to_thread(
        _load_and_maybe_purge, mission_id, org_id=org)
    if wc is None:
        out["reason"] = miss or "no_context"
        return out
    if not scope_renderable(wc, org_id=org, project_id=project_id):
        out["reason"] = "scope_mismatch"
        return out

    disk_revision = int(wc.revision)

    def _run() -> List[Any]:
        from app.services.gis_context.revalidation import (
            reaffirm_decisions,
            revalidate_claims,
        )

        receipts: List[Any] = []
        if claim_ids:
            resolver = _db_token_resolver(project_id) if project_id else None
            # Resolve each claim's owning store (per-session stores).
            for cid in claim_ids:
                store = _find_claim_store(cid)
                receipts.extend(revalidate_claims(
                    wc, [cid], claim_store=store, token_resolver=resolver,
                    turn_id=turn_id, max_claims=1))
        if reaffirm_texts:
            receipts.extend(reaffirm_decisions(
                wc, reaffirm_texts, turn_id=turn_id))
        return receipts

    try:
        receipts = await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001 — engine failure is not an upgrade
        out["reason"] = f"engine_error:{type(exc).__name__}"[:48]
        return out

    restored = sum(1 for r in receipts if r.verdict == "restored")
    if restored:
        try:
            await asyncio.to_thread(
                _store().save, wc, expected_revision=disk_revision)
        except Exception as exc:  # noqa: BLE001 — persistence failure observable
            out["reason"] = f"save_failed:{type(exc).__name__}"[:48]
    out["ok"] = True
    out["restored"] = restored
    out["rejected"] = len(receipts) - restored
    out["receipts"] = [
        r.to_bounded_dict() for r in receipts[:12]
    ]
    return out


__all__ = [
    "MISSION_BINDING_KEY",
    "assemble_gis_context_card",
    "bind_session_mission",
    "mission_scope_ref",
    "request_revalidation",
]
