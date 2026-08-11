"""C-F12: per-session cap on the in-memory conversation tail.

Finding C-F12 (findings-C-backend-async.md): ``ChatExecutionEngine._sessions``
is an LRU bounded by *session count* (SESSION_CACHE_SIZE, default 200), but
each resident session's ``messages`` list was appended to every turn and never
trimmed — a long-lived streaming session accumulates hundreds of turns in
memory (tens of MB) while the LLM only ever reads the recent tail
(``truncate_history_by_budget``, ~6000-token budget). The DB is the source of
truth (every append is also persisted via ``_save_msg_async``); the cache only
needs the recent tail, bounded by SESSION_MESSAGE_CAP.
"""
import os

import pytest
from unittest.mock import AsyncMock, patch

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry

SIMPLE_RESPONSE = {"choices": [{"message": {"content": "ok", "tool_calls": None}}]}


def _new_engine(monkeypatch, message_cap: int) -> ChatEngine:
    """Fresh engine whose per-session message cap is read from env at __init__."""
    monkeypatch.setenv("SESSION_MESSAGE_CAP", str(message_cap))
    return ChatEngine(ToolRegistry())


def _seed(engine: ChatEngine, session_id: str, old_turns: int) -> list:
    """Seed a session with a system prompt plus ``old_turns`` user/assistant pairs."""
    messages = [{"role": "system", "content": "sys"}]
    for i in range(old_turns):
        messages.append({"role": "user", "content": f"u{i}"})
        messages.append({"role": "assistant", "content": f"a{i}"})
    engine._sessions[session_id] = messages
    return messages


# ── non-streaming path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_messages_bounded_across_turns(monkeypatch):
    """F12 core: the in-memory message list must not grow without bound.

    Pre-fix the list holds system + 2 messages per turn (41 after 20 turns);
    with the per-session cap it must stay <= SESSION_MESSAGE_CAP.
    """
    engine = _new_engine(monkeypatch, message_cap=10)
    engine._sessions["s1"] = [{"role": "system", "content": "sys"}]

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=SIMPLE_RESPONSE), \
         patch.object(engine, "_save_msg_async", new_callable=AsyncMock), \
         patch.object(engine, "_maybe_plan", new_callable=AsyncMock, return_value=None), \
         patch.object(engine, "_compose_request_messages", new_callable=AsyncMock,
                      side_effect=lambda sid, msgs: msgs):
        for _ in range(20):
            await engine.chat("hi", session_id="s1")

    assert len(engine._sessions["s1"]) <= 10


@pytest.mark.asyncio
async def test_trim_keeps_system_prompt_and_newest_turn(monkeypatch):
    """The trim must keep messages[0] (the system prompt the context assembler
    reads unconditionally) and the newest complete turn."""
    engine = _new_engine(monkeypatch, message_cap=10)
    engine._sessions["s1"] = [{"role": "system", "content": "sys"}]

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=SIMPLE_RESPONSE), \
         patch.object(engine, "_save_msg_async", new_callable=AsyncMock), \
         patch.object(engine, "_maybe_plan", new_callable=AsyncMock, return_value=None), \
         patch.object(engine, "_compose_request_messages", new_callable=AsyncMock,
                      side_effect=lambda sid, msgs: msgs):
        for _ in range(20):
            await engine.chat("hi", session_id="s1")

    tail = engine._sessions["s1"]
    assert tail[0] == {"role": "system", "content": "sys"}
    assert tail[-2:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]


