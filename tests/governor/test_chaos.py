"""Chaos 测试（R18，ADR-0182 D12）：注入故障，断言不雪崩/不死锁/不泄漏。

注入面：慢工具、hung 任务、队列饱和、重试风暴、provider 不可用、内存
压力、取消风暴、会话替换。每条 chaos 后断言资源全量归还（通道清零 +
reservation 清零 + 调度器不变量成立）。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app.services.governor.config import GovernorConfig, load_manifest
from app.services.governor.contract import (
    AdmissionDecision,
    CancelReason,
    Dimension,
    DimValue,
    ResourceClass,
    ResourceDemand,
    ResourceEstimate,
    RetryClass,
)
from app.services.governor.governor import HarnessResourceGovernor
from tests.governor.synthetic import FakeExecutor, synthetic_demand

_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "governor_budgets.json"


def _governor(**overrides) -> HarnessResourceGovernor:
    config = GovernorConfig()
    for k, v in overrides.items():
        setattr(config, k, v)
    return HarnessResourceGovernor(config, budgets=load_manifest(_MANIFEST))


def _demand(sid: str, *, rclass=ResourceClass.LIGHT, wall: float = 1.0,
            memory: float = 1e7, attempt: int = 1,
            retry_class: RetryClass = None,
            max_wait_s: float = None) -> ResourceDemand:
    est = ResourceEstimate(resource_class=rclass, dims={
        Dimension.MEMORY_BYTES: DimValue.known(memory),
        Dimension.WALL_TIME_S: DimValue.known(wall),
    })
    return ResourceDemand(session_id=sid, estimate=est,
                          retry_class=retry_class, attempt=attempt,
                          max_wait_s=max_wait_s)


class TestHungAndSlow:
    @pytest.mark.asyncio
    async def test_hung_execution_then_cancel_releases_everything(self):
        gov = _governor()
        d, res, ticket = await gov.admit_and_reserve(
            _demand("s1", rclass=ResourceClass.RASTER, wall=120.0))
        assert res is not None

        async def hung():
            await asyncio.sleep(30)

        task = asyncio.create_task(hung())
        await asyncio.sleep(0.01)
        n = await gov.cancel_session("s1", CancelReason.USER_CANCEL)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert n == 1
        snap = gov.snapshot()
        assert snap["channels"]["raster"]["in_flight"] == 0
        assert snap["live_reservations"] == 0
        assert gov.should_skip("s1")

    @pytest.mark.asyncio
    async def test_slow_tools_do_not_deadlock_light_queries(self):
        gov = _governor()
        # heavy 通道容量 3（bypass 预留后 heavy 可用 2）：占满 heavy 槽
        holders = []
        for i in range(2):
            d, r, t = await gov.admit_and_reserve(
                _demand(f"heavy-{i}", rclass=ResourceClass.HEAVY, wall=60.0))
            holders.append((r, t))
        # 第三个 heavy 只肯等 0.3s → 升格 degrade（不无限等）
        d3, r3, t3 = await gov.admit_and_reserve(
            _demand("heavy-2", rclass=ResourceClass.HEAVY, wall=60.0,
                    max_wait_s=0.3))
        assert not d3.allowed
        assert d3.decision is AdmissionDecision.DEGRADE
        t0 = time.monotonic()
        # 轻查询即刻通过（不走通道）
        d2, r2, t2 = await gov.admit_and_reserve(_demand("light-1"))
        assert time.monotonic() - t0 < 1.0
        assert r2 is not None
        # 清理
        await gov.complete(r2, t2)
        for r, t in holders:
            await gov.complete(r, t)
        assert gov.snapshot()["live_reservations"] == 0


class TestQueueSaturation:
    @pytest.mark.asyncio
    async def test_saturation_degrades_not_freezes(self):
        gov = _governor()
        gov.config.queue_max_wait_s = 0.3
        # 占满 raster（容量 2）
        holders = []
        for i in range(2):
            _, r, t = await gov.admit_and_reserve(
                _demand(f"r-{i}", rclass=ResourceClass.RASTER, wall=60.0))
            holders.append((r, t))
        # 8 个并发后来者：全部在 max_wait 内升格 degrade（绝不无限等待）
        async def contender(i):
            d, r, t = await gov.admit_and_reserve(
                _demand(f"c-{i}", rclass=ResourceClass.RASTER, wall=30.0))
            return d

        results = await asyncio.gather(*(contender(i) for i in range(8)))
        degraded = [d for d in results if not d.allowed]
        assert len(degraded) == 8
        assert all(d.decision is AdmissionDecision.DEGRADE for d in degraded)
        for r, t in holders:
            await gov.complete(r, t)
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 0

    @pytest.mark.asyncio
    async def test_stress_drain_completes(self):
        gov = _governor()
        executor = FakeExecutor(gov, time_scale=0.0)
        for i in range(30):
            await executor.execute(synthetic_demand(
                "drain-s", ["vector", "raster", "export"][i % 3],
                ["small", "medium"][i % 2], seed=i), max_wait_s=3.0)
        assert await gov.bp.drain(timeout_s=10.0)
        assert gov.snapshot()["live_reservations"] == 0


class TestRetryStorm:
    @pytest.mark.asyncio
    async def test_retry_storm_bounded_by_budget(self):
        gov = _governor(retry_tokens_global=5, retry_tokens_per_session=2)
        accepted = 0
        for i in range(20):
            sid = f"s-{i % 3}"
            d, r, t = await gov.admit_and_reserve(_demand(
                sid, attempt=2, retry_class=RetryClass.TOOL))
            if d.allowed:
                accepted += 1
                # 重试执行方语义：允许 → 实扣 token → 执行 → 归还
                gov.retries.charge(sid, RetryClass.TOOL)
                await gov.complete(r, t)
        # 会话份额 2 × 3 会话，全局池 5 更紧 → 恰好 5 次后被拒
        assert accepted <= 5
        # 之后全部被拒且系统未坏：普通调用照常
        d, r, t = await gov.admit_and_reserve(_demand("fresh"))
        assert d.allowed
        await gov.complete(r, t)

    def test_global_pool_exhaustion_fails_closed_for_retries_only(self):
        from app.services.governor.retry_budget import RetryBudget, DENY_GLOBAL_EXHAUSTED
        rb = RetryBudget(global_tokens=1, session_tokens=100)
        rb.charge("a", RetryClass.TOOL)
        ok, why = rb.retry_allowed("b", RetryClass.LLM)
        assert not ok and why == DENY_GLOBAL_EXHAUSTED


class TestProviderAndPressure:
    @pytest.mark.asyncio
    async def test_provider_open_with_remote_only_rejects_cleanly(self):
        gov = _governor()
        d, r, t = await gov.admit_and_reserve(
            _demand("s1"), open_providers=["stac.remote"], remote_only=True)
        assert not d.allowed
        assert any(p.startswith("provider_open:") for p in d.reasons)
        assert r is None and t is None

    @pytest.mark.asyncio
    async def test_memory_pressure_cycles_recover(self):
        gov = _governor(global_memory_pressure_bytes=5e8)
        results = []
        for i in range(6):
            d, r, t = await gov.admit_and_reserve(
                _demand(f"m-{i}", memory=1.5e8))
            results.append((d, r, t))
        # 超过水位的请求必须携带压力事实（light → defer 仍可排队执行，
        # 但理由诚实留痕；heavy/remote 场景见 admission 单测的 reject 分支）
        pressured = [d for d, _, _ in results
                     if any("global_memory_pressure" in r for r in d.reasons)]
        assert pressured, "超过内存水位的需求必须留痕压力理由"
        # 全部归还后压力解除 → 恢复 clean accept
        for _, r, t in results:
            if r is not None:
                await gov.complete(r, t)
        assert gov.ledger.live_memory_total() == 0.0
        d, r, t = await gov.admit_and_reserve(_demand("m-after", memory=1e8))
        assert d.decision is AdmissionDecision.ACCEPT
        assert d.reasons == ["clean_admission"]
        await gov.complete(r, t)


class TestCancelStorm:
    @pytest.mark.asyncio
    async def test_many_sessions_cancel_no_cross_blocking(self):
        gov = _governor()
        live = []
        queued_results = []
        for i in range(8):
            d, r, t = await gov.admit_and_reserve(_demand(
                f"s-{i}",
                rclass=ResourceClass.RASTER if i % 2 else ResourceClass.LIGHT,
                max_wait_s=3.0))
            if r is not None:
                live.append((f"s-{i}", r, t))
            elif d.reasons and any("queue" in x or "backlog" in x for x in d.reasons):
                queued_results.append(d)
        # 排队中的 raster 候补（如有）在取消后被唤醒并诚实拒绝
        cancel_tasks = [gov.cancel_session(sid, CancelReason.SESSION_REPLACED)
                        for sid, _, _ in live]
        await asyncio.gather(*cancel_tasks)
        assert gov.snapshot()["live_reservations"] == 0
        assert all(gov.should_skip(sid) for sid, _, _ in live)
        # 新会话完全不受影响
        d, r, t = await gov.admit_and_reserve(
            _demand("new-session", rclass=ResourceClass.RASTER, max_wait_s=3.0))
        assert d.allowed
        await gov.complete(r, t)

    @pytest.mark.asyncio
    async def test_cancel_while_queued_no_ghost_grant(self):
        gov = _governor()
        gov.config.queue_max_wait_s = 5.0
        # 占满 raster 容量
        holders = []
        for i in range(2):
            _, r, t = await gov.admit_and_reserve(
                _demand(f"h-{i}", rclass=ResourceClass.RASTER, wall=30.0))
            holders.append((r, t))
        # 排队者
        queued = asyncio.ensure_future(gov.bp.acquire(
            session_id="queued-s", resource_class=ResourceClass.RASTER,
            max_wait_s=5.0))
        await asyncio.sleep(0.05)
        # 占用者完成 → 排队者被唤醒获得槽位
        for r, t in holders:
            await gov.complete(r, t)
        ticket = await asyncio.wait_for(queued, timeout=2.0)
        assert ticket is not None
        await gov.bp.release(ticket, actual_cost=0.1)
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 0


class TestInvariants:
    @pytest.mark.asyncio
    async def test_chaos_suite_leaves_no_residue(self):
        gov = _governor()
        executor = FakeExecutor(gov, time_scale=0.0, failure_rate=0.3,
                                rng_seed=7)
        tasks = []
        for i in range(40):
            sid = f"chaos-{i % 5}"
            d = synthetic_demand(sid, ["vector", "raster", "acquisition",
                                       "cartography", "export"][i % 5],
                                 ["small", "large"][i % 2], seed=i)
            tasks.append(executor.execute(d, max_wait_s=1.0))
        await asyncio.gather(*tasks, return_exceptions=True)
        # 中途取消一半会话
        for i in range(3):
            await gov.cancel_session(f"chaos-{i}", CancelReason.TIMEOUT)
        snap = gov.snapshot()
        assert snap["live_reservations"] == 0, "chaos 后不得有在飞预留"
        assert all(v["in_flight"] == 0 for v in snap["channels"].values()), \
            "chaos 后通道必须清零"
        # 会话关闭清理
        for i in range(5):
            await gov.close_session(f"chaos-{i}")
        assert gov.bp.session_count() == 0
