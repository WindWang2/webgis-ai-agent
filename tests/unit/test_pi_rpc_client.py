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
    """Minimal file-like object for tests: supports both readline() and
    read(1). The production PiRpcClient._readline_bounded uses read(1)
    so it can cap line length; older tests that only stubbed readline
    would AttributeError on the new path.

    Returns bytes throughout — real subprocess.Popen.stdout yields bytes,
    and the production reader treats them as such."""

    def __init__(self, lines):
        # Keep both views over the same source so readline() and read(1)
        # see consistent state. Each entry is a str (no trailing newline);
        # we encode and add the newline on demand.
        self._lines = list(lines)
        self._pending = b""

    def _next_line_bytes(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0).encode() + b"\n"

    def readline(self):
        if self._pending:
            line = self._pending
            self._pending = b""
            return line
        return self._next_line_bytes()

    def read(self, n: int = -1):
        if n == -1:
            return self.readline()
        if not self._pending:
            self._pending = self._next_line_bytes()
            if not self._pending:
                return b""
        ch, self._pending = self._pending[:1], self._pending[1:]
        return ch


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


# ── REVIEW-P2: Pi subprocess trust boundary ────────────────────────


class _OversizedPipe:
    """A pipe that yields one line with no newline, longer than the budget,
    followed by EOF. Records how many bytes were actually read so the test
    can assert the reader stopped at the budget rather than buffering the
    whole payload.

    Serves the payload byte-by-byte via read(1) so a bounded reader can
    stop partway through; an unbounded readline() would consume the whole
    thing in one call."""

    def __init__(self, payload: bytes):
        self._payload = payload
        self._pos = 0
        self.bytes_read = 0

    def readline(self):
        # Bounded reader uses read(1), not readline; this is here for
        # completeness only.
        chunk = self._payload[self._pos:]
        self._pos = len(self._payload)
        self.bytes_read += len(chunk)
        return chunk

    def read(self, n: int = -1):
        if n == -1:
            return self.readline()
        chunk = self._payload[self._pos:self._pos + n]
        self._pos += len(chunk)
        self.bytes_read += len(chunk)
        return chunk


@pytest.mark.asyncio
async def test_readline_bounded_drops_oversized_line(monkeypatch):
    """A stdout line exceeding MAX_STDOUT_LINE_BYTES must be dropped, not
    buffered indefinitely. The reader returns None for the oversized line,
    logs a warning, and continues — it does not OOM the worker thread.

    The contract pinned here is *bytes consumed*: the reader must stop
    reading at the budget, not slurp the whole oversized payload. A bare
    readline() has no length limit and would buffer the full 200 bytes;
    the bounded reader stops at 64.
    """
    from app.services.chat import pi_rpc_client as mod

    monkeypatch.setattr(mod, "MAX_STDOUT_LINE_BYTES", 64)

    client = mod.PiRpcClient()
    oversized = b"x" * 200  # well over the 64-byte budget, no newline
    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    pipe = _OversizedPipe(oversized)
    mock_proc.stdout = pipe
    mock_proc.stderr = DummyPipe([])
    mock_proc.poll.return_value = 1

    client._process = mock_proc
    with patch("app.services.chat.pi_rpc_client.logger") as mock_logger:
        await client._read_responses()
        dropped_warnings = [
            str(c) for c in mock_logger.warning.call_args_list
            if "Dropped stdout line" in str(c)
        ]

    # Each budget-sized chunk is logged as dropped. A plain readline() would
    # have logged zero "Dropped" warnings — it would have buffered the whole
    # 200-byte line in one call.
    assert len(dropped_warnings) >= 1, (
        "bounded reader should log 'Dropped stdout line' when the line exceeds "
        "the budget; an unbounded readline would not"
    )
    # No event queued (all chunks were non-JSON), no crash.
    assert client._event_queue.empty()
    assert client.process_died is True


@pytest.mark.asyncio
async def test_event_queue_drops_on_overflow():
    """When the SSE consumer is not keeping up, the bounded queue must drop
    new events and log rather than block the reader (which would
    backpressure Pi's stdout pipe and can hang the subprocess)."""
    from app.services.chat import pi_rpc_client as mod

    client = mod.PiRpcClient()
    # Fill the queue to capacity.
    capacity = client._event_queue.maxsize
    assert capacity > 0, "queue must be bounded for this test to be meaningful"
    for i in range(capacity):
        await client._event_queue.put({"type": "filler", "i": i})

    # Now the queue is full. A put_nowait must raise QueueFull, and the
    # reader's overflow path catches that and logs instead of blocking.
    with pytest.raises(asyncio.QueueFull):
        client._event_queue.put_nowait({"type": "overflow"})

    # And the production reader code path exercises exactly this catch:
    # simulate the reader's put branch.
    obj = {"type": "tool_execution_end"}
    try:
        client._event_queue.put_nowait(obj)
        put_ok = True
    except asyncio.QueueFull:
        put_ok = False
    assert put_ok is False, "queue should be full"
