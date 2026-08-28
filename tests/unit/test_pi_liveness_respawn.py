"""Unit tests for #1038: Pi liveness probe and lazy respawn with backoff.

Validates:
1. Proactive liveness detection via PiRpcClient.is_alive() when process exits.
2. Lazy respawn restores dead PiBridge on subsequent request.
3. Exponential backoff prevents tight respawn crash loops.
4. Chat route self-heals via _ensure_pi_bridge_available().
5. Native tools schema dump is refreshed on respawn.
"""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_pi_bridge import PiBridge
from app.services.chat.pi_rpc_client import PiRpcClient, PiRpcError


@pytest.fixture
def mock_rpc():
    rpc = MagicMock(spec=PiRpcClient)
    rpc.process_died = False
    rpc.is_alive.return_value = True
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    return rpc


def test_pi_rpc_client_proactive_is_alive():
    """is_alive() proactively detects subprocess termination and sets process_died."""
    client = PiRpcClient()
    mock_proc = MagicMock()
    # Process still running
    mock_proc.poll.return_value = None
    client._process = mock_proc
    client._process_died = False

    assert client.is_alive() is True
    assert client.process_died is False

    # Process terminated unexpectedly
    mock_proc.poll.return_value = 1
    assert client.is_alive() is False
    assert client.process_died is True
    assert client.process_died_event.is_set()


@pytest.mark.asyncio
async def test_pi_bridge_respawn_if_dead_success(mock_rpc):
    """respawn_if_dead() successfully respawns a dead subprocess and resets backoff."""
    bridge = PiBridge(rpc=mock_rpc)
    mock_rpc.process_died = True
    mock_rpc.is_alive.return_value = False

    async def _mock_start():
        mock_rpc.process_died = False
        mock_rpc.is_alive.return_value = True

    mock_rpc.start.side_effect = _mock_start

    result = await bridge.respawn_if_dead()
    assert result is True
    assert mock_rpc.start.call_count == 1
    assert bridge.is_alive() is True
    assert bridge._consecutive_respawn_failures == 0
    assert bridge._respawn_backoff_s == 1.0


@pytest.mark.asyncio
async def test_pi_bridge_respawn_backoff_on_failure(mock_rpc):
    """Repeated respawn failures trigger exponential backoff and enforce cooldown."""
    bridge = PiBridge(rpc=mock_rpc)
    mock_rpc.process_died = True
    mock_rpc.is_alive.return_value = False
    mock_rpc.start.side_effect = PiRpcError("Node binary missing")

    # 1. First failure -> backoff set to 1.0s
    res1 = await bridge.respawn_if_dead()
    assert res1 is False
    assert bridge._consecutive_respawn_failures == 1
    assert bridge._respawn_backoff_s == 1.0

    # 2. Immediate second call is within cooldown -> rejected without calling start()
    res2 = await bridge.respawn_if_dead()
    assert res2 is False
    assert mock_rpc.start.call_count == 1  # Not called again

    # 3. Simulate cooldown expiration (2 seconds later)
    bridge._last_respawn_attempt = time.monotonic() - 2.0
    res3 = await bridge.respawn_if_dead()
    assert res3 is False
    assert mock_rpc.start.call_count == 2
    assert bridge._consecutive_respawn_failures == 2
    assert bridge._respawn_backoff_s == 2.0


@pytest.mark.asyncio
async def test_ensure_pi_bridge_available_route_integration():
    """_ensure_pi_bridge_available() triggers lazy respawn on dead bridge and recovers."""
    import app.api.routes.chat as chat_mod

    mock_bridge = MagicMock(spec=PiBridge)
    mock_bridge._process_died = True
    mock_bridge.is_alive.return_value = False
    mock_bridge.respawn_if_dead = AsyncMock(return_value=True)

    with patch.object(chat_mod, "USE_NEW_AGENT", True), \
         patch.object(chat_mod, "pi_bridge", mock_bridge):
        available = await chat_mod._ensure_pi_bridge_available()
        assert available is True
        assert mock_bridge.respawn_if_dead.call_count == 1


@pytest.mark.asyncio
async def test_pi_rpc_client_start_refreshes_native_tools_schema_dump(tmp_path, monkeypatch):
    """PiRpcClient.start() invokes dump_native_tools to refresh schema dump on spawn/respawn."""
    dump_mock = MagicMock(return_value=tmp_path / "native-tools.json")
    monkeypatch.setattr("app.services.chat.pi_native_surface.dump_native_tools", dump_mock)

    dummy_entry = tmp_path / "rpc-entry.js"
    dummy_entry.write_text("// stub", encoding="utf-8")

    client = PiRpcClient(pi_rpc_entry=dummy_entry, session_dir=tmp_path / "sessions")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.stdin = MagicMock()
    fake_proc.stdout = MagicMock()
    fake_proc.stderr = MagicMock()

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: fake_proc)
    monkeypatch.setattr(client, "_wait_for_ready", AsyncMock(return_value=None))

    try:
        await client.start()
        assert dump_mock.call_count == 1
        assert client.is_alive() is True
    finally:
        if client._reader_task:
            client._reader_task.cancel()
        if client._stderr_task:
            client._stderr_task.cancel()
