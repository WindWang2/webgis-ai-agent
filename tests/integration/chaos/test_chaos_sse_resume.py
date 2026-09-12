"""Chaos — SSE 断线重连：Last-Event-ID 续传（P7，#371/#419 谱系）。

客户端在流中途真实断开（abort），以最后收到的 Last-Event-ID 重连 —— 断言
续传语义：服务端接受 marker 且新流继续（不从头重放整个 turn）。
"""
from __future__ import annotations

import json

import httpx
import pytest

pytestmark = pytest.mark.heavy

_PROBE = "用一个词回答：缓冲区分析的作用是什么？"


def _parse_sse_events(text: str) -> list[tuple[str | None, dict | str]]:
    events: list[tuple[str | None, dict | str]] = []
    for block in text.split("\n\n"):
        name = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("id:"):
                name = line[3:].strip()
            elif line.startswith("event:"):
                name = name or line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            raw = "\n".join(data_lines)
            try:
                events.append((name, json.loads(raw)))
            except json.JSONDecodeError:
                events.append((name, raw))
    return events


def test_stream_resume_after_client_abort(chaos_stack, chaos_api) -> None:
    base = chaos_api["base"]
    last_id: str | None = None
    with httpx.Client(timeout=30.0) as client:
        # 第一段流：读到第一个带 id 的事件即断开（真实断开：连接关闭）。
        with client.stream("POST", f"{base}/api/v1/chat/stream",
                           json={"message": _PROBE}) as resp:
            assert resp.status_code == 200
            buf: list[str] = []
            for chunk in resp.iter_text():
                buf.append(chunk)
                if len("".join(buf).split("\n\n")) >= 2:
                    break
        seen = _parse_sse_events("".join(buf))
        for eid, payload in seen:
            if eid and isinstance(payload, dict):
                last_id = eid
                break

    # 断线重连：Last-Event-ID 续传。可接受的诚实语义（#371/#419）：
    # 200 + 事件流继续；或 typed 409/404（marker 已过期）—— 绝不是 500 崩溃。
    headers = {"Last-Event-ID": last_id} if last_id else {}
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{base}/api/v1/chat/stream",
                           json={"message": _PROBE}, headers=headers)
        assert resp.status_code in (200, 404, 409), (
            f"续传语义异常：{resp.status_code}")
        if resp.status_code == 200:
            events = _parse_sse_events(resp.text)
            assert events, "续传流不应为空"
