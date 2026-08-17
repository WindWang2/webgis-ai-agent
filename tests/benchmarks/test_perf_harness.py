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

import networkx as nx
from shapely.geometry import LineString

from app.lib.tool_cache import _reset_redis_client_for_tests
from app.services import tool_metrics
from app.tools.registry import ToolRegistry

BASELINES_PATH = Path(__file__).parent / "baselines.json"
UPDATE_BASELINES = os.environ.get("PERF_UPDATE_BASELINES") == "1"

# The raster workloads write temp .tif files under <repo>/data/. That dir is
# gitignored and does not exist on a fresh CI checkout (the main Backend Tests
# job creates it via fixtures; the isolated perf gate job does not). Ensure it
# exists once at import so rasterio.open("w", ...) works in both environments.
_PERF_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_PERF_DATA_DIR.mkdir(parents=True, exist_ok=True)

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


def _quality_audit_topology_ms() -> float:
    """Issue #539 workload: bounded pairwise topology audit on 200 concentric
    bbox-nested rings (degenerate all-pairs case on master: ~1.05 s, quadratic —
    400 rings ~4.2 s). Guards the cap+truncation fix against a regression that
    reintroduces the unbounded O(P²) scan."""
    import math

    from app.services.spatial_quality_service import SpatialQualityEngine

    n_pts = 24
    features = []
    for i in range(1, 201):
        r = 1.0 + 0.02 * i
        ring = []
        for a in range(n_pts):
            ang = 2.0 * math.pi * a / n_pts
            ring.append([round(r * math.cos(ang), 6), round(r * math.sin(ang), 6)])
        ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {"id": i},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    geojson = {"type": "FeatureCollection", "features": features}

    t0 = time.perf_counter()
    report = SpatialQualityEngine.audit_dataset(geojson, crs="EPSG:4326")
    dt = (time.perf_counter() - t0) * 1000
    assert report.total_features == 200
    topo = [i for i in report.issues if i.dimension == "topology"]
    assert len(topo) == SpatialQualityEngine.MAX_TOPOLOGY_ISSUES, "audit must stop at the issue budget"
    assert report.truncated is True
    return dt


def _two_opt_ladder_ms() -> float:
    """Issue #540 workload: 2-opt on the adversarial interleaved ladder at
    n=160 (master: ~3.1 s, ~n^3.9). Guards the O(1)-delta scan."""
    import math

    from app.services.network.vrp import NetworkRouteOptimizationService

    half = 80
    pts = []
    for i in range(half):
        pts.append((0.0, float(i)))
        pts.append((1.0, float(i)))
    cost = [[math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) for j in range(160)] for i in range(160)]

    svc = NetworkRouteOptimizationService()
    t0 = time.perf_counter()
    tour = svc._two_opt(list(range(160)), cost, is_roundtrip=False)  # noqa: SLF001
    dt = (time.perf_counter() - t0) * 1000
    assert len(tour) == 160
    return dt


_PERF_GRID_112: Optional[Any] = None


def _perf_grid_112():
    """~50k-directed-edge grid, built once; _apply_barriers works on a copy so
    the source graph is never mutated. Excludes the ~0.66 s graph build (equal
    before/after) so the workload measures the barrier-application hotspot."""
    global _PERF_GRID_112
    if _PERF_GRID_112 is None:
        n = 112
        g = nx.DiGraph()
        for r in range(n):
            for c in range(n):
                g.add_node((r, c), x=116.0 + c * 0.001, y=39.0 + r * 0.001)
        eid = 0
        for r in range(n):
            for c in range(n):
                for dr, dc in ((0, 1), (1, 0)):
                    nr, nc = r + dr, c + dc
                    if nr >= n or nc >= n:
                        continue
                    geom = LineString([(116.0 + c * 0.001, 39.0 + r * 0.001),
                                       (116.0 + nc * 0.001, 39.0 + nr * 0.001)])
                    for u, v in (((r, c), (nr, nc)), ((nr, nc), (r, c))):
                        g.add_edge(u, v, id=eid, length_m=100.0, travel_time_s=60.0,
                                   geometry=geom.__geo_interface__)
                        eid += 1
        _PERF_GRID_112 = g
    return _PERF_GRID_112


