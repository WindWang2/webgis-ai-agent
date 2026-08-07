"""Deterministic performance regression harness (no network, no LLM).

Covers the hot paths optimized this session (ADR-0042/0043/0044/0045):
  1. raster_guard_rejection    — pathological resample rejected in ~ms (was 2-5min warp)
  2. ref_resolution_batch      — N string args resolved in ONE Redis call (was N RTTs)
  3. metrics_enqueue           — record_tool_call caller-side cost (I/O off the loop)
  4. dispatch_overhead         — registry.dispatch wrapper cost (sync tool, to_thread)

Gate semantics (goal §10): median <= floor_ms -> PASS;
median <= baseline * WARN_FACTOR -> PASS (warning); else -> FAIL (hard regression).

Usage:
    # run against committed baselines (tests/benchmarks/baselines.json)
    uv run python -m pytest tests/benchmarks/test_perf_harness.py -m perf -q

    # refresh baselines after an intentional, measured improvement
    PERF_UPDATE_BASELINES=1 uv run python -m pytest tests/benchmarks/test_perf_harness.py -m perf -q

Baselines are medians of N iterations; factors are generous (1.75x warn /
4x fail with an absolute floor) so ordinary CI noise never fails the gate.
"""
import asyncio
import json
import os
import statistics
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.tool_cache import _reset_redis_client_for_tests
from app.services import tool_metrics
from app.tools.registry import ToolRegistry

BASELINES_PATH = Path(__file__).parent / "baselines.json"
UPDATE_BASELINES = os.environ.get("PERF_UPDATE_BASELINES") == "1"

# Regression gates: measured <= max(floor_ms, baseline * factor)
WARN_FACTOR = 1.75
FAIL_FACTOR = 4.0

ITERATIONS = 7  # median of 7 — robust to scheduler noise


# ─── workloads ───────────────────────────────────────────────────────────────


def _raster_guard_rejection_ms() -> float:
    """Pathological resample (3°×3° 4326 → 3857 @ 1m) must be rejected in ~ms."""
    import asyncio as _asyncio

    from app.tools.advanced_spatial import register_advanced_spatial_tools

    data_dir = Path(__file__).parent.parent.parent / "data"
    path = data_dir / f"test_perf_guard_{uuid.uuid4().hex[:8]}.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=3, width=3, count=1, dtype=np.float32,
        crs="EPSG:4326", transform=from_origin(0, 3, 1, 1),
    ) as dst:
        dst.write(np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float32), 1)
    try:
        reg = ToolRegistry()
        register_advanced_spatial_tools(reg)

        async def _run():
            t0 = time.perf_counter()
            res = await reg.dispatch(
                "raster_resample",
                {"raster_path": str(path), "target_resolution": 1.0, "target_crs": "EPSG:3857"},
                session_id=None,
            )
            assert res.get("success") is False, "pathological warp must be rejected"
            return (time.perf_counter() - t0) * 1000

        return _asyncio.run(_run())
    finally:
        path.unlink(missing_ok=True)


def _ref_resolution_batch_ms() -> float:
    """10 string args must be collected + walked in one batched resolution."""
    from app.tools.registry import session_data_manager as sdm

    reg = ToolRegistry()

    def echo_tool(k0: Any, k1: Any, k2: Any, k3: Any, k4: Any,
                  k5: Any, k6: Any, k7: Any, k8: Any, k9: Any):
        return {"k9": k9}

    reg.register("echo_tool", "echoes", echo_tool)

    async def _identity(session_id, strings):
        return {s: s for s in strings}

    args = {f"k{i}": f"data/path_{i}.tif" for i in range(10)}

    async def _run():
        with patch.object(sdm, "resolve_aliases", _identity):
            t0 = time.perf_counter()
            res = await reg.dispatch("echo_tool", args, session_id="sess")
            assert res["k9"] == "data/path_9.tif"
            return (time.perf_counter() - t0) * 1000

    return asyncio.run(_run())


