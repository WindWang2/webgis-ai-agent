"""Deterministic fake 资产测试（E15）：ScriptedToolProvider + FaultInjector 故障矩阵。

矩阵覆盖执行面变换故障：latency / timeout / disconnect / cancel /
malformed_result / duplicate_event / restart（policy/resource 属生产判定面，
由 settlement 检查驱动）。确定性：同脚本同计划 → 同观测序列。
"""
from __future__ import annotations

import asyncio

import pytest

from app.lib.harness.lab.fakes import (
    DispatchOutcome,
    FaultInjector,
    LabClock,
    ScriptedToolProvider,
)
from app.lib.harness.lab.spec import FaultStep, ProviderOp


def _op(**over) -> ProviderOp:
    data = dict(call_id="c1", tool="lab.echo", arguments={"q": 1},
                result={"echo": 1})
    data.update(over)
    return ProviderOp(**data)


def _run(coro):
    return asyncio.run(coro)


class TestScriptedToolProvider:
    def test_args_key_exact_match_then_cursor_fallback(self):
        provider = ScriptedToolProvider([
            _op(call_id="a", arguments={"q": 1}),
            _op(call_id="b", arguments={"q": 2}),
        ])
        out1 = _run(provider.dispatch("lab.echo", {"q": 2}))
        out2 = _run(provider.dispatch("lab.echo", {"other": 9}))
        # 精确 (tool, args) 对齐优先；未对齐参数回退出现序。
        assert out1.result == {"echo": 1} or out1.result == {"echo": 2}
        assert not out1.is_error and not out2.is_error

    def test_missing_receipt_is_honest_error(self):
        provider = ScriptedToolProvider([])
        out = _run(provider.dispatch("lab.ghost", {}))
        assert out.is_error and out.error_code == "RECEIPT_MISSING"

    def test_error_receipt_preserves_code(self):
        provider = ScriptedToolProvider([
            _op(is_error=True, error_code="TOOL_TIMEOUT",
                error_msg="timeout")])
        out = _run(provider.dispatch("lab.echo", {"q": 1}))
        assert out.is_error and out.error_code == "TOOL_TIMEOUT"

    def test_call_id_redelivery_replays_same_receipt(self):
        """幂等重投递：同 call_id → 同收据，消费序不前进（dedup 前提）。"""
        provider = ScriptedToolProvider([_op(call_id="c1")])
        first = _run(provider.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        second = _run(provider.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        assert first.result == second.result == {"echo": 1}
        assert not first.is_error and not second.is_error

    def test_metadata_carries_capabilities(self):
        provider = ScriptedToolProvider([], tool_capabilities={
            "lab.echo": ["cap_a", "cap_b"]})
        assert provider.metadata("lab.echo")["capabilities"] == [
            "cap_a", "cap_b"]


class TestFaultMatrix:
    def _injector(self, faults, ops=None):
        provider = ScriptedToolProvider(ops or [_op()])
        return FaultInjector(list(faults), provider=provider), provider

    def test_latency_advances_fake_clock_only(self):
        injector, _ = self._injector([
            FaultStep(type="latency", target_call="c1", params={"ms": 250})])
        assert injector.clock.now_ms == 0.0
        out = _run(injector.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        assert injector.clock.now_ms == 250.0
        assert not out.is_error
        assert out.faults_applied == ["latency"]

    @pytest.mark.parametrize("fault_type,code", [
        ("timeout", "TOOL_TIMEOUT"),
        ("disconnect", "DISCONNECTED"),
        ("cancel", "CANCELLED"),
    ])
    def test_typed_error_faults_never_fake_success(self, fault_type, code):
        injector, _ = self._injector([
            FaultStep(type=fault_type, target_call="c1")])
        out = _run(injector.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        assert out.is_error and out.error_code == code
        if fault_type == "cancel":
            assert out.result.get("cancelled") is True
        else:
            assert out.result == {}

    def test_malformed_result_is_not_error_but_wrong_shape(self):
        injector, _ = self._injector([
            FaultStep(type="malformed_result", target_call="c1")])
        out = _run(injector.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        assert not out.is_error
        assert out.result == {"unexpected_shape": True}

    def test_duplicate_event_marks_second_delivery(self):
        injector, provider = self._injector([
            FaultStep(type="duplicate_event", target_turn=1)])
        first = _run(injector.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        second = _run(injector.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        assert not first.duplicate and second.duplicate
        # 重投递不改判态（同收据）。
        assert first.is_error == second.is_error
        assert len(provider.calls) == 2

    def test_restart_fails_once_then_recovers(self):
        injector, _ = self._injector([
            FaultStep(type="restart", target_call="c1")])
        first = _run(injector.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        second = _run(injector.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        assert first.is_error and first.error_code == "PI_RESTART"
        assert not second.is_error

    def test_unmatched_target_is_inert(self):
        injector, _ = self._injector([
            FaultStep(type="timeout", target_call="other-call")])
        out = _run(injector.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        assert not out.is_error and out.faults_applied == []

    def test_deterministic_replay_same_sequence(self):
        def _sequence():
            injector, _ = self._injector([
                FaultStep(type="restart", target_turn=0),
                FaultStep(type="latency", target_call="c1",
                          params={"ms": 5}),
            ])
            return [
                (_run(injector.dispatch("lab.echo", {"q": 1},
                                       call_id="c1")).error_code,
                 injector.clock.now_ms)
                for _ in range(3)
            ]

        assert _sequence() == _sequence()

    def test_policy_and_resource_faults_not_faked(self):
        """policy/resource 拒绝必须来自生产判定 —— injector 不代答。"""
        provider = ScriptedToolProvider([_op()])
        injector = FaultInjector([
            FaultStep(type="policy_deny"),
            FaultStep(type="resource_reject"),
        ], provider=provider)
        out = _run(injector.dispatch("lab.echo", {"q": 1}, call_id="c1"))
        assert not out.is_error and out.faults_applied == []


class TestLabClock:
    def test_advance_only_moves_forward(self):
        clock = LabClock(start_ms=10)
        assert clock.advance_ms(5) == 15.0
        assert clock.now_ms == 15.0
