"""Redis-backed session data manager - persistent storage with TTL and LRU eviction"""
import json
import time
import uuid
import logging
from datetime import datetime
from typing import Any, Optional

import redis

logger = logging.getLogger(__name__)

# TTL constants (seconds)
DATA_TTL = 2 * 60 * 60        # 2 hours for individual data refs
STATE_TTL = 4 * 60 * 60       # 4 hours for map state
EVENTS_TTL = 4 * 60 * 60      # 4 hours for event log
SESSION_TTL = 4 * 60 * 60     # 4 hours umbrella for session-level keys
MAX_EVENTS = 20                # Max events kept per session


class _AliasesProxy:
    """Dict-like proxy for Redis alias hash so that code accessing
    ``session_data_manager._aliases.get(session_id, {}).get(...)``
    and ``session_id in session_data_manager._aliases``
    continues to work without changes."""

    def __init__(self, redis_client: redis.Redis):
        self._r = redis_client

    def get(self, session_id: str, default: Any = None) -> dict:
        """Return a plain dict mapping alias -> ref_id for *session_id*."""
        try:
            raw = self._r.hgetall(f"session:{session_id}:aliases")
            if not raw:
                return default if default is not None else {}
            return {k.decode(): v.decode() for k, v in raw.items()}
        except redis.RedisError:
            logger.exception("Failed to read aliases for session %s", session_id)
            return default if default is not None else {}

    def __contains__(self, session_id: str) -> bool:
        """Support ``session_id in proxy`` (used by registry.py)."""
        try:
            return self._r.exists(f"session:{session_id}:aliases") > 0
        except redis.RedisError:
            return False

    def __getitem__(self, session_id: str) -> dict:
        """Support ``proxy[session_id]`` (used by registry.py ``in`` check)."""
        result = self.get(session_id)
        if not result:
            raise KeyError(session_id)
        return result


