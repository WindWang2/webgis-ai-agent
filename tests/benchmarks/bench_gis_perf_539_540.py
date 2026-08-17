"""Standalone performance repro for issues #539 and #540.

  - #539: spatial quality audit topology dimension is O(P²) unbounded (concentric
    rings: every bbox-nested pair is a candidate → up to 5 GEOS calls per pair).
  - #540: network engine hotspots — (1) barrier full-edge scan B×E, (2)
    closest_facility builds a full Route per reachable pair, (3) VRP 2-opt
    recomputes the whole tour cost per candidate (O(n³)), + service_area
    per-call graph copy residue.

Not a pytest file on purpose: it runs against ANY revision (git worktree /
git stash swap) to produce before/after numbers. The deterministic regression
gate lives in tests/benchmarks/test_perf_harness.py (perf marker = nightly).

Usage:
    .venv/bin/python tests/benchmarks/bench_gis_perf_539_540.py [--quick]
    PERF_TAG=before|after .venv/bin/python tests/benchmarks/bench_gis_perf_539_540.py [--quick]

Writes per-workload median wall-clock ms to logs/gis_perf_539_540_{tag}.json
(logs/ is gitignored) and prints a table.
"""
from __future__ import annotations

import json
import math
import os
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import networkx as nx  # noqa: E402
from shapely.geometry import LineString  # noqa: E402


def median_ms(fn, iters=3) -> float:
    times = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(times)


# ─── #539: quality-audit topology ────────────────────────────────────────────


def _concentric_rings_geojson(k: int, n_pts: int = 24) -> dict:
    """k concentric filled polygons with nested bboxes (worst case for the
    STRtree candidate loop — every pair survives the bbox prune, and every
    inner ring genuinely overlaps the outer one)."""
    features = []
    for i in range(1, k + 1):
        r = 1.0 + 0.02 * i  # radius grows → strict bbox nesting
        ring = []
        for a in range(n_pts):
            ang = 2.0 * math.pi * a / n_pts
            ring.append([round(r * math.cos(ang), 6), round(r * math.sin(ang), 6)])
        ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {"id": i, "ring": i},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    return {"type": "FeatureCollection", "features": features}


def _audit_topology(k: int) -> float:
    from app.services.spatial_quality_service import SpatialQualityEngine

    geojson = _concentric_rings_geojson(k)
    report = SpatialQualityEngine.audit_dataset(geojson, crs="EPSG:4326")
    assert report.total_features == k
    return 0.0


def workload_audit_topology_200() -> float:
    return _audit_topology(200)


def workload_audit_topology_400() -> float:
    return _audit_topology(400)


# ─── #540: VRP 2-opt ─────────────────────────────────────────────────────────


def _ladder_points(n: int) -> list:
    """Adversarial 'ladder': two parallel rows, interleaved order — with the
    tour in that natural order, first-improvement 2-opt performs Θ(n) full
    scans whose candidates grow cubically (measured ~2.75M pair evaluations at
    n=320 → ~124 s for the naive full-cost recompute on master)."""
    half = n // 2
    pts = []
    for i in range(half):
        pts.append((0.0, float(i)))
        pts.append((1.0, float(i)))
    if n % 2:
        pts.append((0.5, float(half)))
    return pts


def _euclid_cost(n_points: list) -> list:
    mat = []
    for i, (x1, y1) in enumerate(n_points):
        row = []
        for x2, y2 in n_points:
            row.append(math.hypot(x1 - x2, y1 - y2))
        mat.append(row)
    return mat


def _two_opt_bench(n: int) -> float:
    from app.services.network.vrp import NetworkRouteOptimizationService

    pts = _ladder_points(n)
    cost = _euclid_cost(pts)
    svc = NetworkRouteOptimizationService()
    svc._two_opt(list(range(n)), cost, is_roundtrip=False)  # noqa: SLF001
    return 0.0


def workload_two_opt_80() -> float:
    return _two_opt_bench(80)


def workload_two_opt_160() -> float:
    return _two_opt_bench(160)


def workload_two_opt_320() -> float:
    return _two_opt_bench(320)


# ─── #540: barrier full-edge scan ────────────────────────────────────────────


def _grid_graph(n: int) -> "nx.DiGraph":
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
    return g


def _barrier_polygons(n: int, count: int) -> list:
    """``count`` big polygon barriers, each covering a large fraction of the
    grid so the old code hits many edges per barrier (worst case B×E)."""
    from app.services.network.models import Barrier

    out = []
    span = n * 0.001
    for i in range(count):
        frac = 0.25 + 0.1 * i
        x0 = 116.0 + span * 0.1
        y0 = 39.0 + span * 0.1
        x1 = x0 + span * frac
        y1 = y0 + span * frac
        out.append(Barrier(
            barrier_id=f"b{i}",
            barrier_type="polygon",
            geometry={"type": "Polygon", "coordinates": [[
                [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0],
            ]]},
            impedance_factor=float("inf"),
        ))
    return out


_GRID_GRAPH_112: "nx.DiGraph" = None


def _grid_graph_112() -> "nx.DiGraph":
    """The 112×112 benchmark grid, built once and REUSED (``_apply_barriers``
    works on a copy, so the source graph is never mutated). Building the graph
    takes ~0.66 s and is identical before/after the fix — excluding it keeps
    the workload measuring the barrier-application hotspot itself."""
    global _GRID_GRAPH_112
    if _GRID_GRAPH_112 is None:
        _GRID_GRAPH_112 = _grid_graph(112)
    return _GRID_GRAPH_112


