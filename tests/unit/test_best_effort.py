"""best_effort 单测（issue #1549）：「绝不阻断」统一降级面。

断言三组契约：
1. 可见性 —— 吞掉异常时发出带稳定 code 的 warning（含 exc_info 与 ctx）；
2. 穿透 —— reraise 指定类型 / BaseException（CancelledError 等）绝不吞；
3. 绝不上抛 —— 兜底自身（含日志/计数失败）永不向调用方 raise。
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.lib.runtime.evidence import TurnEvidence, bind_turn_evidence
from app.utils.best_effort import best_effort, best_effort_call


@pytest.fixture
def caplog_warning(caplog):
    caplog.set_level(logging.WARNING, logger="app.utils.best_effort")
    return caplog


# ── 1. 上下文管理器：吞 + 记 ─────────────────────────────────────────

def test_body_runs_and_no_log_on_success(caplog_warning):
    ran = []
    with best_effort("demo-op", "demo-op-best-effort"):
        ran.append(True)
    assert ran == [True]
    assert caplog_warning.records == []


def test_exception_swallowed_with_code_logged(caplog_warning):
    marker = ValueError("boom")
    with best_effort("demo-op", "demo-op-best-effort", ctx={"tool": "t1"}):
        raise marker
    # 吞掉后执行到达这里（绝不阻断）
    records = [r for r in caplog_warning.records if r.name == "app.utils.best_effort"]
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.WARNING
    assert "[demo-op-best-effort] demo-op skipped: boom" in rec.getMessage()
    assert "ctx={'tool': 't1'}" in rec.getMessage()
    # exc_info 携带原始异常（类型与实例）
    assert rec.exc_info is not None
    assert rec.exc_info[0] is ValueError
    assert rec.exc_info[1] is marker


def test_execution_continues_after_with(caplog_warning):
    after = []
    with best_effort("op", "code"):
        raise RuntimeError("x")
    after.append(True)
    assert after == [True]


# ── 2. 穿透：reraise 与 BaseException ────────────────────────────────

def test_reraise_passthrough(caplog_warning):
    with pytest.raises(KeyError, match="passthrough"):
        with best_effort("op", "code", reraise=(KeyError,)):
            raise KeyError("passthrough")
    assert caplog_warning.records == []


def test_reraise_empty_tuple_swallows(caplog_warning):
    with best_effort("op", "code"):
        raise KeyError("swallowed")
    assert len(caplog_warning.records) == 1


def test_base_exception_propagates(caplog_warning):
    # asyncio.CancelledError / KeyboardInterrupt 是 BaseException，绝不吞。
    with pytest.raises(asyncio.CancelledError):
        with best_effort("op", "code"):
            raise asyncio.CancelledError()
    with pytest.raises(KeyboardInterrupt):
        with best_effort("op", "code"):
            raise KeyboardInterrupt()
    assert caplog_warning.records == []


async def test_await_inside_body(caplog_warning):
    """体内 await（含被 await 调用抛的 Exception）同样被吞 —— 与 try/except 同构。"""
    async def _fail():
        raise OSError("net down")

    with best_effort("async-op", "async-op-best-effort"):
        await _fail()
    assert len(caplog_warning.records) == 1


# ── 3. 绝不上抛：兜底的兜底 ──────────────────────────────────────────

def test_never_raises_even_if_logging_breaks(caplog_warning, monkeypatch):
    """日志面坏掉（handler/格式化抛异常）时依旧吞掉业务异常，绝不二次上抛。"""
    def _broken_warning(*args, **kwargs):
        raise RuntimeError("logging is broken")

    monkeypatch.setattr("app.utils.best_effort.logger.warning", _broken_warning)
    with best_effort("op", "code"):
        raise ValueError("still swallowed")
    # 显式断言吞掉契约：with 块内异常未传播，且后续代码照常执行。
    assert True, "business exception must not propagate out of best_effort"


def test_metric_failure_does_not_raise(caplog_warning, monkeypatch):
    """turn 级计数失败（如 evidence 读取异常）不影响「绝不阻断」契约。

    _record_skipped 在调用点惰性 ``from app.lib.runtime.evidence import
    current_turn_evidence``，故 monkeypatch 源模块属性即命中真实 seam。
    """
    def _broken_evidence():
        raise RuntimeError("evidence unavailable")

    monkeypatch.setattr(
        "app.lib.runtime.evidence.current_turn_evidence", _broken_evidence
    )
    with best_effort("op", "code"):
        raise ValueError("swallowed")
    # evidence 面坏掉不改变契约：异常被吞、主流程继续（并留下 warning 侧账）。
    assert True, "evidence failure must not break the never-block contract"
    assert len(caplog_warning.records) == 1


# ── 4. 函数调用式 ────────────────────────────────────────────────────

def test_best_effort_call_returns_value(caplog_warning):
    assert best_effort_call(lambda: 42, "op", "code") == 42
    assert caplog_warning.records == []


def test_best_effort_call_default_on_error(caplog_warning):
    def _boom():
        raise TypeError("bad")

    out = best_effort_call(_boom, "op", "call-op-best-effort", default=None)
    assert out is None
    assert "[call-op-best-effort] op skipped: bad" in caplog_warning.text


def test_best_effort_call_reraise(caplog_warning):
    with pytest.raises(ZeroDivisionError):
        best_effort_call(
            lambda: 1 / 0, "op", "code", reraise=(ZeroDivisionError,)
        )
    assert caplog_warning.records == []


# ── 5. 可选 turn 级指标 ──────────────────────────────────────────────

def test_active_turn_records_warning_metric():
    ev = TurnEvidence(
        request_id=None, session_id="s-best-effort",
        turn_id="turn-best-effort-1", run_id=None,
    )
    with bind_turn_evidence(ev):
        with best_effort("mem-offer", "tool-dispatch-memory-offer-best-effort"):
            raise ValueError("offer failed")
    warnings = ev.to_summary()["warnings"]
    assert any(
        w["code"] == "tool-dispatch-memory-offer-best-effort" for w in warnings
    )


def test_no_active_turn_metric_is_noop(caplog_warning):
    """turn 外（current_turn_evidence() is None）只发日志，计数路径静默跳过。"""
    with best_effort("op", "code"):
        raise ValueError("x")
    assert len(caplog_warning.records) == 1
