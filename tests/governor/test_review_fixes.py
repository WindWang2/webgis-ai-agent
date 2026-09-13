"""Review 修复回归（M7）：P0 observe 端到端、P1 取消/迟到 complete 双归还、
P1 排队任务取消泄漏、公平 bypass 对已排队 small 的覆盖。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

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