def _barriers_bench(grid_n: int, n_barriers: int) -> float:
    from app.services.network.routing import NetworkRoutingService

    router = NetworkRoutingService()
    g = _grid_graph_112()
    barriers = _barrier_polygons(grid_n, n_barriers)
    t0 = time.perf_counter()
    router._apply_barriers(g, barriers)  # noqa: SLF001
    return (time.perf_counter() - t0) * 1000.0


def workload_barriers_scan() -> float:
    return _barriers_bench(112, 3)  # ~50k directed edges × 3 barriers


# ─── #540: closest_facility per-pair Route construction ──────────────────────


_CF_GRIDS: dict = {}


def _cf_grid(grid_n: int):
    """Grid graph+dataset for a size, built once and reused. ``network_closest_
    facility`` never mutates the caller's graph (node-exact snaps, no barriers),
    so sharing across iterations is safe — and the ~1.1 s graph build is
    identical before/after the fix, so excluding it keeps the workload on the
    hotspot (per-pair Route construction)."""
    if grid_n not in _CF_GRIDS:
        from app.services.network.graph_builder import NetworkGraphBuilder

        builder = NetworkGraphBuilder()
        _CF_GRIDS[grid_n] = builder.build_graph(_grid_geojson_edges(grid_n, 0.001))
    return _CF_GRIDS[grid_n]


def _closest_facility_bench(grid_n: int, n_dem: int, n_fac: int, seed: int = 11) -> float:
    from app.services.network.facility import NetworkClosestFacilityService
    from app.services.network.models import DemandPoint, Facility

    # Node-exact demand/facility coordinates on the graph-builder's returned
    # dataset (its node ids are what snapping/tree resolution uses).
    step = 0.001
    g, ds = _cf_grid(grid_n)

    def coord(i: int) -> list:
        r, c = divmod(i, grid_n)
        return [116.0 + c * step, 39.0 + r * step]

    rng = random.Random(seed)
    nodes = list(range(grid_n * grid_n))
    rng.shuffle(nodes)
    demands = [DemandPoint(demand_id=f"d{i}", weight=1.0,
                           geometry={"type": "Point", "coordinates": coord(n)})
               for i, n in enumerate(nodes[:n_dem])]
    facilities = [Facility(facility_id=f"f{i}",
                           geometry={"type": "Point", "coordinates": coord(n)})
                  for i, n in enumerate(nodes[n_dem:n_dem + n_fac])]
    svc = NetworkClosestFacilityService()
    res = svc.network_closest_facility(
        demand_points=demands, facilities=facilities, graph=g, network_dataset=ds,
        target_facility_count=3,
    )
    got = [r.destination_id for r in res.routes]
    assert len(res.routes) == n_dem * 3, f"routes={len(res.routes)} demand={n_dem} fac={n_fac} got={got[:5]}"
    return 0.0


def _grid_geojson_edges(n: int, step: float = 0.001) -> dict:
    features = []
    for r in range(n):
        features.append({
            "type": "Feature", "properties": {"id": f"h{r}"},
            "geometry": {"type": "LineString", "coordinates": [
                [116.0, 39.0 + r * step], [116.0 + (n - 1) * step, 39.0 + r * step]]},
        })
    for c in range(n):
        features.append({
            "type": "Feature", "properties": {"id": f"v{c}"},
            "geometry": {"type": "LineString", "coordinates": [
                [116.0 + c * step, 39.0], [116.0 + c * step, 39.0 + (n - 1) * step]]},
        })
    return {"type": "FeatureCollection", "features": features}


def workload_closest_facility_30x40() -> float:
    return _closest_facility_bench(20, 30, 40)


def workload_closest_facility_60x80() -> float:
    return _closest_facility_bench(25, 60, 80)


WORKLOADS = {
    "audit_topology_200rings": workload_audit_topology_200,
    "audit_topology_400rings": workload_audit_topology_400,
    "two_opt_ladder_80": workload_two_opt_80,
    "two_opt_ladder_160": workload_two_opt_160,
    "two_opt_ladder_320": workload_two_opt_320,
    "barriers_scan_25kE_x3": workload_barriers_scan,
    "closest_facility_30x40": workload_closest_facility_30x40,
    "closest_facility_60x80": workload_closest_facility_60x80,
}

QUICK = {
    "audit_topology_200rings": workload_audit_topology_200,
    "two_opt_ladder_80": workload_two_opt_80,
    "two_opt_ladder_160": workload_two_opt_160,
    "barriers_scan_25kE_x3": workload_barriers_scan,
    "closest_facility_30x40": workload_closest_facility_30x40,
}


def main() -> int:
    quick = "--quick" in sys.argv
    if quick and "--full" not in sys.argv:
        workloads = QUICK
    else:
        workloads = WORKLOADS

    tag = os.environ.get("PERF_TAG", "local")
    iters = 3 if not quick else 2
    results: dict = {}
    print(f"{'workload':<28} {'median_ms':>12}   note")
    print("-" * 60)
    for name, fn in workloads.items():
        ms = median_ms(fn, iters=iters)
        results[name] = round(ms, 3)
        print(f"{name:<28} {ms:>12.3f}")

    out = Path(__file__).resolve().parent.parent.parent / "logs" / f"gis_perf_539_540_{tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())