#!/usr/bin/env python3
"""Performance benchmark: Pi bridge vs ChatEngine.

Usage:
    # Compare Pi bridge vs ChatEngine for a simple prompt
    python -m pytest tests/benchmarks/test_pi_perf.py -v --benchmark

    # Run with specific number of iterations
    python -m pytest tests/benchmarks/test_pi_perf.py -v --benchmark --benchmark-iterations=10

Requires:
    - USE_NEW_AGENT=true for Pi bridge tests
    - A configured LLM provider for real latency measurements
    - Without API key, tests will be skipped with a warning
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_pi_bridge import PiBridge


def _make_mock_rpc() -> MagicMock:
    """Build a MagicMock PiRpcClient with an asyncio events queue.

    The bridge delegates all subprocess I/O to ``PiRpcClient`` (ADR-0022
    refactor), so the benchmark injects a mock RPC client rather than mocking
    ``_wait_for_ready``/``_send_request`` on the bridge directly (those methods
    no longer live on ``PiBridge``).
    """
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    rpc.process_died = False
    return rpc


# ─── Benchmark tests ───────────────────────────────────────────────

class TestPiBridgePerformance:
    """Performance benchmarks for Pi bridge operations."""

    @pytest.fixture
    def bridge(self):
        return PiBridge(rpc=_make_mock_rpc())

    @pytest.mark.asyncio
    async def test_prompt_latency(self, bridge):
        """Measure prompt() round-trip latency with pre-drained event queue."""
        # Seed the RPC event queue so prompt()'s drain loop exits immediately
        # after consuming the agent_end event (avoids waiting for
        # PI_EVENT_DRAIN_TIMEOUT).
        async def fake_request(cmd, data=None):
            if cmd == "prompt":
                await bridge._rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
                })
                await bridge._rpc.events.put({"type": "agent_end"})

        bridge._rpc.request = AsyncMock(side_effect=fake_request)

        # Warm-up call: the first prompt() pays one-time costs (lazy import of
        # _extract_text_from_event inside the drain loop, asyncio task
        # scheduling setup) that are not representative of steady-state latency.
        # The benchmark measures the warmed-up second call.
        await bridge.prompt("warmup")

        start = time.perf_counter()
        result = await bridge.prompt("Hello")
        elapsed_ms = (time.perf_counter() - start) * 1000
        # With a mocked RPC and pre-seeded queue, the warmed-up round-trip
        # should be well under 100ms (the assertion guards against accidental
        # blocking I/O).
        assert result["content"] == "Hi"
        assert elapsed_ms < 100, f"prompt() took {elapsed_ms:.1f}ms"

    @pytest.mark.asyncio
    async def test_stream_prompt_latency(self, bridge):
        """Measure stream_prompt() time to first event with pre-drained queue."""
        async def fake_request(cmd, data=None):
            if cmd == "prompt":
                await bridge._rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
                    "assistantMessageEvent": {"type": "text_delta", "content": "Hi"},
                })
                await bridge._rpc.events.put({"type": "agent_end"})

        bridge._rpc.request = AsyncMock(side_effect=fake_request)

        start = time.perf_counter()
        first_event = None
        async for ev in bridge.stream_prompt("Hi"):
            first_event = ev
            break
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert first_event is not None
        assert elapsed_ms < 100, f"stream_prompt first event took {elapsed_ms:.1f}ms"
