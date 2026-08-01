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
