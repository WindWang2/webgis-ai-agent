"""Process-local hotpath turn context (skill bundle + ClaimStore per session).

Not a second Artifact Registry / Provenance DB — ClaimStore remains in-memory
session-scoped like D03.
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
    skill_bundle: Any = None
    skill_guidance: Dict[str, Any] = field(default_factory=dict)
    mission_id: str = ""
    claim_store: Any = None
    last_pi_card: Dict[str, Any] = field(default_factory=dict)


def _evict_if_needed() -> None:
    if len(_CTX) <= _MAX_SESSIONS:
        return
    # FIFO-ish: drop oldest insertion order (dict preserves order).
    drop = list(_CTX.keys())[: max(1, len(_CTX) - _MAX_SESSIONS)]
    for k in drop:
        _CTX.pop(k, None)


def get_turn_context(session_id: str) -> HotpathTurnContext:
    sid = str(session_id or "_anon")[:64]
    with _LOCK:
        ctx = _CTX.get(sid)
        if ctx is None:
            ctx = HotpathTurnContext(session_id=sid)
            _CTX[sid] = ctx
            _evict_if_needed()
        return ctx


def set_skill_bundle(session_id: str, bundle: Any, guidance: Optional[Dict] = None) -> None:
    ctx = get_turn_context(session_id)
    with _LOCK:
        ctx.skill_bundle = bundle
        if guidance is not None:
            ctx.skill_guidance = dict(guidance)


def set_mission_id(session_id: str, mission_id: str) -> None:
    ctx = get_turn_context(session_id)
    with _LOCK:
        ctx.mission_id = str(mission_id or "")[:64]


def get_or_create_claim_store(session_id: str):
    from app.services.gis_harness.evidence_claim.store import ClaimStore

    ctx = get_turn_context(session_id)
    with _LOCK:
        if ctx.claim_store is None:
            ctx.claim_store = ClaimStore()
        return ctx.claim_store


def reset_turn_context(session_id: Optional[str] = None) -> None:
    """Test isolation helper."""
    with _LOCK:
        if session_id is None:
            _CTX.clear()
        else:
            _CTX.pop(str(session_id)[:64], None)


__all__ = [
    "HotpathTurnContext",
    "get_or_create_claim_store",
    "get_turn_context",
    "reset_turn_context",
    "set_mission_id",
    "set_skill_bundle",
]
