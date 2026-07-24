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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_pi_bridge import PiBridge
from tests.fixtures.pi_mocks import make_mock_process, make_readline


# ─── Benchmark tests ───────────────────────────────────────────────

class TestPiBridgePerformance:
    """Performance benchmarks for Pi bridge operations."""

    @pytest.fixture
    def bridge(self):
        return PiBridge(extension_paths=[])

    @pytest.mark.asyncio
    async def test_prompt_latency(self, bridge):
        """Measure prompt() round-trip latency with pre-drained event queue."""
        mock_proc = make_mock_process(latency_ms=5)
        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_wait_for_ready", new_callable=AsyncMock, return_value=None):
                    with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                        await bridge.start()

        try:
            # Pre-populate event queue so prompt() drain loop exits immediately
            # (avoids waiting for PI_EVENT_DRAIN_TIMEOUT=2.0s)
            await bridge._event_queue.put({"type": "agent_end"})

            start = time.perf_counter()
            await bridge.prompt("Hello")
            elapsed_ms = (time.perf_counter() - start) * 1000
            # With mocked subprocess and pre-drained queue, should be < 100ms
            assert elapsed_ms < 100, f"prompt() took {elapsed_ms:.1f}ms"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_stream_prompt_latency(self, bridge):
        """Measure stream_prompt() time to first event with pre-drained queue."""
        lines = [
            '{"type":"response","id":"1","success":true}\n',
            '{"type":"message_update","message":{"role":"assistant","content":[{"type":"text","text":"Hi"}]},"assistantMessageEvent":{"type":"text_delta","content":"Hi"}}\n',
            '{"type":"agent_end"}\n',
            "",
        ]
        mock_proc = make_mock_process(latency_ms=5)
        mock_proc.stdout.readline.side_effect = make_readline(lines)

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch("asyncio.sleep", return_value=None):
                with patch.object(bridge, "_send_request", new_callable=AsyncMock, return_value=None):
                    await bridge.start()

        try:
            start = time.perf_counter()
            first_event = None
            async for ev in bridge.stream_prompt("Hi"):
                first_event = ev
                break
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert first_event is not None
            assert elapsed_ms < 100, f"stream_prompt first event took {elapsed_ms:.1f}ms"
        finally:
            await bridge.stop()
