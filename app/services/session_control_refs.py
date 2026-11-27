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
    # list_refs 的顺序是 LRU / recency，不是创建序：被读或覆写过的旧 archive
    # 会排到新 archive 之后。按信封自身的 created_at 排序（缺失记 0，稳定
    # 排序回落到 list_refs 顺序），否则会误删更新的计划历史。
    created: dict[str, float] = {}
    for ref in archives:
        payload = await backend.get(session_id, ref)
        try:
            created[ref] = float(payload.get("created_at") or 0.0) if isinstance(payload, dict) else 0.0
        except (TypeError, ValueError):
            created[ref] = 0.0
    archives.sort(key=lambda ref: created[ref])
    for ref in archives[:-MAX_PLAN_HISTORY]:
        if not await backend.delete_ref(session_id, ref):
            raise RuntimeError("session plan history retention unavailable")