class RedisSessionDataManager:
    """Session-level data store backed by Redis with cursor support (LRU)."""

    def __init__(self, redis_url: str, capacity: int = 200):
        self._r = redis.Redis.from_url(redis_url, decode_responses=False)
        self.capacity = capacity
        # Expose _aliases as a dict-like proxy for backward compatibility
        self._aliases = _AliasesProxy(self._r)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    def ping(self):
        """Verify Redis is reachable. Raises on failure."""
        self._r.ping()

    # ------------------------------------------------------------------
    # Internal key helpers
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------
    def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
        """Store data and return a generated cursor ref_id."""
        ref_id = f"ref:{prefix}-{uuid.uuid4().hex[:8]}"
        data_key = self._data_key(session_id, ref_id)

        pipe = self._r.pipeline()

        # Register session as active
        pipe.sadd(self._active_key(), session_id)

        # LRU eviction: remove oldest refs if at capacity
        order_key = self._refs_order_key(session_id)
        current_count = self._r.zcard(order_key)
        if current_count >= self.capacity:
            # Fetch oldest entries to evict
            overflow = current_count - self.capacity + 1
            oldest = self._r.zrange(order_key, 0, overflow - 1)
            for old_ref_bytes in oldest:
                old_ref = old_ref_bytes.decode() if isinstance(old_ref_bytes, bytes) else old_ref_bytes
                self._evict_ref(pipe, session_id, old_ref)
                logger.debug("Session %s: evicted %s (capacity=%d)", session_id, old_ref, self.capacity)

        # Store the data
        pipe.set(data_key, json.dumps(data, ensure_ascii=False), ex=DATA_TTL)
        now = time.time()
        pipe.zadd(order_key, {ref_id: now})
        pipe.sadd(self._index_key(session_id), ref_id)

        # Refresh session-level key TTLs
        self._refresh_session_ttl(pipe, session_id)

        pipe.execute()
        return ref_id

    def set_alias(self, session_id: str, ref_id: str, alias: str):
        """Map an alias to a ref_id."""
        pipe = self._r.pipeline()
        pipe.hset(self._aliases_key(session_id), alias, ref_id)
        pipe.hset(self._refs_key(session_id), ref_id, alias)
        self._refresh_session_ttl(pipe, session_id)
        pipe.execute()

    def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
        """Retrieve data by cursor ref_id or alias."""
        # Try alias lookup first
        ref_id = self._r.hget(self._aliases_key(session_id), ref_id_or_alias)
        if ref_id is not None:
            ref_id = ref_id.decode() if isinstance(ref_id, bytes) else ref_id
        else:
            ref_id = ref_id_or_alias

        data_key = self._data_key(session_id, ref_id)
        raw = self._r.get(data_key)
        if raw is None:
            return None

        # Refresh TTL on access (LRU touch)
        pipe = self._r.pipeline()
        pipe.expire(data_key, DATA_TTL)
        pipe.zadd(self._refs_order_key(session_id), {ref_id: time.time()})
        pipe.execute()

        return json.loads(raw)

    def list_refs(self, session_id: str) -> dict[str, str]:
        """List all refs and their aliases: {ref_id: alias_or_empty}."""
        # Get all ref_ids from the sorted set (ordered by recency)
        ref_ids_bytes = self._r.zrange(self._refs_order_key(session_id), 0, -1)
        if not ref_ids_bytes:
            return {}

        ref_ids = [
            r.decode() if isinstance(r, bytes) else r for r in ref_ids_bytes
        ]

        # Build ref_id -> alias mapping
        raw_refs = self._r.hgetall(self._refs_key(session_id))
        ref_to_alias = {}
        for k, v in raw_refs.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            ref_to_alias[key] = val

        return {rid: ref_to_alias.get(rid, "") for rid in ref_ids}

    def set_map_state(self, session_id: str, key: str, value: Any):
        """Set a map state metadata field."""
        pipe = self._r.pipeline()
        pipe.hset(self._state_key(session_id), key, json.dumps(value, ensure_ascii=False))
        pipe.expire(self._state_key(session_id), STATE_TTL)
        pipe.sadd(self._active_key(), session_id)
        self._refresh_session_ttl(pipe, session_id)
        pipe.execute()

    def get_map_state(self, session_id: str) -> dict[str, Any]:
        """Return all map state as a Python dict."""
        raw = self._r.hgetall(self._state_key(session_id))
        if not raw:
            return {}
        result = {}
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else k
            result[key] = json.loads(v)
        return result

    def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict):
        """Update a single layer's attributes within map state."""
        state = self.get_map_state(session_id)
        layers = list(state.get("layers", []))
        for layer in layers:
            if layer.get("id") == layer_id:
                layer.update(updates)
                break
        else:
            layers.append({"id": layer_id, **updates})
        self.set_map_state(session_id, "layers", layers)

    def remove_layer_from_state(self, session_id: str, layer_id: str):
        """Remove a layer from map state."""
        state = self.get_map_state(session_id)
        layers = state.get("layers", [])
        self.set_map_state(
            session_id,
            "layers",
            [l for l in layers if l.get("id") != layer_id],
        )

    def append_event(self, session_id: str, event: str, data: dict):
        """Append an event to the session event log (capped at MAX_EVENTS)."""
        entry = json.dumps(
            {
                "event": event,
                "data": data,
                "timestamp": datetime.now().isoformat(),
            },
            ensure_ascii=False,
        )
        pipe = self._r.pipeline()
        key = self._events_key(session_id)
        pipe.lpush(key, entry)
        # Keep only the most recent MAX_EVENTS
        pipe.ltrim(key, 0, MAX_EVENTS - 1)
        pipe.expire(key, EVENTS_TTL)
        pipe.sadd(self._active_key(), session_id)
        self._refresh_session_ttl(pipe, session_id)
        pipe.execute()

    def get_event_log(self, session_id: str) -> list[dict]:
        """Return recent events (newest first)."""
        raw_list = self._r.lrange(self._events_key(session_id), 0, -1)
        events = []
        for item in raw_list:
            text = item.decode() if isinstance(item, bytes) else item
            events.append(json.loads(text))
        return events

    def clear_session(self, session_id: str):
        """Remove all data for a session."""
        pipe = self._r.pipeline()

        # Remove all data keys
        index_key = self._index_key(session_id)
        ref_ids = self._r.smembers(index_key)
        for ref_bytes in ref_ids:
            ref_id = ref_bytes.decode() if isinstance(ref_bytes, bytes) else ref_bytes
            pipe.delete(self._data_key(session_id, ref_id))

        # Remove session-level structures
        pipe.delete(
            index_key,
            self._aliases_key(session_id),
            self._refs_key(session_id),
            self._state_key(session_id),
            self._events_key(session_id),
            self._refs_order_key(session_id),
        )
        pipe.srem(self._active_key(), session_id)
        pipe.execute()

    def cleanup_idle_sessions(self, max_sessions: int = 100):
        """Evict oldest sessions when total count exceeds *max_sessions*.

        In the Redis implementation this is less critical because TTL handles
        expiry, but we still cap active sessions for safety.
        """
        active = self._r.smembers(self._active_key())
        if not active or len(active) <= max_sessions:
            return

        # Sort sessions by their earliest ref timestamp to find oldest
        scored = []
        for sid_bytes in active:
            sid = sid_bytes.decode() if isinstance(sid_bytes, bytes) else sid_bytes
            # Use the minimum score (earliest ref) as the session age proxy
            order_key = self._refs_order_key(sid)
            earliest = self._r.zrange(order_key, 0, 0, withscores=True)
            score = earliest[0][1] if earliest else 0
            scored.append((sid, score))

        scored.sort(key=lambda x: x[1])
        to_remove = len(scored) - max_sessions + 10
        for sid, _ in scored[:to_remove]:
            self.clear_session(sid)
        logger.info("Cleaned up %d idle sessions", min(to_remove, len(scored)))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    def _evict_ref(self, pipe, session_id: str, ref_id: str):
        """Remove a single ref and its alias from a pipeline."""
        pipe.delete(self._data_key(session_id, ref_id))
        pipe.zrem(self._refs_order_key(session_id), ref_id)
        pipe.srem(self._index_key(session_id), ref_id)

        # Remove any alias pointing to this ref_id
        alias = self._r.hget(self._refs_key(session_id), ref_id)
        if alias:
            alias_str = alias.decode() if isinstance(alias, bytes) else alias
            pipe.hdel(self._aliases_key(session_id), alias_str)
        pipe.hdel(self._refs_key(session_id), ref_id)

    def _refresh_session_ttl(self, pipe, session_id: str):
        """Refresh TTL on session-level keys to keep them alive."""
        session_keys = [
            self._aliases_key(session_id),
            self._refs_key(session_id),
            self._refs_order_key(session_id),
            self._index_key(session_id),
        ]
        for key in session_keys:
            pipe.expire(key, SESSION_TTL)
