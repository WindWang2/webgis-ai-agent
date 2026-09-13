"""三层背压闸（R5，ADR-0182 D6）：session / subsystem-channel / global。

结构（自外向内）：

1. **session 层**：每会话并发上限 + heavy 上限（对齐 ADR-0100
   ``_SessionWaveGate`` 的语义，但在其外层 —— governor 不替换它）；
2. **subsystem 层**：raster / browser / export / external / llm 五个封闭
   通道（recon：geocompute 已按资源画像分队列，这里是对 dispatch 面的
   harness 侧镜像，不重建 geocompute 队列）；global heavy 池是第六通道
   （跨会话 heavy 上限的宿主）；
3. **global 层**：即通道容量本身（heavy/raster/browser/export 的容量就是
   全局上限）+ 会话表的有界性。

每个通道内嵌 :class:`governor.fairness.FairScheduler`（加权公平 + aging +
small bypass）。所有 ``acquire`` 带 ``max_wait``：超时 → 抛
:class:`QueueTimeoutError`，由 admission 升格 degrade/reject（D6：拒绝
无界等待）。

取消/超时安全：waiter 在 cancel 路径必然清理；wait_for 竞争窗口（事件
已置位但调用方按超时处理）以「立即归还槽位」闭合 —— 无孤儿槽位。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from app.services.governor import metrics as gmetrics
from app.services.governor.contract import Dimension, ResourceClass, ResourceEstimate
from app.services.governor.fairness import FairScheduler, FairWaiter

logger = logging.getLogger(__name__)


class QueueTimeoutError(Exception):
    """排队超过 max_wait（admission 据此升格 degrade/reject）。"""

    def __init__(self, channel: str, waited_s: float, session_id: str = ""):
        self.channel = channel
        self.waited_s = waited_s
        self.session_id = session_id
        super().__init__(
            f"queue timeout on channel '{channel}' after {waited_s:.1f}s "
            f"(session={session_id or '-'})"
        )


#: ResourceClass → 背压通道名（封闭映射；LIGHT/MEDIUM 直通无通道）。
_CHANNEL_OF: Dict[ResourceClass, str] = {
    ResourceClass.RASTER: "raster",
    ResourceClass.BROWSER: "browser",
    ResourceClass.EXPORT: "export",
    ResourceClass.LLM: "llm",
    ResourceClass.HEAVY: "heavy",
}


def channel_for(resource_class: ResourceClass) -> str:
    return _CHANNEL_OF.get(resource_class, "")


@dataclass
class AcquireTicket:
    """成功占用的凭证（release 时原样交还；携带全部归还所需事实）。"""

    channel: str                    # "" = 直通（无通道闸）
    session_id: str
    heavy: bool = False             # 是否占用了会话 heavy 槽
    small: bool = False
    enqueued_at: float = field(default_factory=time.monotonic)


class _ChannelGate:
    """单通道异步闸：容量 + FairScheduler + small bypass 预留槽。"""

    def __init__(self, name: str, capacity: int, aging_threshold_s: float):
        self.name = name
        self._sched = FairScheduler(capacity, aging_threshold_s=aging_threshold_s)
        self._lock = asyncio.Lock()   # 串行化 FairScheduler（同步核心）

    @property
    def capacity(self) -> int:
        return self._sched.capacity

    @property
    def in_flight(self) -> int:
        return self._sched.in_flight

    @property
    def waiting(self) -> int:
        return self._sched.waiting

    async def acquire(self, session_id: str, *, small: bool, priority: int,
                      est_cost: float, weight: float, max_wait_s: float,
                      ) -> None:
        """占用一个槽位（可能等待）；超时抛 QueueTimeoutError。

        成功时槽位已计入 scheduler.in_flight；调用方保证最终 release。
        """
        waiter = FairWaiter(
            session_id=session_id, weight=weight, priority=priority,
            small=small, est_cost=max(0.1, est_cost),
        )
        waiter.granted_event = asyncio.Event()
        async with self._lock:
            if self._sched.try_grant_immediate(waiter):
                return
            self._sched.enqueue(waiter)
        started = time.monotonic()
        try:
            await asyncio.wait_for(waiter.granted_event.wait(), timeout=max_wait_s)
        except asyncio.TimeoutError:
            async with self._lock:
                removed = self._sched.cancel(waiter.seq)
                if not removed:
                    # cancel 找不到 → 已被 pop_next_grantable 判给（竞争窗口）
                    # → 槽位原样归还并唤醒下一个候补（无孤儿槽位）
                    self._return_slot_and_wake_locked(
                        session_id=session_id, small=small, actual_cost=0.0)
            raise QueueTimeoutError(self.name, time.monotonic() - started, session_id)

    async def release(self, *, session_id: str, small: bool,
                      actual_cost: float) -> None:
        async with self._lock:
            self._return_slot_and_wake_locked(
                session_id=session_id, small=small, actual_cost=actual_cost)

    def _return_slot_and_wake_locked(self, *, session_id: str, small: bool,
                                     actual_cost: float) -> None:
        """归还槽位 + 唤醒下一个公平候补（调用方必须已持锁）。"""
        self._sched.complete(session_id=session_id, small=small,
                             actual_cost=actual_cost)
        nxt = self._sched.pop_next_grantable()
        if nxt is not None and nxt.granted_event is not None:
            nxt.granted_event.set()


class _SessionGate:
    """会话层闸：总并发（Semaphore）+ heavy 上限（Condition）。"""

    def __init__(self, concurrency: int, heavy_cap: int):
        self._sem = asyncio.Semaphore(concurrency)
        self._heavy_cond = asyncio.Condition()
        self._heavy_cap = max(1, heavy_cap)
        self._heavy_in_flight = 0

    async def acquire(self, *, heavy: bool) -> None:
        await self._sem.acquire()
        if not heavy:
            return
        try:
            async with self._heavy_cond:
                while self._heavy_in_flight >= self._heavy_cap:
                    await self._heavy_cond.wait()
                self._heavy_in_flight += 1
        except BaseException:
            self._sem.release()
            raise

    async def release(self, *, heavy: bool) -> None:
        if heavy:
            async with self._heavy_cond:
                self._heavy_in_flight = max(0, self._heavy_in_flight - 1)
                self._heavy_cond.notify_all()
        self._sem.release()


class BackpressureManager:
    """三层背压的持有者（每进程一个，由 governor facade 持有）。"""

    def __init__(self, *, global_heavy: int, raster: int, browser: int,
                 export: int, external: int, llm: int,
                 session_concurrency: int, session_heavy: int,
                 aging_threshold_s: float = 10.0,
                 small_bypass_threshold_s: float = 5.0):
        self._channels: Dict[str, _ChannelGate] = {
            "heavy": _ChannelGate("heavy", global_heavy, aging_threshold_s),
            "raster": _ChannelGate("raster", raster, aging_threshold_s),
            "browser": _ChannelGate("browser", browser, aging_threshold_s),
            "export": _ChannelGate("export", export, aging_threshold_s),
            "external": _ChannelGate("external", external, aging_threshold_s),
            "llm": _ChannelGate("llm", llm, aging_threshold_s),
        }
        self._session_concurrency = max(1, session_concurrency)
        self._session_heavy = max(1, session_heavy)
        #: 估时低于该阈值的任务可走通道的 small bypass 预留槽
        self._small_threshold_s = max(0.0, small_bypass_threshold_s)
        self._session_gates: Dict[str, _SessionGate] = {}
        self._sessions_lock = asyncio.Lock()

    # ── 观测 ─────────────────────────────────────────────────────────

    def channel_state(self) -> Dict[str, Tuple[int, int]]:
        return {name: (g.in_flight, g.waiting) for name, g in self._channels.items()}

    def session_count(self) -> int:
        return len(self._session_gates)

    def channel(self, name: str) -> Optional[_ChannelGate]:
        return self._channels.get(name)

    # ── 会话生命周期 ─────────────────────────────────────────────────

    async def drop_session(self, session_id: str) -> None:
        """会话关闭时摘除闸（有界性：会话表不随历史无限增长）。"""
        async with self._sessions_lock:
            self._session_gates.pop(session_id, None)

    async def _gate_for(self, session_id: str) -> _SessionGate:
        async with self._sessions_lock:
            gate = self._session_gates.get(session_id)
            if gate is None:
                gate = _SessionGate(self._session_concurrency, self._session_heavy)
                self._session_gates[session_id] = gate
            return gate

    # ── 组合获取/归还：session 闸 → 通道闸（固定顺序，无死锁）─────────

    async def acquire(self, *, session_id: str, resource_class: ResourceClass,
                      estimate: Optional[ResourceEstimate] = None,
                      priority: int = 1, weight: float = 1.0,
                      max_wait_s: float = 45.0) -> AcquireTicket:
        small = resource_class is ResourceClass.LIGHT
        channel_name = channel_for(resource_class)
        est_wall = 1.0
        if estimate is not None:
            est_wall = max(0.1, estimate.adjudged(Dimension.WALL_TIME_S))
        if channel_name and est_wall <= self._small_threshold_s:
            small = True   # 小估时任务享受 bypass 预留槽（防 heavy 队头阻塞）
        gate = await self._gate_for(session_id)
        heavy = resource_class is ResourceClass.HEAVY
        await gate.acquire(heavy=heavy)
        try:
            if channel_name:
                channel = self._channels[channel_name]
                await channel.acquire(
                    session_id, small=small, priority=priority,
                    est_cost=est_wall, weight=weight, max_wait_s=max_wait_s,
                )
            return AcquireTicket(
                channel=channel_name, session_id=session_id,
                heavy=heavy, small=small,
            )
        except BaseException:
            await gate.release(heavy=heavy)
            raise

    async def release(self, ticket: AcquireTicket, *,
                      actual_cost: float = 1.0) -> None:
        """按获取的逆序归还：通道 → 会话。绝不抛（归还路径自吞）。"""
        try:
            if ticket.channel:
                channel = self._channels.get(ticket.channel)
                if channel is not None:
                    await channel.release(
                        session_id=ticket.session_id, small=ticket.small,
                        actual_cost=actual_cost,
                    )
        except Exception:  # noqa: BLE001 — 归还绝不阻断业务
            gmetrics.inc_internal_error("backpressure_release_channel")
        finally:
            try:
                gate = self._session_gates.get(ticket.session_id)
                if gate is not None:
                    await gate.release(heavy=ticket.heavy)
            except Exception:  # noqa: BLE001
                gmetrics.inc_internal_error("backpressure_release_session")

    async def drain(self, *, timeout_s: float = 30.0) -> bool:
        """测试/关闭用：等待全部通道在飞清零（有界）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if all(g.in_flight == 0 for g in self._channels.values()):
                return True
            await asyncio.sleep(0.05)
        return False


__all__ = [
    "QueueTimeoutError",
    "AcquireTicket",
    "BackpressureManager",
    "channel_for",
]
