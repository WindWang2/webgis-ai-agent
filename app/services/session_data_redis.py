"""Redis-backed session data manager - persistent storage with TTL and LRU eviction"""
import asyncio
import json
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as aioredis
from app.services.session_data_protocol import BaseSessionStore

logger = logging.getLogger(__name__)

DATA_TTL = 2 * 60 * 60
STATE_TTL = 4 * 60 * 60
EVENTS_TTL = 4 * 60 * 60
SESSION_TTL = 4 * 60 * 60
MAX_EVENTS = 20


class RedisSessionStore(BaseSessionStore):

    """Session-level data store backed by Redis with cursor support (LRU)."""

    def __init__(
        self,
        redis_url: str,
        capacity: int = 200,
        socket_timeout: float = 5.0,
        redis: Optional[aioredis.Redis] = None,
    ):
        # 审计 TEST-13：不要在此创建 Redis 客户端。Redis.from_url 返回的客户端在
        # 第一次 async 操作时会把它内部的连接池绑定到当时的 event loop；而本单例在
        # 模块 import 时就构造（session_data_manager = create_session_data_manager()），
        # 此时要么没有运行中的 loop，要么 ping() 会临时 new_event_loop 跑一次再关闭。
        # 连接池一旦绑定到那个错误/已关闭的 loop，pytest-asyncio 每个测试用新 loop 时
        # 就会报 "Future attached to a different loop"。
        # 改为懒构造：存配置，首次 async 调用时由 _ensure_connected() 在正确的 loop 上创建。
        self._redis_url = redis_url
        self._socket_timeout = socket_timeout
        # 测试注入：允许测试套件传入一个已经构造好的客户端（例如 fakeredis）。
        # 跳过 lazy 构造，避免 `redis_url` 与注入的客户端不一致。生产路径不传此参数。
        self._injected_redis = redis
        self._r: Optional[aioredis.Redis] = redis
        self._bound_loop: Optional[asyncio.AbstractEventLoop] = None
        self.capacity = capacity

    async def _ensure_connected(self) -> aioredis.Redis:
        """懒构造 Redis 客户端，绑定到当前运行中的 event loop。

        首次 async 调用时触发；之后复用同一个客户端。这样连接池总是绑定到真正
        运行测试/请求的 loop，避免 import 期 event loop 错配。

        审计 TEST-13：pytest-asyncio 每个测试用新 event loop。如果上一个测试
        的 loop 已关闭但 self._r 还绑定着它，下一个测试调用就会报
        "Event loop is closed"。检测 loop 变化时重新创建客户端。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        # 测试注入：注入的客户端在构造时已绑定，不需要懒构造或重连。
        if self._injected_redis is not None:
            return self._injected_redis

        # 如果客户端已存在且绑定的 loop 仍是当前 loop，复用
        if self._r is not None and self._bound_loop is not None and self._bound_loop is loop:
            return self._r

        # loop 变了（或首次创建）—— 重建客户端
        if self._r is not None:
            try:
                await self._r.aclose()
            except Exception:
                pass  # 旧 loop 可能已关闭，aclose 会失败，忽略

        self._r = aioredis.Redis.from_url(
            self._redis_url,
            decode_responses=False,
            socket_timeout=self._socket_timeout,
            socket_connect_timeout=self._socket_timeout,
        )
        self._bound_loop = loop
        return self._r

    async def ping(self) -> None:
        """Async health check. Lazily connects then pings.

        审计 TEST-13：原来是 sync 方法 + new_event_loop，会在 import 期把连接池绑死到
        临时 loop。改为 async，由调用方在自己的 loop 里 await（启动健康检查/测试用）。
        """
        client = await self._ensure_connected()
        await client.ping()

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
        """存数据；Redis 不可达时降级返回伪 ref_id 而非抛错。

        审计 C3：socket_timeout=1s 的 Redis 必然会偶发超时，若不隔离会让
        dispatcher.dispatch_tool 直接 raise → 整个 chat turn 崩溃 + tracker
        卡死。降级返回 ref:redis-unavailable-xxx 让上层 chat_engine 把它当
        普通失败工具结果处理（_untrusted + 自愈消息）。
        """
        await self._ensure_connected()
        ref_id = f"ref:{prefix}-{uuid.uuid4().hex[:16]}"
        data_key = self._data_key(session_id, ref_id)
        order_key = self._refs_order_key(session_id)
        try:
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
                pipe.hsetnx(
                    self._state_key(session_id),
                    "_started_at",
                    # BUG-09：直接存 ISO 字符串，不再 json.dumps（旧实现双重编码：
                    # isoformat() 已是 str，再 json.dumps 会多套一层引号）。
                    datetime.now(timezone.utc).isoformat(),
                )
                pipe.expire(self._state_key(session_id), STATE_TTL)
                pipe.sadd(self._active_key(), session_id)
                pipe.set(data_key, json.dumps(data, ensure_ascii=False), ex=DATA_TTL)
                pipe.zadd(order_key, {ref_id: time.time()})
                pipe.sadd(self._index_key(session_id), ref_id)
                self._refresh_session_ttl(pipe, session_id)
                await pipe.execute()
        except aioredis.RedisError as e:
            logger.error(
                "Redis store failed for session %s (prefix=%s): %s — returning unavailable ref",
                session_id, prefix, e,
            )
            return f"ref:redis-unavailable-{uuid.uuid4().hex[:16]}"
        return ref_id

    async def overwrite(self, session_id: str, ref_id: str, data: Any) -> bool:
        """Overwrite the data stored at an existing ``ref_id`` (same key).

        ``store`` always mints a new ref_id, which breaks callers like plan_mode's
        ``update_plan_status``: they hold the original plan_id and would keep reading
        the stale payload. Redis ``get`` also returns a deserialized copy, so in-place
        mutation of that copy is never persisted. This method writes back to the SAME
        ``data`` key so subsequent ``get(ref_id)`` returns the updated payload.

        Returns True on success, False if Redis is unavailable (caller degrades).
        """
        await self._ensure_connected()
        data_key = self._data_key(session_id, ref_id)
        try:
            async with self._r.pipeline() as pipe:
                pipe.set(data_key, json.dumps(data, ensure_ascii=False), ex=DATA_TTL)
                self._refresh_session_ttl(pipe, session_id)
                await pipe.execute()
        except aioredis.RedisError as e:
            logger.error(
                "Redis overwrite failed for session %s ref %s: %s",
                session_id, ref_id, e,
            )
            return False
        return True

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        await self._ensure_connected()
        async with self._r.pipeline() as pipe:
            pipe.hset(self._aliases_key(session_id), alias, ref_id)
            pipe.hset(self._refs_key(session_id), ref_id, alias)
            self._refresh_session_ttl(pipe, session_id)
            await pipe.execute()

    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str:
        await self._ensure_connected()
        ref_id = await self._r.hget(self._aliases_key(session_id), ref_or_alias)
        if ref_id is None:
            return ref_or_alias
        return ref_id.decode() if isinstance(ref_id, bytes) else ref_id

    async def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
        """读数据；Redis 不可达时返回 None（cache-miss 语义），让上层工具走自愈路径。

        审计 C3：同 store —— Redis 抖动不能杀死整个 chat turn。
        """
        await self._ensure_connected()
        try:
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
                self._refresh_session_ttl(pipe, session_id)
                await pipe.execute()

            raw_str = raw.decode() if isinstance(raw, bytes) else raw
            try:
                return json.loads(raw_str)
            except Exception:
                return raw_str
        except aioredis.RedisError as e:
            logger.error(
                "Redis get failed for session %s ref %s: %s — returning cache-miss",
                session_id, ref_id_or_alias, e,
            )
            return None

    # P2-1: get_ref_data was a byte-identical override of
    # BaseSessionStore.get_ref_data and has been removed. The inherited Base
    # implementation delegates to self.get() and self.get_session_metadata()
    # (both overridden below), which carry the backend-specific logic.

    async def list_refs(self, session_id: str) -> dict[str, str]:
        await self._ensure_connected()
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
        await self._ensure_connected()
        async with self._r.pipeline() as pipe:
            pipe.hsetnx(
                self._state_key(session_id),
                "_started_at",
                # BUG-09：直接存 ISO 字符串，不双重编码。
                datetime.now(timezone.utc).isoformat(),
            )
            pipe.hset(self._state_key(session_id), key, json.dumps(value, ensure_ascii=False))
            pipe.expire(self._state_key(session_id), STATE_TTL)
            pipe.sadd(self._active_key(), session_id)
            self._refresh_session_ttl(pipe, session_id)
            await pipe.execute()

    async def get_started_at(self, session_id: str) -> Optional[str]:
        await self._ensure_connected()
        raw = await self._r.hget(self._state_key(session_id), "_started_at")
        return self._decode_started_at(raw)

    @staticmethod
    def _decode_started_at(raw: Any) -> Optional[str]:
        """Normalize a stored ``_started_at`` value to a plain ISO string.

        BUG-09: ``_started_at`` used to be double-encoded (``json.dumps`` of an
        already-string ISO timestamp). New writes store the raw ISO string. To
        stay correct while old (double-encoded) values still live in Redis
        (until TTL), accept both: a bare ISO string is returned as-is, while a
        JSON-quoted string is unwrapped once.
        """
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        # A double-encoded value looks like '"2024-..Z"' (leading quote).
        # json.loads unwraps that; a bare ISO string is not valid JSON alone,
        # so the JSONDecodeError fall-through returns it unchanged.
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
        return decoded if isinstance(decoded, str) else raw

    async def get_map_state(self, session_id: str) -> dict[str, Any]:
        await self._ensure_connected()
        raw = await self._r.hgetall(self._state_key(session_id))
        if not raw:
            return {}
        out: dict[str, Any] = {}
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else k
            if key == "_started_at":
                # BUG-09: _started_at is now stored as a bare ISO string (not
                # JSON). Use the tolerant decoder to also handle legacy values.
                out[key] = self._decode_started_at(v)
            else:
                out[key] = json.loads(v)
        return out

    async def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict) -> None:
        """审计 M11：read-modify-write 必须用 WATCH/MULTI 防并发覆盖。

        之前两个并发 update_layer_in_state 都读旧 layers list，后写的覆盖先写的。
        WATCH state key + retry 3 次；超出则放弃（log warning，不抛）。
        """
        await self._ensure_connected()
        state_key = self._state_key(session_id)
        for attempt in range(3):
            try:
                async with self._r.pipeline(transaction=True) as pipe:
                    await pipe.watch(state_key)
                    # 读当前 layers。json.loads 接受 str 或 bytes，无需区分类型
                    # （BUG-05：旧 isinstance 嵌套冗余且脆弱）。
                    raw_layers = await pipe.hget(state_key, "layers")
                    layers = json.loads(raw_layers) if raw_layers is not None else []
                    layers = list(layers)
                    # mutate
                    for layer in layers:
                        if layer.get("id") == layer_id:
                            layer.update(updates)
                            break
                    else:
                        layers.append({"id": layer_id, **updates})
                    # 写回
                    pipe.multi()
                    pipe.hset(state_key, "layers", json.dumps(layers, ensure_ascii=False))
                    pipe.expire(state_key, STATE_TTL)
                    pipe.sadd(self._active_key(), session_id)
                    await pipe.execute()
                    return  # 成功
            except aioredis.WatchError:
                continue  # 重试
            except (json.JSONDecodeError, ValueError) as e:
                # BUG-05：layers 键存了无法解析的脏数据 —— 降级，不抛。
                logger.warning(
                    "update_layer_in_state: corrupt 'layers' for %s layer %s: %s",
                    session_id, layer_id, e,
                )
                return
            except aioredis.RedisError as e:
                logger.warning(
                    "update_layer_in_state Redis failed for %s layer %s: %s",
                    session_id, layer_id, e,
                )
                return  # 降级，不抛
        logger.warning(
            "update_layer_in_state gave up after 3 retries for %s layer %s (concurrent contention)",
            session_id, layer_id,
        )

    async def remove_layer_from_state(self, session_id: str, layer_id: str) -> None:
        """审计 M11：同 update_layer_in_state，用 WATCH/MULTI 防并发覆盖。"""
        await self._ensure_connected()
        state_key = self._state_key(session_id)
        for attempt in range(3):
            try:
                async with self._r.pipeline(transaction=True) as pipe:
                    await pipe.watch(state_key)
                    raw_layers = await pipe.hget(state_key, "layers")
                    if raw_layers is not None:
                        if isinstance(raw_layers, bytes):
                            layers = json.loads(raw_layers.decode())
                        else:
                            layers = json.loads(raw_layers)
                    else:
                        layers = []
                    new_layers = [layer for layer in layers if layer.get("id") != layer_id]
                    pipe.multi()
                    pipe.hset(state_key, "layers", json.dumps(new_layers, ensure_ascii=False))
                    pipe.expire(state_key, STATE_TTL)
                    pipe.sadd(self._active_key(), session_id)
                    await pipe.execute()
                    return
            except aioredis.WatchError:
                continue
            except aioredis.RedisError as e:
                logger.warning(
                    "remove_layer_from_state Redis failed for %s layer %s: %s",
                    session_id, layer_id, e,
                )
                return
        logger.warning(
            "remove_layer_from_state gave up after 3 retries for %s layer %s",
            session_id, layer_id,
        )

    async def append_event(self, session_id: str, event: str, data: dict) -> None:
        """追加事件日志；Redis 不可达时降级为 no-op（log 一条警告）。

        审计 C3：append_event 失败不应阻断主流程 —— 事件日志仅用于 [环境感知]
        的上下文注入和前端 SSE 回放，缺一条不会破坏正确性。
        """
        await self._ensure_connected()
        entry = json.dumps(
            {"event": event, "data": data, "timestamp": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
        )
        try:
            async with self._r.pipeline() as pipe:
                key = self._events_key(session_id)
                # rpush 保持与 memory (deque.append) 相同的"最旧在前"时序。
                # 消费方按 [-3:]/[-5:] 从尾部切片，所以"最旧在前"才能拿到最新 N 条；
                # lpush 会让 Redis 的事件序列与 memory 倒置。
                pipe.rpush(key, entry)
                pipe.ltrim(key, -MAX_EVENTS, -1)
                pipe.expire(key, EVENTS_TTL)
                pipe.sadd(self._active_key(), session_id)
                self._refresh_session_ttl(pipe, session_id)
                await pipe.execute()
        except aioredis.RedisError as e:
            logger.warning(
                "Redis append_event failed for session %s event %s: %s — event dropped",
                session_id, event, e,
            )

    async def get_event_log(self, session_id: str) -> list[dict]:
        await self._ensure_connected()
        raw_list = await self._r.lrange(self._events_key(session_id), 0, -1)
        return [
            json.loads(item.decode() if isinstance(item, bytes) else item)
            for item in raw_list
        ]

    async def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        """Fetch session metadata in a single async pipeline."""
        await self._ensure_connected()
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
                if key == "_started_at":
                    # BUG-09: bare ISO string now, not JSON. Tolerant decoder
                    # also unwraps legacy double-encoded values.
                    started_at = self._decode_started_at(v)
                    map_state[key] = started_at
                    continue
                try:
                    map_state[key] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    continue

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
        await self._ensure_connected()
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
        await self._ensure_connected()
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
        # 调用方（store）已 _ensure_connected()；这里防御性再确认一次。
        await self._ensure_connected()
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


# Backward-compatible alias
RedisSessionDataManager = RedisSessionStore
