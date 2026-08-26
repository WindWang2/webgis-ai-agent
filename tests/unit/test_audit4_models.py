"""audit4 (#985-#987, #997, #1005 部分): 模型库护栏测试。

覆盖: usage 采集（payload/流式/证据记账）、连接相位重试、角色化配置解析、
运行时覆盖传播、标题调用预算、缓存头移除。
"""
import json

import httpx
import pytest

from app.services.chat.llm_client import (
    LLMConfig,
    _build_headers,
    _build_payload,
    call_llm,
    call_llm_stream,
    _registry,
)
from app.services.chat import model_config
from app.services.chat.model_config import ModelRole, resolve_llm_config
from app.lib.runtime.evidence import TurnEvidence


@pytest.fixture(autouse=True)
def _hermetic_pool(monkeypatch):
    """每个测试独享 mock 池（无真实 socket），并隔离运行时覆盖。"""
    _registry._reset_for_tests()
    model_config.clear_runtime_overrides()
    yield
    _registry._reset_for_tests()
    model_config.clear_runtime_overrides()


def _cfg(**kw):
    base = dict(base_url="http://provider.test/v1", model="m-test", api_key="k-test")
    base.update(kw)
    return LLMConfig(**base)


# ── #985: usage 采集 ───────────────────────────────────────────────────────

def test_stream_payload_requests_usage():
    payload = _build_payload(_cfg(), [{"role": "user", "content": "hi"}], None, stream=True)
    assert payload["stream_options"] == {"include_usage": True}


def test_nonstream_payload_has_no_stream_options():
    payload = _build_payload(_cfg(), [{"role": "user", "content": "hi"}], None, stream=False)
    assert "stream_options" not in payload


def test_temperature_included_only_when_set():
    assert "temperature" not in _build_payload(_cfg(), [], None, False)
    assert _build_payload(_cfg(temperature=0.2), [], None, False)["temperature"] == 0.2


def _stream_sse_response(chunks: list[dict], status: int = 200) -> httpx.Response:
    body = "".join(
        f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks
    ) + "data: [DONE]\n\n"
    return httpx.Response(
        status, headers={"content-type": "text/event-stream"},
        content=body.encode("utf-8"),
    )


