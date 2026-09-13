"""Turn 内待写记忆缓冲（生产缝的零 IO 旁路）。

dispatch 失败缝、map_intent 解析缝都在工具热路径上——它们只把
**候选**（org/user 未烙印）放进本缓冲，turn 端 harvest 统一烙印身份、
过写入门、落库。进程崩溃丢失可接受：provider_failure 等候选本身就是
短命信号（TTL 7d），下一 turn 会重新产生；durable authority 仍是
RecoveryLedger / 评审证据。

有界：每 session 最多 32 条（超募丢最旧），全部为内存态、无持久化。
"""
from __future__ import annotations

import threading
from collections import OrderedDict, deque
from typing import Deque, List

from app.services.gis_memory.contract import MemoryWriteRequest

MAX_PENDING_PER_SESSION = 32
#: 全局在册 session 上限（review F5：legacy 引擎路径与失败 turn 永不 drain，
#: 无全局上限 = 进程级慢泄漏）。超限逐出最久未写的 session（其候选本就是
#: 短命信号，丢失可接受——下一 turn 重新产生）。
MAX_PENDING_SESSIONS = 512


class PendingMemoryBuffer:
    """session → 待写记忆候选（线程安全；工具线程/事件循环混用）。

    全局 session 维度 LRU 有界（``OrderedDict`` move_to_end + popitem），
    单 session 内 deque 有界。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buf: "OrderedDict[str, Deque[MemoryWriteRequest]]" = OrderedDict()

    def offer(self, session_id: str, req: MemoryWriteRequest) -> None:
        if not session_id or not isinstance(req, MemoryWriteRequest):
            return
        with self._lock:
            dq = self._buf.get(session_id)
            if dq is None:
                while len(self._buf) >= MAX_PENDING_SESSIONS:
                    self._buf.popitem(last=False)
                dq = self._buf.setdefault(
                    session_id, deque(maxlen=MAX_PENDING_PER_SESSION)
                )
            dq.append(req)
            self._buf.move_to_end(session_id)

    def drain(self, session_id: str) -> List[MemoryWriteRequest]:
        with self._lock:
            dq = self._buf.pop(session_id, None)
            return list(dq) if dq else []

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._buf.pop(session_id, None)

    def pending_count(self, session_id: str) -> int:
        with self._lock:
            dq = self._buf.get(session_id)
            return len(dq) if dq else 0


pending_memory_buffer = PendingMemoryBuffer()

__all__ = [
    "MAX_PENDING_PER_SESSION",
    "MAX_PENDING_SESSIONS",
    "PendingMemoryBuffer",
    "pending_memory_buffer",
]
