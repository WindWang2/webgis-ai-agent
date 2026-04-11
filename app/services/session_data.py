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
        self.capacity = capacity

    def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
        """存储数据并返回生成的游标 ID"""
        if session_id not in self._store:
            self._store[session_id] = OrderedDict()
        
        ref_id = f"ref:{prefix}-{uuid.uuid4().hex[:8]}"
        
        # 维护容量
        session_cache = self._store[session_id]
        if len(session_cache) >= self.capacity:
            session_cache.popitem(last=False)
            
        session_cache[ref_id] = data
        return ref_id

    def get(self, session_id: str, ref_id: str) -> Optional[Any]:
        """根据游标 ID 获取原始数据"""
        session_cache = self._store.get(session_id)
        if not session_cache or ref_id not in session_cache:
            return None
        
        # 移动到末尾 (LRU)
        data = session_cache.pop(ref_id)
        session_cache[ref_id] = data
        return data

    def clear_session(self, session_id: str):
        """清理会话数据"""
        self._store.pop(session_id, None)

# 单例模式供全局使用
session_data_manager = SessionDataManager()