@pytest.mark.asyncio
async def test_stream_done_event_carries_usage(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"content": "你好"}}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return _stream_sse_response(chunks)

    async with _mock_pool(monkeypatch, handler):
        events = []
        async for ev in call_llm_stream(_cfg(), [{"role": "user", "content": "hi"}]):
            events.append(ev)
    done = [e for e in events if e[0] == "done"][-1]
    assert done[1]["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert done[1]["message"]["content"] == "你好"


class _mock_pool:
    """把进程级池化客户端替换为 MockTransport 支撑的实例（同一事件循环内）。"""

    def __init__(self, monkeypatch, handler):
        self._monkeypatch = monkeypatch
        self._handler = handler

    async def __aenter__(self):
        import app.services.chat.llm_client as lc
        handler = self._handler

        class _FakeRegistry:
            created_count = 0

            async def acquire(self, base_url: str) -> httpx.AsyncClient:
                return httpx.AsyncClient(transport=httpx.MockTransport(handler))

            def active_client_count(self):
                return 1

        self._orig = lc._registry
        lc._registry = _FakeRegistry()
        return self

    async def __aexit__(self, *exc):
        import app.services.chat.llm_client as lc
        lc._registry = self._orig


def test_evidence_add_llm_usage_accumulates():
    ev = TurnEvidence(request_id=None, session_id=None, turn_id="t1", run_id=None)
    ev.add_llm_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    ev.add_llm_usage({"prompt_tokens": 1, "completion_tokens": 2})  # total 缺失 → 自算
    ev.add_llm_usage(None)  # 安全跳过
    ev.add_llm_usage({"prompt_tokens": "x"})  # 非法值跳过
    assert ev.prompt_tokens == 11
    assert ev.completion_tokens == 7
    assert ev.total_tokens == 18
    assert ev.llm_usage_reports == 2
    summary = ev.to_summary()
    assert summary["llm_usage"]["total_tokens"] == 18


# ── #986: 连接相位重试 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_llm_retries_connect_error(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 3}})

    async with _mock_pool(monkeypatch, handler):
        resp = await call_llm(_cfg(), [{"role": "user", "content": "hi"}])
    assert calls["n"] == 2
    assert resp["usage"]["total_tokens"] == 3


@pytest.mark.asyncio
async def test_call_llm_retries_429(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with _mock_pool(monkeypatch, handler):
        await call_llm(_cfg(), [{"role": "user", "content": "hi"}])
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_call_llm_no_retry_on_400(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    async with _mock_pool(monkeypatch, handler):
        with pytest.raises(httpx.HTTPStatusError):
            await call_llm(_cfg(), [{"role": "user", "content": "hi"}])
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_stream_retries_pre_yield_429(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="unavailable")
        return _stream_sse_response([{"choices": [{"delta": {"content": "hi"}}]}])

    async with _mock_pool(monkeypatch, handler):
        events = []
        async for ev in call_llm_stream(_cfg(), [{"role": "user", "content": "x"}]):
            events.append(ev)
    assert calls["n"] == 2
    assert any(e[0] == "done" for e in events)


# ── #1005 部分: 无效缓存头移除 ────────────────────────────────────────────

def test_no_nonstandard_cache_headers():
    headers = _build_headers(_cfg(use_prompt_caching=True))
    assert "X-Prompt-Cache" not in headers
    assert "deepseek-caching" not in headers


# ── #997: 角色化解析 + 运行时覆盖 ──────────────────────────────────────────

def test_resolve_llm_config_roles(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://s.test/v1", raising=False)
    monkeypatch.setattr(settings, "LLM_MODEL", "exec-model", raising=False)
    monkeypatch.setattr(settings, "LLM_PLANNER_MODEL", "plan-model", raising=False)
    monkeypatch.setattr(settings, "LLM_TITLE_MODEL", "", raising=False)
    monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 9999, raising=False)
    monkeypatch.setattr(settings, "LLM_TEMPERATURE", None, raising=False)
    monkeypatch.setattr(settings, "LLM_TIMEOUT_S", 77.0, raising=False)

    exec_cfg = resolve_llm_config(ModelRole.EXECUTION)
    assert exec_cfg.model == "exec-model"
    assert exec_cfg.max_tokens == 9999
    assert exec_cfg.timeout_s == 77.0

    assert resolve_llm_config(ModelRole.PLANNER).model == "plan-model"
    title = resolve_llm_config(ModelRole.TITLE)
    assert title.model == "exec-model"  # LLM_TITLE_MODEL 空 → 回退
    assert title.max_tokens == 512      # #1005: 64 → 512
    assert resolve_llm_config(ModelRole.SPATIAL).max_tokens == 4096


def test_runtime_override_reaches_all_roles(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "LLM_MODEL", "old-model", raising=False)
    model_config.set_runtime_override(model="new-model")
    for role in ModelRole:
        if role is ModelRole.PLANNER:
            monkeypatch.setattr(settings, "LLM_PLANNER_MODEL", "", raising=False)
        if role is ModelRole.TITLE:
            monkeypatch.setattr(settings, "LLM_TITLE_MODEL", "", raising=False)
        assert resolve_llm_config(role).model == "new-model", role


# ── #1006: session_overview 排除 XML 合成载体消息 ──────────────────────────

def test_session_overview_excludes_synthetic_tool_carriers():
    from app.services.chat.context.session_overview import build_session_overview

    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "成都的小学分布"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "[工具执行结果]\nsearch_poi: ..."},  # XML 载体
        {"role": "assistant", "content": "分析中"},
        {"role": "user", "content": "[工具执行结果]\nbuffer: ..."},   # XML 载体
        {"role": "user", "content": "再算一下密度"},                  # 真实提问
    ]
    import asyncio as _aio
    overview = _aio.run(build_session_overview("s-x", messages=msgs, _fetched=True))
    joined = overview or ""
    assert "2 轮提问" in joined, f"合成载体被计入轮数: {joined!r}"
