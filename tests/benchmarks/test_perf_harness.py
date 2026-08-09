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
from typing import Any, Optional
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


_PERF_TILE_RASTER_PATH: Optional[Path] = None


def _get_perf_tile_raster() -> Path:
    global _PERF_TILE_RASTER_PATH
    if _PERF_TILE_RASTER_PATH is None or not _PERF_TILE_RASTER_PATH.exists():
        data_dir = Path(__file__).parent.parent.parent / "data"
        _PERF_TILE_RASTER_PATH = data_dir / "test_perf_tile_static.tif"
        arr = np.ones((1, 200, 200), dtype=np.float32) * 42.0
        with rasterio.open(
            _PERF_TILE_RASTER_PATH, "w", driver="GTiff", height=200, width=200, count=1,
            dtype=np.float32, crs="EPSG:4326", transform=from_origin(116.0, 40.0, 0.005, 0.005),
        ) as dst:
            dst.write(arr)
    return _PERF_TILE_RASTER_PATH


def _network_snapping_cached_ms() -> float:
    """Network PERF-02 workload: STRtree + node-lookup cached across many snaps.

    Builds a synthetic grid network, then snaps 50 points. The first snap
    builds the spatial index; the remaining 49 reuse the cache. Guards the
    PERF-02 fix (PointSnappingService STRtree caching) against a regression
    that rebuilds the tree per snap.
    """
    from app.services.network.snapping import PointSnappingService
    from app.services.network.models import NetworkDataset, Node, Edge

    n = 24  # → ~1100 edges
    nodes, edges = [], []
    nid = 0
    node_map = {}
    for r in range(n):
        for c in range(n):
            node_map[(r, c)] = nid
            nodes.append(Node(id=nid, x=116.0 + c * 0.001, y=39.0 + r * 0.001))
            nid += 1
    eid = 0
    for r in range(n):
        for c in range(n):
            if c < n - 1:
                edges.append(Edge(id=eid, u=node_map[(r, c)], v=node_map[(r, c + 1)],
                                  length_m=100.0, travel_time_s=60.0))
                eid += 1
            if r < n - 1:
                edges.append(Edge(id=eid, u=node_map[(r, c)], v=node_map[(r + 1, c)],
                                  length_m=100.0, travel_time_s=60.0))
                eid += 1
    ds = NetworkDataset(dataset_id="perf_grid", nodes=nodes, edges=edges, crs="EPSG:4326")
    svc = PointSnappingService()
    pts = [(116.005 + i * 0.0004, 39.005 + i * 0.0004) for i in range(50)]

    t0 = time.perf_counter()
    for p in pts:
        svc.snap_point(p, ds)
    return (time.perf_counter() - t0) * 1000


def _geojson_bbox_large_ms() -> float:
    """GIS-10 workload: canonical bbox walker over a large FeatureCollection.

    Guards the geojson_bbox walker (used for Spatial Meta Profile + auto
    view.center injection) against a regression that reintroduces per-feature
    overhead or mishandles GeometryCollection.
    """
    from app.utils.geojson import geojson_bbox

    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": i, "name": "pt"},
                "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.0001, 39.0]},
            }
            for i in range(10000)
        ],
    }
    t0 = time.perf_counter()
    bbox = geojson_bbox(fc)
    dt = (time.perf_counter() - t0) * 1000
    assert bbox is not None and bbox[2] > bbox[0]
    return dt


def _metric_byte_estimate_ms() -> float:
    """PERF-01 workload: _estimate_json_bytes over a large tool-result dict.

    Guards the cheap byte estimate (which replaced a second full json.dumps on
    every dispatch) against a regression that makes it O(N²) or re-enables the
    full serialization.
    """
    from app.tools.registry import _estimate_json_bytes

    big = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": i, "name": "x" * 20, "v": i * 1.5},
                "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.001, 39.0]},
            }
            for i in range(10000)
        ],
    }
    t0 = time.perf_counter()
    est = _estimate_json_bytes(big)
    dt = (time.perf_counter() - t0) * 1000
    assert est > 1_000_000  # ~1.4MB
    return dt


def _raster_tile_streaming_ms() -> float:
    """Windowed raster tile rendering: 256x256 PNG generation from GeoTIFF."""
    from app.services.raster_tile_service import render_raster_tile

    path = _get_perf_tile_raster()
    t0 = time.perf_counter()
    png_bytes = render_raster_tile(str(path), z=8, x=210, y=100, tile_size=256)
    elapsed = (time.perf_counter() - t0) * 1000
    assert png_bytes.startswith(b"\x89PNG")
    return elapsed


WORKLOADS = {
    "raster_guard_rejection": _raster_guard_rejection_ms,
    "ref_resolution_batch": _ref_resolution_batch_ms,
    "metrics_enqueue": _metrics_enqueue_ms,
    "dispatch_overhead": _dispatch_overhead_ms,
    "reclassify_windowed": _reclassify_windowed_ms,
    "h3_binning_10k": _h3_binning_ms,
    "artifact_cache_hit": _artifact_cache_hit_ms,
    "raster_tile_streaming": _raster_tile_streaming_ms,
    # deep-audit-performance-convergence additions (PERF-01/02 + GIS-10 coverage)
    "network_snapping_cached": _network_snapping_cached_ms,
    "geojson_bbox_large": _geojson_bbox_large_ms,
    "metric_byte_estimate": _metric_byte_estimate_ms,
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
        # TEST-01: the previous behavior was `pytest.skip(...)`, which silently
        # hid 1.75x–4x regressions from CI ("N skipped" reads as fine). A perf
        # regression in the warn band must be observable so it can be
        # investigated or the baseline deliberately refreshed. Fail (soft) with
        # a clear message distinguishing it from a hard (>4x) regression.
        pytest.fail(
            f"PERF REGRESSION (warn band): '{name}' median {measured:.3f} ms "
            f"is {measured/baseline_ms:.2f}x baseline {baseline_ms:.3f} ms "
            f"(warn at {warn_at:.3f} ms, hard-fail at {fail_at:.3f} ms). "
            f"Investigate or refresh the baseline with PERF_UPDATE_BASELINES=1.",
            pytrace=False,
        )

    assert measured <= warn_at, f"'{name}' {measured:.3f} ms vs {warn_at:.3f} ms"
