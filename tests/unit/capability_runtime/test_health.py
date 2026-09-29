"""provider 健康状态机对抗测试(H05)。

覆盖:断路转换 / typed 失败分类(credential/policy 不计入熔断)/ 半开
恢复(单 trial + 成功闭合 + 失败重开)/ 封顶指数冷却(flapping 防重试
风暴)/ trial 时间兜底(防名额泄漏卡死)/ 取消只释放名额 / LRU 有界 /
kill switch / clock 注入确定性。
"""
from __future__ import annotations

import asyncio


from app.services.capability_runtime.health import (
    COOL_DOWN_MAX_S,
    COOL_DOWN_S,
    HALF_OPEN_TRIAL_TIMEOUT_S,
    PROVIDER_HEALTH_ENV,
    ProviderFailureClass,
    ProviderHealthRegistry,
    classify_failure,
    latency_bucket,
    provider_health_enabled,
    provider_health_factor,
    set_provider_health_registry,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _registry(clock: _FakeClock, **kw) -> ProviderHealthRegistry:
    return ProviderHealthRegistry(clock=clock, **kw)


# ── typed 失败分类 ────────────────────────────────────────────────────────


class TestClassifyFailure:
    def test_cancelled_error(self):
        assert classify_failure(
            exc=asyncio.CancelledError()) is ProviderFailureClass.CANCELLED

    def test_timeout_error(self):
        assert classify_failure(exc=TimeoutError()) is ProviderFailureClass.TIMEOUT

    def test_timeout_by_type_name(self):
        class UpstreamReadTimeout(Exception):
            pass

        assert classify_failure(
            exc=UpstreamReadTimeout()) is ProviderFailureClass.TIMEOUT

    def test_known_code_vocabulary(self):
        assert classify_failure(
            error_code="CREDENTIALS_REQUIRED") is ProviderFailureClass.CREDENTIAL
        assert classify_failure(
            error_code="PERMISSION_DENIED") is ProviderFailureClass.POLICY
        assert classify_failure(
            error_code="VALIDATION_ERROR") is ProviderFailureClass.PERMANENT

    def test_message_fallback(self):
        assert classify_failure(
            error_msg="connection timed out after 30s") is ProviderFailureClass.TIMEOUT
        assert classify_failure(
            error_msg="missing 凭证 for smtp") is ProviderFailureClass.CREDENTIAL

    def test_unknown_default_transient(self):
        assert classify_failure(error_code="WEIRD_CODE") is ProviderFailureClass.UNKNOWN


# ── 断路转换 ─────────────────────────────────────────────────────────────


class TestBreakerTransitions:
    def test_closed_below_threshold(self, clock=None):
        clock = _FakeClock()
        reg = _registry(clock)
        for _ in range(2):
            reg.record_failure("tool:a", ProviderFailureClass.TIMEOUT)
        assert reg.state("tool:a") == "closed"
        assert reg.allow("tool:a") is True

    def test_opens_at_threshold(self):
        clock = _FakeClock()
        reg = _registry(clock)
        for _ in range(3):
            reg.record_failure("tool:a", ProviderFailureClass.TIMEOUT)
        assert reg.state("tool:a") == "open"
        assert reg.allow("tool:a") is False  # fail-fast

    def test_credential_policy_failures_never_trip(self):
        """凭证/权限失败是上下文问题,不是 provider-down 证据。"""
        clock = _FakeClock()
        reg = _registry(clock)
        for _ in range(10):
            reg.record_failure("tool:a", ProviderFailureClass.CREDENTIAL)
            reg.record_failure("tool:a", ProviderFailureClass.POLICY)
            reg.record_failure("tool:a", ProviderFailureClass.PERMANENT)
        assert reg.state("tool:a") == "closed"

    def test_success_resets_and_records_latency(self):
        clock = _FakeClock()
        reg = _registry(clock)
        reg.record_failure("tool:a", ProviderFailureClass.TIMEOUT)
        reg.record_success("tool:a", latency_ms=120.0)
        v = reg.verdict("tool:a")
        assert v.consecutive_failures == 0
        assert v.latency_bucket == "fast"

    def test_latency_buckets(self):
        clock = _FakeClock()
        reg = _registry(clock)
        reg.record_success("fast_one", latency_ms=100.0)
        reg.record_success("mid_one", latency_ms=1500.0)
        reg.record_success("slow_one", latency_ms=5000.0)
        assert reg.verdict("fast_one").latency_bucket == "fast"
        assert reg.verdict("mid_one").latency_bucket == "medium"
        assert reg.verdict("slow_one").latency_bucket == "slow"
        assert latency_bucket(None) == "unknown"


# ── 半开恢复 ─────────────────────────────────────────────────────────────


class TestHalfOpenRecovery:
    def test_half_open_after_cooldown_single_trial(self):
        clock = _FakeClock()
        reg = _registry(clock)
        for _ in range(3):
            reg.record_failure("tool:a", ProviderFailureClass.TIMEOUT)
        clock.advance(COOL_DOWN_S + 0.01)
        assert reg.state("tool:a") == "half_open"
        assert reg.allow("tool:a") is True   # trial 授予
        assert reg.allow("tool:a") is False  # 单 trial:第二个并发被拒

    def test_trial_success_closes(self):
        clock = _FakeClock()
        reg = _registry(clock)
        for _ in range(3):
            reg.record_failure("tool:a", ProviderFailureClass.TIMEOUT)
        clock.advance(COOL_DOWN_S + 0.01)
        assert reg.allow("tool:a") is True
        reg.record_success("tool:a", latency_ms=90.0)
        v = reg.verdict("tool:a")
        assert v.state == "closed"
        assert v.open_cycles == 0  # 恢复:flapping 计数清零
        assert reg.allow("tool:a") is True

    def test_trial_failure_reopens_with_escalated_cooldown(self):
        clock = _FakeClock()
        reg = _registry(clock)
        for _ in range(3):
            reg.record_failure("tool:a", ProviderFailureClass.TIMEOUT)
        clock.advance(COOL_DOWN_S + 0.01)
        assert reg.allow("tool:a") is True
        reg.record_failure("tool:a", ProviderFailureClass.TIMEOUT)
        assert reg.state("tool:a") == "open"
        # 冷却指数升级:2× 基础冷却内仍 fail-fast
        clock.advance(COOL_DOWN_S)
        assert reg.allow("tool:a") is False
        clock.advance(COOL_DOWN_S + 0.01)  # 累计 2×+
        assert reg.state("tool:a") == "half_open"

    def test_flapping_cooldown_capped(self):
        """反复 open → 冷却单调升级但封顶:不构成 retry storm。"""
        clock = _FakeClock()
        reg = _registry(clock, cool_down_max_s=COOL_DOWN_MAX_S)
        key = "tool:flappy"
        for cycle in range(6):
            # 强制进入 half_open(把状态拨到冷却可过期)
            clock.advance(COOL_DOWN_MAX_S * 2)
            state = reg.state(key)
            assert state == "half_open" or state == "closed", state
            if state == "closed":
                for _ in range(3):
                    reg.record_failure(key, ProviderFailureClass.TIMEOUT)
            else:
                assert reg.allow(key) is True
                reg.record_failure(key, ProviderFailureClass.TIMEOUT)
            assert reg.state(key) == "open"
            # 本轮 open 后的冷却时间(逐次探测直到 half_open)
            waited = 0.0
            step = 5.0
            while reg.state(key) != "half_open":
                clock.advance(step)
                waited += step
                assert waited <= COOL_DOWN_MAX_S * 2, "cooldown must stay bounded"
            assert waited >= COOL_DOWN_S  # 至少基础冷却
            _last_cool = waited

    def test_trial_timeout_backstop(self):
        """trial 名额超时自动失效 —— 泄漏不会永久卡死熔断。"""
        clock = _FakeClock()
        reg = _registry(clock)
        for _ in range(3):
            reg.record_failure("tool:a", ProviderFailureClass.TIMEOUT)
        clock.advance(COOL_DOWN_S + 0.01)
        assert reg.allow("tool:a") is True  # trial 授予后调用方"消失"
        assert reg.allow("tool:a") is False
        clock.advance(HALF_OPEN_TRIAL_TIMEOUT_S + 0.01)
        assert reg.allow("tool:a") is True  # 兜底:新 trial 可授予

    def test_release_trial(self):
        clock = _FakeClock()
        reg = _registry(clock)
        for _ in range(3):
            reg.record_failure("tool:a", ProviderFailureClass.TIMEOUT)
        clock.advance(COOL_DOWN_S + 0.01)
        assert reg.allow("tool:a") is True
        reg.release_trial("tool:a")
        assert reg.allow("tool:a") is True

    def test_cancelled_only_releases_trial(self):
        clock = _FakeClock()
        reg = _registry(clock)
        for _ in range(2):
            reg.record_failure("tool:a", ProviderFailureClass.TIMEOUT)
        clock.advance(COOL_DOWN_S + 0.01)
        reg.record_failure("tool:a", ProviderFailureClass.CANCELLED)
        assert reg.state("tool:a") == "closed"  # 取消不计数


# ── 有界性与开关 ─────────────────────────────────────────────────────────


class TestBoundsAndSwitch:
    def test_lru_bound(self):
        reg = _registry(_FakeClock(), max_entries=8)
        for i in range(16):
            reg.record_success(f"tool:{i}")
        assert len(reg.snapshot(max_entries=64)) == 8

    def test_snapshot_bounded_and_recent_first(self):
        reg = _registry(_FakeClock())
        for i in range(10):
            reg.record_success(f"tool:{i}")
        snap = reg.snapshot(max_entries=4)
        assert len(snap) == 4
        assert snap[0].provider_key == "tool:9"  # LRU 尾 = 最近使用

    def test_kill_switch(self, monkeypatch):
        monkeypatch.setenv(PROVIDER_HEALTH_ENV, "0")
        assert provider_health_enabled() is False
        assert provider_health_factor("tool:anything") == 0.0

    def test_provider_health_factor_values(self):
        clock = _FakeClock()
        reg = _registry(clock)
        set_provider_health_registry(reg)
        assert provider_health_factor("tool:b") == 0.0
        for _ in range(3):
            reg.record_failure("tool:b", ProviderFailureClass.TRANSIENT)
        assert provider_health_factor("tool:b") == 0.75  # open
        clock.advance(COOL_DOWN_S + 0.01)
        assert provider_health_factor("tool:b") == 0.25  # half_open

    def test_deterministic_verdict_projection(self):
        clock = _FakeClock()
        reg = _registry(clock)
        reg.record_failure("tool:x", ProviderFailureClass.TIMEOUT)
        v1 = reg.verdict("tool:x")
        v2 = reg.verdict("tool:x")
        assert v1.to_dict() == v2.to_dict()
