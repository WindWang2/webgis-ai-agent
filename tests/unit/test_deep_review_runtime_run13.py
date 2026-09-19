"""RUN-13: the session snapshot must load under the distributed turn lock.

``_get_or_create_session`` writes ``self._sessions[session_id]``. It used to
run BEFORE the distributed turn lock (``session_lock``) with a comment
claiming it took the "same" lock — it does not (``self._session_locks`` is a
plain in-process lock), so while a concurrent turn (possibly on another
replica) was committing, the waiter snapshotted history without it and then
used/overwrote the stale tail.

These tests pin the ordering: the loader runs while the turn lock is held.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.services.chat.execution_engine as ee_mod
from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry


def _make_registry() -> ToolRegistry:
    r = ToolRegistry()

    @r.tool(name="echo_tool", description="echo", tier=1)
    def echo_tool(text: str = "") -> dict:
        return {"echo": text, "success": True}

    return r


class _RecorderLock:
    def __init__(self):
        self.inside = False

    async def acquire(self):
        self.inside = True
        return True

    def release(self):
        self.inside = False

    def locked(self):
        return self.inside

    async def __aenter__(self):
        self.inside = True
        return self

    async def __aexit__(self, *exc_info):
        self.inside = False
        return False


def _order_recording_engine(monkeypatch):
    eng = ChatEngine(_make_registry())
    rec = _RecorderLock()
    monkeypatch.setattr(ee_mod, "session_lock", lambda _sid, **kw: rec)

    load_states: list[bool] = []

    async def _recorded_get_or_create(session_id, user_id=None):
        load_states.append(rec.inside)
        return [{"role": "system", "content": "sys"}]

    monkeypatch.setattr(eng, "_get_or_create_session", _recorded_get_or_create)
    monkeypatch.setattr(eng, "_save_msg_async", AsyncMock(return_value=None))
    monkeypatch.setattr(eng, "_maybe_plan", AsyncMock(return_value=None))
    monkeypatch.setattr(eng, "_generate_title", AsyncMock(return_value=None))
    monkeypatch.setattr(eng, "_persist_map_state", AsyncMock())
    return eng, load_states


@pytest.mark.asyncio
async def test_nostream_snapshot_load_under_turn_lock(monkeypatch):
    eng, load_states = _order_recording_engine(monkeypatch)
    monkeypatch.setattr(
        eng,
        "_chat_locked",
        AsyncMock(return_value={"session_id": "s-run13", "content": "ok"}),
    )
    monkeypatch.setattr(eng, "_flush_plan", AsyncMock())

    result = await eng.chat("hi", session_id="s-run13")

    assert result["content"] == "ok"
    assert load_states == [True], (
        "RUN-13: history snapshot must load while the distributed turn lock "
        "is held, not before it"
    )


@pytest.mark.asyncio
async def test_stream_snapshot_load_under_turn_lock(monkeypatch):
    eng, load_states = _order_recording_engine(monkeypatch)
    monkeypatch.setattr(eng, "_flush_plan", AsyncMock())

    async def fake_stream(*a, **k):
        yield ("done", {"message": {"content": "answer", "tool_calls": None}})

    monkeypatch.setattr(eng, "_call_llm_stream", fake_stream)

    events = []
    async for e in eng.chat_stream("hi", session_id="s-run13-stream"):
        events.append(e)

    assert any("task_complete" in e or "done" in e for e in events)
    assert load_states == [True], (
        "RUN-13: streamed history snapshot must load under the turn lock"
    )
