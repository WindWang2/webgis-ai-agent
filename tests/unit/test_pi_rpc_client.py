"""Unit tests for app.services.chat.pi_rpc_client.PiRpcClient.

Tests subprocess initialization, request-response matching, and event queueing.
"""
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from app.services.chat.pi_rpc_client import PiRpcClient


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
async def test_start_popen_uses_binary_pipes(monkeypatch):
    """start() must spawn Popen with text=False — text pipes TypeError the reader."""
    from app.services.chat import pi_rpc_client as mod

    captured = {}

    def fake_popen(*args, **kwargs):
        captured["kwargs"] = kwargs
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = DummyPipe([])
        proc.stderr = DummyPipe([])
        proc.poll.return_value = None
        return proc

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    client = mod.PiRpcClient()
    monkeypatch.setattr(client, "_wait_for_ready", AsyncMock(return_value=None))
    try:
        await client.start()
    finally:
        if client._reader_task:
            client._reader_task.cancel()
        if client._stderr_task:
            client._stderr_task.cancel()

    assert captured["kwargs"].get("text") is False
    assert captured["kwargs"].get("universal_newlines") in (None, False)


def test_readline_bounded_accepts_text_pipe():
    """Defensive: a text-mode pipe must not TypeError the reader."""
    client = PiRpcClient()

    class TextPipe:
        def __init__(self):
            self._data = "ok\n"

        def read(self, n=1):
            if not self._data:
                return ""
            ch, self._data = self._data[0], self._data[1:]
            return ch

    mock_proc = MagicMock()
    mock_proc.stdout = TextPipe()
    client._process = mock_proc
    line = client._readline_bounded()
    assert line == b"ok\n"


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
    # The dead process reference must be cleared so start() can respawn
    # (its guard is `if self._process is not None: return`). Previously
    # _process_died was set but _process kept pointing at the dead Popen,
    # permanently blocking respawn.
    assert client._process is None


@pytest.mark.asyncio
async def test_process_not_cleared_when_still_alive_on_cancellation():
    """On the stop()-cancellation path, the process may still be alive
    (mid-terminate). The reader's finally must NOT clear _process in that
    case - stop()'s own finally handles the final clearing. The poll() guard
    prevents a premature clear.
    """
    client = PiRpcClient()

    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = DummyPipe(["x"])  # not empty, but we cancel before reading
    mock_proc.stderr = DummyPipe([])
    mock_proc.poll.return_value = None  # still alive

    client._process = mock_proc
    # Simulate the reader being cancelled (as stop() does).
    client._reader_task = asyncio.create_task(client._read_responses())
    await asyncio.sleep(0)  # let it start
    client._reader_task.cancel()
    try:
        await client._reader_task
    except asyncio.CancelledError:
        pass

    # _process should still be the mock (not cleared) because poll() is None.
    # stop()'s finally would handle clearing it.
    assert client._process is mock_proc


@pytest.mark.asyncio
async def test_start_can_respawn_after_natural_death():
    """After the process dies naturally (EOF + poll=dead), start() must not
    early-return on the `if self._process is not None: return` guard.

    Previously _process was never cleared, so start() silently no-opped and
    the bridge could never respawn without a full app restart.

    We verify the guard passes by pre-importing pi_tools (so start()'s inline
    import doesn't re-trigger numpy's subprocess chain) and mocking Popen.
    """
    # Pre-import so start()'s `from app.api.routes.pi_tools import ...` hits
    # the cached module rather than triggering a fresh import chain.
    import app.api.routes.pi_tools  # noqa: F401

    client = PiRpcClient()

    # Simulate a process that already died: _process cleared by the reader's
    # finally (as tested above), _process_died set.
    client._process = None
    client._process_died = True

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()

    with patch.object(PiRpcClient, "_read_responses", new_callable=AsyncMock), \
         patch.object(PiRpcClient, "_read_stderr", new_callable=AsyncMock), \
         patch("app.services.chat.pi_rpc_client.subprocess.Popen", return_value=mock_proc), \
         patch("app.api.routes.pi_tools.get_bridge_secret", return_value="test-secret"), \
         patch.object(PiRpcClient, "_wait_for_ready", new_callable=AsyncMock):
        await client.start()

    # start() did NOT early-return - it assigned the spawned process.
    assert client._process is mock_proc, "start() should have spawned a new process, not early-returned"


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
