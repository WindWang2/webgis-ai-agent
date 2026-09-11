"""Performance budget measurements (quality-e2e-v9, ADR-0146 P5).

Four first-wave budget lines, all in-process and deterministic (fixed
fixtures, no network, no LLM): wall-clock is treated as an order-of-magnitude
regression gate, never a microbenchmark — repo philosophy (count/bytes first)
applies; budgets.json carries the rationale per line.
"""
from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path
from typing import Any, Callable, Dict, List


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return float("inf")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]


# ── SSE concurrent-50 (route-level batching path) ─────────────────────────


async def _token_stream(count: int):
    for i in range(count):
        yield f"event: token\ndata: {{\"content\": \"t{i}\"}}\n\n"
        await asyncio.sleep(0)


async def measure_sse_concurrent_50(conns: int = 50, tokens_per_conn: int = 200) -> float:
    """Wall time for 50 concurrent SSE streams through the route batcher."""
    from app.api.routes.chat import _sse_batched

    async def _drain() -> int:
        n = 0
        async for chunk in _sse_batched(_token_stream(tokens_per_conn)):
            n += 1
        return n

    start = time.perf_counter()
    results = await asyncio.gather(*(_drain() for _ in range(conns)))
    elapsed_ms = (time.perf_counter() - start) * 1000
    if any(r == 0 for r in results):
        raise RuntimeError("sse batched stream yielded nothing")
    return elapsed_ms


# ── MVT tile encode P95 ───────────────────────────────────────────────────


def _point_fc(n: int) -> Dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": f"p{i}", "value": i},
                "geometry": {"type": "Point",
                             "coordinates": [116.0 + (i % 40) * 0.02,
                                             39.5 + (i // 40) * 0.02]},
            }
            for i in range(n)
        ],
    }


def _tile_for(lon: float, lat: float, z: int) -> tuple[int, int]:
    import math

    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def measure_mvt_tile_p95(iterations: int = 10) -> float:
    from app.services.mvt import encode_tile

    fc = _point_fc(2000)
    # Tile chosen from the first feature so the encode path provably has work
    # (a mis-picked tile encodes empty and the assertion below would lie).
    zx, zy = _tile_for(116.0, 39.5, 11)
    timings: List[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        tile = encode_tile(fc, 11, zx, zy)
        timings.append((time.perf_counter() - start) * 1000)
        if not tile:
            raise RuntimeError("mvt tile encoded empty")
    return _percentile(timings, 95)


# ── Lakehouse cube window read P95 ────────────────────────────────────────


def measure_cube_window_p95(iterations: int = 10, tmp_root: str | None = None) -> float:
    """Cube window read P95; explicit SKIP when zarr is absent."""
    try:
        import zarr  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — typed skip path
        raise SkipMeasurement(f"zarr not installed: {exc}") from exc

    from app.services.lakehouse.cube_store import read_cube_window, write_cube

    import tempfile

    root = Path(tmp_root or tempfile.mkdtemp(prefix="perf-cube-")) / "cube"
    times = [f"2026-01-{d:02d}" for d in range(1, 5)]
    bands = {"ndvi": [[[float((y + x + t) % 7)] * 64 for x in range(64)]
                      for y in range(64)] for t in range(4)}
    write_cube({"ndvi": bands}, times, root)

    timings: List[float] = []
    for i in range(iterations):
        start = time.perf_counter()
        out = read_cube_window(root, bands=["ndvi"],
                               time=slice(i % 2, (i % 2) + 2),
                               y=slice(0, 32), x=slice(0, 32))
        timings.append((time.perf_counter() - start) * 1000)
        if "ndvi" not in out["bands"]:
            raise RuntimeError("cube window read returned wrong shape")
    return _percentile(timings, 95)


# ── Chat first-token route overhead P95 ───────────────────────────────────


async def measure_chat_first_token_p95(iterations: int = 20) -> float:
    """First-event latency through the route batcher (route overhead only;
    the external LLM TTFT is deliberately excluded — only our added latency
    is budgetable)."""
    from app.api.routes.chat import _sse_batched

    timings: List[float] = []
    for _ in range(iterations):
        gen = _sse_batched(_token_stream(3))
        start = time.perf_counter()
        await gen.__anext__()
        timings.append((time.perf_counter() - start) * 1000)
        # drain to close the generator frame
        async for _ in gen:
            pass
    return _percentile(timings, 95)


class SkipMeasurement(Exception):
    """A budget line cannot run in this environment (typed, reported as SKIP)."""


MEASUREMENTS: Dict[str, Callable[..., float]] = {
    "sse_concurrent_50_total_ms": lambda **kw: asyncio.run(
        measure_sse_concurrent_50()),
    "mvt_tile_p95_ms": measure_mvt_tile_p95,
    "cube_window_p95_ms": measure_cube_window_p95,
    "chat_first_token_p95_ms": lambda **kw: asyncio.run(
        measure_chat_first_token_p95()),
}


def percentile(values: List[float], pct: float) -> float:
    """Public re-export for gate tests."""
    return _percentile(values, pct)


def mean(values: List[float]) -> float:
    return statistics.fmean(values)
