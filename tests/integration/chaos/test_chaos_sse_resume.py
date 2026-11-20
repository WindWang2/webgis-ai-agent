"""Chaos — SSE 断线重连：Last-Event-ID 续传（P7，#371/#419 谱系）。

客户端在流中途真实断开（abort），以最后收到的 Last-Event-ID 重连 —— 断言
续传语义：服务端接受同一会话 marker，回放未读尾部并诚实报告中断，
不会从头重放、重新执行或把被取消的 turn 伪装成成功。
"""
from __future__ import annotations

import json

import httpx
import pytest

pytestmark = pytest.mark.heavy

# Existing deterministic stub's plain-text branch: no unrelated GIS dependencies.
_PROBE = "建立会话"


def _parse_sse_events(text: str) -> list[tuple[int | None, str | None, dict | str]]:
    events = []
    # Ignore a trailing partial frame: a transport chunk need not end on a frame.
    for block in text.replace("\r\n", "\n").split("\n\n")[:-1]:
        event_id = None
        name = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("id:"):
                event_id = int(line[3:].strip())
            elif line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            raw = "\n".join(data_lines)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            events.append((event_id, name, payload))
    return events


def test_stream_resume_after_client_abort(chaos_stack, chaos_pi_api) -> None:
    base = chaos_pi_api["base"]
    with httpx.Client(timeout=40.0, trust_env=False) as client:
        # Close a real live HTTP stream after the server issues its canonical
        # session + ownership capability and Pi confirms prompt dispatch, before
        # the delayed model can finish. Session alone precedes Pi execution.
        with client.stream("POST", f"{base}/api/v1/chat/stream",
                           json={"message": _PROBE}) as resp:
            assert resp.status_code == 200
            buf = ""
            seen = []
            for chunk in resp.iter_text():
                buf += chunk
                seen = _parse_sse_events(buf)
                if any(name == "task_start" for _, name, _ in seen):
                    break
        starts = [payload for _, name, payload in seen if name == "task_start"]
        assert starts and starts[0].get("agent_runtime") == "pi", seen
        session_events = [(eid, payload) for eid, name, payload in seen
                          if name == "session"]
        assert len(session_events) == 1, seen
        _, session = session_events[0]
        assert isinstance(session, dict)
        assert session.get("session_id") and session.get("owner_token"), session
        assert not any(name in ("done", "task_complete", "error")
                       for _, name, _ in seen), "must abort before completion"
        ids = [eid for eid, _, _ in seen if eid is not None]
        assert ids, "resume requires a numeric cursor, not an event name"
        last_id = max(ids)

    with httpx.Client(timeout=40.0, trust_env=False) as client:
        resp = client.post(
            f"{base}/api/v1/chat/stream",
            json={"message": _PROBE, "session_id": session["session_id"]},
            headers={"Last-Event-ID": str(last_id),
                     "X-Session-Token": session["owner_token"]})
        # A fresh, owned cursor must hit its buffer; 404/409 would hide a
        # broken fixture/ownership path, not prove successful resumption.
        assert resp.status_code == 200, resp.text
        events = _parse_sse_events(resp.text)
        assert events, "续传流不应为空"
        replay_ids = [eid for eid, _, _ in events if eid is not None]
        assert all(eid > last_id for eid in replay_ids), events
        assert replay_ids == sorted(set(replay_ids)), events
        assert not any(name in ("session", "task_start", "done", "task_complete")
                       for _, name, _ in events), events
        # Real disconnect cancels this producer. Resume must truthfully report
        # the interrupted turn, never a new turn or a fabricated completion.
        _, name, terminal = events[-1]
        assert name == "error" and isinstance(terminal, dict), events
        assert terminal.get("resumed") is True, terminal
        assert terminal.get("session_id") == session["session_id"], terminal
        assert not terminal.get("pending"), terminal
