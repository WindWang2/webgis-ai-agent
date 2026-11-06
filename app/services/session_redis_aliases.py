"""Redis alias publication/deletion with live-ref and concurrent-rebinding fences."""
from __future__ import annotations

import logging
from typing import Any

from redis import asyncio as aioredis

logger = logging.getLogger(__name__)


def _text(value: Any) -> str | None:
    return value.decode() if isinstance(value, bytes) else value


async def delete_ref(store: Any, session_id: str, ref_id: str) -> bool:
    await store._ensure_connected()
    for _ in range(4):
        try:
            async with store._r.pipeline() as pipe:
                data_key, aliases_key = store._data_key(session_id, ref_id), store._aliases_key(session_id)
                await pipe.watch(data_key, aliases_key)
                exists = bool(await pipe.exists(data_key))
                aliases = await pipe.hgetall(aliases_key)
                owned = [key for key, value in aliases.items() if _text(value) == ref_id]
                pipe.multi()
                store._evict_ref(pipe, session_id, ref_id, None)
                if owned:
                    pipe.hdel(aliases_key, *owned)
                pipe.hdel(store._ref_revisions_key(session_id), ref_id)
                pipe.hdel(store._ref_digests_key(session_id), ref_id)
                await pipe.execute()
            store._l1_invalidate_session(session_id)
            store._invalidate_derived_caches(session_id, ref_id, reason="DELETE")
            return exists
        except aioredis.WatchError:
            continue
        except aioredis.RedisError:
            logger.warning("Redis ref deletion unavailable", exc_info=True)
            return False
    return False


async def set_alias_if_absent(store: Any, session_id: str, ref_id: str, alias: str) -> bool:
    await store._ensure_connected()
    for _ in range(4):
        try:
            async with store._r.pipeline() as pipe:
                data_key, aliases_key = store._data_key(session_id, ref_id), store._aliases_key(session_id)
                await pipe.watch(data_key, aliases_key)
                if not await pipe.exists(data_key) or await pipe.hexists(aliases_key, alias):
                    return False
                pipe.multi()
                pipe.hset(aliases_key, alias, ref_id)
                pipe.hset(store._refs_key(session_id), ref_id, alias)
                store._refresh_session_ttl(pipe, session_id)
                await pipe.execute()
                store._l1_invalidate_session(session_id)
                return True
        except aioredis.WatchError:
            continue
        except aioredis.RedisError:
            return False
    return False


async def resolve_alias(store: Any, session_id: str, alias: str) -> str:
    await store._ensure_connected()
    for _ in range(4):
        try:
            async with store._r.pipeline() as pipe:
                key = store._aliases_key(session_id)
                await pipe.watch(key)
                ref_id = _text(await pipe.hget(key, alias))
                if ref_id is None:
                    return alias
                data_key = store._data_key(session_id, ref_id)
                await pipe.watch(data_key)
                if await pipe.exists(data_key):
                    return ref_id
                reverse = _text(await pipe.hget(store._refs_key(session_id), ref_id))
                pipe.multi()
                pipe.hdel(key, alias)
                if reverse == alias:
                    pipe.hdel(store._refs_key(session_id), ref_id)
                await pipe.execute()
                store._l1_invalidate_session(session_id)
                return alias
        except aioredis.WatchError:
            continue
        except aioredis.RedisError:
            logger.warning("Redis alias resolution unavailable", exc_info=True)
            return alias
    return alias
