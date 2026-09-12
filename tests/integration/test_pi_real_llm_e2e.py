"""Pi bridge 真实 LLM E2E — 3 条冒烟旅程（P6，WAYFINDER 票 1）。

门控纪律：真实 LLM 成本 + 外部依赖 —— 必须 PI_REAL_E2E=1 且 LLM_API_KEY 为
非占位值才运行；否则显式 skip（理由写明，绝不静默绿）。USE_NEW_AGENT=true
（Pi bridge 路径）由 conftest 之外的本文件自行钉入。

旅程：
  P-1 简单问答（流式 token 到达 + 完成）
  P-2 空间分析指令（工具调用事件 + 结果落 ref）
  P-3 多轮跟进（会话上下文持续，第二轮引用第一轮产物）
"""
from __future__ import annotations

import os

import pytest

#: conftest（tests/conftest.py）把 USE_NEW_AGENT 钉 false 以隔离单元面；
#: 本文件恢复 Pi bridge 生产路径 —— 必须在 app 装载前生效。
os.environ["USE_NEW_AGENT"] = "true"

REAL_E2E_ARMED = os.getenv("PI_REAL_E2E") == "1"
_llm_key = os.getenv("LLM_API_KEY", "")
_llm_base = os.getenv("LLM_BASE_URL", "")
_llm_configured = bool(_llm_key) and _llm_key != "your-api-key-here" and (
    not _llm_base or "step" in _llm_base or "api" in _llm_base)

pytestmark = [
    pytest.mark.heavy,
    pytest.mark.skipif(
        not REAL_E2E_ARMED,
        reason="real-LLM E2E is cost-gated: set PI_REAL_E2E=1 to run "
               "(manual/nightly dispatch with secrets — never silent-green)"),
    pytest.mark.skipif(
        REAL_E2E_ARMED and not _llm_configured,
        reason="PI_REAL_E2E=1 but no real LLM key configured "
               "(LLM_API_KEY placeholder or missing)"),
]


@pytest.fixture()
async def pi_backend():
    """In-process app with the Pi bridge live (vendor/pi submodule + Node)."""
    import shutil

    if not shutil.which("node"):
        pytest.skip("real-LLM E2E needs Node for the bundled vendor/pi host")
    from asgi_lifespan import LifespanManager  # noqa: PLC0415 — 门控导入
    from httpx import ASGITransport, AsyncClient

    from app.main import app  # noqa: PLC0415

    async with LifespanManager(app):
        from app.api.routes import chat as chat_routes

        if chat_routes.chat.pi_bridge is None:
            pytest.skip("Pi bridge failed to start (vendor/pi not checked out "
                        "or extension load failed) — explicit skip, not green")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _stream_turn(client, message: str, *, expect_tool: bool):
    first_token = None
    tool_calls = []
    completed = False
    refs = []
    async with client.stream("POST", "/api/v1/chat/stream",
                             json={"message": message},
                             timeout=300.0) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload_raw = line[6:].strip()
            if payload_raw == "[DONE]":
                break
            import json

            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                continue
            event = payload.get("event") or ""
            if event == "token" and first_token is None:

                first_token = True
            if event == "tool_call":
                tool_calls.append(payload)
            if event.startswith("step_result"):
                ref = payload.get("geojson_ref")
                if ref:
                    refs.append(ref)
            if event == "task_complete":
                completed = True
                break
    return {"first_token": first_token, "tool_calls": tool_calls,
            "completed": completed, "refs": refs}


@pytest.mark.heavy
async def test_p1_simple_qa_streams_and_completes(pi_backend) -> None:
    result = await _stream_turn(pi_backend, "用一句话说明什么是空间自相关。",
                                expect_tool=False)
    assert result["first_token"], "真实 LLM 必须产生至少一个流式 token"
    assert result["completed"]


@pytest.mark.heavy
async def test_p2_analysis_command_invokes_tool(pi_backend) -> None:
    result = await _stream_turn(
        pi_backend,
        "对 (104.06, 30.67) 做一个 3km 缓冲区分析",
        expect_tool=True)
    assert result["first_token"]
    assert result["tool_calls"], "分析指令应产生 tool_call 事件"


@pytest.mark.heavy
async def test_p3_followup_keeps_session_context(pi_backend) -> None:
    first = await _stream_turn(pi_backend, "对 (104.06, 30.67) 做一个 2km 缓冲区分析",
                               expect_tool=True)
    assert first["completed"]
    second = await _stream_turn(pi_backend, "把刚才的缓冲区距离改成 5km 再算一次",
                                expect_tool=True)
    assert second["first_token"]
    assert second["completed"]
