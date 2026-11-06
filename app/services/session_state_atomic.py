"""Atomic targeted map-state read/modify/write for both storage backends.

The callback is synchronous and pure: Redis may retry it after a WATCH conflict.
It receives independent decoded data and returns only fields to replace.
"""
from __future__ import annotations

import asyncio
import copy
import json
import weakref
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from redis import asyncio as aioredis

Mutation = Callable[[Mapping[str, Any]], Mapping[str, Any]]
_FALLBACK_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[int, str], weakref.ReferenceType[asyncio.Lock]]] = weakref.WeakKeyDictionary()


def _adapter_lock(store: Any, session_id: str) -> asyncio.Lock:
    locks = _FALLBACK_LOCKS.setdefault(asyncio.get_running_loop(), {})
    for key, existing_reference in list(locks.items()):
        if existing_reference() is None:
            locks.pop(key, None)
    key = (id(store), session_id)
    reference = locks.get(key)
    lock = reference() if reference is not None else None
    if lock is None:
        if len(locks) >= 1024:
            raise RuntimeError("atomic state adapter capacity exhausted")
        lock = asyncio.Lock()
        locks[key] = weakref.ref(lock)
    return lock


async def atomic_update_state(
    store: Any, session_id: str, keys: Sequence[str], mutate: Mutation,
) -> Mapping[str, Any] | None:
    """Return committed replacements, or None on storage/CAS failure."""
    if hasattr(store, "_r"):
        from app.services.session_data_redis import STATE_TTL
        from datetime import datetime, timezone

        await store._ensure_connected()
        state_key = store._state_key(session_id)
        for _ in range(4):
            try:
                async with store._r.pipeline() as pipe:
                    await pipe.watch(state_key)
                    values = await pipe.hmget(state_key, list(keys))
                    snapshot = {key: json.loads(value) if value is not None else None
                                for key, value in zip(keys, values)}
                    updated = dict(mutate(snapshot))
                    payloads = {key: json.dumps(value, ensure_ascii=False) for key, value in updated.items()}
                    pipe.multi()
                    pipe.hsetnx(state_key, "_started_at", datetime.now(timezone.utc).isoformat())
                    if payloads:
                        pipe.hset(state_key, mapping=payloads)
                    pipe.expire(state_key, STATE_TTL)
                    pipe.sadd(store._active_key(), session_id)
                    store._refresh_session_ttl(pipe, session_id)
                    await pipe.execute()
                store._l1_invalidate_session(session_id)
                return updated
            except aioredis.WatchError:
                continue
            except aioredis.RedisError:
                return None
        return None
    lock = getattr(store, "_map_state_lock", None) or _adapter_lock(store, session_id)
    async with lock:
        if hasattr(store, "_map_state"):
            state = store._map_state.get(session_id, {})
            snapshot = copy.deepcopy({key: state.get(key) for key in keys})
            updated = dict(mutate(snapshot))
            for key, value in updated.items():
                store._set_map_state_unlocked(session_id, key, value)
            return updated
        # Custom adapters are serialized here. A single replacement field is
        # atomic; multi-field adapters must provide their own commit primitive.
        getter = getattr(store, "get_state_field", None)
        if callable(getter):
            snapshot = {key: await getter(session_id, key) for key in keys}
        else:
            snapshot = await store.get_map_state(session_id)
        updated = dict(mutate(copy.deepcopy(snapshot)))
        writer = getattr(store, "set_map_state_fields", None)
        if callable(writer):
            ok = await writer(session_id, updated)
        elif len(updated) == 1:
            key, value = next(iter(updated.items()))
            ok = await store.set_map_state(session_id, key, value)
        else:
            raise RuntimeError("adapter does not support atomic multi-field state writes")
        return updated if ok is not False else None
