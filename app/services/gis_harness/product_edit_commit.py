"""Commit semantic edits before physical effects, and fence stale effects under the engine lock."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.distributed_lock import session_lock_registry


async def commit_product_edit(session_id: str, base_digest: str, payload: Mapping[str, Any]) -> bool:
    from app.services.session_plan import load_session_plan, save_session_plan

    async with session_lock_registry.lock(session_id, fail_on_degraded=True, fail_on_lost=True):
        envelope = await load_session_plan(session_id)
        if envelope is None or not isinstance(envelope.gis_chapter, dict):
            return False
        current = envelope.gis_chapter.get("product_spec") or {}
        if not isinstance(current, dict) or str(current.get("digest") or "") != base_digest:
            return False
        envelope.gis_chapter["product_spec"] = dict(payload)
        return await save_session_plan(envelope, expected_revision=envelope.revision)


async def apply_product_component(session_id: str, digest: str, component_id: str, *, enabled: bool | None = None) -> Mapping[str, Any]:
    from app.services.mapspec import mapspec_lifecycle_engine, PatchComponentIntent, RemoveComponentIntent
    from app.services.mapspec.mutation_contracts import MapSpecResult, MutationOrigin
    from app.services.session_plan import load_session_plan

    async def guard(sid: str, intent: Any, origin: MutationOrigin, prior: Any) -> MapSpecResult | None:
        envelope = await load_session_plan(sid)
        current = (envelope.gis_chapter or {}).get("product_spec") if envelope else None
        if not isinstance(current, dict) or current.get("digest") != digest:
            return MapSpecResult(is_error=True, superseded=True, origin=origin,
                                 error_msg="Product edit superseded before physical commit")
        return None

    intent = RemoveComponentIntent(component_id=component_id) if enabled is None else PatchComponentIntent(component_id=component_id, enabled=enabled)
    result = await mapspec_lifecycle_engine.apply_mutation(session_id, intent, pre_commit_check=guard)
    return {"success": not result.is_error and not result.superseded, "message": result.error_msg}
