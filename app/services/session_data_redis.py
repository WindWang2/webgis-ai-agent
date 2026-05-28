"""Redis-backed session data manager - persistent storage with TTL and LRU eviction"""
import asyncio
import json
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

DATA_TTL = 2 * 60 * 60
STATE_TTL = 4 * 60 * 60
EVENTS_TTL = 4 * 60 * 60
SESSION_TTL = 4 * 60 * 60
MAX_EVENTS = 20


class RedisSessionDataManager:
    """Session-level data store backed by Redis with cursor support (LRU)."""

    def __init__(self, redis_url: str, capacity: int = 200, socket_timeout: float = 1.0):
        self._r = aioredis.Redis.from_url(
            redis_url,
            decode_responses=False,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
        )
        self.capacity = capacity

    def ping(self):
        """Sync health check for startup. Creates an isolated event loop to avoid conflicts."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._r.ping())
        finally:
            loop.close()

    @staticmethod
    def _data_key(session_id: str, ref_id: str) -> str:
        return f"session:{session_id}:data:{ref_id}"

    @staticmethod
    def _aliases_key(session_id: str) -> str:
        return f"session:{session_id}:aliases"

    @staticmethod
    def _refs_key(session_id: str) -> str:
        return f"session:{session_id}:refs"

    @staticmethod
    def _state_key(session_id: str) -> str:
        return f"session:{session_id}:state"

    @staticmethod
    def _events_key(session_id: str) -> str:
        return f"session:{session_id}:events"

    @staticmethod
    def _index_key(session_id: str) -> str:
        return f"session:{session_id}:index"

    @staticmethod
    def _refs_order_key(session_id: str) -> str:
        return f"session:{session_id}:refs_order"

    @staticmethod
    def _active_key() -> str:
        return "sessions:active"

    async def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
        ref_id = f"ref:{prefix}-{uuid.uuid4().hex[:16]}"
        data_key = self._data_key(session_id, ref_id)
        order_key = self._refs_order_key(session_id)

        current_count = await self._r.zcard(order_key)
        if current_count >= self.capacity:
            overflow = current_count - self.capacity + 1
            oldest = await self._r.zrange(order_key, 0, overflow - 1)
            async with self._r.pipeline() as evict_pipe:
                for old_ref_bytes in oldest:
                    old_ref = old_ref_bytes.decode() if isinstance(old_ref_bytes, bytes) else old_ref_bytes
                    await self._evict_ref(evict_pipe, session_id, old_ref)
                await evict_pipe.execute()

        async with self._r.pipeline() as pipe:
            pipe.sadd(self._active_key(), session_id)
            pipe.set(data_key, json.dumps(data, ensure_ascii=False), ex=DATA_TTL)
            pipe.zadd(order_key, {ref_id: time.time()})
            pipe.sadd(self._index_key(session_id), ref_id)
            self._refresh_session_ttl(pipe, session_id)
            await pipe.execute()
        return ref_id

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        async with self._r.pipeline() as pipe:
            pipe.hset(self._aliases_key(session_id), alias, ref_id)
            pipe.hset(self._refs_key(session_id), ref_id, alias)
            self._refresh_session_ttl(pipe, session_id)
            await pipe.execute()

    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str:
        ref_id = await self._r.hget(self._aliases_key(session_id), ref_or_alias)
        if ref_id is None:
            return ref_or_alias
        return ref_id.decode() if isinstance(ref_id, bytes) else ref_id

    async def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
        ref_id = await self._r.hget(self._aliases_key(session_id), ref_id_or_alias)
        if ref_id is not None:
            ref_id = ref_id.decode() if isinstance(ref_id, bytes) else ref_id
        else:
            ref_id = ref_id_or_alias

        data_key = self._data_key(session_id, ref_id)
        raw = await self._r.get(data_key)
        if raw is None:
            return None

        async with self._r.pipeline() as pipe:
            pipe.expire(data_key, DATA_TTL)
            pipe.zadd(self._refs_order_key(session_id), {ref_id: time.time()})
            await pipe.execute()
        return json.loads(raw)

    async def list_refs(self, session_id: str) -> dict[str, str]:
        ref_ids_bytes = await self._r.zrange(self._refs_order_key(session_id), 0, -1)
        if not ref_ids_bytes:
            return {}
        ref_ids = [r.decode() if isinstance(r, bytes) else r for r in ref_ids_bytes]
        raw_refs = await self._r.hgetall(self._refs_key(session_id))
        ref_to_alias = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw_refs.items()
        }
        return {rid: ref_to_alias.get(rid, "") for rid in ref_ids}

    async def set_map_state(self, session_id: str, key: str, value: Any) -> None:
        async with self._r.pipeline() as pipe:
            pipe.hsetnx(
                self._state_key(session_id),
                "_started_at",
                json.dumps(datetime.now(timezone.utc).isoformat(), ensure_ascii=False),
            )
            pipe.hset(self._state_key(session_id), key, json.dumps(value, ensure_ascii=False))
            pipe.expire(self._state_key(session_id), STATE_TTL)
            pipe.sadd(self._active_key(), session_id)
            self._refresh_session_ttl(pipe, session_id)
            await pipe.execute()

    async def get_started_at(self, session_id: str) -> Optional[str]:
        raw = await self._r.hget(self._state_key(session_id), "_started_at")
        if not raw:
            return None
        return json.loads(raw)

    async def get_map_state(self, session_id: str) -> dict[str, Any]:
        raw = await self._r.hgetall(self._state_key(session_id))
        if not raw:
            return {}
        return {
            (k.decode() if isinstance(k, bytes) else k): json.loads(v)
            for k, v in raw.items()
        }

    async def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict) -> None:
        state = await self.get_map_state(session_id)
        layers = list(state.get("layers", []))
        for layer in layers:
            if layer.get("id") == layer_id:
                layer.update(updates)
                break
        else:
            layers.append({"id": layer_id, **updates})
        await self.set_map_state(session_id, "layers", layers)

    async def remove_layer_from_state(self, session_id: str, layer_id: str) -> None:
        state = await self.get_map_state(session_id)
        layers = state.get("layers", [])
        await self.set_map_state(
            session_id, "layers",
            [l for l in layers if l.get("id") != layer_id],
        )

    async def append_event(self, session_id: str, event: str, data: dict) -> None:
        entry = json.dumps(
            {"event": event, "data": data, "timestamp": datetime.now().isoformat()},
            ensure_ascii=False,
        )
        async with self._r.pipeline() as pipe:
            key = self._events_key(session_id)
            pipe.lpush(key, entry)
            pipe.ltrim(key, 0, MAX_EVENTS - 1)
            pipe.expire(key, EVENTS_TTL)
            pipe.sadd(self._active_key(), session_id)
            self._refresh_session_ttl(pipe, session_id)
            await pipe.execute()

    async def get_event_log(self, session_id: str) -> list[dict]:
        raw_list = await self._r.lrange(self._events_key(session_id), 0, -1)
        return [
            json.loads(item.decode() if isinstance(item, bytes) else item)
            for item in raw_list
        ]

    async def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        """Fetch session metadata in a single async pipeline."""
        async with self._r.pipeline() as pipe:
            pipe.hgetall(self._state_key(session_id))
            pipe.zrange(self._refs_order_key(session_id), 0, -1)
            pipe.hgetall(self._refs_key(session_id))
            pipe.lrange(self._events_key(session_id), 0, -1)
            try:
                state_raw, ref_ids_bytes, raw_refs, events_raw = await pipe.execute()
            except aioredis.RedisError as e:
                logger.error("Failed to fetch session metadata via pipeline for %s: %s", session_id, e)
                return {
                    "map_state": await self.get_map_state(session_id),
                    "list_refs": await self.list_refs(session_id),
                    "event_log": await self.get_event_log(session_id),
                    "started_at": await self.get_started_at(session_id),
                }

        map_state: dict = {}
        started_at = None
        if state_raw:
            for k, v in state_raw.items():
                key = k.decode() if isinstance(k, bytes) else k
                try:
                    map_state[key] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    continue
            started_at = map_state.get("_started_at")

        ref_ids = [r.decode() if isinstance(r, bytes) else r for r in (ref_ids_bytes or [])]
        ref_to_alias = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in (raw_refs or {}).items()
        }
        list_refs = {rid: ref_to_alias.get(rid, "") for rid in ref_ids}

        event_log = []
        for item in (events_raw or []):
            text = item.decode() if isinstance(item, bytes) else item
            try:
                event_log.append(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                continue

        return {
            "map_state": map_state,
            "list_refs": list_refs,
            "event_log": event_log,
            "started_at": started_at,
        }

    async def clear_session(self, session_id: str) -> None:
        index_key = self._index_key(session_id)
        ref_ids = await self._r.smembers(index_key)
        async with self._r.pipeline() as pipe:
            for ref_bytes in ref_ids:
                ref_id = ref_bytes.decode() if isinstance(ref_bytes, bytes) else ref_bytes
                pipe.delete(self._data_key(session_id, ref_id))
            pipe.delete(
                index_key,
                self._aliases_key(session_id),
                self._refs_key(session_id),
                self._state_key(session_id),
                self._events_key(session_id),
                self._refs_order_key(session_id),
            )
            pipe.srem(self._active_key(), session_id)
            await pipe.execute()

    async def cleanup_idle_sessions(self, max_sessions: int = 100) -> None:
        active = await self._r.smembers(self._active_key())
        if not active or len(active) <= max_sessions:
            return
        scored = []
        for sid_bytes in active:
            sid = sid_bytes.decode() if isinstance(sid_bytes, bytes) else sid_bytes
            earliest = await self._r.zrange(self._refs_order_key(sid), 0, 0, withscores=True)
            score = earliest[0][1] if earliest else 0
            scored.append((sid, score))
        scored.sort(key=lambda x: x[1])
        to_remove = len(scored) - max_sessions + 10
        for sid, _ in scored[:to_remove]:
            await self.clear_session(sid)
        logger.info("Cleaned up %d idle sessions", min(to_remove, len(scored)))

    async def _evict_ref(self, pipe, session_id: str, ref_id: str) -> None:
        """Add eviction commands to an open pipeline. Alias hget needs immediate await."""
        alias = await self._r.hget(self._refs_key(session_id), ref_id)
        pipe.delete(self._data_key(session_id, ref_id))
        pipe.zrem(self._refs_order_key(session_id), ref_id)
        pipe.srem(self._index_key(session_id), ref_id)
        if alias:
            alias_str = alias.decode() if isinstance(alias, bytes) else alias
            pipe.hdel(self._aliases_key(session_id), alias_str)
        pipe.hdel(self._refs_key(session_id), ref_id)

    def _refresh_session_ttl(self, pipe, session_id: str) -> None:
        for key in [
            self._aliases_key(session_id),
            self._refs_key(session_id),
            self._refs_order_key(session_id),
            self._index_key(session_id),
        ]:
            pipe.expire(key, SESSION_TTL)
