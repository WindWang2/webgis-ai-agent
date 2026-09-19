"""Process-local hotpath turn context (skill bundle + ClaimStore per session).

Not a second Artifact Registry / Provenance DB — ClaimStore remains in-memory
session-scoped like D03.

Stores are keyed by ``tenant_id`` + ``session_id`` so empty/``_anon`` sessions
and distinct tenants never share a ClaimStore.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_LOCK = threading.RLock()
_CTX: Dict[str, "HotpathTurnContext"] = {}
_MAX_SESSIONS = 256


@dataclass
class HotpathTurnContext:
    session_id: str = ""
    tenant_id: str = ""
    skill_bundle: Any = None
    skill_guidance: Dict[str, Any] = field(default_factory=dict)
    mission_id: str = ""
    claim_store: Any = None
    last_pi_card: Dict[str, Any] = field(default_factory=dict)


def _ctx_key(session_id: str, tenant_id: str = "") -> str:
    """Composite key — do NOT collapse empty session_id into ``_anon``.

    Empty ``\"\"`` and literal ``\"_anon\"`` must remain distinct namespaces.
    """
    sid = str(session_id if session_id is not None else "")[:64]
    tid = str(tenant_id if tenant_id is not None else "")[:64]
    return f"t:{tid}|s:{sid}"


def _evict_if_needed() -> None:
    if len(_CTX) <= _MAX_SESSIONS:
        return
    # FIFO-ish: drop oldest insertion order (dict preserves order).
    drop = list(_CTX.keys())[: max(1, len(_CTX) - _MAX_SESSIONS)]
    for k in drop:
        _CTX.pop(k, None)


def get_turn_context(session_id: str, tenant_id: str = "") -> HotpathTurnContext:
    key = _ctx_key(session_id, tenant_id)
    with _LOCK:
        ctx = _CTX.get(key)
        if ctx is None:
            ctx = HotpathTurnContext(
                session_id=str(session_id or "")[:64],
                tenant_id=str(tenant_id or "")[:64],
            )
            _CTX[key] = ctx
            _evict_if_needed()
        return ctx


def set_skill_bundle(
    session_id: str,
    bundle: Any,
    guidance: Optional[Dict] = None,
    *,
    tenant_id: str = "",
) -> None:
    ctx = get_turn_context(session_id, tenant_id=tenant_id)
    with _LOCK:
        ctx.skill_bundle = bundle
        if guidance is not None:
            ctx.skill_guidance = dict(guidance)


def set_mission_id(session_id: str, mission_id: str, *, tenant_id: str = "") -> None:
    ctx = get_turn_context(session_id, tenant_id=tenant_id)
    with _LOCK:
        ctx.mission_id = str(mission_id or "")[:64]


def get_or_create_claim_store(session_id: str, tenant_id: str = ""):
    from app.services.gis_harness.evidence_claim.store import ClaimStore

    ctx = get_turn_context(session_id, tenant_id=tenant_id)
    with _LOCK:
        if ctx.claim_store is None:
            ctx.claim_store = ClaimStore()
        return ctx.claim_store



def find_claim(claim_id: str):
    """Locate a claim across process-local turn ClaimStores (#1406).

    Knowledge projections carry ``verified_by`` claim ids without a session
    key; claims themselves are session-scoped and in-memory. Scanning the
    bounded ``_CTX`` map (≤256) is the honest lookup — returning None keeps
    fail-closed semantics when the claim is not live in this process.
    """
    cid = str(claim_id or "")
    if not cid:
        return None
    with _LOCK:
        for ctx in _CTX.values():
            store = ctx.claim_store
            if store is None:
                continue
            claim = store.get_claim(cid)
            if claim is not None:
                return claim
    return None


def reset_turn_context(
    session_id: Optional[str] = None,
    *,
    tenant_id: str = "",
) -> None:
    """Test isolation helper."""
    with _LOCK:
        if session_id is None and not tenant_id:
            _CTX.clear()
        else:
            _CTX.pop(_ctx_key(session_id or "", tenant_id), None)


__all__ = [
    "HotpathTurnContext",
    "find_claim",
    "get_or_create_claim_store",
    "get_turn_context",
    "reset_turn_context",
    "set_mission_id",
    "set_skill_bundle",
]