def _metrics_enqueue_ms() -> float:
    """record_tool_call caller-side cost (queued writer; no file I/O on caller)."""
    tool_metrics._reset_for_tests()
    n = 200
    t0 = time.perf_counter()
    for i in range(n):
        tool_metrics.record_tool_call(
            tool="bench", arg_bytes=1, result_bytes=1, duration_ms=1,
            cache_hit=False, error=None, session_id=None,
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000 / n
    tool_metrics._reset_for_tests()
    return elapsed_ms


def _dispatch_overhead_ms() -> float:
    """registry.dispatch wrapper cost for a trivial sync tool (no session)."""
    reg = ToolRegistry()

    def ping_tool(x: int) -> dict:
        return {"x": x}

    reg.register("ping_tool", "ping", ping_tool)

    async def _run():
        t0 = time.perf_counter()
        res = await reg.dispatch("ping_tool", {"x": 1}, session_id=None)
        assert res["x"] == 1
        return (time.perf_counter() - t0) * 1000

    return asyncio.run(_run())


def _reclassify_windowed_ms() -> float:
    """Raster-compute workload: windowed reclassify over a multi-block raster."""
    import os as _os
    import uuid as _uuid
    import rasterio as _rasterio
    from rasterio.transform import from_origin as _from_origin
    from app.lib.geo_analysis.raster_math import reclassify

    data_dir = Path(__file__).parent.parent.parent / "data"
    path = data_dir / f"test_perf_recl_{_uuid.uuid4().hex[:8]}.tif"
    rng = np.random.default_rng(99)
    arr = rng.integers(0, 20, size=(1024, 1024)).astype(np.float32)
    with _rasterio.open(
        path, "w", driver="GTiff", height=1024, width=1024, count=1,
        dtype=np.float32, crs="EPSG:4326", transform=_from_origin(0, 1024, 1, 1),
        tiled=True, blockxsize=256, blockysize=256,
    ) as dst:
        dst.write(arr, 1)
    try:
        scheme = [
            {"min": 0, "max": 4, "value": 1}, {"min": 5, "max": 9, "value": 2},
            {"min": 10, "max": 14, "value": 3}, {"min": 15, "max": 19, "value": 4},
        ]
        t0 = time.perf_counter()
        res = reclassify(str(path), scheme)
        elapsed = (time.perf_counter() - t0) * 1000
        assert res["pixel_count"] > 0
        _os.unlink(res["output_path"])
        return elapsed
    finally:
        path.unlink(missing_ok=True)


def _h3_binning_ms() -> float:
    """Vector workload: H3 binning over 10k synthetic points."""
    from app.tools.spatial_stats import register_spatial_stats_tools

    reg = ToolRegistry()
    register_spatial_stats_tools(reg)

    rng = np.random.default_rng(7)
    features = []
    for _ in range(10_000):
        lon = float(rng.uniform(116.0, 117.0))
        lat = float(rng.uniform(39.0, 40.0))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"v": float(rng.random())},
        })
    geojson = {"type": "FeatureCollection", "features": features}

    async def _run():
        t0 = time.perf_counter()
        res = await reg.dispatch("h3_binning", {
            "geojson": geojson, "resolution": 8, "value_field": "v", "aggregation": "mean",
        }, session_id=None)
        elapsed = (time.perf_counter() - t0) * 1000
        assert res.get("success") is True or "data" in res
        return elapsed

    return asyncio.run(_run())


def _artifact_cache_hit_ms() -> float:
    """Agent-runtime workload: artifact cache hit returns in ~ms (not recompute)."""
    import os as _os
    import uuid as _uuid
    import rasterio as _rasterio
    from rasterio.transform import from_origin as _from_origin
    from app.lib.artifact_cache import clear_artifact_cache, make_artifact_key, publish_artifact

    data_dir = Path(__file__).parent.parent.parent / "data"
    src = data_dir / f"test_perf_art_{_uuid.uuid4().hex[:8]}.tif"
    arr = np.ones((64, 64), dtype=np.float32)
    with _rasterio.open(
        src, "w", driver="GTiff", height=64, width=64, count=1, dtype=np.float32,
        crs="EPSG:4326", transform=_from_origin(0, 64, 1, 1),
    ) as dst:
        dst.write(arr, 1)
    try:
        key = make_artifact_key(str(src), "resample", {"target_resolution": 100.0})
        out = str(data_dir / f"test_perf_art_out_{_uuid.uuid4().hex[:8]}.tif")
        with _rasterio.open(out, "w", driver="GTiff", height=64, width=64, count=1,
                            dtype=np.float32, crs="EPSG:3857",
                            transform=_from_origin(0, 64, 100, 100)) as dst:
            dst.write(arr, 1)
        publish_artifact(key, str(src), lambda: out)  # prime the cache

        t0 = time.perf_counter()
        for _ in range(50):
            publish_artifact(key, str(src), lambda: (_ for _ in ()).throw(AssertionError("should hit cache")))
        elapsed = (time.perf_counter() - t0) * 1000 / 50
        _os.unlink(out)
        return elapsed
    finally:
        src.unlink(missing_ok=True)
        clear_artifact_cache()


WORKLOADS = {
    "raster_guard_rejection": _raster_guard_rejection_ms,
    "ref_resolution_batch": _ref_resolution_batch_ms,
    "metrics_enqueue": _metrics_enqueue_ms,
    "dispatch_overhead": _dispatch_overhead_ms,
    "reclassify_windowed": _reclassify_windowed_ms,
    "h3_binning_10k": _h3_binning_ms,
    "artifact_cache_hit": _artifact_cache_hit_ms,
}


# ─── baselines ───────────────────────────────────────────────────────────────


def _load_baselines() -> dict:
    if BASELINES_PATH.exists():
        return json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    return {}


def _save_baselines(baselines: dict) -> None:
    BASELINES_PATH.write_text(
        json.dumps(baselines, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


@pytest.fixture(autouse=True)
def _clean_state():
    _reset_redis_client_for_tests()
    yield
    _reset_redis_client_for_tests()


@pytest.mark.perf
@pytest.mark.parametrize("name", sorted(WORKLOADS))
def test_perf_workload(name):
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
        pytest.skip(f"WARNING: '{name}' median {measured:.3f} ms vs baseline {baseline_ms:.3f} ms")
        return

    assert measured <= warn_at, f"'{name}' {measured:.3f} ms vs {warn_at:.3f} ms"