@pytest.mark.asyncio
async def test_trim_evicts_only_oldest_turns_never_splits_a_turn(monkeypatch):
    """Eviction drops whole turns (user message + its followers) from the
    front — the kept window always starts at a turn boundary, so a
    user/assistant/tool chain can never be orphaned mid-turn."""
    engine = _new_engine(monkeypatch, message_cap=6)
    _seed(engine, "s1", old_turns=10)  # 1 + 20 messages

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=SIMPLE_RESPONSE), \
         patch.object(engine, "_save_msg_async", new_callable=AsyncMock), \
         patch.object(engine, "_maybe_plan", new_callable=AsyncMock, return_value=None), \
         patch.object(engine, "_compose_request_messages", new_callable=AsyncMock,
                      side_effect=lambda sid, msgs: msgs):
        await engine.chat("hi", session_id="s1")

    # 1 + 10 turns + 1 new turn -> trimmed to system + newest 2 turns (5 msgs).
    assert engine._sessions["s1"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u9"},
        {"role": "assistant", "content": "a9"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]


@pytest.mark.asyncio
async def test_trim_drops_oversized_older_turn_whole(monkeypatch):
    """An oversized OLDER turn is dropped entirely — the kept tail never
    contains an orphaned tool message or an assistant tool_calls without its
    results (a split chain is invalid for the LLM API)."""
    engine = _new_engine(monkeypatch, message_cap=6)
    _seed(engine, "s1", old_turns=8)
    engine._sessions["s1"].extend([
        {"role": "assistant", "content": "a8", "tool_calls": [{"id": f"call{i}", "function": {"name": "t", "arguments": "{}"}} for i in range(5)]},
        *[{"role": "tool", "tool_call_id": f"call{i}", "content": "r"} for i in range(5)],
        {"role": "assistant", "content": "a9"},
    ])

    with patch.object(engine, "_call_llm", new_callable=AsyncMock, return_value=SIMPLE_RESPONSE), \
         patch.object(engine, "_save_msg_async", new_callable=AsyncMock), \
         patch.object(engine, "_maybe_plan", new_callable=AsyncMock, return_value=None), \
         patch.object(engine, "_compose_request_messages", new_callable=AsyncMock,
                      side_effect=lambda sid, msgs: msgs):
        await engine.chat("hi", session_id="s1")

    tail = engine._sessions["s1"]
    assert tail[0] == {"role": "system", "content": "sys"}
    # The oversized tool chain was dropped as a whole — no orphaned tool
    # messages survived, and the newest turn is intact.
    assert all(m.get("role") != "tool" for m in tail)
    assert tail[-2:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]


def test_trim_keeps_oversized_newest_turn_whole(monkeypatch):
    """A single NEWEST turn larger than the cap is kept whole (never split) —
    the bound degrades gracefully to one turn's worth of messages."""
    engine = _new_engine(monkeypatch, message_cap=6)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a", "tool_calls": [{"id": f"call{i}", "function": {"name": "t", "arguments": "{}"}} for i in range(5)]},
        *[{"role": "tool", "tool_call_id": f"call{i}", "content": "r"} for i in range(5)],
        {"role": "assistant", "content": "a2"},
    ]
    engine._trim_session_tail(messages)
    # 1 system + 8 messages, cap 6 — kept whole: the user/assistant/tool chain
    # is never split, so the in-memory bound degrades to one oversized turn.
    assert messages == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a", "tool_calls": [{"id": f"call{i}", "function": {"name": "t", "arguments": "{}"}} for i in range(5)]},
        *[{"role": "tool", "tool_call_id": f"call{i}", "content": "r"} for i in range(5)],
        {"role": "assistant", "content": "a2"},
    ]


# ── streaming path ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_stream_messages_bounded_across_turns(monkeypatch):
    """The streaming path must respect the same per-session cap."""
    engine = _new_engine(monkeypatch, message_cap=10)
    engine._sessions["s1"] = [{"role": "system", "content": "sys"}]

    def fake_stream(*a, **kw):
        async def gen():
            yield ("done", {"message": {"content": "ok", "tool_calls": None}})
        return gen()

    async def fake_generate_title(*a, **kw):
        return None

    with patch.object(engine, "_call_llm_stream", side_effect=fake_stream), \
         patch.object(engine, "_save_msg_async", new_callable=AsyncMock), \
         patch.object(engine, "_maybe_plan", new_callable=AsyncMock, return_value=None), \
         patch.object(engine, "_compose_request_messages", new_callable=AsyncMock,
                      side_effect=lambda sid, msgs: msgs), \
         patch.object(engine, "_generate_title", fake_generate_title):
        for _ in range(20):
            async for _ev in engine.chat_stream("hi", session_id="s1"):
                pass

    assert len(engine._sessions["s1"]) <= 10
    assert engine._sessions["s1"][0] == {"role": "system", "content": "sys"}
    assert engine._sessions["s1"][-2:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]


# ── configuration ─────────────────────────────────────────────────────────


def test_session_message_cap_configurable(monkeypatch):
    monkeypatch.setenv("SESSION_MESSAGE_CAP", "64")
    engine = ChatEngine(ToolRegistry())
    assert engine._session_message_cap == 64


def test_session_message_cap_default_is_200():
    saved = os.environ.pop("SESSION_MESSAGE_CAP", None)
    try:
        engine = ChatEngine(ToolRegistry())
        assert engine._session_message_cap == 200
    finally:
        if saved is not None:
            os.environ["SESSION_MESSAGE_CAP"] = saved
