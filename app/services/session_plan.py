"""SessionPlan — Pi-path host-plan envelope (ADR-0076).

Keyed by ``session_id`` in SessionStore (alias ``session-plan``), never by a
Pi tree entry. GIS chapter is an embedded MapProductPlan dump; progress is
capability completion, not a tool-call sequence. ChatEngine does not read or
write this object.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.services.distributed_lock import session_lock_registry
from app.services.session_data import session_data_manager
from app.utils.sse import sse_event

logger = logging.getLogger(__name__)

CURRENT_ALIAS = "session-plan"
STORE_PREFIX = "sessionplan"
REF_PREFIX = f"ref:{STORE_PREFIX}-"
HISTORY_ALIAS_PREFIX = "session-plan-id:"


def is_session_plan_listing(ref_id: str, alias: str = "") -> bool:
    """True for SessionPlan store rows — not GIS data the model should reuse."""
    rid = str(ref_id or "")
    al = str(alias or "")
    return (
        rid.startswith(REF_PREFIX)
        or al == CURRENT_ALIAS
        or al.startswith(HISTORY_ALIAS_PREFIX)
    )


def public_data_refs(refs: dict) -> dict:
    """Drop SessionPlan envelope rows from an LLM-facing ref inventory."""
    return {
        rid: alias
        for rid, alias in (refs or {}).items()
        if not is_session_plan_listing(str(rid), str(alias or ""))
    }


SESSION_PLAN_UPDATED = "session_plan_updated"
SESSION_PLAN_PROGRESS = "session_plan_progress"
SESSION_PLAN_SUPERSEDED = "session_plan_superseded"
CANONICAL_PLAN_EVENT_NAMES = frozenset(
    {"plan_ready", "plan_step_done", "plan_finalized"}
)

ProgressStatus = Literal["pending", "complete", "voided", "unavailable"]


class CapabilityProgress(BaseModel):
    """One capability / data-requirement row in the progress chapter."""

    capability: str
    status: ProgressStatus = "pending"
    bound_ref: str = ""


class SessionPlan(BaseModel):
    """Current host-plan envelope for one Session."""

    envelope_id: str
    session_id: str
    user_goal: str = ""
    gis_chapter: Optional[dict[str, Any]] = None
    progress: list[CapabilityProgress] = Field(default_factory=list)
    replaced: bool = False
    superseded: bool = False
    previous_goal: str = ""
    updated_at: float = 0.0


class SessionPlanEvent(BaseModel):
    """One SessionPlan SSE payload (new event names only)."""

    event: str
    data: dict[str, Any]


def _new_envelope_id() -> str:
    return f"sp-{uuid.uuid4().hex[:12]}"


def _history_alias(envelope_id: str) -> str:
    return f"{HISTORY_ALIAS_PREFIX}{envelope_id}"


def goal_key(gis_chapter: Optional[dict[str, Any]], query: str = "") -> str:
    """Stable same-goal vs new-goal key from a MapProductPlan dump."""
    if not gis_chapter:
        return (query or "").strip()
    intent = gis_chapter.get("intent") or {}
    if not isinstance(intent, dict):
        intent = {}
    scope = intent.get("scope") if isinstance(intent.get("scope"), dict) else {}
    subject = intent.get("subject") if isinstance(intent.get("subject"), dict) else {}
    scope_name = str(scope.get("name") or "").strip().rstrip("市")
    subject_name = str(subject.get("category") or "").strip()
    task = str(intent.get("task") or "").strip()
    if scope_name or subject_name or task:
        return f"{scope_name}|{subject_name}|{task}"
    return str(gis_chapter.get("query") or query or "").strip()


def _init_progress(gis_chapter: dict[str, Any]) -> list[CapabilityProgress]:
    seen: list[str] = []
    for row in list(gis_chapter.get("data_requirements") or []) + list(
        gis_chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        cap = str(row.get("capability") or "").strip()
        if cap and cap not in seen:
            seen.append(cap)
    return [CapabilityProgress(capability=cap, status="pending") for cap in seen]


def _merge_progress(
    rows: list[CapabilityProgress],
    gis_chapter: dict[str, Any],
) -> list[CapabilityProgress]:
    """Same-goal replace: voided rows survive only while the replacement
    chapter still tracks that capability; new capabilities join as pending.

    Keeps the stored progress consistent with the void SSE — the store is the
    plan truth (ADR-0076), so rows must not silently flip back to pending.
    """
    fresh = _init_progress(gis_chapter)
    chapter_caps = {row.capability for row in fresh}
    merged = [row for row in rows if row.capability in chapter_caps]
    known = {row.capability for row in merged}
    merged.extend(row for row in fresh if row.capability not in known)
    return merged


def open_capabilities(plan: Optional[SessionPlan]) -> list[str]:
    """Voided rows are open too — their completion is gone, the chapter
    requirement is pending again."""
    if plan is None:
        return []
    return [
        row.capability
        for row in plan.progress
        if row.status in ("pending", "voided")
    ]


def format_session_plan_projection(plan: Optional[SessionPlan]) -> str:
    """Bounded next-turn note. Not a Cartography Verdict block."""
    if plan is None or plan.gis_chapter is None:
        return (
            "[SessionPlan] recipe=none open= (call webgis_map_intent) "
            "replaced=false superseded=false"
        )
    recipe = str(plan.gis_chapter.get("recipe_id") or "none")
    open_caps = ",".join(open_capabilities(plan)) or "none"
    return (
        f"[SessionPlan] recipe={recipe} open={open_caps} "
        f"replaced={'true' if plan.replaced else 'false'} "
        f"superseded={'true' if plan.superseded else 'false'}"
    )


def events_to_sse(events: list[SessionPlanEvent], session_id: str = "") -> str:
    """Serialize SessionPlan events. Never uses CanonicalPlan event names."""
    chunks: list[str] = []
    for item in events:
        if item.event in CANONICAL_PLAN_EVENT_NAMES:
            raise ValueError(f"CanonicalPlan event name forbidden on Pi path: {item.event}")
        payload = dict(item.data)
        if session_id and "session_id" not in payload:
            payload["session_id"] = session_id
        chunks.append(sse_event(item.event, payload))
    return "".join(chunks)


async def load_session_plan(
    session_id: str,
    *,
    store: Any = None,
) -> Optional[SessionPlan]:
    backend = store if store is not None else session_data_manager
    try:
        ref_id = await backend.resolve_alias(session_id, CURRENT_ALIAS)
        if ref_id == CURRENT_ALIAS:
            return None
        data = await backend.get(session_id, ref_id)
    except Exception:
        logger.exception("[SessionPlan] load failed session=%s", session_id)
        return None
    if not isinstance(data, dict):
        return None
    try:
        return SessionPlan.model_validate(data)
    except Exception:
        logger.warning("[SessionPlan] invalid envelope session=%s", session_id)
        return None


async def save_session_plan(
    plan: SessionPlan,
    *,
    store: Any = None,
) -> None:
    backend = store if store is not None else session_data_manager
    plan.updated_at = time.time()
    payload = plan.model_dump()
    ref_id = await backend.resolve_alias(plan.session_id, CURRENT_ALIAS)
    if ref_id != CURRENT_ALIAS:
        if await backend.overwrite(plan.session_id, ref_id, payload):
            return
    new_ref = await backend.store(plan.session_id, payload, prefix=STORE_PREFIX)
    await backend.set_alias(plan.session_id, new_ref, CURRENT_ALIAS)


async def ensure_session_plan_slot(
    session_id: str,
    *,
    store: Any = None,
) -> SessionPlan:
    """Host opens an empty envelope before tools run. No SSE (GIS chapter empty).

    Read-mostly and called on every Pi tool callback, so the fast path is
    lockless: only a miss (envelope absent) takes the session lock and
    re-checks inside — double-checked locking, keeping per-callback slot
    checks free of lock traffic while first-creation stays serialized."""
    current = await load_session_plan(session_id, store=store)
    if current is not None:
        return current
    async with session_lock_registry.lock(session_id):
        return await _ensure_slot_unlocked(session_id, store=store)


async def _ensure_slot_unlocked(session_id: str, *, store: Any) -> SessionPlan:
    current = await load_session_plan(session_id, store=store)
    if current is not None:
        return current
    plan = SessionPlan(
        envelope_id=_new_envelope_id(),
        session_id=session_id,
        updated_at=time.time(),
    )
    await save_session_plan(plan, store=store)
    return plan


async def _archive_envelope(plan: SessionPlan, *, store: Any) -> None:
    backend = store
    payload = plan.model_dump()
    alias = _history_alias(plan.envelope_id)
    ref_id = await backend.resolve_alias(plan.session_id, alias)
    if ref_id != alias:
        if await backend.overwrite(plan.session_id, ref_id, payload):
            return
    new_ref = await backend.store(plan.session_id, payload, prefix=STORE_PREFIX)
    await backend.set_alias(plan.session_id, new_ref, alias)


def _updated_event(plan: SessionPlan) -> SessionPlanEvent:
    gis = plan.gis_chapter or {}
    return SessionPlanEvent(
        event=SESSION_PLAN_UPDATED,
        data={
            "session_id": plan.session_id,
            "envelope_id": plan.envelope_id,
            "plan_id": gis.get("plan_id") or "",
            "recipe_id": gis.get("recipe_id") or "",
            "query": gis.get("query") or plan.user_goal,
            "replaced": plan.replaced,
        },
    )


def _progress_event(plan: SessionPlan, row: CapabilityProgress) -> SessionPlanEvent:
    return SessionPlanEvent(
        event=SESSION_PLAN_PROGRESS,
        data={
            "session_id": plan.session_id,
            "envelope_id": plan.envelope_id,
            "capability": row.capability,
            "status": row.status,
            "bound_ref": row.bound_ref,
        },
    )


def _superseded_event(old: SessionPlan, new: SessionPlan) -> SessionPlanEvent:
    return SessionPlanEvent(
        event=SESSION_PLAN_SUPERSEDED,
        data={
            "session_id": new.session_id,
            "old_envelope_id": old.envelope_id,
            "envelope_id": new.envelope_id,
            "previous_query": old.user_goal,
            "query": new.user_goal,
        },
    )


def _tool_to_capability() -> dict[str, str]:
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        return dict(get_algorithm_registry().tool_to_capability())
    except Exception:
        return {}


def capabilities_hit_by_tool(
    plan: SessionPlan,
    tool_name: str,
) -> list[str]:
    hits: list[str] = []
    mapped = _tool_to_capability().get(tool_name)
    if mapped:
        hits.append(mapped)
    gis = plan.gis_chapter or {}
    for row in list(gis.get("data_requirements") or []) + list(
        gis.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        if row.get("resolved_tool") == tool_name:
            cap = str(row.get("capability") or "").strip()
            if cap:
                hits.append(cap)
    seen: list[str] = []
    for cap in hits:
        if cap not in seen:
            seen.append(cap)
    return seen


def _mark_progress(
    plan: SessionPlan,
    capabilities: list[str],
    *,
    status: ProgressStatus,
    bound_ref: str = "",
) -> list[CapabilityProgress]:
    changed: list[CapabilityProgress] = []
    pending = {row.capability for row in plan.progress}
    for cap in capabilities:
        if cap not in pending:
            plan.progress.append(CapabilityProgress(capability=cap, status="pending"))
            pending.add(cap)
    for row in plan.progress:
        if row.capability in capabilities and row.status != status:
            row.status = status
            if bound_ref:
                row.bound_ref = bound_ref
            changed.append(row)
    gis = plan.gis_chapter or {}
    req_status = "available" if status == "complete" else (
        "pending" if status == "voided" else status
    )
    for row in gis.get("data_requirements") or []:
        if isinstance(row, dict) and row.get("capability") in capabilities:
            row["status"] = req_status
            if bound_ref:
                row["bound_ref"] = bound_ref
    step_status = "done" if status == "complete" else (
        "pending" if status == "voided" else status
    )
    for row in gis.get("analysis_steps") or []:
        if isinstance(row, dict) and row.get("capability") in capabilities:
            row["status"] = step_status
            if bound_ref:
                row["bound_ref"] = bound_ref
    return changed


async def apply_tool_result(
    session_id: str,
    tool_name: str,
    raw_result: Any,
    *,
    success: bool,
    geojson_ref: Optional[str] = None,
    store: Any = None,
) -> list[SessionPlanEvent]:
    """Mutate the SessionPlan after a successful unified dispatch.

    Intent replaces / supersedes the GIS chapter. Product updates the same
    envelope. Other tools complete matching capabilities. The whole
    load→mutate→save runs under the per-session lock: a Pi turn may issue
    parallel tool callbacks and the supersede branch must not be lost to a
    last-write-wins interleave (ADR-0051 lock pattern).
    """
    if not session_id or not success:
        return []
    async with session_lock_registry.lock(session_id) as lock:
        return await _apply_tool_result_unlocked(
            session_id,
            tool_name,
            raw_result,
            geojson_ref=geojson_ref,
            store=store,
            lock=lock,
        )


async def _apply_tool_result_unlocked(
    session_id: str,
    tool_name: str,
    raw_result: Any,
    *,
    geojson_ref: Optional[str] = None,
    store: Any = None,
    lock: Any = None,
) -> list[SessionPlanEvent]:
    backend = store if store is not None else session_data_manager
    plan = await _ensure_slot_unlocked(session_id, store=backend)
    raw = raw_result if isinstance(raw_result, dict) else {}
    events: list[SessionPlanEvent] = []

    if lock is not None and lock.lost:
        logger.error(
            "[SessionPlan] Lock ownership for session %s was lost; aborting envelope mutation",
            session_id,
        )
        return []

    if tool_name == "webgis_map_intent":
        gis = raw.get("plan")
        if not isinstance(gis, dict):
            return []
        query = str(gis.get("query") or (raw.get("intent") or {}).get("query") or "")
        new_key = goal_key(gis, query)
        old_key = goal_key(plan.gis_chapter, plan.user_goal)
        if plan.gis_chapter and old_key and new_key and old_key != new_key:
            if lock is not None and lock.lost:
                return []
            old = plan.model_copy(deep=True)
            old.superseded = True
            await _archive_envelope(old, store=backend)
            new = SessionPlan(
                envelope_id=_new_envelope_id(),
                session_id=session_id,
                user_goal=query,
                gis_chapter=gis,
                progress=_init_progress(gis),
                previous_goal=old.user_goal,
            )
            if lock is not None and lock.lost:
                return []
            await save_session_plan(new, store=backend)
            events.append(_superseded_event(old, new))
            events.append(_updated_event(new))
            return events

        replaced = plan.gis_chapter is not None
        if replaced:
            for row in plan.progress:
                if row.status != "voided":
                    row.status = "voided"
                    events.append(_progress_event(plan, row))
        plan.gis_chapter = gis
        plan.user_goal = query or plan.user_goal
        plan.progress = _merge_progress(plan.progress, gis)
        plan.replaced = replaced
        if lock is not None and lock.lost:
            return []
        await save_session_plan(plan, store=backend)
        events.append(_updated_event(plan))
        return events

    if tool_name == "webgis_map_product" and plan.gis_chapter is not None:
        if raw.get("completeness") is not None:
            plan.gis_chapter["completeness"] = raw["completeness"]
        if raw.get("status"):
            plan.gis_chapter["status"] = raw["status"]
        if raw.get("recipe_id"):
            plan.gis_chapter["recipe_id"] = raw["recipe_id"]
        evidence = raw.get("map_product_evidence") or {}
        resolution = evidence.get("capability_resolution") or []
        done_caps = [
            str(item.get("capability"))
            for item in resolution
            if isinstance(item, dict)
            and item.get("capability")
            and item.get("status") in ("available", "resolved", "done")
        ]
        # completeness.missing == [] says the *product outputs* are complete;
        # it is not evidence that never-run capabilities executed.
        changed = _mark_progress(
            plan, done_caps, status="complete", bound_ref=geojson_ref or ""
        )
        if lock is not None and lock.lost:
            return []
        await save_session_plan(plan, store=backend)
        events.append(_updated_event(plan))
        events.extend(_progress_event(plan, row) for row in changed)
        return events

    if plan.gis_chapter is None:
        return []
    hits = capabilities_hit_by_tool(plan, tool_name)
    if not hits:
        return []
    changed = _mark_progress(
        plan, hits, status="complete", bound_ref=geojson_ref or ""
    )
    if not changed:
        return []
    if lock is not None and lock.lost:
        return []
    await save_session_plan(plan, store=backend)
    return [_progress_event(plan, row) for row in changed]
