"""R9/R10 + facade + dispatch adapter 测试：取消硬保证、重试停摆、门面管线。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.governor.config import (
    GovernorConfig,
    load_manifest,
    reset_governor_config_for_tests,
)
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
from app.services.governor.dispatch_adapter import (
    GovernorDispatchAdapter,
    classify_tool,
)
from app.services.governor.governor import HarnessResourceGovernor
from app.services.governor.retry_budget import (
    DENY_CANCELLED,
    DENY_DEGRADE_TAKEN,
    DENY_SESSION_EXHAUSTED,
    RetryBudget,
)

_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "governor_budgets.json"


def _demand(session_id: str = "s1", *, rclass=ResourceClass.LIGHT,
            memory: float = 1e7, wall: float = 1.0,
            retry_class: RetryClass = None, attempt: int = 1) -> ResourceDemand:
    est = ResourceEstimate(resource_class=rclass, dims={
        Dimension.MEMORY_BYTES: DimValue.known(memory),
        Dimension.WALL_TIME_S: DimValue.known(wall),
    })
    return ResourceDemand(session_id=session_id, estimate=est,
                          retry_class=retry_class, attempt=attempt)


def _governor(**overrides) -> HarnessResourceGovernor:
    """干净的测试 governor：清掉 GOVERNOR_* env → 重置默认配置 → 覆盖。"""
    import os
    for key in list(os.environ):
        if key.startswith("GOVERNOR_"):
            os.environ.pop(key)
    reset_governor_config_for_tests()
    config = GovernorConfig()
    for key, val in overrides.items():
        setattr(config, key, val)
    return HarnessResourceGovernor(config, budgets=load_manifest(_MANIFEST))


class TestRetryBudget:
    def test_charge_until_exhausted(self):
        rb = RetryBudget(global_tokens=100, session_tokens=2)
        assert rb.retry_allowed("s1", RetryClass.TOOL) == (True, "allow")
        rb.charge("s1", RetryClass.TOOL)
        rb.charge("s1", RetryClass.TOOL)
        ok, why = rb.retry_allowed("s1", RetryClass.TOOL)
        assert not ok and why == DENY_SESSION_EXHAUSTED

    def test_cancel_stops_retry_hards(self):
        rb = RetryBudget(global_tokens=100, session_tokens=10)
        rb.cancel_session("s1")
        ok, why = rb.retry_allowed("s1", RetryClass.TOOL)
        assert not ok and why == DENY_CANCELLED
        # 取消后 charge 不再扣全局池
        before = rb.snapshot()["global_left"]
        rb.charge("s1", RetryClass.TOOL)
        assert rb.snapshot()["global_left"] == before

    def test_degrade_retry_mutex(self):
        rb = RetryBudget()
        ok, why = rb.retry_allowed("s1", RetryClass.HTTP,
                                   operation="op1", degrade_taken=True)
        assert not ok and why == DENY_DEGRADE_TAKEN
        # 同 (session, operation) 在互斥窗内 retry 一并被拒（不双发）
        ok, why2 = rb.retry_allowed("s1", RetryClass.HTTP,
                                    operation="op1", degrade_taken=False)
        assert not ok and why2 == DENY_DEGRADE_TAKEN
        # 不同 operation 不受牵连
        ok3, _ = rb.retry_allowed("s1", RetryClass.HTTP,
                                  operation="op2", degrade_taken=False)
        assert ok3

    def test_global_pool_shared_and_refundable(self):
        rb = RetryBudget(global_tokens=2, session_tokens=100)
        rb.charge("a", RetryClass.TOOL)
        rb.charge("b", RetryClass.HTTP)
        assert rb.retry_allowed("c", RetryClass.TOOL)[0] is False
        rb.refund_global(1)
        assert rb.retry_allowed("c", RetryClass.TOOL)[0] is True


class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancel_releases_pending_and_blocks_start(self):
        gov = _governor()
        decision, res, ticket = await gov.admit_and_reserve(_demand("s1"))
        assert decision.allowed and res is not None
        n = await gov.cancel_session("s1", CancelReason.USER_CANCEL)
        assert n == 1
        # pending 不启动
        assert gov.should_skip("s1") is True
        d2, r2, t2 = await gov.admit_and_reserve(_demand("s1"))
        assert not d2.allowed
        assert d2.reasons == ["session_cancelled"]
        await gov.close_session("s1")
        assert gov.should_skip("s1") is False

    @pytest.mark.asyncio
    async def test_cancel_releases_backpressure_slot(self):
        gov = _governor()
        d1, r1, t1 = await gov.admit_and_reserve(
            _demand("s1", rclass=ResourceClass.RASTER))
        held, _, _ = await gov.admit_and_reserve(
            _demand("s2", rclass=ResourceClass.RASTER))
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 2
        await gov.cancel_session("s1", CancelReason.SESSION_REPLACED)
        assert gov.snapshot()["channels"]["raster"]["in_flight"] == 1

    @pytest.mark.asyncio
    async def test_close_session_clears_retry_and_cancel(self):
        gov = _governor()
        gov.retries.charge("s9", RetryClass.TOOL)
        await gov.cancel_session("s9", CancelReason.TIMEOUT)
        await gov.close_session("s9")
        assert gov.retries.retry_allowed("s9", RetryClass.TOOL) == (True, "allow")


class TestFacadePipeline:
    @pytest.mark.asyncio
    async def test_accept_reserve_complete_roundtrip(self):
        gov = _governor()
        d, res, ticket = await gov.admit_and_reserve(_demand("s1", memory=5e7))
        assert d.decision is AdmissionDecision.ACCEPT
        assert res is not None and ticket is not None
        snap = gov.snapshot("s1")
        assert snap["budgets"]["session"]["live"]["memory_bytes"] == 5e7
        await gov.complete(res, ticket, actual={
            Dimension.MEMORY_BYTES: 6e7, Dimension.WALL_TIME_S: 2.0})
        snap = gov.snapshot("s1")
        assert snap["budgets"]["session"]["live"]["memory_bytes"] == 0.0
        assert snap["budgets"]["session"]["cumulative"]["wall_time_s"] == 2.0

    @pytest.mark.asyncio
    async def test_observe_mode_passes_through_rejection(self):
        import os
        os.environ["GOVERNOR_MODE"] = "observe"
        try:
            reset_governor_config_for_tests()
            from app.services.governor.config import GovernorMode
            config = GovernorConfig()
            assert config.mode is GovernorMode.OBSERVE
            budgets = load_manifest(_MANIFEST)
            budgets["session"] = budgets["session"].model_copy(
                update={"provisional": False})
            gov = HarnessResourceGovernor(config, budgets=budgets)
            d, res, ticket = await gov.admit_and_reserve(_demand("s1", memory=4e9))
            assert d.decision is AdmissionDecision.REJECT  # 决策诚实留痕
            assert res is not None                          # observe 放行
            await gov.complete(res, ticket)
            assert gov.snapshot("s1")["budgets"]["session"]["live"]["memory_bytes"] == 0.0
        finally:
            os.environ.pop("GOVERNOR_MODE", None)
            reset_governor_config_for_tests()

    @pytest.mark.asyncio
    async def test_queue_timeout_escalates_to_degrade(self):
        gov = _governor()
        gov.config.queue_max_wait_s = 0.2
        # 占满 export 通道（容量 1）
        d1, r1, t1 = await gov.admit_and_reserve(
            _demand("s0", rclass=ResourceClass.EXPORT))
        d2, r2, t2 = await gov.admit_and_reserve(
            _demand("s1", rclass=ResourceClass.EXPORT))
        assert not d2.allowed
        assert d2.decision is AdmissionDecision.DEGRADE
        assert any(r.startswith("queue_timeout:export") for r in d2.reasons)
        await gov.complete(r1, t1)

    @pytest.mark.asyncio
    async def test_retry_attempt_blocked_by_budget(self):
        gov = _governor()
        d, res, ticket = await gov.admit_and_reserve(
            _demand("s1", retry_class=RetryClass.TOOL, attempt=2))
        assert d.allowed  # 第一次重试有 token
        gov.retries.cancel_session("s1")
        d2, _, _ = await gov.admit_and_reserve(
            _demand("s1", retry_class=RetryClass.TOOL, attempt=2))
        assert not d2.allowed
        assert d2.decision is AdmissionDecision.REJECT
        assert d2.reasons == ["retry_budget:deny_cancelled"]

    @pytest.mark.asyncio
    async def test_internal_failure_fails_open(self):
        gov = _governor()
        async def boom():
            raise RuntimeError("ledger exploded")
        gov.ledger.projection_violations = boom  # monkey 破坏准入依赖
        d, res, ticket = await gov.admit_and_reserve(_demand("s1"))
        assert d.decision is AdmissionDecision.ACCEPT
        assert d.reasons == ["governor_fail_open"]


class TestDispatchAdapter:
    def test_classify_tool_closed_mapping(self):
        assert classify_tool("webgis_raster_zonal")[1] is ResourceClass.RASTER
        assert classify_tool("export_map_pdf")[1] is ResourceClass.EXPORT
        assert classify_tool("take_screenshot")[1] is ResourceClass.BROWSER
        assert classify_tool("compute_kde", cost="heavy")[1] is ResourceClass.HEAVY
        assert classify_tool("get_meta")[1] is ResourceClass.LIGHT

    @pytest.mark.asyncio
    async def test_adapter_executes_and_accounts(self):
        gov = _governor()
        adapter = GovernorDispatchAdapter(gov)
        calls = []

        async def inner():
            calls.append(1)
            return {"success": True, "value": 42}

        result = await adapter.run(
            tool_name="get_meta", tool_args={}, session_id="s1",
            dispatch_inner=inner)
        assert result["success"] is True
        assert calls == [1]
        snap = gov.snapshot("s1")
        assert snap["budgets"]["session"]["live"]["memory_bytes"] == 0.0
        assert snap["budgets"]["session"]["cumulative"]["wall_time_s"] > 0

    @pytest.mark.asyncio
    async def test_adapter_reject_does_not_execute(self):
        import os
        os.environ["GOVERNOR_MODE"] = "enforce"
        try:
            reset_governor_config_for_tests()
            from app.services.governor.config import GovernorConfig as GC
            budgets = load_manifest(_MANIFEST)
            budgets["session"] = budgets["session"].model_copy(
                update={"provisional": False,
                        "limits": {Dimension.MEMORY_BYTES: 1e8}})
            gov = HarnessResourceGovernor(GC(), budgets=budgets)
            # heavy 档元数据 → 先验 memory ~1.2GiB ≫ 1e8 上限 → 硬拒
            adapter = GovernorDispatchAdapter(
                gov, metadata_fn=lambda _n: {"cost": "heavy"})
            calls = []

            async def inner():
                calls.append(1)
                return {"success": True}

            result = await adapter.run(
                tool_name="heavy_raster_work", tool_args={}, session_id="s1",
                dispatch_inner=inner)
            assert calls == []
            assert result["success"] is False
            assert result["governor"]["decision"] == "reject"
            assert result["error"]  # 错误族形状（is_error_like_result 可识别）
            assert result["governor"]["suggestions"]
        finally:
            os.environ.pop("GOVERNOR_MODE", None)
            reset_governor_config_for_tests()

    @pytest.mark.asyncio
    async def test_adapter_inner_exception_still_releases(self):
        gov = _governor()
        adapter = GovernorDispatchAdapter(gov)

        async def inner():
            raise ValueError("tool exploded")

        with pytest.raises(ValueError):
            await adapter.run(tool_name="get_meta", tool_args={},
                              session_id="s1", dispatch_inner=inner)
        snap = gov.snapshot("s1")
        assert snap["budgets"]["session"]["live"]["memory_bytes"] == 0.0
        assert snap["channels"]["heavy"]["in_flight"] == 0

    @pytest.mark.asyncio
    async def test_kill_switch_passthrough(self):
        gov = _governor()  # 先建 governor（helper 会清 GOVERNOR_* env）
        import os
        os.environ["GOVERNOR_TOOL_SURFACE"] = "0"
        try:
            adapter = GovernorDispatchAdapter(gov)
            calls = []

            async def inner():
                calls.append(1)
                return {"success": True}

            await adapter.run(tool_name="get_meta", tool_args={},
                              session_id="s1", dispatch_inner=inner)
            assert calls == [1]
            assert gov.snapshot("s1")["budgets"] == {}
        finally:
            os.environ.pop("GOVERNOR_TOOL_SURFACE", None)
