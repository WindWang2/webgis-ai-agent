"""会话数据管理器 - 存储大对象并提供游标引用"""
import uuid
import logging
from typing import Any, Optional
from collections import OrderedDict

logger = logging.getLogger(__name__)

class SessionDataManager:
    """Session-level data store with cursor support (LRU)"""
    def __init__(self, capacity: int = 200):
        # session_id -> {ref_id -> data}
        self._store: dict[str, OrderedDict[str, Any]] = {}
        # session_id -> {alias -> ref_id}
        self._aliases: dict[str, dict[str, str]] = {}
        self.capacity = capacity

    def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
        """存储数据并返回生成的游标 ID"""
        if session_id not in self._store:
            self._store[session_id] = OrderedDict()
        
        ref_id = f"ref:{prefix}-{uuid.uuid4().hex[:8]}"
        
        # 维护容量
        session_cache = self._store[session_id]
        if len(session_cache) >= self.capacity:
            # 清理时也要检查别名
            oldest_ref, _ = session_cache.popitem(last=False)
            self._remove_alias_by_ref(session_id, oldest_ref)
            
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

    def clear_session(self, session_id: str):
        """清理会话数据"""
        self._store.pop(session_id, None)
        self._aliases.pop(session_id, None)

# 单例模式供全局使用
session_data_manager = SessionDataManager()
