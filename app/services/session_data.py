"""会话数据管理器 - 存储大对象并提供游标引用"""
import asyncio
import copy
import os
import uuid
import logging
from typing import Any, Optional
from collections import OrderedDict, deque
from datetime import datetime, timezone

from app.services.session_data_protocol import BaseSessionStore, _layer_matches_removal_family

logger = logging.getLogger(__name__)

# V3 闭环：每 session 地图动作 ACK 上限（超出按插入序淘汰最旧）。
# Redis 后端（session_data_redis）同名常量必须保持一致（ADR-0035 协议对齐）。
MAX_MAP_ACTION_EVENTS = 200

class MemorySessionStore(BaseSessionStore):

    """In-memory SessionStore implementation with cursor support (LRU)"""
    def __init__(self, capacity: int = 200):
        # session_id -> {ref_id -> data}
        self._store: dict[str, OrderedDict[str, Any]] = {}
        # #750: 会话清理标记（进程内 TTL）——跨副本场景走 Redis 后端实现
        self._clearing_markers: dict[str, float] = {}
        # session_id -> {alias -> ref_id}
        self._aliases: dict[str, dict[str, str]] = {}
        # session_id -> {state_key -> value} (e.g., base_layer, current_view)
        self._map_state: dict[str, dict[str, Any]] = {}
        # session_id -> deque of recent user actions (max 20)
        self._event_log: dict[str, deque] = {}
        # session_id -> {action_id -> 地图动作终态 ACK}（V3 闭环；插入序 = 到达顺序）
        self._map_action_events: dict[str, OrderedDict[str, dict]] = {}
        # V3 Performance: session_id -> {ref_id -> descriptor_dict}
        self._descriptors: dict[str, dict[str, Any]] = {}
        # DA-P2-1：增量字节记账——session_id -> {ref_id -> bytes} 与
        # session_id -> total_bytes。此前 store() 每次对全部存量条目重估
        # 字节（O(N×budget)/次写且跑在事件循环上，200 条满员时每写一次
        # 最坏 ~4M 节点访问）。尺寸在写入点一次算好，淘汰/删除/覆写时
        # O(1) 增减。
        self._ref_sizes: dict[str, dict[str, int]] = {}
        self._session_bytes: dict[str, int] = {}
        self.capacity = capacity
        # BUG-14: serialize read-modify-write mutations of the layers list so
        # two concurrent update_layer_in_state calls don't clobber each other.
        # A single instance lock is sufficient for the in-memory backend (it is
        # only a fallback when Redis is unavailable).
        self._lock = asyncio.Lock()
        # F27: dedicated lock for the append_map_action_event dedupe critical
        # section (see its docstring). Kept separate from `self._lock` so ACK
        # appends don't serialize against layer mutations.
        self._map_action_lock = asyncio.Lock()
        # Session last-touch order for cleanup_idle_sessions (not first-insert).
        self._session_order: OrderedDict[str, None] = OrderedDict()

    def _touch_session(self, session_id: str) -> None:
        if session_id in self._session_order:
            self._session_order.move_to_end(session_id)
        else:
            self._session_order[session_id] = None

    async def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
        """存储数据并返回生成的游标 ID"""
        if session_id not in self._store:
            self._store[session_id] = OrderedDict()
        # 同步起点：若此 session 还没碰过 map_state，这里也算它的起点
        if session_id not in self._map_state:
            self._map_state[session_id] = {"_started_at": datetime.now(timezone.utc).isoformat()}

        # 16 hex chars = 64 bits entropy. ref_id + session_id 是能力令牌，需难以枚举。
        ref_id = f"ref:{prefix}-{uuid.uuid4().hex[:16]}"

        # 维护容量：按 LRU 淘汰最久未访问的项（entry 计数 + 字节预算）
        # #912 byte cap: each entry can be 5k FC (~5MB). 200 × 5MB = 1GB per session.
        # SESSION_STORE_MAX_BYTES (env, default 50MB) bounds memory before OOM.
        _max_bytes = int(os.getenv("SESSION_STORE_MAX_BYTES", "52428800"))
        session_cache = self._store[session_id]
        try:
            from app.lib.json_size import estimate_json_bytes as _est_bytes
            _new_size = _est_bytes(data) if isinstance(data, (dict, list)) else len(str(data).encode())
        except Exception:
            _new_size = 0

        # DA-P2-1: O(1) incremental accounting (sizes maintained at each write
        # point: store/overwrite/delete/evict/clear). A lazy one-off sync only
        # for a session created before this field existed (defensive).
        sizes = self._ref_sizes.get(session_id)
        if sizes is None or len(sizes) != len(session_cache):
            sizes = self._ref_sizes.setdefault(session_id, {})
            total_bytes = 0
            for r_id, v in session_cache.items():
                try:
                    from app.lib.json_size import estimate_json_bytes as _eb2
                    sz = _eb2(v) if isinstance(v, (dict, list)) else len(str(v).encode())
                except Exception:
                    sz = 0
                sizes[r_id] = sz
                total_bytes += sz
            self._session_bytes[session_id] = total_bytes
        total_bytes = self._session_bytes.get(session_id, 0)

        while session_cache and (len(session_cache) >= self.capacity or (total_bytes + _new_size > _max_bytes and total_bytes > 0)):
            oldest_ref, _ = session_cache.popitem(last=False)
            total_bytes -= sizes.pop(oldest_ref, 0)
            self._remove_alias_by_ref(session_id, oldest_ref)
            if session_id in self._descriptors:
                self._descriptors[session_id].pop(oldest_ref, None)
            from app.services.mvt import spatial_index_cache, tile_lru_cache
            spatial_index_cache.invalidate_ref(session_id, oldest_ref)
            tile_lru_cache.invalidate_ref(session_id, oldest_ref)
            logger.debug(f"Session {session_id}: evicted {oldest_ref} (capacity={self.capacity})")

        session_cache[ref_id] = data
        sizes[ref_id] = _new_size
        self._session_bytes[session_id] = total_bytes + _new_size
        self._touch_session(session_id)

        # V3 Performance: compute descriptor once at store time so every subsequent
        # descriptor read is O(1) instead of O(features).
        try:
            from app.schemas.ref_descriptor import compute_descriptor
            descriptor = await asyncio.to_thread(compute_descriptor, ref_id, data)
            if session_id not in self._descriptors:
                self._descriptors[session_id] = {}
            self._descriptors[session_id][ref_id] = descriptor.to_dict()
        except Exception as e:
            logger.warning(f"V3: Failed to compute descriptor for {ref_id}: {e}")

        return ref_id

    async def overwrite(self, session_id: str, ref_id: str, data: Any) -> bool:
        """Overwrite the data stored at an existing ``ref_id``.

        Unlike ``store`` (which always mints a new ref_id), this writes back to the
        SAME key so callers that hold the original ref_id (e.g. plan_mode's
        ``update_plan_status``) read the updated payload on the next ``get``.

        Returns True if the ref_id existed and was updated, False otherwise.
        """
        session_cache = self._store.get(session_id)
        if not session_cache or ref_id not in session_cache:
            return False
        session_cache[ref_id] = data
        # DA-P2-1：覆写尺寸 O(1) 增减
        try:
            from app.lib.json_size import estimate_json_bytes as _est_bytes
            new_size = _est_bytes(data) if isinstance(data, (dict, list)) else len(str(data).encode())
        except Exception:
            new_size = 0
        sizes = self._ref_sizes.get(session_id)
        if sizes is not None:
            self._session_bytes[session_id] = (
                self._session_bytes.get(session_id, 0) - sizes.get(ref_id, 0) + new_size
            )
            sizes[ref_id] = new_size
        from app.services.mvt import spatial_index_cache, tile_lru_cache
        spatial_index_cache.invalidate_ref(session_id, ref_id)
        tile_lru_cache.invalidate_ref(session_id, ref_id)
        # #1113 P3-5: match Redis D-4 — drop stale descriptor so next read
        # recomputes (feature_count/bbox/mvt_capable stay consistent).
        self._descriptors.get(session_id, {}).pop(ref_id, None)
        # overwrite is the durability path for plans/checkpoints — bump LRU
        # recency so a just-updated plan is not the next eviction victim.
        session_cache.move_to_end(ref_id)
        self._touch_session(session_id)
        return True

    async def delete_ref(self, session_id: str, ref_id: str) -> bool:
        """Drop one stored ref (and its descriptor/alias). Returns False if missing."""
        session_cache = self._store.get(session_id)
        if not session_cache or ref_id not in session_cache:
            return False
        session_cache.pop(ref_id, None)
        self._remove_alias_by_ref(session_id, ref_id)
        if session_id in self._descriptors:
            self._descriptors[session_id].pop(ref_id, None)
        # DA-P2-1：删除尺寸 O(1) 扣减
        sizes = self._ref_sizes.get(session_id)
        if sizes is not None and ref_id in sizes:
            self._session_bytes[session_id] = (
                self._session_bytes.get(session_id, 0) - sizes.pop(ref_id)
            )
        from app.services.mvt import spatial_index_cache, tile_lru_cache
        spatial_index_cache.invalidate_ref(session_id, ref_id)
        tile_lru_cache.invalidate_ref(session_id, ref_id)
        return True

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        """为引用 ID 设置别名"""
        if session_id not in self._aliases:
            self._aliases[session_id] = {}
        self._aliases[session_id][alias] = ref_id
        self._touch_session(session_id)

    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str:
        """Resolve a ref or alias to its canonical ref_id.

        /review P3-5: public accessor replacing six call sites that previously
        reached into `_aliases` directly. If `ref_or_alias` matches an alias
        registered in this session, returns the underlying ref_id. Otherwise
        returns the input unchanged (caller can decide whether that's an error).
        """
        return self._aliases.get(session_id, {}).get(ref_or_alias, ref_or_alias)

    async def resolve_aliases(self, session_id: str, strings: list[str]) -> dict[str, str]:
        """Batch form of resolve_alias: one call for the whole list."""
        aliases = self._aliases.get(session_id, {})
        return {s: aliases.get(s, s) for s in strings}

    async def get_shared(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
        """P-1（#874）：共享只读读取 —— 返回存储对象本体（零拷贝）。

        进程内后端自己就是权威副本，无需额外缓存：overwrite/delete/evict
        天然反映在下一次读取。只读约定见
        app/services/ref_payload_cache.py；需要可变副本走 ``get()``。
        """
        session_cache = self._store.get(session_id)
        if not session_cache:
            return None
        ref_id = ref_id_or_alias
        aliases = self._aliases.get(session_id, {})
        if ref_id_or_alias in aliases:
            ref_id = aliases[ref_id_or_alias]
        data = session_cache.get(ref_id)
        if data is None:
            return None
        # LRU touch（与 get() 一致；dict 本体共享，move_to_end 保热度）
        session_cache.move_to_end(ref_id)
        self._touch_session(session_id)
        return data

    async def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
        """根据游标 ID 或别名获取原始数据
        
        返回深拷贝副本以统一内存/Redis 后端语义（#701-2）：
        - Redis 侧每次 json.loads 天然返回新对象
        - 内存侧必须 deepcopy，消除可变别名
        调用方就地改 payload 不影响存储；显式更新需调 store_* 方法。
        """
        session_cache = self._store.get(session_id)
        if not session_cache:
            return None
        
        # 尝试作为别名查找
        ref_id = ref_id_or_alias
        aliases = self._aliases.get(session_id, {})
        if ref_id_or_alias in aliases:
            ref_id = aliases[ref_id_or_alias]
        
        if ref_id not in session_cache:
            return None
        
        # 移动到末尾 (LRU)
        data = session_cache.pop(ref_id)
        session_cache[ref_id] = data
        self._touch_session(session_id)

        # 返回深拷贝副本（与 Redis 侧语义对齐）。
        # #799: 与 Redis 后端一致地下线程 —— 50k 要素级 ref 的内联 deepcopy
        # 在事件循环上是 ~100ms 级停顿（降级模式同样不应冻结循环）。
        return await asyncio.to_thread(copy.deepcopy, data)

    # P2-1: get_ref_data was a byte-identical override of
    # BaseSessionStore.get_ref_data and has been removed. The inherited Base
    # implementation delegates to self.get() and self.get_session_metadata()
    # (both overridden below), which carry the backend-specific logic.

    async def list_refs(self, session_id: str) -> dict[str, str]:
        """列出所有引用及其别名"""
        aliases = self._aliases.get(session_id, {})
        # 反转别名映射以便查找
        ref_to_alias = {v: k for k, v in aliases.items()}
        
        results = {}
        session_cache = self._store.get(session_id, {})
        for ref_id in session_cache:
            results[ref_id] = ref_to_alias.get(ref_id, "")
        return results

    def _remove_alias_by_ref(self, session_id: str, ref_id: str):
        """根据 ref_id 移除对应的别名"""
        if session_id in self._aliases:
            aliases = self._aliases[session_id]
            to_delete = [k for k, v in aliases.items() if v == ref_id]
            for k in to_delete:
                del aliases[k]

    async def set_map_state_fields(self, session_id: str, fields: dict) -> bool:
        """#1073: 一批状态字段一次写入（服务端真值，无 seq 语义）。

        Redis 后端为单 WATCH/MULTI（spec 与 CAS 令牌原子落地）；内存后端
        语义上等价（同进程锁内调用方本就串行）。
        """
        if not fields:
            return True
        if session_id not in self._map_state:
            self._map_state[session_id] = {}
            self._map_state[session_id].setdefault(
                "_started_at", datetime.now(timezone.utc).isoformat()
            )
        self._map_state[session_id].update(fields)
        return True

    async def get_state_field(self, session_id: str, field: str) -> Any:
        """v2(audit P6)：定向读单字段 —— 覆盖协议默认（全量 get_map_state
        deepcopy 整个状态含 mapspec）为单字段浅拷贝（列表字段拷贝列表，
        防 #701 的引用逃逸）。"""
        state = self._map_state.get(session_id, {})
        value = state.get(field)
        if isinstance(value, list):
            return list(value)
        if isinstance(value, dict):
            return dict(value)
        return value

    async def commit_mapspec_state(
        self, session_id: str, fields: dict, layer_op: Optional[tuple] = None,
    ) -> bool:
        """v2(audit F4): MapSpec commit 单事务（内存后端语义等价实现）。

        与 Redis 后端的 WATCH/MULTI 语义对齐：fields 写入 + layers 的
        read-modify-write 原子完成（同进程内本就串行，无 crash 窗口）。
        layer_op 语义见 RedisSessionStore.commit_mapspec_state。
        """
        if not fields and layer_op is None:
            return True
        if session_id not in self._map_state:
            self._map_state[session_id] = {}
            self._map_state[session_id].setdefault(
                "_started_at", datetime.now(timezone.utc).isoformat()
            )
        state = self._map_state[session_id]
        if layer_op is not None:
            op, layer_id, layer_payload = layer_op
            layers = list(state.get("layers") or [])
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
                    if not _layer_matches_removal_family(layer.get("id"), layer_id)
                ]
            elif op == "replace":
                layers = list(layer_payload or [])
            state["layers"] = layers
        state.update(fields)
        return True

    async def set_map_state(self, session_id: str, key: str, value: Any, seq: Optional[int] = None) -> bool:
        """设置地图状态元数据

        F4: ``viewport`` 有两个写入方（turn-start 快照 + 前端节流 POST），
        无序号时按到达顺序 last-write-wins，慢速旧 POST 会覆盖新状态。这里为
        每个 key 维护单调 ``seq``：带 ``seq`` 的写入仅在严格新于已存 seq 时
        生效（否则拒绝并返回 False），乱序到达统一收敛到最新 seq。不带 seq
        的写入（服务端真相：ws_service / layer_manager / mapspec）总是生效，
        且不推进已存 seq —— 客户端下一次带 seq 的写入不会被误拒。
        """
        if session_id not in self._map_state:
            self._map_state[session_id] = {}
            # 首次写入即视为 session 起点（避免单独维护"创建"路径）
            self._map_state[session_id].setdefault("_started_at", datetime.now(timezone.utc).isoformat())
        state = self._map_state[session_id]
        if seq is not None:
            stored_seq = state.get(f"_{key}_seq", 0)
            if seq <= stored_seq:
                return False  # stale out-of-order write — resolve to latest seq
            state[f"_{key}_seq"] = seq
        state[key] = value
        state[f"_{key}_updated_at"] = datetime.now(timezone.utc).isoformat()
        self._touch_session(session_id)
        return True

    async def get_started_at(self, session_id: str) -> Optional[str]:
        """返回 session 首次接触时间 (ISO 字符串)，未存在则 None。"""
        state = self._map_state.get(session_id, {})
        return state.get("_started_at")

    async def get_map_state(self, session_id: str) -> dict[str, Any]:
        """获取当前地图所有状态。

        #749: 返回深拷贝——直接返回存储 dict 时，任何调用方就地改动都会
        污染其它读者并绕过 set_map_state 的 seq/F4 单调检查（#701-2 的
        copy 纪律此前只覆盖了 get()，未覆盖 map_state）。#799: deepcopy
        下线程（与 Redis 后端一致），大 state 不再内联阻塞事件循环。"""
        import copy as _copy
        return await asyncio.to_thread(_copy.deepcopy, self._map_state.get(session_id, {}))

    def invalidate_local_cache(self, session_id: str) -> None:
        """Memory is authoritative in-process, so no read cache can be stale."""
        del session_id

    async def get_map_spec_fingerprint(self, session_id: str):
        """#687：mapspec 指纹定向读（内存后端形态；语义同 Redis 版）。"""
        st = self._map_state.get(session_id)
        if not st:
            return None
        fp = st.get("_mapspec_fp")
        return fp if isinstance(fp, str) else None

    async def set_map_spec_fingerprint(self, session_id: str, fingerprint: str) -> None:
        """#687：mapspec 指纹定向写（内存后端形态）。"""
        self._map_state.setdefault(session_id, {})["_mapspec_fp"] = fingerprint

    async def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict) -> bool:
        """更新地图状态中单个图层的属性"""
        # BUG-14: hold the lock across the whole read-modify-write so a
        # concurrent update/remove on the same layers list can't interleave and
        # lose one side's mutation.
        async with self._lock:
            layers = list(self._map_state.get(session_id, {}).get("layers", []))
            for layer in layers:
                if layer.get("id") == layer_id:
                    layer.update(updates)
                    break
            else:
                layers.append({"id": layer_id, **updates})
            return await self.set_map_state(session_id, "layers", layers)

    async def remove_layer_from_state(self, session_id: str, layer_id: str) -> bool:
        """从地图状态中移除指定图层。

        #1074(F-12): 族谓词（id / id-前缀子层）与 spec 侧
        _should_remove_layer 对称 —— 此前精确 id 匹配让伴生子层
        （x-label 等）从期望态消失却在 map_state.layers 残留。
        """
        # BUG-14: same read-modify-write race as update_layer_in_state.
        async with self._lock:
            layers = self._map_state.get(session_id, {}).get("layers", [])
            return await self.set_map_state(
                session_id, "layers",
                [
                    layer for layer in layers
                    if not _layer_matches_removal_family(layer.get("id"), layer_id)
                ],
            )

    async def append_event(self, session_id: str, event: str, data: dict) -> None:
        """追加用户操作到事件日志"""
        if session_id not in self._event_log:
            self._event_log[session_id] = deque(maxlen=20)
        self._event_log[session_id].append({
            "event": event,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._touch_session(session_id)

    async def get_event_log(self, session_id: str) -> list[dict]:
        """获取近期用户操作日志"""
        return list(self._event_log.get(session_id, []))

    async def append_map_action_event(self, session_id: str, event: dict) -> bool:
        """追加地图动作终态 ACK（V3 闭环），按 action_id 幂等。

        首达终态获胜：同一 action_id 已存在时拒绝并返回 False（前端重连/重试
        会重复上报，首个到达的终态即真相）。每 session 上限
        MAX_MAP_ACTION_EVENTS，超出时按插入序淘汰最旧条目。

        F27 不变量：action_id 判重 + 插入是 check-then-insert 临界区，全程持有
        ``_map_action_lock``。此前临界区碰巧没有 await（原子性是"偶然"成立的）；
        任何未来在临界区内引入的 await 都必须保持在锁内，否则并发重复写会
        同时通过判重，破坏首达终态获胜语义。
        """
        action_id = str(event.get("action_id") or "")
        if not action_id:
            return False
        async with self._map_action_lock:
            events = self._map_action_events.setdefault(session_id, OrderedDict())
            if action_id in events:
                return False
            while len(events) >= MAX_MAP_ACTION_EVENTS:
                events.popitem(last=False)
            events[action_id] = dict(event)
            return True

    async def get_map_action_events(self, session_id: str) -> list[dict]:
        """返回该 session 当前全部地图动作 ACK（按到达顺序）。"""
        return list(self._map_action_events.get(session_id, {}).values())

    async def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        """获取会话元数据（聚合查询以减少 Redis 等后端往返）"""
        return {
            "map_state": await self.get_map_state(session_id),
            "list_refs": await self.list_refs(session_id),
            "event_log": await self.get_event_log(session_id),
            "started_at": await self.get_started_at(session_id),
        }

    async def get_ref_descriptor(self, session_id: str, ref_id: str) -> Optional[dict]:
        """V3 Performance: return pre-computed descriptor (O(1)); None if not found."""
        return self._descriptors.get(session_id, {}).get(ref_id)

    async def ref_exists(self, session_id: str, ref_id: str) -> bool:
        """O(1) existence check; mirrors ``get()``'s LRU recency side-effect.

        On a hit, moves the ref to the MRU end of the session's LRU order
        (``move_to_end``, O(1)) so a metadata-only descriptor poll keeps the
        payload alive under capacity eviction — same recency semantics as a
        payload ``get()``. Unlike ``get()`` it does NOT read or re-store the
        payload data itself (no pop/re-insert), so nothing is copied or
        deserialized. Returns False on a miss (no reordering).
        """
        session_cache = self._store.get(session_id)
        if not session_cache or ref_id not in session_cache:
            return False
        session_cache.move_to_end(ref_id)
        self._touch_session(session_id)
        return True

    async def set_session_clearing(self, session_id: str, ttl_s: int = 30) -> None:
        """#750: single-process deployment — an in-memory marker with expiry."""
        import time as _time
        self._clearing_markers[session_id] = _time.monotonic() + ttl_s

    async def is_session_clearing(self, session_id: str) -> bool:
        import time as _time
        deadline = self._clearing_markers.get(session_id)
        if deadline is None:
            return False
        if _time.monotonic() > deadline:
            self._clearing_markers.pop(session_id, None)
            return False
        return True

    async def clear_session(self, session_id: str) -> None:
        """清理会话数据"""
        self._store.pop(session_id, None)
        self._aliases.pop(session_id, None)
        self._map_state.pop(session_id, None)
        self._event_log.pop(session_id, None)
        self._map_action_events.pop(session_id, None)
        self._descriptors.pop(session_id, None)
        self._ref_sizes.pop(session_id, None)
        self._session_bytes.pop(session_id, None)
        self._session_order.pop(session_id, None)
        from app.services.mvt import spatial_index_cache, tile_lru_cache
        spatial_index_cache.invalidate_session(session_id)
        tile_lru_cache.invalidate_session(session_id)
        # #470：会话没了，盘上状态（mapspec revisions/checkpoints/raster PNGs）
        # 一并回收。失败容忍（purge 内部记日志不抛）—— 磁盘清理失败不得阻断
        # 上面的内存清理或调用方（idle 淘汰 / DELETE 会话）的语义。
        try:
            from app.services.mapspec.store import purge_session_disk_state
            await purge_session_disk_state(session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Session {session_id}: disk state purge failed: {e}")

    def is_session_active(self, session_id: str) -> bool:
        """会话是否仍在本 store 中持有状态（供磁盘清扫判断存活性）。"""
        return (
            session_id in self._store
            or session_id in self._map_state
            or session_id in self._session_order
        )

    async def cleanup_idle_sessions(self, max_sessions: int = 100) -> None:
        """Evict least-recently-touched sessions when total exceeds max_sessions."""
        order = self._session_order
        if not order:
            order = OrderedDict((sid, None) for sid in self._store)
        if len(order) <= max_sessions:
            return
        # Evict only the OVERFLOW (the old `+10` kept max-10 sessions and, for
        # max_sessions < 10, the negative slice removed EVERYTHING).
        to_remove = list(order.keys())[:max(0, len(order) - max_sessions)]
        for sid in to_remove:
            await self.clear_session(sid)
        logger.info(f"Cleaned up {len(to_remove)} idle sessions")

def create_session_data_manager():
    """Factory: returns Redis-backed or in-memory manager based on config.

    审计 TEST-13 / B3：本函数在模块 import 时同步调用（session_data_manager 单例）。
    原实现里 import 时同步 `manager.ping()` —— ping() 会创建/复用一个 event loop
    跑一次再关闭，导致 Redis 连接池绑定到那个错误的 loop，后续 pytest-asyncio 用
    新 loop 时报 "Future attached to a different loop"。

    现在的修复：不再在 import 期做任何 Redis I/O。
    - RedisSessionDataManager 的 __init__ 只存配置，不创建客户端；
    - ping() 改为 async，仅在运行中的 loop 里 await 时才懒构造客户端并连池；
    - 这里只决定"用哪种后端"，不做连通性探测。

    连通性：Redis 不可达时由各 async 方法的 try/except RedisError 降级处理
    （store/get/append_event 等已隔离异常），语义与原"启动期降级"等价，
    但不再有 import 期阻塞或 event loop 错配。
    仅当 redis 库本身缺失（ImportError）或构造异常时，才在这里回退到内存版。
    """
    from app.core.config import settings
    if settings.USE_REDIS:
        try:
            from app.services.session_data_redis import RedisSessionDataManager
            manager = RedisSessionDataManager(settings.REDIS_URL)
            logger.info("SessionDataManager: using Redis backend (lazy connect)")
            return manager
        except ImportError as e:
            # redis 库未安装 -> 退化到内存后端
            logger.warning(f"redis library unavailable ({e}), falling back to in-memory")
        except Exception as e:
            # 构造期异常（非连通性，因为 __init__ 不连 Redis）-> 退化到内存后端
            logger.warning(f"RedisSessionDataManager construction failed ({e}), falling back to in-memory")
    logger.info("SessionDataManager: using in-memory backend")
    return MemorySessionStore()


# Backward-compatible alias
SessionDataManager = MemorySessionStore

# Singleton — created once at import time via factory.
session_data_manager = create_session_data_manager()
