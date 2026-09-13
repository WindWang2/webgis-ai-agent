"""加权公平调度核心（R6，ADR-0182 D6）。

**纯决策核心 + 异步薄壳**：抢占选择、虚拟完成时间、aging、small-job bypass
全部是同步纯逻辑（可确定性单测）；异步唤醒只是事件通知。绝不做 FIFO 无脑
排队。

防饥饿三件套：
1. **加权公平**：``key = session_consumption / weight``（虚拟起始时间语义，
   消费多的会话排队尾）；
2. **aging**：等待时间线性折减 key（``-wait/aging_threshold * BOOST``），
   等久了的小会话终将越过独大会话；
3. **small-job bypass**：每通道预留 1 个槽位只给 light 小任务 —— 一个巨大
   raster 任务不再阻塞元数据查询。

priority（INTERACTIVE/NORMAL/BATCH）以加法惩罚参与 key（小者先）。
"""
from __future__ import annotations

import heapq
import itertools
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

#: aging 每 1×threshold 的等待折减量（虚拟时间单位）
AGING_BOOST = 1.0
#: 优先级惩罚（每级 priority 的 key 加项）
PRIORITY_PENALTY = 2.0
#: 会话消费的衰减系数（每次完成时消费 ×decay，防历史惩罚永久化）
_CONSUMPTION_DECAY = 0.5
#: 会话消费的绝对上限（防单会话天文数字把 key 空间拉爆）
_CONSUMPTION_CAP = 1e12


@dataclass(eq=False)
class FairWaiter:
    """一个排队候补（对应一次被 defer 的资源请求）。"""

    session_id: str
    weight: float = 1.0
    priority: int = 1                    # ExecutionPriority 值
    small: bool = False                  # light 小任务（bypass 资格）
    est_cost: float = 1.0                # 预估占用（虚拟时间记账用）
    enqueued_at: float = field(default_factory=time.monotonic)
    seq: int = field(default_factory=lambda: next(_SEQ))
    granted_event: Optional[object] = None   # asyncio.Event（异步壳注入）

    def effective_key(self, now: float, aging_threshold_s: float) -> float:
        """调度键（小者先）：虚拟起始时间 - aging 折减 + 优先级惩罚。"""
        wait = max(0.0, now - self.enqueued_at)
        aging = (wait / aging_threshold_s) * AGING_BOOST if aging_threshold_s > 0 else 0.0
        weight = max(1e-6, self.weight)
        base = self.consumption_snapshot / weight
        return base - aging + self.priority * PRIORITY_PENALTY

    #: 入队时的会话消费快照（公平基准点；防在队期间消费变化导致不公平插队）
    consumption_snapshot: float = 0.0


_SEQ = itertools.count(1)


