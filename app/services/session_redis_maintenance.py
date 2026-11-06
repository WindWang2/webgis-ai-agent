"""Bounded Redis session housekeeping and corruption-tolerant log decoding."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, TYPE_CHECKING

import redis.asyncio as aioredis
from app.services.session_data_protocol import session_activity_is_idle

if TYPE_CHECKING:
    from app.services.session_data_redis import RedisSessionStore

logger = logging.getLogger(__name__)

def decode_json_objects(values: Any, *, source: str) -> list[dict]:
    records = []
    corrupt = 0
    for value in values:
        if value is None:
            continue
        try:
            decoded = json.loads(value)
            if not isinstance(decoded, dict):
                raise ValueError("record is not an object")
            records.append(decoded)
        except (ValueError, TypeError, UnicodeError):
            corrupt += 1
    if corrupt:
        logger.warning("Redis %s skipped %d corrupt records", source, corrupt)
    return records


async def cleanup_idle_sessions(store: "RedisSessionStore", max_sessions: int) -> None:
    from app.services.session_data_protocol import IDLE_SESSION_TTL
    await store._ensure_connected()
    active = await store._r.smembers(store._active_key())
    if not active or len(active) <= max_sessions:
        return
    scored = []
    # #618-7: 单次 ZRANGE withscores 取回 activity 全集分数，替代逐会话
    # 串行 ZSCORE（每 600s 清扫 100+ 会话时省去等量 RTT）。
    activity_scores = await store._r.zrange(store._activity_key(), 0, -1, withscores=True)
    if isinstance(activity_scores, dict):
        score_map = {
            (k.decode() if isinstance(k, bytes) else k): float(v)
            for k, v in activity_scores.items()
        }
    else:
        score_map = {}
        for member, ts in activity_scores or []:
            m = member.decode() if isinstance(member, bytes) else member
            score_map[m] = float(ts)
    for sid_bytes in active:
        sid = sid_bytes.decode() if isinstance(sid_bytes, bytes) else sid_bytes
        # activity 集缺条目（如旧会话从未写 activity）时退回 refs_order 首条时间
        raw_score = score_map.get(sid)
        if raw_score is None:
            earliest = await store._r.zrange(store._refs_order_key(sid), 0, 0, withscores=True)
            raw_score = earliest[0][1] if earliest else None
        if raw_score is None:
            # No timestamp is not proof of age. Remove only an empty index
            # member; existing state/events/refs preserve an unknown-age session.
            keys = [store._state_key(sid), store._events_key(sid), store._refs_key(sid),
                    store._aliases_key(sid), store._map_actions_key(sid),
                    store._ref_revisions_key(sid), store._ref_digests_key(sid)]
            async with store._r.pipeline() as pipe:
                try:
                    await pipe.watch(*keys)
                    if not await pipe.exists(*keys):
                        pipe.multi()
                        pipe.srem(store._active_key(), sid)
                        pipe.zrem(store._activity_key(), sid)
                        await pipe.execute()
                        continue
                except aioredis.WatchError:
                    logger.debug("Redis idle orphan acquired new state: %s", sid)
            scored.append((sid, None))
        else:
            scored.append((sid, float(raw_score)))
    now = time.time()
    # Oldest idle first. score==0 / missing = brand-new — never treated as
    # epoch-idle (#1386 R02). Overflow of *live* sessions is kept.
    idle = [
        (sid, score) for sid, score in scored
        if session_activity_is_idle(score, now, ttl=IDLE_SESSION_TTL)
    ]
    idle.sort(key=lambda x: x[1])
    to_remove = max(0, len(scored) - max_sessions)
    cleaned = 0
    for sid, _ in idle[:to_remove]:
        # #752: per-session isolation — one failing eviction must not
        # abort the rest of the list until the next 10-min tick
        # (clear_session itself degrades on RedisError, but the disk
        # purge / other exceptions stay contained here too).
        try:
            await store.clear_session(sid)
            cleaned += 1
        except Exception as e:  # noqa: BLE001 - eviction is per-session best-effort
            logger.warning("Idle cleanup failed for session %s: %s", sid, e)
    if cleaned:
        logger.info("Cleaned up %d idle sessions", cleaned)
