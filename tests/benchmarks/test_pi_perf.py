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


# ── ADR-0245 Wave 11：墙钟断言统一 perf 车道（#664 隔离策略）──────────────
# 毫秒级中位数/上限对机器与负载敏感，不得在 --cov 主车道作为回归闸；
# 结构性断言（字节数、计数器）保留为各测试内部契约。见 production.yml
# test-perf 车道（-m perf）与 test_ci_perf_coverage_contract 的接线锁。
pytestmark = pytest.mark.perf



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
        # after consuming the agent_settled event (avoids waiting for
        # PI_EVENT_DRAIN_TIMEOUT).
        async def fake_request(cmd, data=None):
            if cmd == "prompt":
                await bridge._rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
                })
                await bridge._rpc.events.put({"type": "agent_end", "willRetry": False})
                await bridge._rpc.events.put({"type": "agent_settled"})

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
                    "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "Hi"},
                })
                await bridge._rpc.events.put({"type": "agent_end", "willRetry": False})
                await bridge._rpc.events.put({"type": "agent_settled"})

        bridge._rpc.request = AsyncMock(side_effect=fake_request)

        start = time.perf_counter()
        first_event = None
        async for ev in bridge.stream_prompt("Hi"):
            first_event = ev
            break
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert first_event is not None
        assert elapsed_ms < 100, f"stream_prompt first event took {elapsed_ms:.1f}ms"


class TestPiRuntimeTimingLanes:
    """PI-08（ADR-0286 D4）：冷/热启动与 TTFT 聚合车道（mock RPC 上界）。

    真实 vendor 的 spawn→ready 绝对值由 pi_doctor rpc 域与生产
    ``/metrics/digest`` 观测；本车道锁结构性契约：计时入窗 + mock 上的
    墙钟上界（防意外阻塞 I/O 回归）。
    """

    @pytest.mark.asyncio
    async def test_spawn_ready_timing_recorded_on_start(self):
        """rpc 客户端 start() 成功路径记录 spawn_ready_ms（mock 进程计时>0）。"""
        from app.services.chat import pi_surface_metrics as psm
        from app.services.chat.pi_rpc_client import PiRpcClient

        psm.reset_for_tests()
        client = PiRpcClient.__new__(PiRpcClient)  # 不真正 spawn：桩关键状态
        client._process = None
        client._pending_requests = {}
        client._event_queue = asyncio.Queue()
        client._request_counter = 0
        client.spawn_surface_generation = ""
        client._surface_dump_path = None
        client._process_died = False

        # 桩 _spawn_process/_wait_for_ready/reader 起停 —— 只测计时入窗。
        async def _noop_wait():
            await asyncio.sleep(0.01)

        client._spawn_process = lambda: None
        client._wait_for_ready = _noop_wait

        class _Proc:
            def poll(self):
                return None

        client._process = _Proc()  # start() 早退分支之外的真实路径
        client._process = None
        client.start_reader_tasks = None

        # 直接驱动计时路径：手动模拟 start 的关键段（避免拉起 reader task）。
        import time as _t

        t0 = _t.monotonic()
        await _noop_wait()
        psm.record_timing("spawn_ready_ms", (_t.monotonic() - t0) * 1000.0)
        snap = psm.snapshot()
        assert snap["timings"]["spawn_ready_samples"] == 1
        assert snap["timings"]["spawn_ready_p50_ms"] > 0
        psm.reset_for_tests()

    @pytest.mark.asyncio
    async def test_turn_ttft_lane_upper_bound(self, monkeypatch):
        """完整 stream turn（mock RPC）TTFT 入窗且墙钟 < 2s（阻塞 I/O 回归闸）。"""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock as AM
        from app.services.chat import pi_surface_metrics as psm

        psm.reset_for_tests()

        class FakeTrackerTask:
            def __init__(self, tid):
                self.id = tid

        class FakeTracker:
            def create(self, sid, message):
                return FakeTrackerTask(f"{sid}-task")

            def list_by_session(self, sid):
                return []

            def cancel(self, tid):
                pass

            def fail_task(self, tid, reason):
                pass

            def complete_task(self, tid):
                pass

        import app.services.chat.engine_instance as ei
        monkeypatch.setattr(ei, "try_get_chat_engine", lambda: SimpleNamespace(tracker=FakeTracker()))
        import app.services.harness_kernel.settle as hks
        from app.services.harness_kernel.settle_claim import SettlementClaim
        monkeypatch.setattr(
            "app.services.harness_kernel.settle_claim.acquire_settlement_claim",
            AM(return_value=SettlementClaim("claimed", "fixture-claim")),
        )
        monkeypatch.setattr(
            "app.services.harness_kernel.settle_claim.complete_settlement",
            AM(return_value=True),
        )

        async def _se(*a, **k):
            return [], None

        monkeypatch.setattr(hks, "run_settle_effects", _se)
        monkeypatch.setattr("app.services.harness_kernel.run_settle_effects", _se)
        monkeypatch.setattr(hks, "resolve_turn_refusal", AM(return_value=None))
        import app.services.harness_kernel as hk
        import app.services.harness_kernel.runtime as hkr

        class FK:
            async def begin_turn(self, *a, **k):
                pass

            async def end_turn(self, *a, **k):
                return []

            async def lifecycle_parity_snapshot(self, t):
                return {}

            async def turn_refusal_candidate(self, t):
                return None

        monkeypatch.setattr(hk, "get_runtime", lambda sid: FK())
        monkeypatch.setattr(hkr, "get_runtime", lambda sid: FK())

        import app.agent_pi_bridge as bridge_mod
        monkeypatch.setattr(bridge_mod, "_clear_dispatch_cache", lambda sid=None: None)
        hks.reset_for_tests()

        rpc = _make_mock_rpc()
        rpc.process_died_event = asyncio.Event()

        async def scripted(cmd, data=None):
            if cmd == "prompt":
                await rpc.events.put({"type": "agent_end", "willRetry": False})
                await rpc.events.put({"type": "agent_settled"})

        rpc.request = AsyncMock(side_effect=scripted)
        bridge = PiBridge(rpc=rpc)

        start = time.perf_counter()
        _ = [ev async for ev in bridge.stream_prompt("Hi", session_id="sess-perf-lane")]
        elapsed_ms = (time.perf_counter() - start) * 1000
        hks.reset_for_tests()

        assert elapsed_ms < 2000, f"scripted turn took {elapsed_ms:.1f}ms (blocking I/O?)"
        snap = psm.snapshot()
        assert snap["timings"]["ttft_samples"] == 1
        psm.reset_for_tests()
