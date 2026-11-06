"""Control-plane envelopes have independent retention from user data references."""
from __future__ import annotations

from typing import Any

CONTROL_REF_PREFIXES = ("ref:sessionplan-", "ref:plan-", "ref:checkpoint-")
MAX_PLAN_HISTORY = 32


def is_control_ref(ref_id: str) -> bool:
    return ref_id.startswith(CONTROL_REF_PREFIXES)


async def trim_plan_history(backend: Any, session_id: str) -> None:
    """Keep the newest 32 archived envelopes; current plan never enters this list."""
    refs = await backend.list_refs(session_id)
    archives = [ref for ref, alias in refs.items() if str(alias or "").startswith("session-plan-id:")]
    for ref in archives[:-MAX_PLAN_HISTORY]:
        if not await backend.delete_ref(session_id, ref):
            raise RuntimeError("session plan history retention unavailable")
