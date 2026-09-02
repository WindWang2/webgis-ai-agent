"""#1108 — Pi bridge turn-lock must not leak under CancelledError in cleanup.

Disconnect storms can re-deliver ``CancelledError`` while ``stream_prompt`` /
``prompt`` finally awaits Redis unregister (or while register runs after
acquire but before the main try). Pre-fix the await was bare, so the error
skipped ``self._lock.release()`` and the singleton PiBridge hung every session
until process restart.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge

# Warm chat routes so first-turn import cost does not blow lock-wait budgets.
import app.api.routes.chat  # noqa: F401

from app.services.chat.pi_bridge_cancel_safety import install as _install_1108

_install_1108()


def _make_rpc() -> MagicMock:
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    return rpc


@pytest.fixture(autouse=True)
def _clean_bridge_state():
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    saved = (bridge_mod._dispatch_service, bridge_mod._dispatch_service_registry)
    bridge_mod._dispatch_service = None
    bridge_mod._dispatch_service_registry = None
    yield
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    bridge_mod._dispatch_service, bridge_mod._dispatch_service_registry = saved


@pytest.mark.asyncio
async def test_stream_prompt_releases_lock_when_unregister_raises_cancelled(monkeypatch):
    """CancelledError from unregister_active_pi_turn must not skip lock.release()."""
    rpc = _make_rpc()

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=fake_request)
    bridge = PiBridge(rpc=rpc)

    async def boom_unregister(session_id, turn_id):
        raise asyncio.CancelledError()

    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", boom_unregister)
    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", AsyncMock())

    try:
        async for _ in bridge.stream_prompt("hi", session_id="sess-1108-unreg"):
            pass
    except asyncio.CancelledError:
        pass

    assert bridge._lock.locked() is False


@pytest.mark.asyncio
async def test_stream_prompt_releases_lock_when_register_raises_cancelled(monkeypatch):
    """CancelledError during register (after acquire) must still release the lock."""
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)

    async def boom_register(session_id, turn_id):
        raise asyncio.CancelledError()

    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", boom_register)
    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        async for _ in bridge.stream_prompt("hi", session_id="sess-1108-reg"):
            pass

    assert bridge._lock.locked() is False


@pytest.mark.asyncio
async def test_prompt_releases_lock_when_unregister_raises_cancelled(monkeypatch):
    """Non-streaming prompt() finally has the same unregister/release invariant."""
    rpc = _make_rpc()

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=fake_request)
    bridge = PiBridge(rpc=rpc)

    async def boom_unregister(session_id, turn_id):
        raise asyncio.CancelledError()

    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", boom_unregister)
    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", AsyncMock())

    try:
        result = await bridge.prompt("hi", session_id="sess-1108-prompt")
        assert result["content"] == ""
    except asyncio.CancelledError:
        pass
    assert bridge._lock.locked() is False


@pytest.mark.asyncio
async def test_stream_prompt_lock_free_after_cancel_during_hanging_unregister(monkeypatch):
    """Second cancel delivered while unregister awaits must unlock the bridge."""
    rpc = _make_rpc()

    async def fake_request(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({"type": "agent_end", "willRetry": False})
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=fake_request)
    bridge = PiBridge(rpc=rpc)

    started = asyncio.Event()
    release = asyncio.Event()

    async def hanging_unregister(session_id, turn_id):
        started.set()
        await release.wait()

    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", hanging_unregister)
    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", AsyncMock())

    async def run():
        async for _ in bridge.stream_prompt("hi", session_id="sess-1108-hang"):
            pass

    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), timeout=2.0)
    task.cancel()
    # Shielded unregister may outlive the wait_for; unblock it for clean shutdown.
    # Catching CancelledError around the shielded cleanup await suppresses the
    # task cancellation (by design — lock release must win); the task may
    # therefore finish normally. Either outcome is fine as long as the lock
    # is unlocked afterwards.
    release.set()
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except asyncio.CancelledError:
        pass

    assert bridge._lock.locked() is False
