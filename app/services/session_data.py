"""会话数据管理器 - 存储大对象并提供游标引用"""
import uuid
import logging
from typing import Any, Optional
from collections import OrderedDict, deque
from datetime import datetime

logger = logging.getLogger(__name__)

class SessionDataManager:
    """Session-level data store with cursor support (LRU)"""
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

    def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
        """存储数据并返回生成的游标 ID"""
        if session_id not in self._store:
            self._store[session_id] = OrderedDict()

        ref_id = f"ref:{prefix}-{uuid.uuid4().hex[:8]}"

        # 维护容量：按 LRU 淘汰最久未访问的项
        session_cache = self._store[session_id]
        while len(session_cache) >= self.capacity:
            oldest_ref, _ = session_cache.popitem(last=False)
            self._remove_alias_by_ref(session_id, oldest_ref)
            logger.debug(f"Session {session_id}: evicted {oldest_ref} (capacity={self.capacity})")

        session_cache[ref_id] = data
        return ref_id

    def set_alias(self, session_id: str, ref_id: str, alias: str):
        """为引用 ID 设置别名"""
        if session_id not in self._aliases:
            self._aliases[session_id] = {}
        self._aliases[session_id][alias] = ref_id

    def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
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

    def list_refs(self, session_id: str) -> dict[str, str]:
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

    def set_map_state(self, session_id: str, key: str, value: Any):
        """设置地图状态元数据"""
        if session_id not in self._map_state:
            self._map_state[session_id] = {}
        self._map_state[session_id][key] = value

    def get_map_state(self, session_id: str) -> dict[str, Any]:
        """获取当前地图所有状态"""
        return self._map_state.get(session_id, {})

    def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict):
        """更新地图状态中单个图层的属性"""
        layers = list(self._map_state.get(session_id, {}).get("layers", []))
        for layer in layers:
            if layer.get("id") == layer_id:
                layer.update(updates)
                break
        else:
            layers.append({"id": layer_id, **updates})
        self.set_map_state(session_id, "layers", layers)

    def remove_layer_from_state(self, session_id: str, layer_id: str):
        """从地图状态中移除指定图层"""
        layers = self._map_state.get(session_id, {}).get("layers", [])
        self.set_map_state(session_id, "layers", [l for l in layers if l.get("id") != layer_id])

    def append_event(self, session_id: str, event: str, data: dict):
        """追加用户操作到事件日志"""
        if session_id not in self._event_log:
            self._event_log[session_id] = deque(maxlen=20)
        self._event_log[session_id].append({
            "event": event,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        })

    def get_event_log(self, session_id: str) -> list[dict]:
        """获取近期用户操作日志"""
        return list(self._event_log.get(session_id, []))

    def clear_session(self, session_id: str):
        """清理会话数据"""
        self._store.pop(session_id, None)
        self._aliases.pop(session_id, None)
        self._map_state.pop(session_id, None)
        self._event_log.pop(session_id, None)

    def cleanup_idle_sessions(self, max_sessions: int = 100):
        """Evict oldest sessions when total exceeds max_sessions."""
        if len(self._store) <= max_sessions:
            return
        # Remove oldest sessions (first inserted in OrderedDict-like fashion)
        to_remove = list(self._store.keys())[:len(self._store) - max_sessions + 10]
        for sid in to_remove:
            self.clear_session(sid)
        logger.info(f"Cleaned up {len(to_remove)} idle sessions")

# 单例模式供全局使用
session_data_manager = SessionDataManager()
