"""Turn-context receipt lifecycle helpers for the Pi bridge (G43, ADR-0295).

Thin, fail-open wrappers around ``turn_context_store``: every call is
best-effort — a receipt write failure must NEVER break a live turn (C22:
the read model then honestly reports the missing row as unknown/limited,
never fabricating one from the previous turn).

State machine (single writer per turn, monotonic):

    assembled ──┬─► not_sent(reason)      model/budget gate refused (C18)
                ├─► submitted(disposition) prompt RPC returned (C19/C20)
                └─► unknown               socket lost / process died (C21)

    turn_outcome (completed/failed/cancelled) is an INDEPENDENT dimension —
    a queued-then-cancelled turn stays submitted+queued with outcome
    cancelled (C20: queued ≠ provider-consumed, cancelled ≠ fabricated-run).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def persist_assembly_receipt(
    receipt: Optional[Any],
    *,
    session_id: str,
    turn_id: str,
    run_id: str = "",
    org_id: str = "",
    project_id: str = "",
    user_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Persist the assembly receipt row (typed receipt or honest legacy row).

    ``receipt=None`` (legacy fallback / kill-switch path) still creates a row
    with ``assembly_mode='legacy'`` and a limited-coverage payload — the
    read model then shows coverage limited instead of fabricating decisions.
    Returns the bounded read model, or None when persistence failed (caller
    keeps the in-memory digest for the SSE event only).
    """
    from app.services import turn_context_store as store

    if receipt is not None:
        payload = receipt.as_bounded_dict()
        mode = str(getattr(receipt, "mode", "typed") or "typed")
        digest = str(getattr(receipt, "digest", "") or "")
        policy_version = int(getattr(receipt, "policy_version", 0) or 0)
    else:
        payload = {
            "schema": "ctx.receipt.v2",
            "session_id": (session_id or "")[:48],
            "turn_id": (turn_id or "")[:48],
            "mode": "legacy",
            "coverage": "limited",
            "items": [],
            "sources": [],
            "note": "legacy assembly path — no typed receipt available",
        }
        mode = "legacy"
        digest = ""
        policy_version = 0
    row_id = await store.record_assembly(
        session_id=session_id, turn_id=turn_id, run_id=run_id,
        org_id=org_id, project_id=project_id, user_id=user_id,
        assembly_mode=mode, policy_version=policy_version,
        digest=digest, payload=payload,
    )
    if row_id is None:
        return None
    read = await store.get_receipt(session_id, turn_id)
    return read


async def mark_not_sent(turn_id: str, *, reason: str) -> None:
    from app.services import turn_context_store as store

    await store.record_not_sent(turn_id, reason=reason)


async def mark_submitted(
    turn_id: str, *, disposition: str, session_id: str = ""
) -> Optional[Dict[str, Any]]:
    from app.services import turn_context_store as store

    await store.record_submitted(turn_id, disposition=disposition)
    if session_id:
        return await store.get_receipt(session_id, turn_id)
    return None


async def mark_unknown(turn_id: str) -> None:
    from app.services import turn_context_store as store

    await store.record_unknown(turn_id)


async def mark_turn_outcome(turn_id: str, *, outcome: str) -> None:
    from app.services import turn_context_store as store

    await store.record_turn_outcome(turn_id, outcome=outcome)


def sse_payload(
    receipt_read: Optional[Dict[str, Any]],
    *,
    session_id: str,
    turn_id: str,
    fallback: Optional[Any] = None,
    status_override: str = "",
) -> Dict[str, Any]:
    """Bounded SSE payload for the ``turn_context_receipt`` additive event.

    ``fallback`` is the in-memory typed receipt used when the durable row is
    unavailable (persist failed — C22: the event still carries the digest so
    the UI can show「已装配/回执落盘失败」honestly).
    """
    if receipt_read is not None:
        payload = dict(receipt_read.get("payload") or {})
        counts = {
            k: payload.get(k)
            for k in ("included", "omitted", "truncated", "sources_total")
        }
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "receipt_id": receipt_read.get("receipt_id"),
            "digest": receipt_read.get("digest"),
            "status": status_override or receipt_read.get("status"),
            "disposition": receipt_read.get("disposition"),
            "turn_outcome": receipt_read.get("turn_outcome"),
            "policy_version": receipt_read.get("policy_version"),
            "mode": receipt_read.get("assembly_mode"),
            "counts": counts,
            "persisted": True,
        }
    if fallback is not None:
        bounded = fallback.as_bounded_dict(max_items=0)
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "receipt_id": None,
            "digest": bounded.get("digest"),
            "status": status_override or "assembled",
            "disposition": None,
            "turn_outcome": None,
            "policy_version": bounded.get("policy_version"),
            "mode": bounded.get("mode"),
            "counts": {
                k: bounded.get(k)
                for k in ("included", "omitted", "truncated", "sources_total")
            },
            "persisted": False,
        }
    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "receipt_id": None,
        "digest": "",
        "status": status_override or "unknown",
        "disposition": None,
        "turn_outcome": None,
        "policy_version": 0,
        "mode": "legacy",
        "counts": {},
        "persisted": False,
    }


async def outcome_for_rt_ev(outcome: Any) -> str:
    """Map the runtime-evidence outcome to the receipt turn_outcome."""
    value = getattr(outcome, "outcome", outcome)
    name = str(getattr(value, "name", value)).lower()
    if "cancel" in name:
        return "cancelled"
    if "fail" in name or "error" in name:
        return "failed"
    if "success" in name:
        return "completed"
    return "failed"


__all__ = [
    "mark_not_sent",
    "mark_submitted",
    "mark_turn_outcome",
    "mark_unknown",
    "outcome_for_rt_ev",
    "persist_assembly_receipt",
    "sse_payload",
]
