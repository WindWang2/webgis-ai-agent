"""Review F9 (<think> split across chunks) and F10 (``data:`` without space)."""
import json

import httpx
import pytest

import app.services.chat.llm_client as lc


def _sse(parts, nospace=False, done_nospace=False):
    pre = "data:" if nospace else "data: "
    out = ""
    for p in parts:
        out += pre + json.dumps({"choices": [{"delta": {"content": p}, "finish_reason": None}]}) + "\n\n"
    out += pre + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n"
    out += ("data:[DONE]" if done_nospace else "data: [DONE]") + "\n\n"
    return out


async def _run(monkeypatch, body):
    tr = httpx.MockTransport(lambda req: httpx.Response(
        200, text=body, headers={"content-type": "text/event-stream"}))
    client = httpx.AsyncClient(transport=tr)

    async def fake_get(base_url):
        return client

    monkeypatch.setattr(lc, "get_llm_http_client", fake_get)
    cfg = lc.LLMConfig(base_url="http://x", model="m", api_key="k")
    toks, done = [], None
    async for ev, d in lc._call_llm_stream_attempt(cfg, [{"role": "user", "content": "hi"}]):
        if ev == "token":
            toks.append((d["content"], d.get("is_reasoning", False)))
        else:
            done = d
    return toks, done["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("parts", [
    ["<thi", "nk>SECRET REASONING</think>", "answer"],
    ["<", "think>SECRET REASONING</", "think>answer"],
    ["<think>SECRET ", "REASONING</th", "ink>", "answer"],
])
async def test_split_think_tags_never_leak(monkeypatch, parts):
    toks, msg = await _run(monkeypatch, _sse(parts))
    assert msg["content"] == "answer"
    assert "SECRET" not in "".join(t for t, r in toks if not r)
    assert msg.get("reasoning_content") == "SECRET REASONING"


@pytest.mark.asyncio
async def test_lone_angle_bracket_is_flushed(monkeypatch):
    toks, msg = await _run(monkeypatch, _sse(["a < b", " and x<"]))
    assert msg["content"] == "a < b and x<"


@pytest.mark.asyncio
async def test_data_without_space_is_parsed(monkeypatch):
    toks, msg = await _run(monkeypatch, _sse(["hello"], nospace=True, done_nospace=True))
    assert msg["content"] == "hello"
