"""会话数据管理器 - 存储大对象并提供游标引用"""
import asyncio
import uuid
import logging
from typing import Any, Optional
from collections import OrderedDict, deque
from datetime import datetime, timezone

from app.services.session_data_protocol import BaseSessionStore, SessionRefDataResult

logger = logging.getLogger(__name__)

class MemorySessionStore(BaseSessionStore):

    """In-memory SessionStore implementation with cursor support (LRU)"""
    def __init__(self, capacity: int = 200):
        # session_id -> {ref_id -> data}
        self._store: dict[str, OrderedDict[str, Any]] = {}
        # session_id -> {alias -> ref_id}
        self._aliases: dict[str, dict[str, str]] = {}
        # session_id -> {state_key -> value} (e.g., base_layer, current_view)
        self._map_state: dict[str, dict[str, Any]] = {}
        # session_id -> deque of recent user actions (max 20)
        self._event_log: dict[str, deque] = {}
        self.capacity = capacity
        # BUG-14: serialize read-modify-write mutations of the layers list so
        # two concurrent update_layer_in_state calls don't clobber each other.
        # A single instance lock is sufficient for the in-memory backend (it is
        # only a fallback when Redis is unavailable).
        self._lock = asyncio.Lock()

    async def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
        """存储数据并返回生成的游标 ID"""
        if session_id not in self._store:
            self._store[session_id] = OrderedDict()
        # 同步起点：若此 session 还没碰过 map_state，这里也算它的起点
        if session_id not in self._map_state:
            self._map_state[session_id] = {"_started_at": datetime.now(timezone.utc).isoformat()}

        # 16 hex chars = 64 bits entropy. ref_id + session_id 是能力令牌，需难以枚举。
        ref_id = f"ref:{prefix}-{uuid.uuid4().hex[:16]}"

        # 维护容量：按 LRU 淘汰最久未访问的项
        session_cache = self._store[session_id]
        while len(session_cache) >= self.capacity:
            oldest_ref, _ = session_cache.popitem(last=False)
            self._remove_alias_by_ref(session_id, oldest_ref)
            logger.debug(f"Session {session_id}: evicted {oldest_ref} (capacity={self.capacity})")

        session_cache[ref_id] = data
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
        return True

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        """为引用 ID 设置别名"""
        if session_id not in self._aliases:
            self._aliases[session_id] = {}
        self._aliases[session_id][alias] = ref_id

    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str:
        """Resolve a ref or alias to its canonical ref_id.

        /review P3-5: public accessor replacing six call sites that previously
        reached into `_aliases` directly. If `ref_or_alias` matches an alias
        registered in this session, returns the underlying ref_id. Otherwise
        returns the input unchanged (caller can decide whether that's an error).
        """
        return self._aliases.get(session_id, {}).get(ref_or_alias, ref_or_alias)

    async def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
        """根据游标 ID 或别名获取原始数据"""
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
        return data

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

    async def set_map_state(self, session_id: str, key: str, value: Any) -> None:
        """设置地图状态元数据"""
        if session_id not in self._map_state:
            self._map_state[session_id] = {}
            # 首次写入即视为 session 起点（避免单独维护"创建"路径）
            self._map_state[session_id].setdefault("_started_at", datetime.now(timezone.utc).isoformat())
        self._map_state[session_id][key] = value

    async def get_started_at(self, session_id: str) -> Optional[str]:
        """返回 session 首次接触时间 (ISO 字符串)，未存在则 None。"""
        state = self._map_state.get(session_id, {})
        return state.get("_started_at")

    async def get_map_state(self, session_id: str) -> dict[str, Any]:
        """获取当前地图所有状态"""
        return self._map_state.get(session_id, {})

    async def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict) -> None:
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
            await self.set_map_state(session_id, "layers", layers)

    async def remove_layer_from_state(self, session_id: str, layer_id: str) -> None:
        """从地图状态中移除指定图层"""
        # BUG-14: same read-modify-write race as update_layer_in_state.
        async with self._lock:
            layers = self._map_state.get(session_id, {}).get("layers", [])
            await self.set_map_state(session_id, "layers", [l for l in layers if l.get("id") != layer_id])

    async def append_event(self, session_id: str, event: str, data: dict) -> None:
        """追加用户操作到事件日志"""
        if session_id not in self._event_log:
            self._event_log[session_id] = deque(maxlen=20)
        self._event_log[session_id].append({
            "event": event,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def get_event_log(self, session_id: str) -> list[dict]:
        """获取近期用户操作日志"""
        return list(self._event_log.get(session_id, []))

    async def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        """获取会话元数据（聚合查询以减少 Redis 等后端往返）"""
        return {
            "map_state": await self.get_map_state(session_id),
            "list_refs": await self.list_refs(session_id),
            "event_log": await self.get_event_log(session_id),
            "started_at": await self.get_started_at(session_id),
        }

    async def clear_session(self, session_id: str) -> None:
        """清理会话数据"""
        self._store.pop(session_id, None)
        self._aliases.pop(session_id, None)
        self._map_state.pop(session_id, None)
        self._event_log.pop(session_id, None)

    async def cleanup_idle_sessions(self, max_sessions: int = 100) -> None:
        """Evict oldest sessions when total exceeds max_sessions."""
        if len(self._store) <= max_sessions:
            return
        # Remove oldest sessions (first inserted in OrderedDict-like fashion)
        to_remove = list(self._store.keys())[:len(self._store) - max_sessions + 10]
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
