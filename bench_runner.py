"""Fast, deterministic quantitative benchmark runner for MVT memory & cache pressure."""

import asyncio
import gc
import gzip
import json
import os
import resource
import sys
import time
from typing import Any, Dict, List

# #478: benchmark must import from THIS repository and write its output back
# here — never from a foreign author-machine worktree. Resolve the repo root
# from the script location so a clean clone reproduces identically.
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# BENCH_FAST=1 shrinks scenario sizes for the CI smoke run (a bounded
# functional check of the runner itself, not the committed evidence).
_BENCH_FAST = os.environ.get("BENCH_FAST") == "1"


def _scale(n: int, fast: int) -> int:
    return fast if _BENCH_FAST else n

import app.services.mvt as mvt_mod
from app.services.mvt import (
    SingleFlightManager,
    SpatialIndexCache,
    SpatialIndexEntry,
    TileLRUCache,
    build_spatial_index_entry,
    encode_tile_from_index,
    single_flight,
    spatial_index_cache,
    tile_lru_cache,
)
# Production tile pipeline used by GET /layers/data/{ref_id}/tiles/... — the
# route body calls exactly this function via asyncio.to_thread (#478: drive
# the production call path, not just cache internals).
from app.api.routes.layer import _encode_tile_cached


def get_rss_kb() -> int:
    """Current RSS in KB."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def generate_geojson(n_features: int, geom_type: str = "Point", center=(116.40, 39.90), spread=0.5) -> dict:
    features = []
    cx, cy = center
    for i in range(n_features):
        u = (i % 1000) / 1000.0
        v = (i // 1000) / max(1, (n_features // 1000))
        lon = cx + (u - 0.5) * spread
        lat = cy + (v - 0.5) * spread
        if geom_type == "Point":
            coords = [lon, lat]
        elif geom_type == "LineString":
            coords = [[lon, lat], [lon + 0.005, lat + 0.005], [lon + 0.01, lat + 0.002]]
        elif geom_type == "Polygon":
            coords = [[
                [lon, lat],
                [lon + 0.008, lat],
                [lon + 0.008, lat + 0.008],
                [lon, lat + 0.008],
                [lon, lat],
            ]]
        features.append({
            "type": "Feature",
            "geometry": {"type": geom_type, "coordinates": coords},
            "properties": {"id": i, "name": f"feature_{i}", "val": i * 1.5},
        })
    return {"type": "FeatureCollection", "features": features}


def count_entry_objects(entry: SpatialIndexEntry) -> Dict[str, Any]:
    n_feat = len(entry.features) if entry.features else 0
    n_gz0 = len(entry.geoms) if entry.geoms else 0
    n_gll = len(entry.geoms_lonlat) if entry.geoms_lonlat else 0
    n_b = len(entry.bounds) if entry.bounds else 0
    # Estimate bytes: feature dict ~350B, shapely geom ~200B, bounds ~80B
    est_bytes = n_feat * 350 + (n_gz0 + n_gll) * 200 + n_b * 80
    return {
        "features": n_feat,
        "geoms_z0": n_gz0,
        "geoms_lonlat": n_gll,
        "total_geometry_objects": n_gz0 + n_gll,
        "bounds_count": n_b,
        "estimated_entry_bytes": est_bytes,
        "estimated_entry_mb": round(est_bytes / (1024 * 1024), 2),
    }


def run_all_benchmarks() -> Dict[str, Any]:
    results = {}
    print("=" * 80)
    print("MVT DATA PLANE DETERMINISTIC BENCHMARK")
    print("=" * 80)

    # 1. 1 x 100k Point / Line / Polygon layer memory
    for gtype in ["Point", "LineString", "Polygon"]:
        fc = generate_geojson(_scale(100_000, 2_000), gtype)
        t0 = time.perf_counter()
        entry = build_spatial_index_entry(("s1", f"ref_100k_{gtype}"), fc)
        t1 = time.perf_counter()
        
        counts = count_entry_objects(entry)
        counts["build_time_sec"] = round(t1 - t0, 4)
        results[f"scenario1_100k_{gtype}"] = counts
        print(f"1 x 100k {gtype:10s}: {counts['total_geometry_objects']} geoms, {counts['estimated_entry_mb']} MB (build: {counts['build_time_sec']}s)")

    # 2. 10 x 10k layers
    cache_10x10k = SpatialIndexCache(max_refs=256)
    t0 = time.perf_counter()
    for i in range(10):
        fc = generate_geojson(_scale(10_000, 500), "Polygon" if i % 2 == 0 else "LineString")
        key = ("s1", f"ref_{i}")
        cache_10x10k.get_or_build(key, lambda k=key, data=fc: build_spatial_index_entry(k, data))
    t1 = time.perf_counter()
    entries_10x10k = list(cache_10x10k)
    total_geoms_10x10k = sum(
        (len(e.geoms or []) + len(e.geoms_lonlat or [])) for e in entries_10x10k
    )
    total_bytes_10x10k = sum(
        count_entry_objects(e)["estimated_entry_bytes"] for e in entries_10x10k
    )
    results["scenario2_10x10k"] = {
        "entries": len(cache_10x10k),
        "total_geometry_objects": total_geoms_10x10k,
        "estimated_total_mb": round(total_bytes_10x10k / (1024 * 1024), 2),
        "build_time_sec": round(t1 - t0, 4),
    }
    print(f"10 x 10k layers: {len(cache_10x10k)} entries, {total_geoms_10x10k} geoms, {results['scenario2_10x10k']['estimated_total_mb']} MB")

    # 3. 100 small layers (50 features each)
    cache_small = SpatialIndexCache(max_refs=256)
    for i in range(_scale(100, 20)):
        fc = generate_geojson(50, "Point")
        key = ("s1", f"small_{i}")
        cache_small.get_or_build(key, lambda k=key, data=fc: build_spatial_index_entry(k, data))
    results["scenario3_100_small"] = {
        "entries": len(cache_small),
        "total_features": sum(len(e.features) for e in list(cache_small)),
    }
    print(f"100 small layers: {len(cache_small)} entries, {results['scenario3_100_small']['total_features']} features")

    # 4. 5 sessions x 10 layers (500 features each)
    cache_multi = SpatialIndexCache(max_refs=256)
    for s in range(5):
        for r in range(10):
            fc = generate_geojson(500, "Point")
            key = (f"sess_{s}", f"ref_{r}")
            cache_multi.get_or_build(key, lambda k=key, data=fc: build_spatial_index_entry(k, data))
    results["scenario4_5sess_10layers"] = {
        "entries": len(cache_multi),
        "distinct_sessions": len(set(k[0] for k in cache_multi.keys())),
    }
    print(f"5 sessions x 10 layers: {len(cache_multi)} entries across {results['scenario4_5sess_10layers']['distinct_sessions']} sessions")

    # 5. Same tile x 50 concurrent requests
    async def bench_single_flight():
        sf = SingleFlightManager(max_inflight=512)
        tc = TileLRUCache(max_tiles=4096)
        fc = generate_geojson(_scale(5000, 500), "LineString")
        entry = build_spatial_index_entry(("s1", "r1"), fc)
        encode_count = 0

        async def fetch():
            nonlocal encode_count
            k = ("s1", "r1", 12, 3371, 1550)
            cached = tc.get(k)
            if cached is not None:
                return cached
            async def _comp():
                nonlocal encode_count
                encode_count += 1
                await asyncio.sleep(0.005)
                raw = encode_tile_from_index(entry, 12, 3371, 1550)
                compressed = gzip.compress(raw)
                tc.put(k, compressed)
                return compressed
            return await sf.run(k, _comp)

        t0 = time.perf_counter()
        res = await asyncio.gather(*[fetch() for _ in range(50)])
        t1 = time.perf_counter()
        return encode_count, len(res), t1 - t0

    enc_count, num_res, duration = asyncio.run(bench_single_flight())
    results["scenario5_50_concurrent"] = {
        "requests": num_res,
        "actual_encodes": enc_count,
        "single_flight_dedup_ratio": f"{num_res}:{enc_count}",
        "duration_sec": round(duration, 4),
    }
    print(f"50 concurrent same-tile: {num_res} requests -> {enc_count} encodes in {duration:.4f}s")

    # 6. 500 distinct tiles
    tc500 = TileLRUCache(max_tiles=4096)
    fc_poly = generate_geojson(_scale(2000, 300), "Polygon")
    entry_poly = build_spatial_index_entry(("s1", "r1"), fc_poly)
    encoded = 0
    t0 = time.perf_counter()
    for z in range(8, 15):
        for x in range(min(10, 1 << z)):
            for y in range(min(10, 1 << z)):
                if encoded >= _scale(500, 50):
                    break
                k = ("s1", "r1", z, x, y)
                raw = encode_tile_from_index(entry_poly, z, x, y)
                tc500.put(k, gzip.compress(raw))
                encoded += 1
    t1 = time.perf_counter()
    results["scenario6_500_tiles"] = {
        "entries": len(tc500),
        "total_tile_bytes": tc500.total_bytes,
        "duration_sec": round(t1 - t0, 4),
    }
    print(f"500 distinct tiles: {len(tc500)} entries, {tc500.total_bytes} bytes ({tc500.total_bytes / 1024:.1f} KB) in {t1 - t0:.4f}s")

    # 7. Overwrite ref lifecycle check
    # In master before fix: spatial_index_cache and tile_lru_cache DO NOT have invalidate methods for (session, ref)
    results["scenario7_overwrite_lifecycle"] = {
        "has_ref_invalidation": hasattr(spatial_index_cache, "invalidate_ref"),
        "has_tile_ref_invalidation": hasattr(tile_lru_cache, "invalidate_ref"),
    }
    print(f"Scenario 7 (Overwrite Invalidation): spatial_index has invalidate_ref: {results['scenario7_overwrite_lifecycle']['has_ref_invalidation']}, tile_cache has invalidate_ref: {results['scenario7_overwrite_lifecycle']['has_tile_ref_invalidation']}")

    # 8. Session clear lifecycle check
    results["scenario8_session_clear_lifecycle"] = {
        "has_session_invalidation": hasattr(spatial_index_cache, "invalidate_session"),
        "has_tile_session_invalidation": hasattr(tile_lru_cache, "invalidate_session"),
    }
    print(f"Scenario 8 (Session Clear Invalidation): spatial_index has invalidate_session: {results['scenario8_session_clear_lifecycle']['has_session_invalidation']}, tile_cache has invalidate_session: {results['scenario8_session_clear_lifecycle']['has_tile_session_invalidation']}")

    # 9. Index eviction and rebuild
    cache_small_lru = SpatialIndexCache(max_refs=5)
    for i in range(10):
        fc = generate_geojson(_scale(100, 20), "Point")
        key = ("s1", f"r_{i}")
        cache_small_lru.get_or_build(key, lambda k=key, data=fc: build_spatial_index_entry(k, data))
    results["scenario9_index_lru_eviction"] = {
        "retained_entries": len(cache_small_lru),
        "keys_present": list(k[1] for k in cache_small_lru.keys()),
    }
    print(f"Scenario 9 (Index LRU Eviction): retained {len(cache_small_lru)} entries -> {results['scenario9_index_lru_eviction']['keys_present']}")

    # 10. Cancelled tile producer
    async def bench_cancellation():
        sf = SingleFlightManager(max_inflight=512)
        started = asyncio.Event()
        async def slow():
            started.set()
            await asyncio.sleep(0.5)
            return b"done"
        task = asyncio.create_task(sf.run("k1", slow))
        await started.wait()
        task.cancel()
        cancelled = False
        try:
            await task
        except asyncio.CancelledError:
            cancelled = True
        inflight_clean = (len(sf) == 0)
        return cancelled, inflight_clean

    can, clean = asyncio.run(bench_cancellation())
    results["scenario10_cancellation"] = {
        "cancelled_properly": can,
        "inflight_cleared": clean,
    }
    print(f"Scenario 10 (Cancellation): Cancelled: {can}, Inflight Cleared: {clean}")

    # 11. Production route body: the exact tile pipeline GET /layers/data/
    #     {ref}/tiles/{z}/{x}/{y}.mvt runs — single-flight dedupe around
    #     asyncio.to_thread(_encode_tile_cached) on the route's real cache
    #     singletons (spatial_index_cache / tile_lru_cache / single_flight).
    async def bench_route_body():
        fc = generate_geojson(_scale(20_000, 1_000), "Polygon")
        sid, ref = "bench_session", "bench_ref"

        async def _fetch():
            cached = tile_lru_cache.get((sid, ref, 12, 3371, 1550))
            if cached is not None:
                return cached

            def _work():
                data = fc if spatial_index_cache.get((sid, ref)) is None else None
                return _encode_tile_cached(sid, ref, 12, 3371, 1550, data)

            async def _comp():
                return await asyncio.to_thread(_work)

            return await single_flight.run((sid, ref, 12, 3371, 1550), _comp)

        first = await _fetch()
        warm = await _fetch()  # LRU hit: byte-identical, zero re-encode
        encodes = 2
        t0 = time.perf_counter()
        res = await asyncio.gather(*[_fetch() for _ in range(_scale(50, 20))])
        dt = time.perf_counter() - t0
        return first, warm, encodes, res, dt

    first_body, warm_body, _, concurrent_res, route_dt = asyncio.run(bench_route_body())
    results["scenario11_production_route_body"] = {
        "tile_bytes": len(first_body),
        "warm_cache_hit_identical": warm_body == first_body,
        "concurrent_identical": all(r == first_body for r in concurrent_res),
        "requests": len(concurrent_res),
        "duration_sec": round(route_dt, 4),
    }
    print(
        f"Production route body: {len(first_body)} B tile, warm hit identical: "
        f"{warm_body == first_body}, {len(concurrent_res)} concurrent identical: "
        f"{all(r == first_body for r in concurrent_res)}"
    )
    # leave the global caches as we found them — the benchmark must not leak
    # its synthetic entries into a possibly-running app process's caches.
    spatial_index_cache.invalidate_session("bench_session")
    tile_lru_cache.invalidate_session("bench_session")

    print("=" * 80)
    print("BENCHMARK COMPLETED SUCCESSFULLY")
    return results


if __name__ == "__main__":
    res = run_all_benchmarks()
    # Output lives in THIS repository (script directory) unless an explicit
    # path is passed — reproducible from any clean clone (#478).
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_REPO_ROOT, "bench_results_after.json")
    with open(out_path, "w") as f:
        json.dump(res, f, indent=2)
    print(f"results written to {out_path}")
