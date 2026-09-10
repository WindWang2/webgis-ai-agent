"""跨进程 trace 传播测试（ADR-0131 D2）。

覆盖：submit → Celery headers → worker 恢复的完整链路（submit 层用
假 celery task + 假 store 验证 headers 真实进入 apply_async）、
JsonFormatter 结构化输出、RuntimeCorrelationFilter 的 trace 字段注入。
"""
from __future__ import annotations

import json
import logging
import unittest.mock
from types import SimpleNamespace

import pytest

from app.lib.observability.spans import (
    SpanStage,
    context_kwargs_from_headers,
    start_span,
    trace_headers,
)
from app.lib.runtime.context import bind_runtime_context
from app.services.jobs.worker import trace_kwargs_from_task


# ── worker 侧恢复 ────────────────────────────────────────────────────────


def test_trace_kwargs_from_task_roundtrip():
    with bind_runtime_context(trace_id="a" * 32, span_id="b" * 16):
        headers = trace_headers()
    task = SimpleNamespace(request=SimpleNamespace(headers=headers))
    kwargs = trace_kwargs_from_task(task)
    assert kwargs["trace_id"] == "a" * 32
    assert len(kwargs["span_id"]) == 16


def test_trace_kwargs_from_task_garbage_and_missing():
    assert trace_kwargs_from_task(None) == {}
    task = SimpleNamespace(request=SimpleNamespace(headers={"traceparent": "junk"}))
    assert trace_kwargs_from_task(task) == {}
    no_headers = SimpleNamespace(request=SimpleNamespace())
    assert trace_kwargs_from_task(no_headers) == {}
    no_request = SimpleNamespace()
    assert trace_kwargs_from_task(no_request) == {}


def test_span_scope_span_id_propagates_not_outer():
    """传播的是**当前 span** 的 span_id（worker 侧续树），不是请求入口的。"""
    with bind_runtime_context(trace_id="c" * 32, span_id="0" * 16):
        with start_span(SpanStage.TOOL, "dispatching"):
            headers = trace_headers()
    task = SimpleNamespace(request=SimpleNamespace(headers=headers))
    kwargs = trace_kwargs_from_task(task)
    assert kwargs["span_id"] != "0" * 16


def test_worker_side_restores_tree_from_headers():
    """worker 侧拿到的 header 可以直接 bind 成合法子上下文。"""
    with bind_runtime_context(trace_id="d" * 32, span_id="e" * 16):
        with start_span(SpanStage.WORKFLOW, "submit"):
            headers = trace_headers()
    task = SimpleNamespace(request=SimpleNamespace(headers=headers))
    restored = trace_kwargs_from_task(task)
    with bind_runtime_context(session_id="s1", **restored):
        from app.lib.runtime.context import current_runtime_context

        ctx = current_runtime_context()
        assert ctx.trace_id == "d" * 32  # 同一 trace
        assert ctx.session_id == "s1"    # 原有关联不丢


# ── submit 侧注入（假 celery task 验证 apply_async 收到 headers）──────────


