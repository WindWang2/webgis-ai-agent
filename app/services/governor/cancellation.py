"""取消协调（R9，ADR-0182 D9）。

取消源词表：user_cancel / session_replaced / plan_invalidated / timeout /
client_disconnect。cancel 后的硬保证：

1. pending 任务**不启动**（``should_skip`` 由 dispatch 前置检查）；
2. reservation **立即释放**（facade 执行 —— 账面/背压槽位同步归还）；
3. retry token 清零（RetryBudget.cancel_session —— 用户取消后重试停止）；
4. 取消事实与延迟进入观测面（governor_cancellation_latency_seconds）。

与 ``app/lib/cancellation.py``（CancellationToken/link/checkpoint 主干，
recon §3.6）的关系：governor **挂靠**该主干 —— 本模块持有的是 governor
自己的取消事实表；执行中的协作取消仍由既有 token/checkpoint 传播
（to_thread 不可抢占是诚实约束，registry.py 注释同款，V1 不侵入）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from app.services.governor.contract import CancelReason
from app.services.governor.metrics import observe_cancellation_latency


@dataclass
class CancellationRecord:
    session_id: str
    reason: CancelReason
    cancelled_at: float = field(default_factory=time.monotonic)
    released_reservation_ids: List[str] = field(default_factory=list)


class CancellationCoordinator:
    """governor 面的取消事实表（线程安全）。"""

    def __init__(self, retry_budget=None,
                 on_cancel_callbacks: Optional[List[Callable[[str, CancelReason], None]]] = None):
        self._cancelled: Dict[str, CancellationRecord] = {}
        self._lock = threading.Lock()
        self._retry_budget = retry_budget          # RetryBudget（可选注入）
        self._callbacks = list(on_cancel_callbacks or [])

    # ── 查询 ─────────────────────────────────────────────────────────

    def is_cancelled(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._cancelled

    def record_of(self, session_id: str) -> Optional[CancellationRecord]:
        with self._lock:
            rec = self._cancelled.get(session_id)
            return (CancellationRecord(
                session_id=rec.session_id, reason=rec.reason,
                cancelled_at=rec.cancelled_at,
                released_reservation_ids=list(rec.released_reservation_ids),
            ) if rec else None)

    # ── 取消 ─────────────────────────────────────────────────────────

    def cancel(self, session_id: str, reason: CancelReason, *,
               release_reservations: Optional[Callable[[str], List[str]]] = None,
               ) -> CancellationRecord:
        """取消一个会话的所有 governor 视角执行。

        ``release_reservations``：facade 注入的释放钩子（返回释放的
        reservation id 列表）。幂等：重复 cancel 返回既有记录。
        """
        with self._lock:
            existing = self._cancelled.get(session_id)
            if existing is not None:
                return existing
        started = time.monotonic()
        released: List[str] = []
        if release_reservations is not None:
            try:
                released = list(release_reservations(session_id) or [])
            except Exception:  # noqa: BLE001 — 释放钩子故障不阻断取消事实
                released = []
        if self._retry_budget is not None:
            try:
                self._retry_budget.cancel_session(session_id)
            except Exception:  # noqa: BLE001
                pass
        rec = CancellationRecord(
            session_id=session_id, reason=reason,
            released_reservation_ids=released,
        )
        with self._lock:
            existing = self._cancelled.get(session_id)
            if existing is not None:
                return existing
            self._cancelled[session_id] = rec
        for cb in self._callbacks:
            try:
                cb(session_id, reason)
            except Exception:  # noqa: BLE001 — 回调绝不阻断
                pass
        observe_cancellation_latency(max(0.0, time.monotonic() - started))
        return rec

    def close_session(self, session_id: str) -> None:
        """会话关闭：摘除取消事实（有界性；新会话同名不受历史污染）。"""
        with self._lock:
            self._cancelled.pop(session_id, None)

    def cancelled_count(self) -> int:
        with self._lock:
            return len(self._cancelled)


__all__ = [
    "CancellationRecord",
    "CancellationCoordinator",
]
