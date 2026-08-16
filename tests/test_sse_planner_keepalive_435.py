"""#435: SSE keepalive during the planner LLM call (planner-phase sibling of
#409).

In ``chat_stream``, the planning phase runs between the ``task_start`` event
and the first token/tool event with zero SSE output: ``_maybe_plan`` makes a
non-streaming planner LLM call (flat 120s timeout). On planning turns the
stream was silent for up to 120s — idle-timeout proxies (~30-60s) reset
exactly such silent streams, and the resume path then reconnect-loops until
the planner returns. #409 covered the token stream (15s pump) and the
parallel tool wave (5s pump) but not the planner await.

Deterministic: the planner wait is faked with asyncio.sleep; the heartbeat
interval is monkeypatched to tens of milliseconds (no wall-clock waits).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.chat import execution_engine as ee_mod
from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry
from app.utils.sse import sse_event_type


def _event_type(block: str) -> str:
    return sse_event_type(block)


@pytest.fixture
def engine(monkeypatch):
    """Real ChatEngine with DB/title side effects stubbed (planner left real
    enough to be replaced per-test)."""
    eng = ChatEngine(ToolRegistry())

    async def fake_get_or_create_session(session_id, user_id=None):
        return []

    monkeypatch.setattr(eng, "_get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(eng, "_generate_title", AsyncMock())
    monkeypatch.setattr(eng, "_save_msg_async", AsyncMock())
    return eng


async def _collect(agen) -> list[str]:
    out: list[str] = []
    async for item in agen:
        out.append(item)
    return out


def _fast_stream(*args, **kwargs):
    async def _gen():
        yield ("token", {"content": "a"})
        yield ("done", {"message": {"content": "a"}})

    return _gen()


# ─── engine-level: heartbeat while the planner thinks ────────────────────────


@pytest.mark.asyncio
async def test_planner_wait_emits_keepalive_before_first_token(engine, monkeypatch):
    """#435: a slow planner call must not silence the stream — keep_alive
    events arrive between task_start and the first token event."""
    monkeypatch.setattr(ee_mod, "_PLANNER_KEEPALIVE_S", 0.02)

    async def slow_planner(*a, **kw):
        await asyncio.sleep(0.10)  # >> heartbeat interval, << test budget
        return None

    monkeypatch.setattr(engine, "_maybe_plan", slow_planner)
    monkeypatch.setattr(engine, "_call_llm_stream", _fast_stream)

    events = await _collect(engine.chat_stream("做一个成都 POI 分析", session_id="s-plan-kv"))

    types = [_event_type(e) for e in events]
    assert "task_start" in types and "token" in types, types
    ka = [i for i, t in enumerate(types) if t == "keep_alive"]
    assert ka, f"no keep_alive during the planner wait: {types}"
    assert types.index("task_start") < ka[0], "heartbeat must come after task_start"
    assert ka[0] < types.index("token"), "heartbeat must come before the first token"
    assert types[-1] == "done"


@pytest.mark.asyncio
async def test_planner_wait_heartbeats_repeat_for_long_planner(engine, monkeypatch):
    """A planner silent for several heartbeat intervals emits repeated
    keep_alive events (a single ping would not carry a 60s+ wait past a 30s
    idle proxy)."""
    monkeypatch.setattr(ee_mod, "_PLANNER_KEEPALIVE_S", 0.02)

    async def very_slow_planner(*a, **kw):
        await asyncio.sleep(0.12)
        return None

    monkeypatch.setattr(engine, "_maybe_plan", very_slow_planner)
    monkeypatch.setattr(engine, "_call_llm_stream", _fast_stream)

    events = await _collect(engine.chat_stream("plan", session_id="s-plan-kv2"))
    ka = sum(1 for e in events if _event_type(e) == "keep_alive")
    assert ka >= 2, f"expected repeated heartbeats, got {ka}"


@pytest.mark.asyncio
async def test_fast_planner_no_spurious_heartbeat(engine, monkeypatch):
    """A planner that returns quickly must not change the event stream shape:
    no keep_alive fires between task_start and the first token."""
    monkeypatch.setattr(ee_mod, "_PLANNER_KEEPALIVE_S", 5.0)

    async def fast_planner(*a, **kw):
        return None

    monkeypatch.setattr(engine, "_maybe_plan", fast_planner)
    monkeypatch.setattr(engine, "_call_llm_stream", _fast_stream)

    events = await _collect(engine.chat_stream("hi", session_id="s-plan-fast"))
    types = [_event_type(e) for e in events]
    assert "keep_alive" not in types, types
    assert types[0] == "task_start"


@pytest.mark.asyncio
async def test_planner_exception_still_degrades_to_no_plan(engine, monkeypatch):
    """_maybe_plan normally swallows its own exceptions; if it ever raises,
    the keepalive wrapper must propagate the exception (not swallow it into a
    hanging or silent stream) — same contract as the bare await it replaces."""
    monkeypatch.setattr(ee_mod, "_PLANNER_KEEPALIVE_S", 0.02)

    async def exploding_planner(*a, **kw):
        await asyncio.sleep(0.05)
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(engine, "_maybe_plan", exploding_planner)
    monkeypatch.setattr(engine, "_call_llm_stream", _fast_stream)

    with pytest.raises(RuntimeError, match="planner exploded"):
        await _collect(engine.chat_stream("q", session_id="s-plan-err"))


@pytest.mark.asyncio
async def test_disconnect_cancels_planner_wait(engine, monkeypatch):
    """Client disconnect during the planner wait must cancel the planner
    coroutine — the wrapper must not leave the LLM call running detached
    (semantics of the bare await it replaces)."""
    monkeypatch.setattr(ee_mod, "_PLANNER_KEEPALIVE_S", 0.02)

    cancelled = {"flag": False}

    async def slow_planner(*a, **kw):
        try:
            await asyncio.sleep(2.0)
            return None
        except asyncio.CancelledError:
            cancelled["flag"] = True
            raise

    monkeypatch.setattr(engine, "_maybe_plan", slow_planner)
    monkeypatch.setattr(engine, "_call_llm_stream", _fast_stream)

    gen = engine.chat_stream("q", session_id="s-plan-disc")
    # Drive the generator until it is provably inside the planner wait: the
    # first keep_alive is yielded by the wait pump, so the planner task is
    # guaranteed to be in flight at that suspension point.
    saw_heartbeat = False
    deadline = asyncio.get_event_loop().time() + 2.0
    while not saw_heartbeat:
        assert asyncio.get_event_loop().time() < deadline, "no keep_alive arrived"
        if _event_type(await gen.__anext__()) == "keep_alive":
            saw_heartbeat = True
    assert saw_heartbeat
    await gen.aclose()

    await asyncio.sleep(0.05)
    assert cancelled["flag"], "planner coroutine was not cancelled on disconnect"


@pytest.mark.asyncio
async def test_heartbeat_payload_is_sse_event_shaped(engine, monkeypatch):
    """The heartbeat must be produced via the sse_event helper (invariant #6:
    SSE Event Format Consistency) — 'event: keep_alive\\ndata: {...}\\n\\n'."""
    monkeypatch.setattr(ee_mod, "_PLANNER_KEEPALIVE_S", 0.02)

    async def slow_planner(*a, **kw):
        await asyncio.sleep(0.08)
        return None

    monkeypatch.setattr(engine, "_maybe_plan", slow_planner)
    monkeypatch.setattr(engine, "_call_llm_stream", _fast_stream)

    events = await _collect(engine.chat_stream("q", session_id="s-plan-shape"))
    heartbeats = [e for e in events if _event_type(e) == "keep_alive"]
    assert heartbeats
    for block in heartbeats:
        assert block.startswith("event: keep_alive\ndata: ")
        assert block.endswith("\n\n")