@pytest.fixture()
def _fake_submit_env(monkeypatch):
    """假 DB/store：只验证 submit 的派发行为，不碰真实数据库。"""
    from app.services.jobs import submit as submit_mod

    fake_job = SimpleNamespace(id=7, celery_task_id=None)

    class _FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def commit(self):
            pass

    captured = {}

    def fake_apply_async(*args, **kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(id="celery-1")

    monkeypatch.setattr(submit_mod, "db_session", lambda: _FakeDb())
    monkeypatch.setattr(
        submit_mod.DurableJobStore, "create_sync",
        staticmethod(lambda db, **kw: fake_job), raising=True,
    )
    monkeypatch.setattr(
        submit_mod.DurableJobStore, "is_terminal_job",
        staticmethod(lambda job: False), raising=True,
    )
    monkeypatch.setattr(
        submit_mod.DurableJobStore, "transition_sync",
        staticmethod(lambda db, job_id, to, expected=None: True), raising=True,
    )
    monkeypatch.setattr(
        submit_mod.DurableJobStore, "set_celery_task_id_sync",
        staticmethod(lambda db, job_id, task_id: True), raising=True,
    )
    monkeypatch.setattr(
        submit_mod.cancellation_registry, "register",
        lambda job_id: None, raising=True,
    )
    task = SimpleNamespace(name="fake_task", apply_async=fake_apply_async)
    return submit_mod, task, captured


def test_submit_passes_trace_headers(_fake_submit_env):
    submit_mod, task, captured = _fake_submit_env
    with bind_runtime_context(trace_id="f" * 32, span_id="9" * 16):
        result = submit_mod.submit_durable_job(
            celery_task=task,
            task_type="ndvi",
            display_name="NDVI 分析",
            params={"x": 1},
        )
    assert result["status"] == "analysis_task_started"
    headers = captured["kwargs"]["headers"]
    assert headers["traceparent"].startswith("00-" + "f" * 32 + "-")


def test_submit_without_context_sends_no_headers(_fake_submit_env):
    """无 trace 上下文 → 不带 headers 键（消息形状与旧行为完全一致）。"""
    submit_mod, task, captured = _fake_submit_env
    result = submit_mod.submit_durable_job(
        celery_task=task, task_type="ndvi", display_name="NDVI", params={},
    )
    assert result["status"] == "analysis_task_started"
    assert "headers" not in captured["kwargs"]


def test_submit_origin_still_restored_in_worker(_fake_submit_env):
    """端到端：submit 派发 → worker 恢复，trace/session 双通道并存。"""
    submit_mod, task, captured = _fake_submit_env
    with bind_runtime_context(trace_id="1" * 32, session_id="sess-42"):
        with start_span(SpanStage.TOOL, "dispatch"):
            submit_mod.submit_durable_job(
                celery_task=task, task_type="buffer", display_name="缓冲", params={},
            )
    headers = captured["kwargs"]["headers"]
    # worker 视角
    from app.services.jobs.worker import trace_kwargs_from_task as restore

    restored = restore(SimpleNamespace(request=SimpleNamespace(headers=headers)))
    assert restored["trace_id"] == "1" * 32
    # session 关联走 job 行（既有通道），headers 只补 trace 维度
    assert "session_id" not in context_kwargs_from_headers(headers)


# ── JSON 结构化日志 ──────────────────────────────────────────────────────


def test_json_formatter_output_shape():
    from app.core.logging_config import JsonFormatter, RuntimeCorrelationFilter

    logger = logging.getLogger("platform-v4.json-test")
    logger.handlers = []
    logger.propagate = False
    records: list = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RuntimeCorrelationFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    with bind_runtime_context(
        request_id="req-1", trace_id="2" * 32, span_id="3" * 16
    ):
        logger.info("hello %s", "world", extra={"job_id": 42})

    payload = json.loads(records[0] and handler.format(records[0]))
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "req-1"
    assert payload["trace_id"] == "2" * 32
    assert payload["span_id"] == "3" * 16
    assert payload["session_id"] is None  # 显式 null，可查询
    assert payload["job_id"] == 42        # extra 白名单式并入
    assert payload["ts"].endswith("Z")
    logger.handlers = []


def test_json_formatter_unbound_fields_are_null():
    from app.core.logging_config import JsonFormatter, RuntimeCorrelationFilter

    logger = logging.getLogger("platform-v4.json-unbound")
    logger.handlers = []
    logger.propagate = False
    records: list = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RuntimeCorrelationFilter())
    logger.addHandler(handler)
    logger.warning("no ctx")
    payload = json.loads(handler.format(records[0]))
    assert payload["trace_id"] is None
    assert payload["turn_id"] is None
    logger.handlers = []


def test_correlation_filter_injects_trace_fields():
    """文本模式（默认）下 trace 字段也进 LogRecord（handler 可自选消费）。"""
    from app.core.logging_config import RuntimeCorrelationFilter

    record = logging.LogRecord(
        "t", logging.INFO, "p", 1, "m", None, None
    )
    with bind_runtime_context(trace_id="4" * 32):
        RuntimeCorrelationFilter().filter(record)
    assert record.trace_id == "4" * 32
    assert record.span_id == "-"  # 未绑定 span → 占位符

    bare = logging.LogRecord("t", logging.INFO, "p", 1, "m", None, None)
    RuntimeCorrelationFilter().filter(bare)
    assert bare.trace_id == "-"
