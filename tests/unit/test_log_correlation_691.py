"""#691: 端到端请求关联——日志关联字段、X-Request-ID 回显、metrics 带 mapspec 版本。

子代理的 flip-red 验证用例曾被执行后删除（违反回归锁定纪律），本文件按
其断言语义重建为持久回归。
"""
import json
import logging

import pytest


def test_logging_record_has_correlation_fields():
    """bind_runtime_context 下任意 logger 的记录都带 req/sess/turn/run 字段。"""
    from app.core.logging_config import RuntimeCorrelationFilter
    from app.lib.runtime.context import bind_runtime_context

    rec = logging.LogRecord("t691", logging.INFO, __file__, 1, "hello", None, None)
    with bind_runtime_context(
        request_id="req-1", session_id="sess-1", turn_id="turn-1", run_id="run-1"
    ):
        RuntimeCorrelationFilter().filter(rec)
    assert rec.request_id == "req-1"
    assert rec.session_id == "sess-1"
    assert rec.turn_id == "turn-1"
    assert rec.run_id == "run-1"


def test_logging_record_placeholder_when_unbound():
    """无绑定时字段为占位 '-'，不清日志行不抛。"""
    from app.core.logging_config import RuntimeCorrelationFilter

    rec = logging.LogRecord("t691b", logging.INFO, __file__, 1, "x", None, None)
    RuntimeCorrelationFilter().filter(rec)
    assert rec.request_id == "-" and rec.session_id == "-"
    assert rec.turn_id == "-" and rec.run_id == "-"


def test_tool_metrics_row_carries_mapspec_revision(tmp_path, monkeypatch):
    """record_tool_call 的 JSONL 行带 mapspec_revision/fingerprint。"""
    from app.services import tool_metrics as tm

    captured = {}

    class _Q:
        def put_nowait(self, row):
            captured.update(json.loads(row) if isinstance(row, str) else row)

    monkeypatch.setattr(tm, "_queue", _Q(), raising=False)
    tm.record_tool_call(
        tool="t", arg_bytes=1, result_bytes=2, duration_ms=3, cache_hit=False,
        error=None, session_id="s1",
        mapspec_revision=7, mapspec_fingerprint="fp-abc",
    )
    assert captured.get("mapspec_revision") == 7
    assert captured.get("mapspec_fingerprint") == "fp-abc"


def test_registry_dispatch_passes_mapspec_fields(monkeypatch):
    """dispatch 产物含 mutation_revision/mapspec_fingerprint 时透传进 metrics。"""
    import asyncio
    from unittest.mock import patch
    import app.services.tool_metrics as tm_mod
    from app.tools.registry import ToolRegistry

    captured = {}

    def capture(**kw):
        captured.update(kw)

    def big_tool(x: int) -> dict:
        return {"success": True, "mutation_revision": 9, "mapspec_fingerprint": "fp-xyz"}

    r = ToolRegistry()
    r.register("rev_tool", "t", big_tool)
    with patch.object(tm_mod, "record_tool_call", side_effect=capture):
        asyncio.run(r.dispatch("rev_tool", {"x": 1}, session_id=None))
    assert captured.get("mapspec_revision") == 9
    assert captured.get("mapspec_fingerprint") == "fp-xyz"


@pytest.mark.asyncio
async def test_middleware_echoes_request_id():
    """X-Request-ID 被消费并回显；缺失时生成。"""
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r1 = await ac.get("/api/v1/health", headers={"X-Request-ID": "req-echo-1"})
        assert r1.headers.get("x-request-id") == "req-echo-1"
        r2 = await ac.get("/api/v1/health")
        assert (r2.headers.get("x-request-id") or "").strip() != ""