def _barriers_indexed_ms() -> float:
    """Issue #540 workload: 3 polygon barriers over a ~50k-directed-edge grid
    (master: barrier scan ~1.4 s after removing the ~0.66 s graph build; the
    STRtree index makes it ~0.5 s). Guards the indexed barrier lookup."""
    from app.services.network.models import Barrier
    from app.services.network.routing import NetworkRoutingService

    g = _perf_grid_112()
    n = 112
    span = n * 0.001
    barriers = []
    for i in range(3):
        frac = 0.25 + 0.1 * i
        x0, y0 = 116.0 + span * 0.1, 39.0 + span * 0.1
        x1, y1 = x0 + span * frac, y0 + span * frac
        barriers.append(Barrier(
            barrier_id=f"b{i}", barrier_type="polygon",
            geometry={"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]},
            impedance_factor=float("inf"),
        ))
    router = NetworkRoutingService()
    t0 = time.perf_counter()
    view = router._apply_barriers(g, barriers)  # noqa: SLF001
    dt = (time.perf_counter() - t0) * 1000
    assert view is not g
    assert len(set(g.edges()) - set(view.edges())) > 0
    return dt


_PERF_CF_GRID: Optional[Any] = None


def _perf_cf_grid():
    """20×20 grid graph+dataset built once; closest_facility never mutates the
    caller's graph here (node-exact snaps, no barriers), so sharing is safe and
    the ~1.1 s graph build is excluded from the measured analysis time."""
    from app.services.network.graph_builder import NetworkGraphBuilder

    global _PERF_CF_GRID
    if _PERF_CF_GRID is None:
        step = 0.001
        n = 20
        features = []
        for r in range(n):
            features.append({"type": "Feature", "properties": {"id": f"h{r}"},
                             "geometry": {"type": "LineString", "coordinates": [
                                 [116.0, 39.0 + r * step], [116.0 + (n - 1) * step, 39.0 + r * step]]}})
        for c in range(n):
            features.append({"type": "Feature", "properties": {"id": f"v{c}"},
                             "geometry": {"type": "LineString", "coordinates": [
                                 [116.0 + c * step, 39.0], [116.0 + c * step, 39.0 + (n - 1) * step]]}})
        _PERF_CF_GRID = NetworkGraphBuilder().build_graph({"type": "FeatureCollection", "features": features})
    return _PERF_CF_GRID


def _closest_facility_routes_ms() -> float:
    """Issue #540 workload: 30 demands × 40 facilities, top-3 (master built a
    Route for all 1200 reachable pairs; the analysis was ~0.5 s + ~1.1 s graph
    build). Guards top-K-first Route construction (≤ D×K builds)."""
    from app.services.network.facility import NetworkClosestFacilityService
    from app.services.network.models import DemandPoint, Facility

    step = 0.001
    n = 20
    g, ds = _perf_cf_grid()

    def coord(i):
        r, c = divmod(i, n)
        return [116.0 + c * step, 39.0 + r * step]

    rng = np.random.default_rng(11)
    nodes = list(range(n * n))
    rng.shuffle(nodes)
    demands = [DemandPoint(demand_id=f"d{i}", weight=1.0,
                           geometry={"type": "Point", "coordinates": coord(x)})
               for i, x in enumerate(nodes[:30])]
    facilities = [Facility(facility_id=f"f{i}", geometry={"type": "Point", "coordinates": coord(x)})
                  for i, x in enumerate(nodes[30:70])]
    svc = NetworkClosestFacilityService()
    t0 = time.perf_counter()
    res = svc.network_closest_facility(
        demand_points=demands, facilities=facilities, graph=g, network_dataset=ds,
        target_facility_count=3,
    )
    dt = (time.perf_counter() - t0) * 1000
    assert len(res.routes) == 30 * 3
    return dt


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
    # issues #539/#540 regression guards (see each workload docstring)
    "audit_topology_200rings": _quality_audit_topology_ms,
    "two_opt_ladder_160": _two_opt_ladder_ms,
    "barriers_indexed_50kE_x3": _barriers_indexed_ms,
    "closest_facility_30x40": _closest_facility_routes_ms,
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
