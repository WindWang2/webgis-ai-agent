"""Redis-backed session data manager - persistent storage with TTL and LRU eviction"""
import asyncio
import json
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as aioredis
from app.services.session_data_protocol import (
    BaseSessionStore,
    UNAVAILABLE_REF_PREFIX,
    _layer_matches_removal_family,
)
from app.lib.numpy_json import numpy_json_default as _numpy_json_default

logger = logging.getLogger(__name__)

SESSION_TTL = 4 * 60 * 60
# Payload keys used to expire at 2h while session index keys lived 4h and
# were renewed by viewport writes. Keep data alive for the session lifetime.
DATA_TTL = SESSION_TTL
STATE_TTL = SESSION_TTL
EVENTS_TTL = SESSION_TTL
MAX_EVENTS = 20
# V3 闭环：每 session 地图动作 ACK 上限（与 session_data.MAX_MAP_ACTION_EVENTS 保持一致）
MAX_MAP_ACTION_EVENTS = 200

# L1 (in-process) cache TTL. Short on purpose: every worker process owns its
# own L1, so a long TTL would serve stale state written by another worker for
# too long. 2s is enough to collapse the burst of reads a single chat turn /
# WS-interaction triggers (context_builder + ws_service + tool dispatch all
# read map_state within the same request) while bounding cross-worker staleness.
L1_TTL_SECONDS = 2.0
L1_MAX_SESSIONS = 512  # bound memory; evict oldest entries beyond this


