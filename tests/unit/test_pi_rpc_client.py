"""Unit tests for app.services.chat.pi_rpc_client.PiRpcClient.

Tests subprocess initialization, request-response matching, and event queueing.
"""
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from app.services.chat.pi_rpc_client import PiRpcClient
from app.agent_pi_bridge import PiRpcError


class DummyPipe:
    def __init__(self, lines):
        self.lines = list(lines)

    def readline(self):
        if self.lines:
            return self.lines.pop(0) + "\n"
        return ""


@pytest.mark.asyncio
async def test_pi_rpc_client_request_response_matching():
    client = PiRpcClient()

    # Create mock Popen process with pre-seeded responses
    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = DummyPipe([
        json.dumps({"type": "response", "id": "1", "success": True, "data": {"status": "ok"}}),
    ])
    mock_proc.stderr = DummyPipe([])
    mock_proc.poll.return_value = None

    client._process = mock_proc
    future = asyncio.get_running_loop().create_future()
    client._pending_requests["1"] = future
    client._reader_task = asyncio.create_task(client._read_responses())

    try:
        # Wait for reader to process the line
        result = await asyncio.wait_for(future, timeout=2.0)
        assert result == {"status": "ok"}
    finally:
        if client._reader_task:
            client._reader_task.cancel()


@pytest.mark.asyncio
async def test_pi_rpc_client_events_queueing():
    client = PiRpcClient()

    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = DummyPipe([
        json.dumps({"type": "message_update", "content": "hello"}),
    ])
    mock_proc.stderr = DummyPipe([])
    mock_proc.poll.return_value = None

    client._process = mock_proc
    client._reader_task = asyncio.create_task(client._read_responses())

    try:
        event = await asyncio.wait_for(client.events.get(), timeout=2.0)
        assert event == {"type": "message_update", "content": "hello"}
    finally:
        if client._reader_task:
            client._reader_task.cancel()


@pytest.mark.asyncio
async def test_pi_rpc_client_process_died_flag():
    client = PiRpcClient()

    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = DummyPipe([])  # Immediate EOF
    mock_proc.stderr = DummyPipe([])
    mock_proc.poll.return_value = 1

    client._process = mock_proc
    await client._read_responses()

    assert client.process_died is True


async def test_pi_rpc_client_fail_all_pending_resolves_futures():
    """REVIEW-P1-3 / AGENT-05 regression: fail_all_pending must resolve any
    still-pending request future with PiRpcError. Without this, an in-flight
    prompt that gets aborted sits waiting for a response that will never come
    and the stream consumer hangs the full 30s timeout.
    """
    client = PiRpcClient()

    f1 = asyncio.get_running_loop().create_future()
    f2 = asyncio.get_running_loop().create_future()
    client._pending_requests["1"] = f1
    client._pending_requests["2"] = f2

    client.fail_all_pending("abort requested")

    assert f1.done() and f1.exception() is not None
    assert f2.done() and f2.exception() is not None
    assert "abort requested" in str(f1.exception())
    assert client._pending_requests == {}


async def test_pi_bridge_abort_calls_rpc_and_fails_pending():
    """REVIEW-P1-3 regression: chat.py:320 calls `pi_bridge.abort()` during
    session deletion. ADR-0031 removed the method, leaving the call to
    AttributeError into a swallowed `except Exception` — the in-flight Pi
    prompt kept consuming tokens and writing files after deletion.

    Verify the bridge now (a) calls request("abort") on the RPC client and
    (b) fails any in-flight prompt future, so stream consumers don't sit
    waiting for a response that will never come.
    """
    from unittest.mock import AsyncMock

    from app.agent_pi_bridge import PiBridge
    from app.services.chat.pi_rpc_client import PiRpcError

    bridge = PiBridge.__new__(PiBridge)  # skip __init__ — we mock _rpc directly
    rpc = AsyncMock()
    rpc.request = AsyncMock(return_value={"ok": True})
    # fail_all_pending is sync; AsyncMock returns a coroutine when called
    # unless we use a plain MagicMock for it.
    from unittest.mock import MagicMock

    rpc.fail_all_pending = MagicMock()
    bridge._rpc = rpc

    # Seed a pending future as if a `prompt` call was awaiting a response.
    pending = asyncio.get_running_loop().create_future()
    rpc.fail_all_pending.side_effect = lambda reason: pending.set_exception(
        PiRpcError(reason)
    )

    result = await bridge.abort()

    rpc.request.assert_awaited_once_with("abort")
    rpc.fail_all_pending.assert_called_once_with("abort requested")
    assert result == {"ok": True}
    assert pending.done()
    assert isinstance(pending.exception(), PiRpcError)
    assert "abort requested" in str(pending.exception())

