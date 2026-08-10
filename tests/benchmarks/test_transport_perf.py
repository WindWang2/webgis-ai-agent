"""Deterministic transport/streaming performance harness (no network, no LLM).

Complements ``test_perf_harness.py`` (compute workloads) with the
frontend↔backend *transport* hot paths this goal targets:

  1. sse_serialization_10k       — backend SSE encode throughput (mixed sizes)
  2. sse_batcher_coalesce_500    — SSEBatcher push→drain coalescing throughput
  3. stream_first_event_pi       — POST /chat/stream → first SSE event (mock Pi)
  4. concurrent_stream_p95_8     — p95 first-event across 8 concurrent streams
                                   (surfaces the Pi singleton-lock serialization
                                   and DB-session-hold behaviour)

All workloads mock at the model/Pi boundary (no real LLM, no subprocess) so
they are deterministic and CI-safe. Same gate semantics as the compute harness:
median <= max(floor_ms, baseline * WARN_FACTOR) PASS; * WARN_FACTOR soft-fail;
* FAIL_FACTOR hard-fail. Refresh with ``PERF_UPDATE_BASELINES=1``.

Usage:
    pytest tests/benchmarks/test_transport_perf.py -m perf --no-cov --timeout=180 -q
    PERF_UPDATE_BASELINES=1 pytest tests/benchmarks/test_transport_perf.py -m perf --no-cov -q
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes import chat as chat_route
from app.core.auth import get_current_user_optional, get_owner_token
from app.core.database import get_async_db
from app.utils.sse import SSEBatcher, sse_event

BASELINES_PATH = Path(__file__).parent / "transport_baselines.json"
UPDATE_BASELINES = os.environ.get("PERF_UPDATE_BASELINES") == "1"

WARN_FACTOR = 1.75
FAIL_FACTOR = 4.0
ITERATIONS = 7


# ─── baseline gate ────────────────────────────────────────────────────────────


def _load_baselines() -> dict:
    if BASELINES_PATH.exists():
        return json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    return {}


def _save_baselines(baselines: dict) -> None:
    BASELINES_PATH.write_text(
        json.dumps(baselines, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# ─── shared deterministic SSE fixtures ────────────────────────────────────────


def _token_payload(i: int) -> dict:
    return {"content": f"tok{i} ", "session_id": "sess-bench"}


def _step_result_payload(i: int, size_bytes: int) -> dict:
    # medium tool result (~500B) — representative step_result
    pad = "x" * max(0, size_bytes - 120)
    return {
        "tool": "search_poi",
        "name": f"poi-{i}",
        "result": {"success": True, "count": i, "pad": pad},
        "session_id": "sess-bench",
    }


def _mixed_events(n: int) -> list[str]:
    """A realistic event mix: 70% token, 20% step_result (~500B), 10% large meta (~8KB)."""
    events: list[str] = []
    for i in range(n):
        r = i % 10
        if r < 7:
            events.append(sse_event("token", _token_payload(i)))
        elif r < 9:
            events.append(sse_event("step_result", _step_result_payload(i, 500)))
        else:
            events.append(sse_event("step_result", _step_result_payload(i, 8000)))
    return events


# ─── mock Pi bridge + transport app ───────────────────────────────────────────


class _MockPiBridge:
    """Deterministic Pi bridge stand-in.

    Yields a ``task_start`` immediately (no RPC), then ``n_tokens`` token
    events, then a terminal ``done``. Used to measure transport/framework
    overhead (auth → guard → StreamingResponse → first yield) in isolation
    from real model latency.
    """

    def __init__(self, n_tokens: int = 30) -> None:
        self.n_tokens = n_tokens

    async def stream_prompt(
        self, message: str, session_id: str | None = None
    ) -> AsyncIterator[str]:
        yield sse_event("task_start", {"task_id": "t", "session_id": session_id or "s"})
        for i in range(self.n_tokens):
            yield sse_event("token", _token_payload(i))
        yield sse_event("done", {"session_id": session_id or "s"})


def _build_transport_app(n_tokens: int = 30) -> FastAPI:
    """Minimal app exposing only the chat router with mocked boundaries.

    Dep overrides drop auth/DB so the workload measures the streaming
    transport path, not authn or DB query cost. The Pi path is forced on
    with a deterministic mock bridge.
    """
    app = FastAPI()
    app.include_router(chat_route.router, prefix="/api/v1")

    # Force the Pi path with a deterministic bridge.
    chat_route.USE_NEW_AGENT = True
    chat_route.pi_bridge = _MockPiBridge(n_tokens=n_tokens)

    # Anonymous caller, no owner token, no DB session held by the dependency.
    async def _anon_user():
        return {"user_id": None}

    async def _no_db():
        yield None  # _guard_body_session returns early when session_id is None

    app.dependency_overrides[get_current_user_optional] = _anon_user
    app.dependency_overrides[get_owner_token] = lambda: None
    app.dependency_overrides[get_async_db] = _no_db
    return app


def _post_body(msg: str = "hi") -> dict:
    return {"message": msg, "session_id": None, "map_state": None}


# ─── workloads (each returns median ms over ITERATIONS) ───────────────────────


def _sse_serialization_ms() -> float:
    """Encode 10k mixed SSE events (token/step_result/large) via sse_event()."""
    payloads: list[tuple[str, dict]] = []
    for i in range(10_000):
        r = i % 10
        if r < 7:
            payloads.append(("token", _token_payload(i)))
        elif r < 9:
            payloads.append(("step_result", _step_result_payload(i, 500)))
        else:
            payloads.append(("step_result", _step_result_payload(i, 8000)))
    t0 = time.perf_counter()
    for et, data in payloads:
        sse_event(et, data)
    return (time.perf_counter() - t0) * 1000


def _sse_batcher_coalesce_ms() -> float:
    """Push 500 token events through SSEBatcher(32, 0.08) and drain; total ms.

    Exercises the coalescing path used by the legacy streaming generator. The
    Pi path currently bypasses the batcher (finding D-F6); this workload
    protects the batcher's own throughput.
    """
    async def _run() -> float:
        batcher = SSEBatcher(max_events=32, max_delay_s=0.08)
        t0 = time.perf_counter()
        for i in range(500):
            batcher.push(sse_event("token", _token_payload(i)))
            async for _chunk in batcher.drain():
                pass
        for _chunk in batcher.flush():
            pass
        return (time.perf_counter() - t0) * 1000

    return asyncio.run(_run())


def _stream_first_event_pi_ms() -> float:
    """POST /chat/stream → time to first SSE event (mock Pi, single stream).

    Measures end-to-end transport overhead: framework dispatch, dependency
    resolution, StreamingResponse setup, first generator yield. This is the
    KPI for the "first-event acknowledgement" work and the DB-session-hold
    fix (fewer session setups → lower TTFE).
    """
    app = _build_transport_app(n_tokens=5)

    async def _once() -> float:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            t0 = time.perf_counter()
            async with client.stream("POST", "/api/v1/chat/stream", json=_post_body()) as resp:
                async for _chunk in resp.aiter_bytes():
                    return (time.perf_counter() - t0) * 1000
            return (time.perf_counter() - t0) * 1000

    return asyncio.run(_once())


def _concurrent_stream_p95_8_ms() -> float:
    """p95 time-to-first-event across 8 concurrent POST /chat/stream.

    With the singleton Pi bridge holding a process-wide lock across the whole
    turn (finding C-F16 / B-P1-4), concurrent turns serialize and p95
    inflates toward N×single. After a per-session/turn concurrency fix, p95
    should approach the single-stream TTFE.
    """
    app = _build_transport_app(n_tokens=5)

    async def _one(client: httpx.AsyncClient) -> float:
        t0 = time.perf_counter()
        async with client.stream("POST", "/api/v1/chat/stream", json=_post_body()) as resp:
            async for _chunk in resp.aiter_bytes():
                return (time.perf_counter() - t0) * 1000
        return (time.perf_counter() - t0) * 1000

    async def _run() -> float:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            samples: list[float] = []
            for _ in range(3):  # repeat to stabilize p95
                results = await asyncio.gather(*(_one(client) for _ in range(8)))
                samples.extend(results)
            # p95 across all samples
            samples.sort()
            idx = int(len(samples) * 0.95)
            return samples[min(idx, len(samples) - 1)]

    return asyncio.run(_run())


WORKLOADS = {
    "sse_serialization_10k": _sse_serialization_ms,
    "sse_batcher_coalesce_500": _sse_batcher_coalesce_ms,
    "stream_first_event_pi": _stream_first_event_pi_ms,
    "concurrent_stream_p95_8": _concurrent_stream_p95_8_ms,
}


@pytest.fixture(autouse=True)
def _clean_chat_globals():
    """Restore chat route module globals after each workload (mock injection)."""
    saved_use = getattr(chat_route, "USE_NEW_AGENT", False)
    saved_bridge = chat_route.pi_bridge
    yield
    chat_route.USE_NEW_AGENT = saved_use
    chat_route.pi_bridge = saved_bridge


@pytest.mark.perf
@pytest.mark.parametrize("name", sorted(WORKLOADS))
def test_transport_perf_workload(name):
    measured = statistics.median(WORKLOADS[name]() for _ in range(ITERATIONS))
    baselines = _load_baselines()

    if UPDATE_BASELINES or name not in baselines:
        baseline = {"median_ms": round(measured, 3), "iterations": ITERATIONS}
        all_baselines = dict(baselines)
        all_baselines[name] = baseline
        _save_baselines(all_baselines)
        pytest.skip(f"baseline recorded for '{name}': {measured:.3f} ms")
        return

    baseline_ms = baselines[name]["median_ms"]
    floor_ms = baselines[name].get("floor_ms", 1.0)
    warn_at = max(floor_ms, baseline_ms * WARN_FACTOR)
    fail_at = max(floor_ms, baseline_ms * FAIL_FACTOR)

    if measured > fail_at:
        pytest.fail(
            f"HARD REGRESSION: '{name}' median {measured:.3f} ms "
            f"(baseline {baseline_ms:.3f} ms, fail at {fail_at:.3f} ms). "
            f"Run PERF_UPDATE_BASELINES=1 only after a measured improvement."
        )
    if measured > warn_at:
        pytest.fail(
            f"PERF REGRESSION (warn band): '{name}' median {measured:.3f} ms "
            f"is {measured/baseline_ms:.2f}x baseline {baseline_ms:.3f} ms "
            f"(warn at {warn_at:.3f} ms, hard-fail at {fail_at:.3f} ms). "
            f"Investigate or refresh the baseline with PERF_UPDATE_BASELINES=1.",
            pytrace=False,
        )

    assert measured <= warn_at, f"'{name}' {measured:.3f} ms vs {warn_at:.3f} ms"
