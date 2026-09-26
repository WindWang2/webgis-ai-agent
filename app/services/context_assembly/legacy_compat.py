"""Legacy byte-equivalent compat for the cartography turn blocks (F04).

Retirement boundary: the pre-F04 ``chat._build_cartography_turn_context``
orchestration (verdict → memory → knowledge → gis_memory → gis_context,
``"".join``ed) now lives here and delegates to the SAME typed providers the
new assembly uses — one implementation, two consumers:

- typed path (default): providers run inside the budgeted/fenced/deduped
  pipeline with a receipt;
- legacy path (kill-switch ``GIS_TYPED_CONTEXT_ASSEMBLY=0`` or the compat
  wrapper): the same providers, joined byte-identically to the pre-F04
  string.

When the Pi route stops needing the joined string entirely, this module is
the single file to delete (plus its tests).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from app.services.context_assembly.contract import TurnContextRequest
from app.services.context_assembly.providers import collect_cartography_wave

logger = logging.getLogger(__name__)

#: the legacy join order of the five cartography domains (byte contract)
LEGACY_CARTOGRAPHY_ORDER: Tuple[str, ...] = (
    "cartography_verdict",
    "cartography_memory",
    "project_knowledge",
    "gis_memory",
    "gis_context_card",
)


async def build_cartography_turn_blocks(
    session_id: Optional[str],
    project_id: Optional[str] = None,
    org_id: Optional[str] = None,
    user_id: Optional[str] = None,
    query_text: str = "",
) -> Dict[str, str]:
    """The five cartography blocks, keyed by ``ContextDomain`` value.

    Read-only wrt the pipeline: providers fail open to "" exactly like the
    pre-F04 builders did. The fetch is deliberately lighter than the typed
    path's (map_state + mapspec only) — the five blocks never read the
    SessionPlan, so compat callers keep pre-F04 I/O and avoid the
    plan-slot side effect.
    """
    import asyncio

    from app.services.context_assembly.contract import SharedTurnFacts
    from app.services.session_data import session_data_manager

    req = TurnContextRequest(
        session_id=session_id or "",
        project_id=project_id or "",
        org_id=org_id or "",
        user_id=user_id or "",
        query_text=query_text or "",
    )
    facts = SharedTurnFacts()
    state, mapspec = await asyncio.gather(
        session_data_manager.get_map_state(req.session_id),
        _fetch_mapspec(req.session_id),
        return_exceptions=True,
    )
    facts.map_state = state if isinstance(state, dict) else {}
    facts.mapspec = mapspec if isinstance(mapspec, dict) else None
    pairs: List[Tuple[object, str]] = await collect_cartography_wave(req, facts)
    return {
        str(domain.value): text
        for domain, text in pairs
    }


async def _fetch_mapspec(session_id: str):
    try:
        from app.services.mapspec.store import mapspec_store_instance

        return await mapspec_store_instance.get_mapspec(session_id)
    except Exception:  # noqa: BLE001 — 缺席按空投影（pre-F04 同纪律）
        return None


def join_cartography_blocks(blocks: Dict[str, str]) -> str:
    """Pre-F04 byte contract: ``"".join`` in the fixed legacy order."""
    return "".join(
        blocks.get(domain, "") for domain in LEGACY_CARTOGRAPHY_ORDER
    )


__all__ = [
    "LEGACY_CARTOGRAPHY_ORDER",
    "build_cartography_turn_blocks",
    "join_cartography_blocks",
]