class RedisSessionStore(BaseSessionStore):

    """Session-level data store backed by Redis with cursor support (LRU)."""

    def __init__(
        self,
        redis_url: str,
        capacity: int = 200,
        socket_timeout: float = 5.0,
        redis: Optional[aioredis.Redis] = None,
    ):
        self._ack_batch_script = None  # v2(P5)：ACK 批 Lua 的 EVALSHA 句柄
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
        # L1 in-process cache: { (session_id, kind): (value, expires_at_monotonic) }
        # kind ∈ {"map_state", "metadata"}. Read-through, write-invalidate.
        # Per-process (not coordinated across workers) — kept fresh-ish via short TTL.
        self._l1: dict[tuple[str, str], tuple[Any, float]] = {}
        self._l1_order: list[tuple[str, str]] = []  # LRU order (MRU at end)

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

    # ─── L1 (in-process) cache helpers ────────────────────────────────────
    # Read-through: get_* checks L1 first, falls back to Redis, populates L1.
    # Write-invalidate: every set_*/update_*/remove_* drops the affected
    # session's L1 entries so the next read sees the fresh Redis value.
    # Per-process: a second worker writing won't bust THIS process's L1, but
    # the short L1_TTL_SECONDS bounds the staleness window.
    # ──────────────────────────────────────────────────────────────────────

    def _l1_get(self, session_id: str, kind: str) -> Optional[Any]:
        key = (session_id, kind)
        entry = self._l1.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            # expired — drop it
            self._l1.pop(key, None)
            try:
                self._l1_order.remove(key)
            except ValueError:
                pass
            return None
        # MRU bump
        try:
            self._l1_order.remove(key)
            self._l1_order.append(key)
        except ValueError:
            pass
        return value

    def _l1_put(self, session_id: str, kind: str, value: Any) -> None:
        key = (session_id, kind)
        if key in self._l1:
            self._l1_order.remove(key)
        self._l1[key] = (value, time.monotonic() + L1_TTL_SECONDS)
        self._l1_order.append(key)
        # evict oldest beyond capacity
        while len(self._l1_order) > L1_MAX_SESSIONS:
            old = self._l1_order.pop(0)
            self._l1.pop(old, None)

    def _l1_invalidate_session(self, session_id: str) -> None:
        """Drop all L1 entries for a session (call on every write to that session)."""
        stale = [k for k in self._l1 if k[0] == session_id]
        for k in stale:
            self._l1.pop(k, None)
            try:
                self._l1_order.remove(k)
            except ValueError:
                pass

    def invalidate_local_cache(self, session_id: str) -> None:
        """Public critical-read hook used after acquiring a distributed lock."""
        self._l1_invalidate_session(session_id)

    def clear_l1_cache(self) -> None:
        """Drop all L1 entries (test escape hatch / memory pressure)."""
        self._l1.clear()
        self._l1_order.clear()

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
    def _map_actions_key(session_id: str) -> str:
        return f"session:{session_id}:map_actions"

    @staticmethod
    def _map_actions_order_key(session_id: str) -> str:
        return f"session:{session_id}:map_actions_order"

    @staticmethod
    def _index_key(session_id: str) -> str:
        return f"session:{session_id}:index"

    @staticmethod
    def _refs_order_key(session_id: str) -> str:
        return f"session:{session_id}:refs_order"
    
    @staticmethod
    def _descriptor_key(session_id: str, ref_id: str) -> str:
        """V3 Performance: descriptor metadata key (sibling to data key)."""
        return f"session:{session_id}:meta:{ref_id}"

    @staticmethod
    def _active_key() -> str:
        return "sessions:active"

    @staticmethod
    def _activity_key() -> str:
        return "sessions:activity"

    async def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
        """存数据；Redis 不可达时降级返回伪 ref_id 而非抛错。

        审计 C3：socket_timeout=1s 的 Redis 必然会偶发超时，若不隔离会让
        dispatcher.dispatch_tool 直接 raise → 整个 chat turn 崩溃 + tracker
        卡死。降级返回 ref:redis-unavailable-xxx 让上层 chat_engine 把它当
        普通失败工具结果处理（_untrusted + 自愈消息）。
        
        V3 Performance: computes and stores descriptor metadata alongside data
        in a sibling Redis key. This eliminates per-request 100k-feature scans.
        """
        await self._ensure_connected()
        ref_id = f"ref:{prefix}-{uuid.uuid4().hex[:16]}"
        data_key = self._data_key(session_id, ref_id)
        order_key = self._refs_order_key(session_id)
        
        # V3: compute descriptor once at store time
        from app.schemas.ref_descriptor import compute_descriptor
        descriptor = await asyncio.to_thread(compute_descriptor, ref_id, data)
        descriptor_key = self._descriptor_key(session_id, ref_id)

        # P1: 大 GeoJSON 的 json.dumps 在事件循环上要 0.6-4s（同
        # tool_dispatch_service 的论证），必须在 pipe 外先线程化序列化，
        # pipe 事务内只做 set。
        payload_json = await asyncio.to_thread(
            json.dumps, data, ensure_ascii=False, default=_numpy_json_default
        )

        try:
            # Insert first. Evicting before the write used to delete live refs
            # and then return an unavailable sentinel if the insert pipeline
            # hit RedisError — silent data loss at capacity.
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
                pipe.set(data_key, payload_json, ex=DATA_TTL)
                pipe.set(descriptor_key, json.dumps(descriptor.to_dict(), ensure_ascii=False), ex=DATA_TTL)
                pipe.zadd(order_key, {ref_id: time.time()})
                pipe.sadd(self._index_key(session_id), ref_id)
                self._refresh_session_ttl(pipe, session_id)
                await pipe.execute()
        except aioredis.RedisError as e:
            logger.error(
                "Redis store failed for session %s (prefix=%s): %s — returning unavailable ref",
                session_id, prefix, e,
            )
            return f"{UNAVAILABLE_REF_PREFIX}{uuid.uuid4().hex[:16]}"
        # RUN-06: a new ref must be visible to the next get_session_metadata /
        # list_refs round within the same chat turn. The metadata L1 bundle
        # caches list_refs + event_log (2s TTL); drop it so we don't serve the
        # stale bundle. (set_map_state/update_layer/remove_layer already do this.)
        self._l1_invalidate_session(session_id)
        try:
            current_count = await self._r.zcard(order_key)
            if current_count > self.capacity:
                overflow = current_count - self.capacity
                oldest = await self._r.zrange(order_key, 0, overflow - 1)
                old_refs = []
                for old_ref_bytes in oldest:
                    old_ref = old_ref_bytes.decode() if isinstance(old_ref_bytes, bytes) else old_ref_bytes
                    if old_ref == ref_id:
                        continue
                    old_refs.append(old_ref)
                if old_refs:
                    # #618-7: 一次 HMGET 取全部待驱逐 ref 的 alias（resolve_aliases
                    # 同款批量模式），替代逐 ref 串行 HGET。
                    alias_values = await self._r.hmget(self._refs_key(session_id), old_refs)
                    aliases = {
                        ref: (al.decode() if isinstance(al, bytes) else al)
                        for ref, al in zip(old_refs, alias_values)
                        if al is not None
                    }
                    async with self._r.pipeline() as evict_pipe:
                        for old_ref in old_refs:
                            self._evict_ref(evict_pipe, session_id, old_ref, aliases.get(old_ref))
                        await evict_pipe.execute()
                    # P-1（#874）：被驱逐 ref 的进程内 payload 缓存一并失效
                    from app.services.ref_payload_cache import ref_payload_cache
                    for old_ref in old_refs:
                        ref_payload_cache.invalidate(session_id, old_ref)
        except aioredis.RedisError as e:
            logger.error(
                "Redis post-store eviction failed for session %s: %s — new ref kept",
                session_id, e,
            )
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
        # P1 (#521): same as store() — checkpoint rollback overwrites
        # multi-MB materialized blobs per ref; serializing inline would block
        # the event loop for the whole dump.
        payload_json = await asyncio.to_thread(
            json.dumps, data, ensure_ascii=False, default=_numpy_json_default
        )
        try:
            async with self._r.pipeline() as pipe:
                pipe.set(data_key, payload_json, ex=DATA_TTL)
                # D-4: the payload changed, so the cached descriptor (bbox /
                # feature_count / geometry_types from the OLD payload) is stale.
                # Drop it so the next get_ref_descriptor recomputes from the new
                # payload instead of returning store-time metadata forever.
                pipe.delete(self._descriptor_key(session_id, ref_id))
                pipe.zadd(self._refs_order_key(session_id), {ref_id: time.time()})
                self._refresh_session_ttl(pipe, session_id)
                await pipe.execute()
        except aioredis.RedisError as e:
            logger.error(
                "Redis overwrite failed for session %s ref %s: %s",
                session_id, ref_id, e,
            )
            return False
        from app.services.mvt import spatial_index_cache, tile_lru_cache
        spatial_index_cache.invalidate_ref(session_id, ref_id)
        tile_lru_cache.invalidate_ref(session_id, ref_id)
        # P-1（#874）：payload 变更/删除时进程内解析缓存同步失效
        from app.services.ref_payload_cache import ref_payload_cache
        ref_payload_cache.invalidate(session_id, ref_id)
        return True

    async def delete_ref(self, session_id: str, ref_id: str) -> bool:
        """Drop one stored ref (data + descriptor + index). Returns False on Redis error."""
        await self._ensure_connected()
        try:
            exists = await self._r.exists(self._data_key(session_id, ref_id))
            async with self._r.pipeline() as pipe:
                self._evict_ref(pipe, session_id, ref_id, None)
                await pipe.execute()
            self._l1_invalidate_session(session_id)
        except aioredis.RedisError as e:
            logger.error(
                "Redis delete_ref failed for session %s ref %s: %s",
                session_id, ref_id, e,
            )
            return False
        from app.services.mvt import spatial_index_cache, tile_lru_cache
        spatial_index_cache.invalidate_ref(session_id, ref_id)
        tile_lru_cache.invalidate_ref(session_id, ref_id)
        from app.services.ref_payload_cache import ref_payload_cache
        ref_payload_cache.invalidate(session_id, ref_id)
        return bool(exists)

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        """写路径 best-effort：Redis 不可达时 log warning 并丢弃，不抛。

        F22：alias 写入失败不应杀死整个 chat turn（审计 C3 同款隔离）。
        """
        await self._ensure_connected()
        try:
            async with self._r.pipeline() as pipe:
                pipe.hset(self._aliases_key(session_id), alias, ref_id)
                pipe.hset(self._refs_key(session_id), ref_id, alias)
                self._refresh_session_ttl(pipe, session_id)  # #730: per-ref payload TTL refresh dropped — reads (get/ref_exists) already refresh, this fanout was O(refs) per write
                await pipe.execute()
        except aioredis.RedisError as e:
            logger.warning(
                "Redis set_alias failed for session %s ref %s: %s — alias dropped",
                session_id, ref_id, e,
            )

    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str:
        """读路径 cache-miss 语义：Redis 不可达时原样返回输入（与 memory 后端
        未命中别名一致），不抛（F22）。"""
        await self._ensure_connected()
        try:
            ref_id = await self._r.hget(self._aliases_key(session_id), ref_or_alias)
        except aioredis.RedisError as e:
            logger.warning(
                "Redis resolve_alias failed for session %s: %s — returning input unchanged",
                session_id, e,
            )
            return ref_or_alias
        if ref_id is None:
            return ref_or_alias
        return ref_id.decode() if isinstance(ref_id, bytes) else ref_id

    async def resolve_aliases(self, session_id: str, strings: list[str]) -> dict[str, str]:
        """Batch alias resolution via a single HMGET round-trip.

        The registry resolves every string argument of a tool call; doing it
        one resolve_alias (HGET) at a time cost N serialized round-trips per
        dispatch. One HMGET collapses that to a single round-trip.

        F22：本方法在每次 tool dispatch 上运行（chat/registry.py），Redis 抖动
        不能让所有工具调用失败 —— 不可达时返回 identity map（cache-miss 语义）。
        """
        if not strings:
            return {}
        await self._ensure_connected()
        try:
            ref_ids = await self._r.hmget(self._aliases_key(session_id), strings)
        except aioredis.RedisError as e:
            logger.warning(
                "Redis resolve_aliases failed for session %s: %s — returning identity map",
                session_id, e,
            )
            return {s: s for s in strings}
        out = {}
        for s, ref in zip(strings, ref_ids):
            if ref is None:
                out[s] = s
            else:
                out[s] = ref.decode() if isinstance(ref, bytes) else ref
        return out

    async def get_shared(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
        """P-1（#874）：共享只读读取 —— 进程内已解析 payload 缓存。

        解引用热路径（registry._resolve_references、数据面序列化）此前每次
        都全量 GET + json.loads（50k 要素 ≈ 171ms/次 + 11MB Redis 流量）。
        命中返回同一对象（只读约定）；miss 时读取一次并按原始字节长度入
        缓存（TTL 5s 兜底跨副本写入）。TTL/recency 刷新 pipeline 只在 miss
        路径执行（命中路径零 Redis 往返）。
        """
        from app.services.ref_payload_cache import ref_payload_cache
        try:
            ref_id = ref_id_or_alias
            resolved = await self._r.hget(self._aliases_key(session_id), ref_id_or_alias)
            if resolved is not None:
                ref_id = resolved.decode() if isinstance(resolved, bytes) else resolved

            cached = ref_payload_cache.get(session_id, ref_id)
            if cached is not None:
                return cached

            data_key = self._data_key(session_id, ref_id)
            raw = await self._r.get(data_key)
            if raw is None:
                return None
            raw_str = raw.decode() if isinstance(raw, bytes) else raw
            try:
                data = await asyncio.to_thread(json.loads, raw_str)
            except Exception:  # noqa: BLE001 非 JSON payload 原样返回（与 get() 同语义），不入缓存
                return raw_str
            ref_payload_cache.put(session_id, ref_id, data, len(raw_str))

            # Best-effort TTL/recency 刷新（仅 miss 路径；失败不转为 miss）。
            try:
                async with self._r.pipeline(transaction=True) as pipe:
                    await pipe.watch(data_key)
                    if not await pipe.exists(data_key):
                        pipe.reset()
                        return data
                    pipe.multi()
                    pipe.expire(data_key, DATA_TTL)
                    pipe.expire(self._descriptor_key(session_id, ref_id), DATA_TTL)
                    pipe.zadd(self._refs_order_key(session_id), {ref_id: time.time()})
                    self._refresh_session_ttl(pipe, session_id)
                    await pipe.execute()
            except aioredis.WatchError:
                pass
            except aioredis.RedisError as e:
                logger.warning(
                    "Redis TTL refresh failed for session %s ref %s: %s — returning cached data",
                    session_id, ref_id_or_alias, e,
                )
            return data
        except aioredis.RedisError as e:
            logger.error(
                "Redis get_shared failed for session %s ref %s: %s — returning cache-miss",
                session_id, ref_id_or_alias, e,
            )
            return None

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

            # Best-effort TTL/recency refresh: a transient Redis error on the
            # expire/zadd pipeline must NOT turn a successful read into a
            # cache-miss (the previous code shared one try/except with the read
            # and returned None on a refresh hiccup — live data looked deleted
            # during Redis jitter).
            try:
                async with self._r.pipeline(transaction=True) as pipe:
                    # CONC-F6: gate the recency bump on the data key still
                    # existing. An unguarded zadd raced store()-side eviction
                    # (which zrems the ref after deleting its data key) and
                    # re-created the refs_order member — a permanently dangling
                    # entry with no payload. WATCH + immediate exists() + MULTI
                    # makes the check-and-bump atomic.
                    await pipe.watch(data_key)
                    if not await pipe.exists(data_key):
                        pipe.reset()
                        return
                    pipe.multi()
                    pipe.expire(data_key, DATA_TTL)
                    pipe.expire(self._descriptor_key(session_id, ref_id), DATA_TTL)
                    pipe.zadd(self._refs_order_key(session_id), {ref_id: time.time()})
                    self._refresh_session_ttl(pipe, session_id)
                    await pipe.execute()
            except aioredis.WatchError:
                pass  # key changed under us — skip the refresh, data returned below
            except aioredis.RedisError as e:
                logger.warning(
                    "Redis TTL refresh failed for session %s ref %s: %s — returning cached data",
                    session_id, ref_id_or_alias, e,
                )

            raw_str = raw.decode() if isinstance(raw, bytes) else raw
            try:
                # P1: 大 GeoJSON 反序列化同样离线到工作线程（同 store 的 dumps）。
                return await asyncio.to_thread(json.loads, raw_str)
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
        """读路径 cache-miss 语义：Redis 不可达时返回空表，不抛（F22）。
        本方法在每次 tool dispatch 上运行（chat/registry.py）。"""
        await self._ensure_connected()
        try:
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
        except aioredis.RedisError as e:
            logger.warning(
                "Redis list_refs failed for session %s: %s — returning empty",
                session_id, e,
            )
            return {}

    async def leave_active_set(self, session_id: str) -> None:
        """#1074(F-15): 把会话从 sessions:active / activity 有序集摘除。

        tombstone 经通用写者落键时会把已删会话 re-add 进 active 集（滞留
        ≤4h：清扫空转、活跃计数失真）。删除路径写完 tombstone 后定向摘除。
        best-effort —— 失败只影响清理效率，不影响正确性。
        """
        try:
            await self._ensure_connected()
            async with self._r.pipeline(transaction=False) as pipe:
                pipe.srem(self._active_key(), session_id)
                pipe.zrem(self._activity_key(), session_id)
                await pipe.execute()
        except aioredis.RedisError as e:
            logger.warning(
                "leave_active_set failed for %s: %s", session_id, e
            )

    async def set_map_state_fields(self, session_id: str, fields: dict) -> bool:
        """#1073: 单事务写多个状态字段（无 seq 语义的服务端真值）。

        mapspec 与其 CAS 令牌（_cartographic_mutation_revision）此前是两笔
        独立 WATCH/MULTI —— crash 落在两写之间会让 spec=世代 N+1 而令牌=N，
        持旧 expected_revision 的客户端通过相等 CAS 在新 spec 上重复应用。
        单 MULTI 保证原子。
        """
        if not fields:
            return True
        await self._ensure_connected()
        payloads: dict[str, str] = {}
        for key, value in fields.items():
            try:
                payloads[key] = await asyncio.to_thread(
                    json.dumps, value, ensure_ascii=False, default=_numpy_json_default
                )
            except (TypeError, ValueError) as e:
                logger.warning(
                    "set_map_state_fields un-serializable value for %s %s: %s",
                    session_id, key, e,
                )
                return False
        state_key = self._state_key(session_id)
        try:
            async with self._r.pipeline(transaction=True) as pipe:
                pipe.hsetnx(
                    state_key,
                    "_started_at",
                    datetime.now(timezone.utc).isoformat(),
                )
                pipe.hset(state_key, mapping=payloads)
                pipe.expire(state_key, STATE_TTL)
                pipe.sadd(self._active_key(), session_id)
                self._refresh_session_ttl(pipe, session_id)
                await pipe.execute()
            self._l1_invalidate_session(session_id)
            return True
        except aioredis.RedisError as e:
            logger.error(
                "set_map_state_fields failed for %s: %s", session_id, e
            )
            return False

    async def commit_mapspec_state(
        self,
        session_id: str,
        fields: dict,
        layer_op: Optional[tuple] = None,
    ) -> bool:
        """v2(audit F4): MapSpec commit 单事务（spec+revision+指纹+runtime layers）。

        此前 save_mapspec（spec/令牌一笔 MULTI）与 runtime layers 写
        （update/remove_layer_in_state 另一笔 WATCH/MULTI）是两个事务，
        crash 落在两写之间会留下 spec=世代 N+1 而 layers=世代 N 的错配。
        本方法把 layers 的 read-modify-write 合入同一 WATCH/MULTI：EXEC 前
        到达的并发 layers 写（WS 感知通道，不走分布式锁）触发 WatchError
        重读合并 —— 保留 update/remove_layer_in_state 的合并语义。

        layer_op: ("upsert", layer_id, layer_dict) | ("remove", layer_id, None)
                  | ("replace", "", layers_list)。fields 为 Python 对象。
        """
        if not fields and layer_op is None:
            return True
        await self._ensure_connected()
        state_key = self._state_key(session_id)
        # v2(review 5/6-A4)：fields 预序列化在 WATCH 窗口外 —— 大 mapspec 的
        # dumps（~10ms 级）此前持窗口，WS 感知写并发时 3 次重试的碰撞概率
        # 被无谓放大（finalize 在用户拖动期间以百分比级概率假失败）。
        pre_serialized: dict[str, str] = {}
        for key, value in fields.items():
            pre_serialized[key] = await asyncio.to_thread(
                json.dumps, value, ensure_ascii=False,
                default=_numpy_json_default,
            )
        for _attempt in range(3):
            if _attempt:
                # 小退避 + 抖动：削平 WS 写风暴下的连续碰撞。
                await asyncio.sleep(0.01 * _attempt + (id(_attempt) % 100) / 10000)
            try:
                async with self._r.pipeline(transaction=True) as pipe:
                    await pipe.watch(state_key)
                    payloads: dict[str, str] = dict(pre_serialized)
                    if layer_op is not None:
                        op, layer_id, layer_payload = layer_op
                        raw_layers = await pipe.hget(state_key, "layers")
                        layers = list(json.loads(raw_layers)) if raw_layers is not None else []
                        if op == "upsert":
                            for layer in layers:
                                if layer.get("id") == layer_id:
                                    layer.update(layer_payload)
                                    break
                            else:
                                layers.append({"id": layer_id, **layer_payload})
                        elif op == "remove":
                            layers = [
                                layer for layer in layers
                                if not _layer_matches_removal_family(
                                    layer.get("id"), layer_id,
                                )
                            ]
                        elif op == "replace":
                            layers = list(layer_payload or [])
                        payloads["layers"] = await asyncio.to_thread(
                            json.dumps, layers, ensure_ascii=False,
                            default=_numpy_json_default,
                        )
                    pipe.multi()
                    pipe.hsetnx(
                        state_key,
                        "_started_at",
                        datetime.now(timezone.utc).isoformat(),
                    )
                    pipe.hset(state_key, mapping=payloads)
                    pipe.expire(state_key, STATE_TTL)
                    pipe.sadd(self._active_key(), session_id)
                    self._refresh_session_ttl(pipe, session_id)
                    await pipe.execute()
                self._l1_invalidate_session(session_id)
                return True
            except aioredis.WatchError:
                continue  # 并发 layers 写 —— 重读合并后重试
            except (TypeError, ValueError, json.JSONDecodeError) as e:
                logger.warning(
                    "commit_mapspec_state invalid payload for %s: %s",
                    session_id, e,
                )
                return False
            except aioredis.RedisError as e:
                logger.error(
                    "commit_mapspec_state failed for %s: %s", session_id, e
                )
                return False
        logger.warning(
            "commit_mapspec_state gave up after 3 retries for %s (contention)",
            session_id,
        )
        return False

    async def set_map_state(self, session_id: str, key: str, value: Any, seq: Optional[int] = None) -> bool:
        await self._ensure_connected()
        # P1 (#521): serialize off the event loop (same as store()). The map_state
        # entry points are client-controlled and only capped at the DTO layer, so
        # a multi-MB value must not stall the loop for the whole dump.
        try:
            payload_json = await asyncio.to_thread(
            json.dumps, value, ensure_ascii=False, default=_numpy_json_default
        )
        except (TypeError, ValueError) as e:
            logger.warning("set_map_state un-serializable value for %s %s: %s", session_id, key, e)
            return False
        # F4: same dual-writer sequencing as the memory store. The seq check is
        # a read-modify-write, so guard it with WATCH/MULTI (like
        # update_layer_in_state) — otherwise two in-flight POSTs could both pass
        # the check and the older one land last.
        state_key = self._state_key(session_id)
        for attempt in range(3):
            try:
                async with self._r.pipeline(transaction=True) as pipe:
                    await pipe.watch(state_key)
                    stored_raw = await pipe.hget(state_key, f"_{key}_seq")
                    stored_seq = int(stored_raw) if stored_raw is not None else 0
                    if seq is not None and seq <= stored_seq:
                        return False  # stale out-of-order write — resolve to latest seq
                    pipe.multi()
                    pipe.hsetnx(
                        state_key,
                        "_started_at",
                        # BUG-09：直接存 ISO 字符串，不双重编码。
                        datetime.now(timezone.utc).isoformat(),
                    )
                    pipe.hset(state_key, key, payload_json)
                    if seq is not None:
                        # Unsequenced writes (server-side truth) leave the
                        # stored seq untouched so the client's next write passes.
                        pipe.hset(state_key, f"_{key}_seq", seq)
                    pipe.hset(state_key, f"_{key}_updated_at", datetime.now(timezone.utc).isoformat())
                    pipe.expire(state_key, STATE_TTL)
                    pipe.sadd(self._active_key(), session_id)
                    self._refresh_session_ttl(pipe, session_id)  # #730: per-ref payload TTL refresh dropped — reads (get/ref_exists) already refresh, this fanout was O(refs) per write
                    await pipe.execute()
                # Write-through invalidation: drop L1 so next read refetches from Redis.
                self._l1_invalidate_session(session_id)
                return True
            except aioredis.WatchError:
                continue  # 重试
            except (TypeError, ValueError, json.JSONDecodeError) as e:
                # 损坏的 seq 值 —— 降级，不抛。
                logger.warning("set_map_state corrupt seq for %s %s: %s", session_id, key, e)
                return False
            except aioredis.RedisError as e:
                logger.warning(
                    "set_map_state Redis failed for %s %s: %s",
                    session_id, key, e,
                )
                return False
        logger.warning(
            "set_map_state gave up after 3 retries for %s %s (concurrent contention)",
            session_id, key,
        )
        return False

    async def get_started_at(self, session_id: str) -> Optional[str]:
        """读路径 cache-miss 语义：Redis 不可达时返回 None，不抛（F22）。"""
        await self._ensure_connected()
        try:
            raw = await self._r.hget(self._state_key(session_id), "_started_at")
        except aioredis.RedisError as e:
            logger.warning(
                "Redis get_started_at failed for session %s: %s — returning None",
                session_id, e,
            )
            return None
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
        # L1 hot read — a single chat turn reads map_state several times
        # (context_builder + ws_service + tool dispatch). Avoid repeated Redis
        # HGETALL round-trips for the same session within L1_TTL_SECONDS.
        cached_raw = self._l1_get(session_id, "map_state")
        if cached_raw is not None:
            # #749: every reader must get its own object tree — the L1 entry
            # is shared within the TTL. #795: re-parse the cached RAW fields
            # instead of deepcopying the parsed tree — a fresh json.loads
            # yields an equally isolated tree at 3-5x less CPU at 1MiB-class
            # specs (measured, audit bench_c2), and mapspec-bearing state is
            # read several times per cartographic tool call.
            return await asyncio.to_thread(self._parse_state_fields_sync, cached_raw)
        # #378: get_map_state sits on the request-admission path
        # (_guard_body_session), so a Redis blip must degrade to empty
        # map_state (cache-miss semantics) — never a 500 on every chat
        # request. Same RedisError isolation as every sibling read.
        try:
            await self._ensure_connected()
            raw = await self._r.hgetall(self._state_key(session_id))
        except aioredis.RedisError as e:
            logger.warning(
                "Redis get_map_state failed for session %s: %s — returning empty state",
                session_id, e,
            )
            return {}
        if not raw:
            return {}
        # #687：逐字段 json.loads 卸载到线程（对齐 payload 路径的既有做法——
        # 大 mapspec 字段的冷解析曾整段跑在事件循环上，而 apply_mutation
        # 每次变更都会打穿 L1 触发冷读）。
        raw_items = [
            (k.decode() if isinstance(k, bytes) else k, v) for k, v in raw.items()
        ]
        # #795: 缓存原始字段（bytes 不可变，共享安全）；命中路径重解析。
        self._l1_put(session_id, "map_state", raw_items)
        return await asyncio.to_thread(self._parse_state_fields_sync, raw_items)

    @staticmethod
    def _parse_state_fields_sync(raw_items: list) -> dict[str, Any]:
        """同步：HGETALL 原始字段 → 解析后的 map_state（纯函数，线程安全）。"""
        out: dict[str, Any] = {}
        for key, v in raw_items:
            if key == "_started_at" or key.endswith("_updated_at"):
                # BUG-09: _started_at is stored as a bare ISO string (not
                # JSON). F4: _<key>_updated_at timestamps are stored the same
                # way. Use the tolerant decoder to also handle legacy values.
                out[key] = RedisSessionStore._decode_started_at(v)
            else:
                out[key] = json.loads(v)
        return out


    async def get_state_field(self, session_id: str, field: str) -> Any:
        """#1064: 定向读单个状态字段（单 HGET，不 HGETALL/不解析 mapspec）。

        授权/tombstone 检查只需要一个字段（owner_token_digest、
        _cartographic_deleted），此前经 get_map_state/get_session_metadata
        物化整个状态包（1MiB 级 mapspec 冷读/重解析/传输）。字段值按 JSON
        解码（与 get_map_state 的 per-field 语义一致）；缺失或不可解析返回
        None。
        """
        try:
            await self._ensure_connected()
            v = await self._r.hget(self._state_key(session_id), field)
        except aioredis.RedisError as e:
            logger.warning(
                "Redis get_state_field(%s) failed for %s: %s — treating as miss",
                field, session_id, e,
            )
            return None
        if v is None:
            return None
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return v

    async def get_map_spec_fingerprint(self, session_id: str) -> Optional[str]:
        """#687：定向读 mapspec 指纹字段（O(1)，不触发全字段冷解析/L1）。

        供 MapSpecStore.save_mapspec 的 no-op 快比较核对 Redis 侧状态；
        字段由 set_map_spec_fingerprint 以 JSON 字符串写入。
        """
        try:
            await self._ensure_connected()
            v = await self._r.hget(self._state_key(session_id), "_mapspec_fp")
        except aioredis.RedisError as e:
            logger.warning(
                "Redis get_map_spec_fingerprint failed for %s: %s — treating as miss",
                session_id, e,
            )
            return None
        if v is None:
            return None
        try:
            fp = json.loads(v)
            return fp if isinstance(fp, str) else None
        except (json.JSONDecodeError, TypeError):
            return None

    async def set_map_spec_fingerprint(self, session_id: str, fingerprint: str) -> None:
        """#687：定向写 mapspec 指纹字段（小字符串，序列化成本可忽略）。"""
        try:
            await self._ensure_connected()
            await self._r.hset(
                self._state_key(session_id), "_mapspec_fp", json.dumps(fingerprint)
            )
        except aioredis.RedisError as e:
            logger.warning(
                "Redis set_map_spec_fingerprint failed for %s: %s — no-op fast "
                "path will fall back to sidecar-only on next boot",
                session_id, e,
            )

    async def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict) -> bool:
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
                    # P1 (#521): serialize the (possibly large) layers list off
                    # the event loop before entering the transaction.
                    payload_json = await asyncio.to_thread(
            json.dumps, layers, ensure_ascii=False, default=_numpy_json_default
        )
                    # 写回
                    pipe.multi()
                    pipe.hset(state_key, "layers", payload_json)
                    pipe.expire(state_key, STATE_TTL)
                    pipe.sadd(self._active_key(), session_id)
                    # D-2: a layer edit is session activity — refresh the whole
                    # session KEY FAMILY (aliases/refs/refs_order/index/state/
                    # events/ACKs) and bump the activity zset so a long editing
                    # session is not evicted as idle and keeps its metadata.
                    # (Per-ref payloads are TTL-refreshed on get()/ref_exists()
                    # reads; this call refreshes the shared family keys.)
                    self._refresh_session_ttl(pipe, session_id)
                    await pipe.execute()
                    self._l1_invalidate_session(session_id)
                    return True
            except aioredis.WatchError:
                continue  # 重试
            except (json.JSONDecodeError, ValueError) as e:
                # BUG-05：layers 键存了无法解析的脏数据 —— 降级，不抛。
                logger.warning(
                    "update_layer_in_state: corrupt 'layers' for %s layer %s: %s",
                    session_id, layer_id, e,
                )
                return False
            except aioredis.RedisError as e:
                logger.warning(
                    "update_layer_in_state Redis failed for %s layer %s: %s",
                    session_id, layer_id, e,
                )
                return False
        logger.warning(
            "update_layer_in_state gave up after 3 retries for %s layer %s (concurrent contention)",
            session_id, layer_id,
        )
        return False

    async def remove_layer_from_state(self, session_id: str, layer_id: str) -> bool:
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
                    # #1074(F-12): 族谓词与 spec 侧删层对称（见内存后端注释）。
                    new_layers = [
                        layer for layer in layers
                        if not _layer_matches_removal_family(layer.get("id"), layer_id)
                    ]
                    # P1 (#521): serialize off the event loop (see update_layer_in_state).
                    payload_json = await asyncio.to_thread(
            json.dumps, new_layers, ensure_ascii=False, default=_numpy_json_default
        )
                    pipe.multi()
                    pipe.hset(state_key, "layers", payload_json)
                    pipe.expire(state_key, STATE_TTL)
                    pipe.sadd(self._active_key(), session_id)
                    # D-2: same rationale as update_layer_in_state.
                    self._refresh_session_ttl(pipe, session_id)
                    await pipe.execute()
                    self._l1_invalidate_session(session_id)
                    return True
            except aioredis.WatchError:
                continue
            except aioredis.RedisError as e:
                logger.warning(
                    "remove_layer_from_state Redis failed for %s layer %s: %s",
                    session_id, layer_id, e,
                )
                return False
        logger.warning(
            "remove_layer_from_state gave up after 3 retries for %s layer %s",
            session_id, layer_id,
        )
        return False

    async def append_event(self, session_id: str, event: str, data: dict) -> None:
        """追加事件日志；Redis 不可达时降级为 no-op（log 一条警告）。

        审计 C3：append_event 失败不应阻断主流程 —— 事件日志仅用于 [环境感知]
        的上下文注入和前端 SSE 回放，缺一条不会破坏正确性。
        """
        await self._ensure_connected()
        # P1 (#521): tool-result event payloads can be large; serialize off-loop.
        entry = await asyncio.to_thread(
            json.dumps,
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
                self._refresh_session_ttl(pipe, session_id)  # #730: per-ref payload TTL refresh dropped — reads (get/ref_exists) already refresh, this fanout was O(refs) per write
                await pipe.execute()
        except aioredis.RedisError as e:
            logger.warning(
                "Redis append_event failed for session %s event %s: %s — event dropped",
                session_id, event, e,
            )
            return
        # RUN-06: a newly-appended event must be visible to the next
        # get_session_metadata round within the same chat turn. The metadata L1
        # bundle caches event_log (2s TTL); drop it so we don't serve a stale
        # bundle missing this event.
        self._l1_invalidate_session(session_id)

    async def get_event_log(self, session_id: str) -> list[dict]:
        """读路径 cache-miss 语义：Redis 不可达时返回空列表，不抛（F22）。"""
        await self._ensure_connected()
        try:
            raw_list = await self._r.lrange(self._events_key(session_id), 0, -1)
        except aioredis.RedisError as e:
            logger.warning(
                "Redis get_event_log failed for session %s: %s — returning empty",
                session_id, e,
            )
            return []
        return [
            json.loads(item.decode() if isinstance(item, bytes) else item)
            for item in raw_list
        ]

    async def append_map_action_event(self, session_id: str, event: dict) -> bool:
        """追加地图动作终态 ACK（V3 闭环），按 action_id 幂等 —— 与 memory 后端语义一致。

        存储：hash ``session:{sid}:map_actions``（field=action_id, value=json）
        + zset ``session:{sid}:map_actions_order``（score=到达时间戳，维护插入序，
        hash 本身无序）。首达终态获胜：action_id 已存在 → 返回 False。每 session
        上限 MAX_MAP_ACTION_EVENTS，写入时淘汰最旧字段。TTL 同 STATE_TTL。
        去重+淘汰是 read-modify-write，用 WATCH/MULTI 防并发（同 set_map_state）；
        Redis 不可达时降级丢弃（log warning，返回 False），不阻断主流程。
        """
        action_id = str(event.get("action_id") or "")
        if not action_id:
            return False
        await self._ensure_connected()
        actions_key = self._map_actions_key(session_id)
        order_key = self._map_actions_order_key(session_id)
        # P1 (#521): ACK payloads are bounded but serialize off-loop for consistency.
        payload = await asyncio.to_thread(json.dumps, event, ensure_ascii=False)
        for attempt in range(3):
            try:
                async with self._r.pipeline(transaction=True) as pipe:
                    await pipe.watch(actions_key, order_key)
                    if await pipe.hexists(actions_key, action_id):
                        return False  # duplicate — 首达终态获胜
                    evict_ids: list[str] = []
                    count = await pipe.zcard(order_key)
                    if count >= MAX_MAP_ACTION_EVENTS:
                        overflow = count - MAX_MAP_ACTION_EVENTS + 1
                        raw_oldest = await pipe.zrange(order_key, 0, overflow - 1)
                        evict_ids = [
                            r.decode() if isinstance(r, bytes) else r for r in raw_oldest
                        ]
                    # 到达序严格单调：同一次请求/同一时钟 tick 内多次写入若 zset
                    # score 相同，Redis 退回按 member 字典序排序，会破坏"按到达
                    # 顺序读回"的协议对齐（memory 后端是纯插入序）。在事务内读取
                    # 当前最大 score，必要时在其上取微小增量，保证 score 严格递增。
                    last_raw = await pipe.zrevrange(order_key, 0, 0, withscores=True)
                    last_score = last_raw[0][1] if last_raw else 0.0
                    arrival = time.time()
                    if arrival <= last_score:
                        arrival = last_score + 1e-6
                    pipe.multi()
                    pipe.hset(actions_key, action_id, payload)
                    pipe.zadd(order_key, {action_id: arrival})
                    if evict_ids:
                        pipe.hdel(actions_key, *evict_ids)
                        pipe.zrem(order_key, *evict_ids)
                    pipe.expire(actions_key, STATE_TTL)
                    pipe.expire(order_key, STATE_TTL)
                    pipe.sadd(self._active_key(), session_id)
                    # D-2: an ACK is session activity — refresh the session key
                    # family + bump the activity zset so a long ACK-only session
                    # keeps its metadata and is not evicted as idle.
                    self._refresh_session_ttl(pipe, session_id)
                    await pipe.execute()
                return True
            except aioredis.WatchError:
                continue  # 重试
            except aioredis.RedisError as e:
                # action_id 是客户端可控值（可含换行等控制字符），日志用 %r
                # repr 转义，防 log injection。
                logger.warning(
                    "Redis append_map_action_event failed for session %s action %r: %s — event dropped",
                    session_id, action_id, e,
                )
                return False
        logger.warning(
            "append_map_action_event gave up after 3 retries for %s action %r (concurrent contention)",
            session_id, action_id,
        )
        return False

    # #1081: 批量 ACK 落库 Lua —— 复刻 singular 路径的全部语义（首达终态
    # 获胜去重、MAX 逐出、到达分严格单调、TTL 扇出一次），单脚本原子执行。
    _ACK_BATCH_LUA = """
local max_events = tonumber(ARGV[1])
local state_ttl = tonumber(ARGV[2])
local session_ttl = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local session_id = ARGV[5]
local n = tonumber(ARGV[6])
local results = {}
local last = redis.call('ZRANGE', KEYS[2], -1, -1, 'WITHSCORES')
local last_score = 0.0
if #last >= 2 then last_score = tonumber(last[2]) end
local arrival = now
if arrival <= last_score then arrival = last_score + 0.000001 end
for i = 0, n - 1 do
  local action_id = ARGV[7 + i * 2]
  local payload = ARGV[8 + i * 2]
  if not action_id or action_id == '' then
    results[#results + 1] = 'invalid'
  elseif redis.call('HEXISTS', KEYS[1], action_id) == 1 then
    results[#results + 1] = 'duplicate'
  else
    local count = redis.call('ZCARD', KEYS[2])
    local overflow = count - max_events + 1
    if overflow > 0 then
      local oldest = redis.call('ZRANGE', KEYS[2], 0, overflow - 1)
      for _, oid in ipairs(oldest) do
        redis.call('HDEL', KEYS[1], oid)
        redis.call('ZREM', KEYS[2], oid)
      end
    end
    redis.call('HSET', KEYS[1], action_id, payload)
    redis.call('ZADD', KEYS[2], arrival, action_id)
    arrival = arrival + 0.000001
    results[#results + 1] = 'stored'
  end
end
redis.call('EXPIRE', KEYS[1], state_ttl)
redis.call('EXPIRE', KEYS[2], state_ttl)
redis.call('SADD', KEYS[3], session_id)
for i = 4, #KEYS do
  redis.call('EXPIRE', KEYS[i], session_ttl)
end
redis.call('ZADD', KEYS[#KEYS], now, session_id)
return results
"""

    async def append_map_action_event_batch(
        self, session_id: str, events: list
    ) -> list:
        """#1081: 单 Lua 脚本落整批 ACK（1 RTT，锁持有时长 ~250ms→~5ms 网络化）。"""
        if not events:
            return []
        await self._ensure_connected()
        prepared: list[tuple[str, str]] = []
        for ev in events:
            action_id = str((ev or {}).get("action_id") or "")
            if not action_id:
                prepared.append(("", "{}"))
                continue
            payload = await asyncio.to_thread(json.dumps, ev, ensure_ascii=False)
            prepared.append((action_id, payload))
        keys = [
            self._map_actions_key(session_id),
            self._map_actions_order_key(session_id),
            self._active_key(),
            self._aliases_key(session_id),
            self._refs_key(session_id),
            self._refs_order_key(session_id),
            self._index_key(session_id),
            self._state_key(session_id),
            self._events_key(session_id),
            self._activity_key(),
        ]
        args: list = [
            str(MAX_MAP_ACTION_EVENTS),
            str(STATE_TTL),
            str(SESSION_TTL),
            repr(time.time()),
            session_id,
            str(len(prepared)),
        ]
        for action_id, payload in prepared:
            args.extend((action_id, payload))
        try:
            # v2(audit P5)：EVALSHA（register_script 缓存 Script 对象）——
            # 每批 ~1.5KB 脚本体不再随请求传输；NOSCRIPT 时 redis-py 自动
            # 回退 EVAL。
            if self._ack_batch_script is None:
                self._ack_batch_script = self._r.register_script(self._ACK_BATCH_LUA)
            results = await self._ack_batch_script(keys=keys, args=args)
            return [
                r.decode() if isinstance(r, bytes) else str(r) for r in results
            ]
        except aioredis.RedisError as e:
            logger.warning(
                "Redis append_map_action_event_batch failed for %s: %s — falling "
                "back to per-event path",
                session_id, e,
            )
            return await super().append_map_action_event_batch(session_id, events)

    async def get_map_action_events(self, session_id: str) -> list[dict]:
        """返回该 session 当前全部地图动作 ACK（按到达顺序，与 memory 后端一致）。"""
        await self._ensure_connected()
        try:
            raw_ids = await self._r.zrange(self._map_actions_order_key(session_id), 0, -1)
            if not raw_ids:
                return []
            action_ids = [r.decode() if isinstance(r, bytes) else r for r in raw_ids]
            values = await self._r.hmget(self._map_actions_key(session_id), action_ids)
            events = []
            for v in values:
                if v is None:
                    continue  # 字段已被淘汰/清理
                events.append(json.loads(v.decode() if isinstance(v, bytes) else v))
            return events
        except aioredis.RedisError as e:
            logger.warning(
                "Redis get_map_action_events failed for session %s: %s — returning empty",
                session_id, e,
            )
            return []

    async def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        """Fetch session metadata in a single async pipeline.

        L1 hot read — this is called at the start of every chat turn and
        bundles 4 Redis calls; cache for L1_TTL_SECONDS to collapse repeats.
        #1080（#795 同型）：L1 命中路径此前 deepcopy 整个 bundle（含
        mapspec 级 map_state，重会话实测 ~55ms）；改为缓存原始字节字段、
        每读者在线程内重解析（C 级 json.loads，同负载 ~32ms，且不复制
        list_refs/event_log 的 Python 对象树）。
        """
        cached = self._l1_get(session_id, "metadata_raw")
        if cached is not None:
            return await asyncio.to_thread(
                self._parse_metadata_bundle_sync, cached[0], cached[1],
                cached[2], cached[3],
            )
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
        # #1080: 缓存原始字段（bytes），读者各自解析 —— 不再有共享对象树
        # 需要 deepcopy 防污染（旧 "metadata" 键的解析树条目随之淘汰）。
        self._l1_put(
            session_id, "metadata_raw",
            (state_raw, ref_ids_bytes, raw_refs, events_raw),
        )
        return await asyncio.to_thread(
            self._parse_metadata_bundle_sync, state_raw, ref_ids_bytes,
            raw_refs, events_raw,
        )

    def _parse_metadata_bundle_sync(self, state_raw, ref_ids_bytes, raw_refs, events_raw):
        """#1080: 原始 Redis 字段 → metadata dict（同步、线程内执行）。"""
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

    def _clearing_key(self, session_id: str) -> str:
        return f"session:{session_id}:clearing"

    async def set_session_clearing(self, session_id: str, ttl_s: int = 30) -> None:
        """#750: cross-replica clearing marker — an in-flight turn on another
        pod checks this before writing messages for the session."""
        try:
            await self._ensure_connected()
            await self._r.set(self._clearing_key(session_id), "1", ex=ttl_s)
        except aioredis.RedisError as e:
            logger.warning(
                "set_session_clearing failed for %s: %s (pod-local suppression only)",
                session_id, e,
            )

    async def is_session_clearing(self, session_id: str) -> bool:
        try:
            await self._ensure_connected()
            return bool(await self._r.exists(self._clearing_key(session_id)))
        except aioredis.RedisError:
            return False  # degrade to pod-local semantics

    async def clear_session(self, session_id: str) -> None:
        """#752: RedisError isolation like every sibling method — a Redis
        blip on session DELETE previously raised (500 on an idempotent-ish
        operation) and aborted the rest of the idle-eviction list."""
        try:
            await self._ensure_connected()
            index_key = self._index_key(session_id)
            ref_ids = await self._r.smembers(index_key)
            async with self._r.pipeline() as pipe:
                for ref_bytes in ref_ids:
                    ref_id = ref_bytes.decode() if isinstance(ref_bytes, bytes) else ref_bytes
                    pipe.delete(self._data_key(session_id, ref_id))
                    pipe.delete(self._descriptor_key(session_id, ref_id))
                pipe.delete(
                    index_key,
                    self._aliases_key(session_id),
                    self._refs_key(session_id),
                    self._state_key(session_id),
                    self._events_key(session_id),
                    self._refs_order_key(session_id),
                    self._map_actions_key(session_id),
                    self._map_actions_order_key(session_id),
                )
                pipe.srem(self._active_key(), session_id)
                pipe.zrem(self._activity_key(), session_id)
                await pipe.execute()
        except aioredis.RedisError as e:
            logger.warning(
                "Redis clear_session failed for session %s: %s — L1/disk purge still attempted",
                session_id, e,
            )
        # F13: write-through invalidation, like every other writer — otherwise a
        # session recreated with the same id within L1_TTL_SECONDS reads the
        # DELETED session's map_state/refs/event_log from L1.
        self._l1_invalidate_session(session_id)
        from app.services.mvt import spatial_index_cache, tile_lru_cache
        spatial_index_cache.invalidate_session(session_id)
        from app.services.ref_payload_cache import ref_payload_cache
        ref_payload_cache.invalidate_session(session_id)
        tile_lru_cache.invalidate_session(session_id)
        # #470：与 Redis 键删除同语义 —— 会话没了，盘上 mapspec revisions/
        # checkpoints/raster PNGs 一并回收（idle 淘汰 + 显式删除共用此路径）。
        # 失败容忍：磁盘清理失败不得让 Redis 清理回滚或抛给调用方。
        try:
            from app.services.mapspec.store import purge_session_disk_state
            await purge_session_disk_state(session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Session %s: disk state purge failed: %s", session_id, e)

    async def is_session_active(self, session_id: str) -> bool:
        """会话是否仍在 active 集（供磁盘清扫判断存活性）。

        Redis 不可达时返回 True（按"仍活跃"处理）—— 宁可漏回收也不误删
        活会话的磁盘状态。
        """
        try:
            await self._ensure_connected()
            return bool(await self._r.sismember(self._active_key(), session_id))
        except Exception as e:  # noqa: BLE001
            logger.warning("is_session_active(%s) failed (%s) — assuming active", session_id, e)
            return True

    async def cleanup_idle_sessions(self, max_sessions: int = 100) -> None:
        await self._ensure_connected()
        active = await self._r.smembers(self._active_key())
        if not active or len(active) <= max_sessions:
            return
        scored = []
        # #618-7: 单次 ZRANGE withscores 取回 activity 全集分数，替代逐会话
        # 串行 ZSCORE（每 600s 清扫 100+ 会话时省去等量 RTT）。
        activity_scores = await self._r.zrange(self._activity_key(), 0, -1, withscores=True)
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
                earliest = await self._r.zrange(self._refs_order_key(sid), 0, 0, withscores=True)
                raw_score = earliest[0][1] if earliest else 0
            scored.append((sid, float(raw_score)))
        scored.sort(key=lambda x: x[1])
        # Evict only the OVERFLOW (the old `+10` kept max-10 sessions and, for
        # max_sessions < 10, the negative slice removed EVERYTHING).
        to_remove = max(0, len(scored) - max_sessions)
        cleaned = 0
        for sid, _ in scored[:to_remove]:
            # #752: per-session isolation — one failing eviction must not
            # abort the rest of the list until the next 10-min tick
            # (clear_session itself degrades on RedisError, but the disk
            # purge / other exceptions stay contained here too).
            try:
                await self.clear_session(sid)
                cleaned += 1
            except Exception as e:  # noqa: BLE001 - eviction is per-session best-effort
                logger.warning("Idle cleanup failed for session %s: %s", sid, e)
        logger.info("Cleaned up %d idle sessions", cleaned)

    def _evict_ref(self, pipe, session_id: str, ref_id: str, alias: Optional[str] = None) -> None:
        """Add eviction commands to an open pipeline.

        #618-7: alias 由调用方在进入 pipeline 前一次性 HMGET 批量解析，这里
        不再执行串行 await —— 命令全部入管道，一次 execute 往返完成。
        """
        pipe.delete(self._data_key(session_id, ref_id))
        pipe.delete(self._descriptor_key(session_id, ref_id))  # V3: evict descriptor too
        pipe.zrem(self._refs_order_key(session_id), ref_id)
        pipe.srem(self._index_key(session_id), ref_id)
        if alias:
            pipe.hdel(self._aliases_key(session_id), alias)
        pipe.hdel(self._refs_key(session_id), ref_id)

    async def get_ref_descriptor(self, session_id: str, ref_id: str) -> "Optional[dict]":
        """V3 Performance: Return pre-computed descriptor; None if not found.

        Avoids re-scanning 100k features on every descriptor request.
        Falls back to on-the-fly compute + cache if the descriptor key is missing
        (refs stored before V3, or descriptor evicted separately).
        """
        await self._ensure_connected()
        descriptor_key = self._descriptor_key(session_id, ref_id)
        try:
            raw = await self._r.get(descriptor_key)
            if raw:
                # 小对象，但为对称性仍在线程解析（避免任何量级的 GIL 停顿）。
                return await asyncio.to_thread(json.loads, raw)
            # Fallback: compute from raw data if descriptor missing
            data_key = self._data_key(session_id, ref_id)
            data_raw = await self._r.get(data_key)
            if data_raw:
                # #590：50k 特征 payload 的 json.loads 是数 MB 级 C 解析 ——
                # 必须在线程执行（与 compute_descriptor 的 to_thread 对称），
                # 否则 overwrite 删 descriptor 键 / 键 TTL 先过期时每次回退
                # 都会冻结事件循环数百 ms-秒级。
                data = await asyncio.to_thread(json.loads, data_raw)
                from app.schemas.ref_descriptor import compute_descriptor
                descriptor = await asyncio.to_thread(compute_descriptor, ref_id, data)
                d = descriptor.to_dict()
                await self._r.set(descriptor_key, json.dumps(d, ensure_ascii=False), ex=DATA_TTL)
                return d
            return None
        except aioredis.RedisError as e:
            logger.error("Redis get_ref_descriptor failed for %s/%s: %s", session_id, ref_id, e)
            return None
        except ValueError as e:
            # #1061(b): 损坏的 descriptor/payload JSON（半写、截断）抛
            # JSONDecodeError(ValueError 子类)——此前逃逸出本方法，在工具已
            # 成功执行后把整个 dispatch 炸成失败。按「descriptor 不可得」
            # 处理（调用方已有缺失语义），不吞连接类异常。
            logger.error(
                "Corrupt ref payload for %s/%s (%s); treating descriptor as missing",
                session_id, ref_id, e,
            )
            return None

    async def ref_exists(self, session_id: str, ref_id: str) -> bool:
        """EXISTS on the data key with ``get()``'s recency side-effects.

        Checks the data key without reading/deserializing the payload. On a hit,
        renews the same three recency markers as ``get()``'s hit branch —
        ``expire(data_key, DATA_TTL)``, ``zadd(refs_order, {ref_id: now})`` and
        ``_refresh_session_ttl`` — so a metadata-only descriptor poll keeps the
        payload alive under TTL/LRU eviction, preserving master's "descriptor
        read keeps payload alive" semantics (master went through
        get_ref_data → get()). A miss performs no writes.
        Redis 不可达时返回 False（与 get() 的 cache-miss 语义一致：上层走
        fallback 后得到 NotFound，和现状 Redis 抖动时的行为相同）。
        """
        await self._ensure_connected()
        data_key = self._data_key(session_id, ref_id)
        try:
            if not await self._r.exists(data_key):
                return False
            async with self._r.pipeline() as pipe:
                pipe.expire(data_key, DATA_TTL)
                pipe.expire(self._descriptor_key(session_id, ref_id), DATA_TTL)
                pipe.zadd(self._refs_order_key(session_id), {ref_id: time.time()})
                self._refresh_session_ttl(pipe, session_id)
                await pipe.execute()
            return True
        except aioredis.RedisError as e:
            logger.warning("Redis ref_exists failed for session %s ref %s: %s", session_id, ref_id, e)
            return False

    async def _session_ref_ids(self, session_id: str) -> list:
        try:
            raw = await self._r.smembers(self._index_key(session_id))
        except aioredis.RedisError:
            return []
        return list(raw or [])

    def _refresh_payload_ttls(self, pipe, session_id: str, ref_ids) -> None:
        for ref_id in ref_ids:
            if isinstance(ref_id, bytes):
                ref_id = ref_id.decode()
            pipe.expire(self._data_key(session_id, ref_id), DATA_TTL)
            pipe.expire(self._descriptor_key(session_id, ref_id), DATA_TTL)

    def _refresh_session_ttl(self, pipe, session_id: str, ref_ids=None) -> None:
        # Refresh the WHOLE per-session key family, not just the ref registry.
        # Session activity (a read, a layer edit, an ACK) must keep every live
        # key alive for as long as the session is active; otherwise the
        # state/events/ACK keys expired at the 4h mark after their last WRITE
        # while payloads (refreshed by get()) stayed alive — an active session
        # silently lost its viewport, event log and ACK history.
        for key in [
            self._aliases_key(session_id),
            self._refs_key(session_id),
            self._refs_order_key(session_id),
            self._index_key(session_id),
            self._state_key(session_id),
            self._events_key(session_id),
            self._map_actions_key(session_id),
            self._map_actions_order_key(session_id),
        ]:
            pipe.expire(key, SESSION_TTL)
        pipe.zadd(self._activity_key(), {session_id: time.time()})
        pipe.expire(self._activity_key(), SESSION_TTL)
        if ref_ids:
            self._refresh_payload_ttls(pipe, session_id, ref_ids)


# Backward-compatible alias
RedisSessionDataManager = RedisSessionStore
