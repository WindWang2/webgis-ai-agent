"""dispatch ↔ provider 健康面接线对抗测试(H05)。

覆盖:结果回填分类(凭证/权限失败不计熔断;timeout 计入)/ 异常回填 /
断路 OPEN 的 bind 拒绝支(typed PROVIDER_UNAVAILABLE,须有健康替代)/
无替代放行(诚实执行,不新增 outage 面)/ 执行前 fail-fast(dedup 槽
诚实释放)/ 取消归还 trial / kill switch 全链直通。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.tool_dispatch_service import ToolDispatchService

from app.services.capability_runtime.dispatch_recording import (
    PROVIDER_UNAVAILABLE_CODE,
    record_dispatch_outcome,
)
from app.services.capability_runtime.health import (
    ProviderFailureClass,
    ProviderHealthRegistry,
    set_provider_health_registry,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


@pytest.fixture()
def health():
    clock = _FakeClock()
    reg = ProviderHealthRegistry(clock=clock)
    set_provider_health_registry(reg)
    reg._clock = clock  # 供测试推进
    yield reg
    set_provider_health_registry(None)


def _trip(reg, key: str, n: int = 3) -> None:
    for _ in range(n):
        reg.record_failure(key, ProviderFailureClass.TIMEOUT)


# ── 结果回填 ─────────────────────────────────────────────────────────────


class TestRecordOutcome:
    def test_success_records_latency(self, health):
        record_dispatch_outcome("tool_a", started_at=0.0,
                                result={"success": True})
        v = health.verdict("tool:tool_a")
        assert v.state == "closed"
        assert v.latency_bucket != "unknown"

    def test_bare_dict_counts_as_success(self, health):
        """无 success 键 = 未声明失败(既有工具面大量裸 dict)。"""
        record_dispatch_outcome("tool_a", result={"rows": 3})
        assert health.verdict("tool:tool_a").state == "closed"

    def test_credential_failure_does_not_trip(self, health):
        for _ in range(5):
            record_dispatch_outcome(
                "tool_a", result={"success": False,
                                  "code": "CREDENTIALS_REQUIRED",
                                  "error": "需要凭证 ['smtp']"})
        assert health.state("tool:tool_a") == "closed"

    def test_permission_failure_does_not_trip(self, health):
        for _ in range(5):
            record_dispatch_outcome(
                "tool_a", result={"success": False,
                                  "code": "PERMISSION_DENIED"})
        assert health.state("tool:tool_a") == "closed"

    def test_typed_timeout_failure_trips(self, health):
        for _ in range(3):
            record_dispatch_outcome(
                "tool_a", result={"success": False,
                                  "error": "upstream timed out after 30s"})
        assert health.state("tool:tool_a") == "open"

    def test_unknown_error_code_is_conservative(self, health):
        """未知错误码的失败结果不计熔断(语义失败 ≠ provider down)。"""
        for _ in range(10):
            record_dispatch_outcome(
                "tool_a", result={"success": False,
                                  "code": "SOME_SEMANTIC_ERROR"})
        assert health.state("tool:tool_a") == "closed"

    def test_exception_timeout_trips(self, health):
        for _ in range(3):
            record_dispatch_outcome("tool_a", exc=TimeoutError("t"))
        assert health.state("tool:tool_a") == "open"

    def test_cancelled_releases_but_never_trips(self, health):
        _trip(health, "tool:a", 2)
        health.record_failure("tool:a", ProviderFailureClass.CANCELLED)
        assert health.state("tool:a") == "closed"

    def test_recording_never_raises(self, health):
        """记录面绝不阻断调度:任何输入都不抛。"""
        record_dispatch_outcome("")
        record_dispatch_outcome("x", started_at="bogus")  # type: ignore[arg-type]
        record_dispatch_outcome("x", result=object())
        # 吞掉契约显式断言：三类畸形输入均未上抛，函数正常返回 None。
        assert True, "recording surface must never raise into dispatch"

    def test_error_shape_family_timeout_trips(self, health):
        """#529 族({\"error\": <str>}):与 dispatch 折叠同口径 —— timeout
        语义的错误形状必须计入熔断,不能记成 success(review P2-1)。"""
        for _ in range(3):
            record_dispatch_outcome(
                "tool_a", result={"error": "upstream socket timeout"})
        assert health.state("tool:tool_a") == "open"

    def test_error_shape_family_does_not_reset_failures(self, health):
        """error-shape 非瞬时失败:不是 success —— 不得清零熔断计数。"""
        health.record_failure("tool:b", ProviderFailureClass.TIMEOUT)
        record_dispatch_outcome("b", result={"error": "no data found"})
        assert health.verdict("tool:b").consecutive_failures == 1

    def test_status_failed_family_classified(self, health):
        """{\"status\": \"failed\"} 族:timeout 文案计入,语义失败不记。"""
        for _ in range(3):
            record_dispatch_outcome(
                "tool_c", result={"status": "failed",
                                  "error": "request timed out"})
        assert health.state("tool:tool_c") == "open"
        for _ in range(10):
            record_dispatch_outcome(
                "tool_d", result={"status": "error", "error": "not found"})
        assert health.state("tool:tool_d") == "closed"

    def test_success_true_shields_error_note(self, health):
        """显式 success=True 的部分成功载荷(带 error note)不是失败。"""
        record_dispatch_outcome(
            "tool_e", result={"success": True, "error": "geocode note"})
        assert health.verdict("tool:tool_e").state == "closed"
        assert health.verdict("tool:tool_e").consecutive_failures == 0


# ── bind 断路拒绝支 ──────────────────────────────────────────────────────


def _patch_plan(monkeypatch, plan):
    import app.services.gis_harness.candidate_planner_v8 as cp

    monkeypatch.setattr(cp, "plan_candidates_v8", lambda cap, ctx, **kw: plan)


def _eligible_plan(cap, tool_id):
    from app.services.gis_harness.candidate_planner_v8 import (
        Candidate,
        CandidatePlan,
    )
    from app.services.gis_harness.qualification_v8 import (
        QualificationResult,
        QualificationStatus,
    )

    return CandidatePlan(
        capability_id=cap,
        candidates=[
            Candidate(kind="tool", id="alt_tool",
                      qualification=QualificationResult(
                          status=QualificationStatus.ELIGIBLE),
                      latency_class="fast", reliability_penalty=0.0,
                      score=0.0),
            Candidate(kind="tool", id=tool_id,
                      qualification=QualificationResult(
                          status=QualificationStatus.ELIGIBLE),
                      latency_class="medium", reliability_penalty=0.0,
                      score=1.0),
        ],
        excluded=[],
    )


def _tool_call(name="my_tool"):
    return {"id": "call_1",
            "function": {"name": name, "arguments": "{}"}}


def _service():
    registry = MagicMock()
    registry.metadata.return_value = {"capabilities": ["cap_x"]}
    return ToolDispatchService(registry=registry)


@pytest.mark.asyncio
async def test_bind_refuses_open_provider_with_healthy_alternative(
        health, monkeypatch):
    _patch_plan(monkeypatch, _eligible_plan("cap_x", "my_tool"))
    _trip(health, "tool:my_tool")           # 目标熔断
    # alt_tool 健康:默认 closed
    svc = _service()
    executed: set = set()
    result = await svc.dispatch(_tool_call(), "s1", executed)
    assert result.status == "error"
    assert result.error_msg == "PROVIDER_UNAVAILABLE"
    assert result.raw_result["code"] == PROVIDER_UNAVAILABLE_CODE
    assert result.raw_result["retryable"] is True
    assert result.capability_evidence["code"] == "PROVIDER_UNAVAILABLE"
    # dedup 槽诚实释放:纠正后的重试不被「在飞」谎言拦截
    assert ("my_tool", '{}') not in executed or executed == set()


@pytest.mark.asyncio
async def test_no_bind_refusal_but_execution_fail_fast_without_alternative(
        health, monkeypatch):
    """全部候选都熔断 → bind 不拒绝(拒绝文本不应虚构替代),但执行包装
    仍 typed fail-fast —— 断路的本义:宁可便宜地失败,不烧满 timeout。"""
    _patch_plan(monkeypatch, _eligible_plan("cap_x", "my_tool"))
    _trip(health, "tool:my_tool")
    _trip(health, "tool:alt_tool")
    svc = _service()
    registry = AsyncMock()
    registry.dispatch = AsyncMock(return_value={"success": True})
    registry.metadata = MagicMock(return_value={"capabilities": ["cap_x"]})
    svc._registry = registry
    result = await svc.dispatch(_tool_call(), "s1", set())
    # bind 未拒绝(capability_evidence None),执行面 fail-fast(retryable)
    assert result.capability_evidence is None
    assert result.error_msg == "PROVIDER_UNAVAILABLE"
    assert result.raw_result["retryable"] is True
    registry.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_kill_switch_disables_bind_refusal(health, monkeypatch):
    monkeypatch.setenv("GIS_PROVIDER_HEALTH", "0")
    _patch_plan(monkeypatch, _eligible_plan("cap_x", "my_tool"))
    _trip(health, "tool:my_tool")
    svc = _service()
    registry = AsyncMock()
    registry.dispatch = AsyncMock(return_value={"success": True})
    registry.metadata = MagicMock(return_value={"capabilities": ["cap_x"]})
    svc._registry = registry
    out = await svc.dispatch(_tool_call(), "s1", set())
    registry.dispatch.assert_awaited_once()
    # kill-switch 下熔断拒绝被绕过：调用照常执行且不产生 typed 拒绝。
    assert out.error_msg != PROVIDER_UNAVAILABLE_CODE


@pytest.mark.asyncio
async def test_dispatch_fail_fast_when_opened_after_bind(health, monkeypatch):
    """bind 后熔断(无 capability 声明工具 / 竞态)→ 执行前 typed fail-fast,
    dedup 槽释放。"""
    svc = _service()
    svc._registry.metadata.return_value = {}  # 无 capability 声明 → bind skipped
    registry = AsyncMock()
    registry.dispatch = AsyncMock(return_value={"success": True})
    registry.metadata = MagicMock(return_value={})
    svc._registry = registry
    _trip(health, "tool:bare_tool")
    executed: set = set()
    result = await svc.dispatch(_tool_call("bare_tool"), "s1", executed)
    assert result.error_msg == PROVIDER_UNAVAILABLE_CODE
    registry.dispatch.assert_not_awaited()
    assert executed == set()  # 槽已释放


@pytest.mark.asyncio
async def test_dispatch_records_outcomes_until_breaker_trips(
        health, monkeypatch):
    """连续 timeout 失败喂断路:第 4 次同参调用前 fail-fast。"""
    _patch_plan(monkeypatch, _eligible_plan("cap_x", "my_tool"))
    registry = AsyncMock()
    side_effects = [
        {"success": False, "error": "upstream timed out"}
    ] * 3 + [{"success": True}]
    registry.dispatch = AsyncMock(side_effect=side_effects)
    registry.metadata = MagicMock(return_value={"capabilities": ["cap_x"]})
    svc = _service()
    svc._registry = registry
    for i in range(3):
        result = await svc.dispatch(_tool_call(), f"s{i}", set())
        assert result.error_msg != PROVIDER_UNAVAILABLE_CODE
    assert health.state("tool:my_tool") == "open"
    # 第 4 次:bind 拒绝(有健康替代 alt_tool)
    result = await svc.dispatch(_tool_call(), "s9", set())
    assert result.error_msg == "PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_cancelled_dispatch_releases_trial_not_failure(
        health, monkeypatch):
    from app.services.jobs.cancellation import OperationCancelled

    _patch_plan(monkeypatch, _eligible_plan("cap_x", "my_tool"))
    for _ in range(3):
        health.record_failure("tool:my_tool", ProviderFailureClass.TIMEOUT)
    # 拨到 half_open:直接改 opened_at(用内部条目;测试确定性优先)
    entry = health._entries["tool:my_tool"]
    entry.opened_at = health._clock() - (entry.cool_down_s + 1)
    assert health.state("tool:my_tool") == "half_open"

    registry = AsyncMock()
    registry.dispatch = AsyncMock(side_effect=OperationCancelled("cancelled"))
    registry.metadata = MagicMock(return_value={"capabilities": ["cap_x"]})
    svc = _service()
    svc._registry = registry
    with pytest.raises(OperationCancelled):
        await svc.dispatch(_tool_call(), "s1", set())
    # 取消:不记失败、trial 名额已归还 → 新 trial 可授予
    assert health.state("tool:my_tool") == "half_open"
    assert health.allow("tool:my_tool") is True


@pytest.mark.asyncio
async def test_half_open_trial_recovers_on_success(health, monkeypatch):
    """半开恢复:trial 成功 → closed,后续调用正常。"""
    _patch_plan(monkeypatch, _eligible_plan("cap_x", "my_tool"))
    for _ in range(3):
        health.record_failure("tool:my_tool", ProviderFailureClass.TIMEOUT)
    entry = health._entries["tool:my_tool"]
    entry.opened_at = health._clock() - (entry.cool_down_s + 1)
    assert health.state("tool:my_tool") == "half_open"

    registry = AsyncMock()
    registry.dispatch = AsyncMock(return_value={"success": True})
    registry.metadata = MagicMock(return_value={"capabilities": ["cap_x"]})
    svc = _service()
    svc._registry = registry
    await svc.dispatch(_tool_call(), "s1", set())
    assert health.verdict("tool:my_tool").state == "closed"
    assert health.verdict("tool:my_tool").open_cycles == 0
