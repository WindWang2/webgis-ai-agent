"""Review 修复回归（M7）：P0 observe 端到端、P1 取消/迟到 complete 双归还、
P1 排队任务取消泄漏、公平 bypass 对已排队 small 的覆盖。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.services.governor.backpressure import QueueTimeoutError
from app.services.governor.config import (
    GovernorConfig,
    GovernorMode,
    load_manifest,
    reset_governor_config_for_tests,
)
from app.services.governor.contract import (
    AdmissionDecision,
    CancelReason as CancelReasonLocal,
    Dimension,
    DimValue,
    ResourceClass,
    ResourceDemand,
    ResourceEstimate,
)
from app.services.governor.dispatch_adapter import GovernorDispatchAdapter
from app.services.governor.governor import HarnessResourceGovernor

_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "governor_budgets.json"


def _config(**overrides) -> GovernorConfig:
    config = GovernorConfig()
    for k, v in overrides.items():
        setattr(config, k, v)
    return config


def _raster_estimate(*, wall: float) -> ResourceEstimate:
    return ResourceEstimate(resource_class=ResourceClass.RASTER, dims={
        Dimension.MEMORY_BYTES: DimValue.known(1e7),
        Dimension.WALL_TIME_S: DimValue.known(wall)})


def _demand(sid: str, *, rclass=ResourceClass.LIGHT, memory: float = 1e7,
            wall: float = 1.0) -> ResourceDemand:
    est = ResourceEstimate(resource_class=rclass, dims={
        Dimension.MEMORY_BYTES: DimValue.known(memory),
        Dimension.WALL_TIME_S: DimValue.known(wall),
    })
    return ResourceDemand(session_id=sid, estimate=est)


class TestObserveEndToEnd:
    @pytest.mark.asyncio
    async def test_adapter_executes_and_completes_in_observe_mode(self):
        """P0 回归：observe 模式下 adapter 必须执行且必须 complete。

        旧缺陷：adapter 只看 decision.allowed（不含模式）→ observe 下的
        reject 照样拦截 + reservation/ticket 被丢弃 → 槽位永久泄漏。
        """
        os.environ["GOVERNOR_MODE"] = "observe"
        try:
            reset_governor_config_for_tests()
            assert _config().mode is GovernorMode.OBSERVE
            budgets = load_manifest(_MANIFEST)
            budgets["session"] = budgets["session"].model_copy(
                update={"provisional": False,
                        "limits": {Dimension.MEMORY_BYTES: 1e8}})
            gov = HarnessResourceGovernor(_config(), budgets=budgets)
            adapter = GovernorDispatchAdapter(
                gov, metadata_fn=lambda _n: {"cost": "heavy"})
            calls = []

            async def inner():
                calls.append(1)
                return {"success": True}

            result = await adapter.run(
                tool_name="heavy_raster_work", tool_args={}, session_id="obs-1",
                dispatch_inner=inner)
            # observe：决策留痕为 reject，但执行照常、槽位如数归还
            assert calls == [1]
            assert result["success"] is True
            snap = gov.snapshot("obs-1")
            assert snap["live_reservations"] == 0
            assert snap["channels"]["heavy"]["in_flight"] == 0
            assert snap["budgets"]["session"]["live"]["memory_bytes"] == 0.0
        finally:
            os.environ.pop("GOVERNOR_MODE", None)
            reset_governor_config_for_tests()


class TestReleaseIdempotency:
    @pytest.mark.asyncio
    async def test_cancel_then_late_complete_no_double_release(self):
        """P1 回归：cancel 后迟到的 complete 不得二次归还通道槽位。"""
        gov = HarnessResourceGovernor(_config(), budgets=load_manifest(_MANIFEST))
        d1, r1, t1 = await gov.admit_and_reserve(
            _demand("a", rclass=ResourceClass.RASTER))
        d2, r2, t2 = await gov.admit_and_reserve(
            _demand("b", rclass=ResourceClass.RASTER))
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 2
        await gov.cancel_session("a", CancelReasonLocal.USER_CANCEL)
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 1
        # 迟到的 complete（adapter 形态）：不得把 b 的槽位也放掉
        await gov.complete(r1, t1)
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 1
        await gov.complete(r2, t2)
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 0

    @pytest.mark.asyncio
    async def test_double_complete_is_idempotent(self):
        gov = HarnessResourceGovernor(_config(), budgets=load_manifest(_MANIFEST))
        d, r, t = await gov.admit_and_reserve(_demand("s1"))
        await gov.complete(r, t)
        snap1 = gov.snapshot("s1")
        await gov.complete(r, t)   # 重入
        snap2 = gov.snapshot("s1")
        assert (snap1["budgets"]["session"]["live"]["memory_bytes"]
                == snap2["budgets"]["session"]["live"]["memory_bytes"] == 0.0)
        assert snap2["channels"]["heavy"]["in_flight"] == 0


class TestQueuedCancellation:
    @pytest.mark.asyncio
    async def test_task_cancel_while_queued_no_slot_leak(self):
        """P1 回归：排队中的调用方任务被 cancel → 候补出队，槽位不泄漏。"""
        gov = HarnessResourceGovernor(_config(), budgets=load_manifest(_MANIFEST))
        # 占满 raster 全部容量（容量 2 = heavy 槽 1 + bypass 槽 1）
        hog = await gov.bp.acquire(session_id="hog",
                                   resource_class=ResourceClass.RASTER,
                                   estimate=_raster_estimate(wall=120.0),
                                   max_wait_s=1.0)
        bypass_holder = await gov.bp.acquire(
            session_id="bh", resource_class=ResourceClass.RASTER,
            estimate=_raster_estimate(wall=0.5), max_wait_s=1.0)
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 2
        waiter = asyncio.ensure_future(gov.bp.acquire(
            session_id="w", resource_class=ResourceClass.RASTER,
            estimate=_raster_estimate(wall=120.0),
            max_wait_s=5.0))
        await asyncio.sleep(0.05)
        assert gov.snapshot()["channels"]["raster"]["waiting"] == 1
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert gov.snapshot()["channels"]["raster"]["waiting"] == 0
        # 归还全部持有者：没有任何幽灵占用
        await gov.bp.release(hog, actual_cost=0.1)
        await gov.bp.release(bypass_holder, actual_cost=0.1)
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 0


class TestBypassForQueuedSmalls:
    def test_pop_next_grantable_skips_heavy_head_for_small(self):
        """P2 回归：队首 heavy 只剩 bypass 槽时，后面的 small 应被授予。"""
        from app.services.governor.fairness import FairScheduler, FairWaiter

        s = FairScheduler(2)  # heavy_capacity = 1, bypass = 1
        assert s.try_grant_immediate(FairWaiter(session_id="hog"))
        assert s.in_flight == 1          # heavy 槽已占满
        heavy = FairWaiter(session_id="h2")
        small = FairWaiter(session_id="s2", small=True)
        s.enqueue(heavy)
        s.enqueue(small)
        got = s.pop_next_grantable()
        assert got is small               # 队首 heavy 被跳过，small 走 bypass
        assert s.in_flight == 2
        s.complete(session_id="s2", small=True)
        # hog 仍占 heavy 槽：归还的 bypass 槽对 heavy 不可用 → 诚实 None
        got2 = s.pop_next_grantable()
        assert got2 is None
        assert s.waiting == 1             # heavy 仍在队
        s.complete(session_id="hog", small=False)
        got3 = s.pop_next_grantable()
        assert got3 is heavy
        s.complete(session_id="h2", small=False)
        assert s.is_mathematically_sane()


class TestFailOpenNoLeak:
    @pytest.mark.asyncio
    async def test_fail_open_after_acquire_releases_ticket(self):
        """P1 回归：acquire 之后的内部异常必须先归还再 fail-open。"""
        gov = HarnessResourceGovernor(_config(), budgets=load_manifest(_MANIFEST))

        async def boom():
            raise RuntimeError("ledger exploded")

        d = _demand("s1", rclass=ResourceClass.RASTER)
        # 破坏 build_reservation 之后的记账路径
        gov.ledger.reserve = boom  # type: ignore[method-assign]
        dec, r, t = await gov.admit_and_reserve(d)
        assert dec.decision is AdmissionDecision.ACCEPT
        assert dec.reasons == ["governor_fail_open"]
        # fail-open 放行但槽位已清 —— 通道不为幽灵占用
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 0


class TestObserveQueueTimeoutNoFakeTicket:
    @pytest.mark.asyncio
    async def test_observe_queue_timeout_returns_no_ticket(self):
        """P2 回归：observe 排队超时不得伪造空 channel ticket。

        旧缺陷：超时后虚构的 ticket 仍被 complete 交给 bp.release；它会
        按 session_id 找到会话闸，对从未占用的会话槽二次归还 semaphore，
        凭空放大会话并发容量。
        """
        os.environ["GOVERNOR_MODE"] = "observe"
        try:
            reset_governor_config_for_tests()
            config = _config(session_concurrency=1)
            assert config.mode is GovernorMode.OBSERVE
            gov = HarnessResourceGovernor(config, budgets=load_manifest(_MANIFEST))
            # 占住会话唯一并发槽：LIGHT 无通道闸，排队超时发生在 session 层
            holder = await gov.bp.acquire(
                session_id="obs-slow", resource_class=ResourceClass.LIGHT,
                max_wait_s=1.0)
            demand = _demand("obs-slow").model_copy(update={"max_wait_s": 0.05})
            dec, res, ticket = await gov.admit_and_reserve(demand)
            assert res is not None
            assert ticket is None            # 未获得槽位 → 无票可归还
            await gov.complete(res, ticket)
            # 会话容量仍被 holder 独占：再来一个必须超时（而非被放大）
            with pytest.raises(QueueTimeoutError):
                await gov.bp.acquire(
                    session_id="obs-slow", resource_class=ResourceClass.LIGHT,
                    max_wait_s=0.05)
            await gov.bp.release(holder)
        finally:
            os.environ.pop("GOVERNOR_MODE", None)
            reset_governor_config_for_tests()


class _InjectedFatal(BaseException):
    pass


class TestRegistrationCleanup:
    @pytest.mark.asyncio
    async def test_base_exception_after_registration_leaves_no_ghost(self):
        """P2 回归：登记录入后遭遇 BaseException 必须摘除 live 表。

        旧清理路径只归还 ledger/背压，却把 reservation 留在 live 表；
        随后 cancel_session/complete 会对同一账本与槽位二次归还，放大
        通道容量。
        """
        gov = HarnessResourceGovernor(_config(), budgets=load_manifest(_MANIFEST))
        original = gov._sync_gauges

        def boom():
            raise _InjectedFatal()

        gov._sync_gauges = boom  # type: ignore[method-assign]
        try:
            with pytest.raises(_InjectedFatal):
                await gov.admit_and_reserve(
                    _demand("s1", rclass=ResourceClass.RASTER))
        finally:
            gov._sync_gauges = original  # type: ignore[method-assign]
        assert gov.snapshot()["live_reservations"] == 0
        # 无幽灵预留 → cancel_session 无可释放，通道不会二次归还
        assert await gov.cancel_session(
            "s1", CancelReasonLocal.USER_CANCEL) == 0
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 0


class TestManifestFailOpen:
    def test_unreadable_manifest_starts_empty(self, monkeypatch):
        """P3 回归：manifest 权限/目录类 OSError 也 fail-open（空预算表）。"""
        import app.services.governor.config as config_mod

        def boom(path=None):
            raise PermissionError("manifest unreadable")

        monkeypatch.setattr(config_mod, "load_manifest", boom)
        gov = HarnessResourceGovernor(_config())
        assert gov.ledger.budget_for("session") is None


class TestCompleteBooksUnchargedActualDims:
    @pytest.mark.asyncio
    async def test_actual_dim_absent_from_charged_is_booked(self):
        """P3 回归：actual 中未出现在 charged 的维也要入累计账。"""
        gov = HarnessResourceGovernor(_config(), budgets=load_manifest(_MANIFEST))
        est = ResourceEstimate(resource_class=ResourceClass.LIGHT, dims={
            Dimension.WALL_TIME_S: DimValue.known(1.0)})
        demand = ResourceDemand(session_id="net-1", estimate=est)
        _, res, ticket = await gov.admit_and_reserve(demand)
        await gov.complete(res, ticket, actual={
            Dimension.WALL_TIME_S: 1.0,
            Dimension.NETWORK_BYTES: 4096.0,
        })
        snap = gov.snapshot("net-1")
        assert (snap["budgets"]["session"]["cumulative"]["network_bytes"]
                == 4096.0)


class TestStoragePressureSnapshotConsistency:
    def test_pressure_and_level_come_from_one_reading(self):
        """P3 回归：snapshot 的 pressure/level 必须同一读（TTL 到期不重取）。"""
        from app.services.governor.storage_pressure import StoragePressureView

        readings = iter([(1, 10), (10, 10)])
        calls = {"n": 0}

        def provider():
            calls["n"] += 1
            return next(readings)

        view = StoragePressureView(provider=provider, cache_ttl_s=-1.0)
        snap = view.snapshot()
        assert snap["pressure"] == pytest.approx(0.1)
        assert snap["level"] == "low"
        assert snap["known"] is True
        assert calls["n"] == 1


class TestStoragePressureReadLock:
    def test_provider_call_does_not_hold_cache_lock(self):
        """P2 回归：慢 provider 不得在持锁状态下调用。

        provider 默认实现汇总 artifact 目录；持锁调用会让所有 pressure()/
        level() 读取者串行阻塞，与「绝不阻塞执行路径」的承诺相悖。
        """
        import threading
        import time as _time

        from app.services.governor.storage_pressure import StoragePressureView

        started = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def provider():
            calls["n"] += 1
            if calls["n"] == 1:
                started.set()
                release.wait(1.0)
            return (1, 10)

        view = StoragePressureView(provider=provider, cache_ttl_s=5.0)
        slow = threading.Thread(target=view.pressure, daemon=True)
        slow.start()
        assert started.wait(1.0)
        began = _time.monotonic()
        try:
            assert view.level() == "low"
        finally:
            release.set()
            slow.join(2.0)
        assert _time.monotonic() - began < 0.25