class FairScheduler:
    """单通道的公平候补队列（同步核心；线程不安全 —— 异步壳负责串行化）。"""

    def __init__(self, capacity: int, *, aging_threshold_s: float = 10.0,
                 bypass_reserve: bool = True):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.aging_threshold_s = aging_threshold_s
        # small bypass：>1 容量时预留 1 槽只给 small 任务
        self._heavy_capacity = (capacity - 1) if (bypass_reserve and capacity > 1) else capacity
        self._in_flight = 0
        self._in_flight_small = 0
        self._heap: List[Tuple[float, int, FairWaiter]] = []
        self._membership: Dict[int, FairWaiter] = {}   # waiter.seq → waiter
        self._consumption: Dict[str, float] = {}       # session → 虚拟消费

    # ── 状态 ─────────────────────────────────────────────────────────

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def waiting(self) -> int:
        return len(self._membership)

    @property
    def heavy_capacity(self) -> int:
        return self._heavy_capacity

    def waiting_sessions(self) -> int:
        return len({w.session_id for w in self._membership.values()})

    # ── 准入决策（同步纯逻辑）────────────────────────────────────────

    def try_grant_immediate(self, waiter: FairWaiter) -> bool:
        """空槽即给（small 可能用到 bypass 预留槽）。"""
        if self._in_flight >= self.capacity:
            return False
        if not waiter.small and self._in_flight >= self._heavy_capacity:
            return False  # 只剩 bypass 预留槽 —— heavy/small 之外的不给
        self._admit(waiter)
        return True

    def enqueue(self, waiter: FairWaiter) -> int:
        """入队；返回当前队位（0 = 下一个可被执行的可能者，观测用）。"""
        waiter.consumption_snapshot = self._consumption.get(waiter.session_id, 0.0)
        key = waiter.effective_key(time.monotonic(), self.aging_threshold_s)
        heapq.heappush(self._heap, (key, waiter.seq, waiter))
        self._membership[waiter.seq] = waiter
        return self._position_of(waiter.seq)

    def pop_next_grantable(self, *, now: Optional[float] = None) -> Optional[FairWaiter]:
        """弹出一个可授予的候补。

        review P2 修复：队首是 heavy 且只剩 bypass 预留槽时，**继续向后
        扫描** small 候补（pop-and-stash），不再直接返回 None —— 已排队的
        small 任务与新到 small 一样能使用 bypass 槽（"小查询永不被 heavy
        队头阻塞"对队列内成员同样成立）。stash 有界 = 队列长度。
        """
        now = now if now is not None else time.monotonic()
        stash: List[Tuple[float, int, FairWaiter]] = []
        result: Optional[FairWaiter] = None
        while self._heap and result is None:
            key, seq, waiter = self._heap[0]
            if seq not in self._membership:
                heapq.heappop(self._heap)   # 已取消的陈旧条目
                continue
            if self._in_flight >= self.capacity:
                break   # 容量满：谁都给不了
            if waiter.small or self._in_flight < self._heavy_capacity:
                heapq.heappop(self._heap)
                self._membership.pop(seq, None)
                self._admit(waiter)
                result = waiter
                break
            # 队首 heavy 且只剩 bypass 槽 —— 暂存，继续找后面的 small
            stash.append(heapq.heappop(self._heap))
        for entry in stash:
            heapq.heappush(self._heap, entry)
        if result is not None:
            self._membership.pop(result.seq, None)
        return result

    def cancel(self, seq: int) -> bool:
        waiter = self._membership.pop(seq, None)
        return waiter is not None   # 堆内陈旧条目由 pop_next_grantable 惰性清理

    # ── 记账 ─────────────────────────────────────────────────────────

    def complete(self, *, session_id: str, small: bool = False,
                 actual_cost: float = 1.0) -> None:
        """归还一个在飞槽位并记账（会话消费衰减 + 钳顶）。"""
        self._in_flight = max(0, self._in_flight - 1)
        if small:
            self._in_flight_small = max(0, self._in_flight_small - 1)
        cur = self._consumption.get(session_id, 0.0)
        # 衰减 + 钳顶：历史消费只影响近期公平，不永久记账
        nxt = (cur + max(0.0, actual_cost)) * _CONSUMPTION_DECAY
        self._consumption[session_id] = min(nxt, _CONSUMPTION_CAP)

    def on_complete(self, waiter: FairWaiter, actual_cost: float = 1.0) -> None:
        """兼容入口：按 waiter 归还。"""
        self.complete(session_id=waiter.session_id, small=waiter.small,
                      actual_cost=actual_cost)

    def consumption_of(self, session_id: str) -> float:
        return self._consumption.get(session_id, 0.0)

    def next_key_preview(self) -> Optional[float]:
        """队首 key（观测；不含惰性清理语义）。"""
        if not self._heap:
            return None
        return self._heap[0][0]

    # ── 内部 ─────────────────────────────────────────────────────────

    def _admit(self, waiter: FairWaiter) -> None:
        self._in_flight += 1
        if waiter.small:
            self._in_flight_small += 1
        self._consumption[waiter.session_id] = min(
            self._consumption.get(waiter.session_id, 0.0) + waiter.est_cost,
            _CONSUMPTION_CAP,
        )

    def _position_of(self, seq: int) -> int:
        order = sorted(self._membership.keys())
        return order.index(seq) if seq in order else -1

    def is_mathematically_sane(self) -> bool:
        """不变量自检（chaos 测试用）：在飞 ≤ 容量；成员与堆一致。"""
        if self._in_flight > self.capacity:
            return False
        heap_seqs = {seq for _, seq, _ in self._heap}
        return set(self._membership.keys()) <= heap_seqs


def fair_share_snapshot(consumption: Dict[str, float]) -> Dict[str, float]:
    """会话消费 → 归一化公平份额（0-1；观测/压测断言用）。"""
    total = sum(consumption.values())
    if total <= 0:
        return {k: 0.0 for k in consumption}
    return {k: v / total for k, v in consumption.items() if v > 0}


def starvation_index(wait_s: float, threshold_s: float) -> float:
    """饥饿指数：wait / threshold（≥1 即进入 aging 加速区）。"""
    if threshold_s <= 0:
        return math.inf if wait_s > 0 else 0.0
    return wait_s / threshold_s


__all__ = [
    "AGING_BOOST",
    "PRIORITY_PENALTY",
    "FairWaiter",
    "FairScheduler",
    "fair_share_snapshot",
    "starvation_index",
]
