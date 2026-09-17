"""event → Situation 投影（红线：先投影 Situation，再决定 Mission）。

设计（DECISIONS D7）：
- 每个带 session_id 的事件投影为一条**有界事实**，写入 session store
  ``map_state["_spatial_event_facts"]`` 环（≤32 条，持 session 锁 RMW，
  仿 gis_situation.diff.advance_snapshot 纪律）。
- 事实只含摘要（event_id/kind/subject/occurred_at/≤120 字符 summary），
  绝不含 payload 大对象。
- Mission 决策链只消费 ``projected_facts(session_id)``（投影后事实），
  不直接读原始事件 payload。
- 无 session 的事件（project 级）不写会话环——不污染任何会话真相。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RING_KEY = "_spatial_event_facts"
MAX_RING = 32
MAX_SUMMARY_CHARS = 120


def _summarize(payload: Dict[str, Any]) -> str:
    """payload 的有界标量摘要（确定性；不含嵌套结构/长值）。"""
    parts: List[str] = []
    for k in sorted(payload.keys())[:6]:
        v = payload[k]
        if isinstance(v, (int, float, bool)):
            parts.append(f"{k}={v}")
        elif isinstance(v, str):
            parts.append(f"{k}={v[:24]}")
    return ";".join(parts)[:MAX_SUMMARY_CHARS]


def fact_from_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """ledger 行 → 有界 Situation 事实。"""
    return {
        "event_id": str(event.get("event_id") or "")[:64],
        "kind": str(event.get("kind") or "")[:64],
        "subject_key": str(event.get("subject_key") or "")[:128],
        "occurred_at": str(event.get("occurred_at") or "")[:40],
        "summary": _summarize(event.get("payload") or {}),
    }


async def project_event(event: Dict[str, Any], *, store: Any = None) -> bool:
    """把事件投影进会话事实环。返回 True=写入；False=无会话/重复/失败。

    best-effort：投影失败绝不阻塞事件处理（fail-open 于事件面）。
    """
    session_id = event.get("session_id")
    if not session_id:
        return False
    if store is None:
        from app.services.session_data import session_data_manager as store
    fact = fact_from_event(event)
    if not fact["event_id"]:
        return False
    try:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(str(session_id)):
            ring = await store.get_state_field(session_id, RING_KEY)
            ring = list(ring) if isinstance(ring, list) else []
            if any(str(e.get("event_id")) == fact["event_id"] for e in ring):
                return False  # 幂等：同事件不重复入环
            ring.append(fact)
            await store.set_map_state(session_id, RING_KEY, ring[-MAX_RING:])
        return True
    except Exception as e:  # noqa: BLE001 — 投影是增值面，绝不外溢
        logger.warning(
            "[spatial_events] situation projection failed for %s: %s",
            session_id, e,
        )
        return False


async def projected_facts(
    session_id: str, *, limit: int = MAX_RING, store: Any = None
) -> List[Dict[str, Any]]:
    """读取会话的投影事件事实（新→旧；Mission 决策与 compiler 第 6 源共用）。"""
    if store is None:
        from app.services.session_data import session_data_manager as store
    try:
        ring = await store.get_state_field(session_id, RING_KEY)
    except Exception:  # noqa: BLE001 — 读失败按无事实（fail-open）
        return []
    if not isinstance(ring, list):
        return []
    ordered = list(reversed(ring))  # 新→旧
    return ordered[: max(1, min(limit, MAX_RING))]


def facts_digest(facts: Optional[List[Dict[str, Any]]]) -> str:
    """投影事实的稳定摘要（进 Mission root_goal 的可审计标记）。"""
    import hashlib
    import json

    basis = json.dumps(facts or [], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
