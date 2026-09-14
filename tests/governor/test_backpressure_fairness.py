"""R4/R5/R6 单测：公平调度核心、三层背压、三级账本。"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.services.governor.backpressure import (
    BackpressureManager,
    QueueTimeoutError,
    channel_for,
)
from app.services.governor.contract import (
    Dimension,
    DimValue,
    ResourceClass,
    ResourceDemand,
    ResourceEstimate,
    ResourceReservation,
)
from app.services.governor.fairness import (
    FairScheduler,
    FairWaiter,
    fair_share_snapshot,
    starvation_index,
)
from app.services.governor.session_budget import (
    SessionBudgetLedger,
)
from app.services.governor.config import load_manifest
from pathlib import Path

_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "governor_budgets.json"


def _waiter(sid: str, *, small: bool = False, priority: int = 1,
            weight: float = 1.0, est: float = 1.0,
            consumption: float = 0.0) -> FairWaiter:
    w = FairWaiter(session_id=sid, small=small, priority=priority,
                   weight=weight, est_cost=est)
    w.consumption_snapshot = consumption
    return w


class TestFairScheduler:
    def test_capacity_grants_and_waits(self):
        s = FairScheduler(2, bypass_reserve=False)  # 全容量给 non-small
        assert s.try_grant_immediate(_waiter("a"))
        assert s.try_grant_immediate(_waiter("b"))
        assert not s.try_grant_immediate(_waiter("c"))
        assert s.in_flight == 2

    def test_heavy_blocked_by_bypass_reserve_small_gets_last_slot(self):
        s = FairScheduler(2)  # heavy_capacity = 1
        assert s.try_grant_immediate(_waiter("a"))           # heavy slot
        assert not s.try_grant_immediate(_waiter("b"))       # heavy blocked
        assert s.try_grant_immediate(_waiter("c", small=True))  # bypass slot
        assert s.in_flight == 2

    def test_aging_lets_starved_session_win(self):
        s = FairScheduler(1, aging_threshold_s=1.0)
        s.try_grant_immediate(_waiter("hog"))
        old = _waiter("patient")
        old.enqueued_at = time.monotonic() - 50 * s.aging_threshold_s
        fresh = _waiter("fresh_heavy", weight=10.0)
        s.enqueue(old)
        s.enqueue(fresh)
        s.complete(session_id="hog")
        winner = s.pop_next_grantable()
        assert winner is old  # aging 折减战胜高权重新客

    def test_weighted_fairness_order(self):
        s = FairScheduler(1, aging_threshold_s=1e9)  # 关闭 aging，纯权重
        w_low = _waiter("s1", weight=1.0)
        w_high = _waiter("s2", weight=10.0)
        s.try_grant_immediate(_waiter("seed"))
        s.enqueue(w_low)
        s.enqueue(w_high)
        s.complete(session_id="seed")
        first = s.pop_next_grantable()
        assert first is w_high  # 高权重虚拟起始时间更早

    def test_priority_penalizes_batch(self):
        s = FairScheduler(1)
        batch = _waiter("batch", priority=2)
        interactive = _waiter("ia", priority=0)
        s.enqueue(batch)
        s.enqueue(interactive)
        assert s.pop_next_grantable() is interactive

    def test_cancel_removes_waiter_no_ghost_wake(self):
        s = FairScheduler(1)
        s.try_grant_immediate(_waiter("a"))
        w = _waiter("b")
        s.enqueue(w)
        assert s.cancel(w.seq) is True
        s.complete(session_id="a")
        assert s.pop_next_grantable() is None
        assert s.waiting == 0

    def test_invariant_sanity(self):
        s = FairScheduler(3)
        for i in range(5):
            if not s.try_grant_immediate(_waiter(f"s{i}")):
                s.enqueue(_waiter(f"s{i}"))
        assert s.is_mathematically_sane()
        for i in range(5):
            w = s.pop_next_grantable()
            if w is None:
                break
        assert s.is_mathematically_sane()

    def test_fair_share_and_starvation_index(self):
        share = fair_share_snapshot({"a": 3.0, "b": 1.0})
        assert share["a"] == pytest.approx(0.75)
        assert starvation_index(30.0, 10.0) == pytest.approx(3.0)


class TestChannelFor:
    def test_closed_mapping(self):
        assert channel_for(ResourceClass.RASTER) == "raster"
        assert channel_for(ResourceClass.BROWSER) == "browser"
        assert channel_for(ResourceClass.EXPORT) == "export"
        assert channel_for(ResourceClass.LLM) == "llm"
        assert channel_for(ResourceClass.HEAVY) == "heavy"
        assert channel_for(ResourceClass.LIGHT) == ""
        assert channel_for(ResourceClass.MEDIUM) == ""


@pytest.mark.asyncio
class TestBackpressureManager:
    async def _mgr(self, **kw) -> BackpressureManager:
        base = dict(global_heavy=2, raster=1, browser=1, export=1,
                    external=4, llm=4, session_concurrency=4,
                    session_heavy=1, aging_threshold_s=10.0)
        base.update(kw)
        return BackpressureManager(**base)

    async def test_light_passes_without_channel(self):
        m = await self._mgr()
        t = await m.acquire(session_id="s1", resource_class=ResourceClass.LIGHT)
        assert t.channel == ""
        await m.release(t)

    async def test_raster_channel_blocks_second(self):
        m = await self._mgr()
        t1 = await m.acquire(session_id="s1", resource_class=ResourceClass.RASTER)
        task_started = asyncio.Event()

        async def second():
            task_started.set()
            return await m.acquire(session_id="s2", resource_class=ResourceClass.RASTER,
                                   max_wait_s=0.3)

        task = asyncio.create_task(second())
        await task_started.wait()
        await asyncio.sleep(0.1)
        with pytest.raises(QueueTimeoutError):
            await task
        await m.release(t1)
        assert m.channel_state()["raster"] == (0, 0)

    async def test_session_heavy_cap_enforced(self):
        # global_heavy=3 → bypass 预留后 heavy 可用 2：两个 heavy 可入，
        # 第三个（另一会话）在 0.2s 内拿不到
        m = await self._mgr(global_heavy=3)
        t1 = await m.acquire(session_id="s1", resource_class=ResourceClass.HEAVY,
                             estimate=_estimate(wall=60.0))
        t2 = await m.acquire(session_id="s2", resource_class=ResourceClass.HEAVY,
                             estimate=_estimate(wall=60.0))
        assert m.channel_state()["heavy"] == (2, 0)
        with pytest.raises(QueueTimeoutError):
            await m.acquire(session_id="s3", resource_class=ResourceClass.HEAVY,
                            estimate=_estimate(wall=60.0), max_wait_s=0.2)
        await m.release(t1)
        await m.release(t2)

    async def test_small_bypass_not_starved_by_heavy(self):
        # raster 容量=2 → heavy 可用 1、bypass 预留 1：
        # 大估时 raster 占住 heavy 槽后，第二个大 raster 拿不到槽，
        # 但小估时 raster 立即经 bypass 槽通过（不被 heavy 队头阻塞）。
        m = await self._mgr(raster=2)
        big = await m.acquire(session_id="s1", resource_class=ResourceClass.RASTER,
                              estimate=_estimate(wall=120.0))
        other_big = asyncio.Event()

        async def second_big():
            other_big.set()
            return await m.acquire(session_id="s2", resource_class=ResourceClass.RASTER,
                                   estimate=_estimate(wall=120.0), max_wait_s=0.2)

        task = asyncio.create_task(second_big())
        await other_big.wait()
        await asyncio.sleep(0.05)
        small_t = await m.acquire(session_id="s3", resource_class=ResourceClass.RASTER,
                                  estimate=_estimate(wall=0.5), max_wait_s=1.0)
        with pytest.raises(QueueTimeoutError):
            await task
        await m.release(small_t)
        await m.release(big)

    async def test_release_restores_slots(self):
        m = await self._mgr()
        t = await m.acquire(session_id="s1", resource_class=ResourceClass.EXPORT)
        await m.release(t)
        t2 = await m.acquire(session_id="s1", resource_class=ResourceClass.EXPORT)
        await m.release(t2)

    async def test_session_gate_released_on_channel_timeout(self):
        m = await self._mgr()
        blocker = await m.acquire(session_id="other", resource_class=ResourceClass.EXPORT)
        with pytest.raises(QueueTimeoutError):
            await m.acquire(session_id="s1", resource_class=ResourceClass.EXPORT,
                            max_wait_s=0.2)
        # 会话闸槽位必须已归还（再占不阻塞 —— 若泄漏则 heavy 通道最终饿死）
        t = await m.acquire(session_id="s1", resource_class=ResourceClass.LIGHT)
        await m.release(t)
        await m.release(blocker)

    async def test_drop_session_bounded(self):
        m = await self._mgr()
        for i in range(5):
            await m.acquire(session_id=f"s{i}", resource_class=ResourceClass.LIGHT)
        for i in range(5):
            await m.drop_session(f"s{i}")
        assert m.session_count() == 0


def _estimate(*, wall: float) -> ResourceEstimate:
    """带墙钟估值的 estimate（small bypass 判定输入）。"""
    return ResourceEstimate(dims={Dimension.WALL_TIME_S: DimValue.known(wall)})


def _demand(session_id: str, *, rclass: ResourceClass = ResourceClass.LIGHT,
            turn_id: str = "", goal_id: str = "",
            memory: float = 1e8) -> ResourceDemand:
    est = ResourceEstimate(resource_class=rclass, dims={
        Dimension.MEMORY_BYTES: DimValue.known(memory),
    })
    return ResourceDemand(session_id=session_id, turn_id=turn_id,
                          goal_id=goal_id, estimate=est)


class TestSessionBudgetLedger:
    def _ledger(self, **overrides):
        budgets = load_manifest(_MANIFEST)
        return SessionBudgetLedger(budgets)

    def test_projection_clean_under_budget(self):
        led = self._ledger()
        v = led.projection_violations(
            _demand("s1", memory=1e8),
            {Dimension.MEMORY_BYTES: 1e8},
        )
        assert v == []

    def test_projection_flags_session_memory(self):
        led = self._ledger()
        v = led.projection_violations(
            _demand("s1", memory=4e9),
            {Dimension.MEMORY_BYTES: 4e9},
        )
        assert v and all(x.check == "dim" for x in v)
        assert any(x.scope == "session" for x in v)

    def test_live_release_frees_cumulative_keeps(self):
        led = self._ledger()
        res = ResourceReservation(
            session_id="s1", turn_id="t1",
            resource_class=ResourceClass.LIGHT,
            charged={Dimension.MEMORY_BYTES: 1e8},
        )
        led.reserve(res, {Dimension.MEMORY_BYTES: 1e8})
        snap = led.snapshot("s1", turn_id="t1")
        assert snap["session"]["live"][Dimension.MEMORY_BYTES.value] == 1e8
        led.release(res, actual={Dimension.MEMORY_BYTES: 1e8,
                                 Dimension.WALL_TIME_S: 12.0})
        snap = led.snapshot("s1", turn_id="t1")
        assert snap["session"]["live"][Dimension.MEMORY_BYTES.value] == 0.0
        # wall_time 是 cumulative —— release 不回滚反而入账
        assert snap["session"]["cumulative"][Dimension.WALL_TIME_S.value] == 12.0

    def test_cumulative_accumulates_across_reservations(self):
        led = self._ledger()
        led.record_context_tokens("s1", "t1", 5000)
        led.record_context_tokens("s1", "t2", 7000)
        snap = led.snapshot("s1")
        assert snap["session"]["cumulative"][Dimension.CONTEXT_TOKENS.value] == 12000

    def test_provisional_budgets_flagged(self):
        led = self._ledger()
        v = led.projection_violations(
            _demand("s1", memory=9e9), {Dimension.MEMORY_BYTES: 9e9})
        assert v
        assert all(x.provisional for x in v)

    def test_active_sessions_counted(self):
        led = self._ledger()
        led.record_context_tokens("s-a", "", 10)
        led.record_context_tokens("s-b", "", 10)
        assert led.active_session_count() >= 2
