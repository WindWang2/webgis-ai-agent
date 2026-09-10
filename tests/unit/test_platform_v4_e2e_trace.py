"""Platform V4 端到端验收测试：同一 trace id 横跨关键模块（ADR-0131 验收 1）。

一条进程内的"典型请求"链路：

    TraceContextMiddleware（模拟 HTTP 边缘）
      → harness span（规划）
        → tool span（提交 durable job）
          → trace_headers（Celery 派发边界）
            → worker 侧恢复（durable_job 的 trace_kwargs_from_task 通道）
              → worker 内的 model span 异常 → 错误分类 → 重试决策

验收断言：全链路 trace_id 恒定、span 父子链完整、错误分类驱动
decide_retry（验收 2：structured error 驱动 retry 决策）。
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.errors import decide_retry
from app.lib.observability.spans import (
    RingSpanExporter,
    SpanStage,
    SpanStatus,
    install_exporter,
    reset_exporters_for_tests,
    start_span,
    trace_headers,
)
from app.lib.observability.trace_context import TraceContextMiddleware
from app.lib.runtime.context import bind_runtime_context, current_runtime_context
from app.services.jobs.worker import trace_kwargs_from_task


@pytest.fixture(autouse=True)
def _clean():
    reset_exporters_for_tests()
    yield
    reset_exporters_for_tests()


class _FakeRequest:
    """最小 ASGI http scope 的请求替身。"""

    def __init__(self, headers=None):
        self.scope = {
            "type": "http", "method": "POST", "path": "/api/v1/chat/stream",
            "headers": headers or [], "query_string": b"",
        }


async def _drive_app(inner, request):
    """模拟中间件栈驱动一次请求（真实 TraceContextMiddleware 逻辑）。"""
    async def receive():
        return {"type": "http.request"}

    sent = []

    async def send(message):
        sent.append(message)

    app = TraceContextMiddleware(inner)
    await app(request.scope, receive, send)
    return sent


def test_one_trace_id_across_modules_end_to_end():
    ring = RingSpanExporter(capacity=64)
    install_exporter(ring)
    seen_trace_ids = {}

    # ── HTTP 边缘：入站 traceparent ──────────────────────────────────
    async def endpoint(scope, receive, send):
        """harness → tool → 派发 → worker 全链路（进程内模拟）。"""
        ctx = current_runtime_context()
        seen_trace_ids["http"] = ctx.trace_id

        # harness 阶段
        with start_span(SpanStage.HARNESS, "plan_turn", turn="t1") as harness_span:
            seen_trace_ids["harness"] = harness_span.trace_id

            # tool 阶段（提交 durable job）
            with start_span(SpanStage.TOOL, "submit_ndvi") as tool_span:
                seen_trace_ids["tool"] = tool_span.trace_id

                # Celery 派发边界：traceparent 进消息头
                headers = trace_headers()
                seen_trace_ids["headers"] = headers["traceparent"].split("-")[1]

                # worker 进程边界：从消息头恢复（Celery 丢 ContextVar）
                task = type("T", (), {})()
                task.request = type("R", (), {})()
                task.request.headers = headers
                restored = trace_kwargs_from_task(task)
                with bind_runtime_context(
                    session_id="sess-e2e", run_id="run-e2e", **restored
                ):
                    worker_ctx = current_runtime_context()
                    seen_trace_ids["worker"] = worker_ctx.trace_id

                    # worker 内 model 调用失败 → 分类 → 重试决策
                    with pytest.raises(TimeoutError):
                        with start_span(SpanStage.MODEL, "llm_call") as model_span:
                            seen_trace_ids["model"] = model_span.trace_id
                            raise TimeoutError("provider hung")

                    decision = decide_retry(TimeoutError("provider hung"), 1)
                    assert decision.will_retry is True
                    assert decision.category.value == "timeout"

        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})

    inbound = [(b"traceparent",
                b"00-11111111111111111111111111111111-2222222222222222-01")]
    asyncio.run(_drive_app(endpoint, _FakeRequest(inbound)))

    # ── 验收 1：同一 trace id 横跨全部关键模块 ────────────────────────
    assert seen_trace_ids["http"] == "1" * 32  # 入站 trace 被采纳
    for stage in ("harness", "tool", "headers", "worker", "model"):
        assert seen_trace_ids[stage] == "1" * 32, f"{stage} trace 断裂"

    # span 树：harness ← tool，worker 侧恢复后续接 model
    spans = {s.stage + ":" + s.name: s for s in ring.snapshot()}
    harness = spans["harness:plan_turn"]
    tool = spans["tool:submit_ndvi"]
    model = spans["model:llm_call"]
    assert harness.parent_span_id == "2" * 16          # 挂到入站 span
    assert tool.parent_span_id == harness.span_id
    # 本测试是进程内模拟：_CURRENT_SPAN 的父子链在"进程边界"两侧共享，
    # model 的 parent 是 tool。真实 Celery worker 是新进程，parent 为 None
    # （fresh ContextVar）而 trace_id 仍由恢复的 headers 接续——该语义由
    # test_platform_trace_propagation 的 worker 侧用例分别证明。
    assert model.parent_span_id == tool.span_id
    assert model.trace_id == "1" * 32                   # trace 恒定

    # 状态语义：全链路只有 model 一个 error，其余 ok
    assert model.status == SpanStatus.ERROR.value
    assert model.error_category == "timeout"
    assert spans["harness:plan_turn"].status == SpanStatus.OK.value
