"""prepared call 进程内存储 —— TTL + LRU + 会话绑定（仿 G24 选择句柄）。

prepared call 是**短命执行意图**：start 之前有效，start 之后权威转移到
durable job 行（快照在 dispatch_spec）。浏览器刷新不依赖本存储 —— 运行
回看走 job 行；本存储只为「准备→审阅→启动」的用户确认窗口服务。

过期/逐出 = 需要**重新准备**（typed 410 语义），绝不能静默执行过期意图。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from app.services.gis_harness.skill_jobs.contracts import PreparedSkillCall

#: TTL（秒）：审阅窗口；过期重新准备。
PREPARED_TTL_S = 15 * 60
#: 容量（进程内）。
PREPARED_MAX = 64


class PreparedStore:
    """线程安全 LRU-TTL 存储。prepared_id → (prepared, inline_payloads, expires_at)。"""

    def __init__(self, *, ttl_s: int = PREPARED_TTL_S, capacity: int = PREPARED_MAX):
        self._ttl = ttl_s
        self._cap = capacity
        self._lock = threading.Lock()
        self._items: "OrderedDict[str, Tuple[PreparedSkillCall, Dict[str, Any], float]]" = OrderedDict()

    def put(self, prepared: PreparedSkillCall,
            inline_payloads: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._items[prepared.prepared_id] = (
                prepared, dict(inline_payloads or {}), time.time() + self._ttl)
            self._items.move_to_end(prepared.prepared_id)
            while len(self._items) > self._cap:
                self._items.popitem(last=False)

    def get(self, prepared_id: str, *, session_id: str = "") -> Optional[Tuple[PreparedSkillCall, Dict[str, Any]]]:
        with self._lock:
            entry = self._items.get(prepared_id)
            if entry is None:
                return None
            prepared, inline, expires = entry
            if time.time() > expires:
                del self._items[prepared_id]
                return None
            # 会话绑定：跨会话取他人 prepared = 越权（同进程内防御；跨进程本就不可见）
            if session_id and prepared.session_id != session_id:
                return None
            self._items.move_to_end(prepared_id)
            return (prepared, inline)

    def pop(self, prepared_id: str, *, session_id: str = "") -> Optional[Tuple[PreparedSkillCall, Dict[str, Any]]]:
        """取出并移除（start 消费语义：一次准备一次启动消费）。

        review P2-6：会话不匹配**不消费**（错会话的启动不得烧掉他人/
        本会话稍后要用的执行意图）；过期/不存在/越权同样返回 None。
        """
        with self._lock:
            entry = self._items.get(prepared_id)
            if entry is None:
                return None
            prepared, inline, expires = entry
            if time.time() > expires:
                del self._items[prepared_id]
                return None
            if session_id and prepared.session_id != session_id:
                return None  # 不删除：归属不符不是消费
            del self._items[prepared_id]
            return (prepared, inline)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


#: 进程级单例（测试可整体替换）。
prepared_store = PreparedStore()
