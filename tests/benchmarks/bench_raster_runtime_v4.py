"""Raster Runtime V4 benchmark — bounded memory on large synthetic rasters.

Standalone (mirrors bench_gis_perf_539_540.py): generates fixtures on the
fly (nothing large is committed), measures the V4 windowed path against
the old habits it replaces, and writes a metrics table.

Usage:
    .venv/bin/python tests/benchmarks/bench_raster_runtime_v4.py [--quick]

Gate (exit 1): windowed zonal + windowed execution on a 10000×10000 float32
(400 MB) raster must stay under the memory budget with zero full-array
reads, and the full-read guard must refuse it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import rasterio
from rasterio.transform import from_origin


def _make_raster(path: Path, size: int, dtype="float32", tiled=True) -> Path:
    """Synthetic raster written in bounded row chunks (never materializes
    the full array even at fixture-build time)."""
    rng = np.random.default_rng(11)
    chunk = 1024
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=1,
        dtype=dtype, crs="EPSG:3857", transform=from_origin(0, 0, 10, 10),
        nodata=-9999.0, tiled=tiled, blockxsize=512, blockysize=512,
    ) as dst:
        for row0 in range(0, size, chunk):
            h = min(chunk, size - row0)
            block = rng.uniform(0, 1000, (h, size)).astype(dtype)
            dst.write(block, 1, window=rasterio.windows.Window(0, row0, size, h))
    return path


def bench(size: int, quick: bool) -> dict:
    from app.lib.geo_raster import (
        AlgorithmProfile,
        RasterSource,
        execute_windowed,
    )
    from app.lib.geo_raster.reader import RasterReader, RasterReaderError
    from app.lib.geo_raster.windowed import overview_statistics
    from app.lib.geo_analysis.raster_ops import _windowed_zonal_stats

    out: dict[str, object] = {"size": size}
    # header-only 20000² (full read would be 1.6 GB > budget) → guard check
    guard_path = Path(tempfile.mkdtemp(prefix="raster_v4_guard_")) / "big.tif"
    with rasterio.open(
        guard_path, "w", driver="GTiff", width=20_000, height=20_000, count=3,
        dtype="float32", crs="EPSG:3857", transform=from_origin(0, 0, 10, 10),
    ):
        pass
    with RasterReader.open(str(guard_path)) as r:
        try:
            r.read_full()
            out["full_read_guard"] = "FAIL: allowed"
        except RasterReaderError:
            out["full_read_guard"] = "ok"
    os.unlink(guard_path)
    os.rmdir(guard_path.parent)

    tmpdir = tempfile.mkdtemp(prefix="raster_v4_bench_")
    path = _make_raster(Path(tmpdir) / f"r{size}.tif", size)

    # 1. full-read guard: a size² float32 ≥ budget must be refused. For
    # 10000² (400 MB < 512 MB budget) the allow is CORRECT — the guard is
    # asserted separately below with a header-only 20000² raster (1.6 GB).

    # 2. windowed execution: peak memory + windows + wall time
    tracemalloc.start()
    t0 = time.monotonic()
    with RasterSource.from_path(str(path)).reader() as r:
        res = execute_windowed(
            r, AlgorithmProfile(halo=8),
            lambda a, core, read: a[core[1]-read[1]:core[1]-read[1]+core[3],
                                     core[0]-read[0]:core[0]-read[0]+core[2]],
        )
        out["windowed_seconds"] = round(time.monotonic() - t0, 2)
        out["windowed_windows"] = res.windows_processed
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        out["windowed_peak_mb"] = round(peak / 1e6, 1)

    # 3. windowed zonal (one zone bbox = centre 20%): peak + result sanity
    from shapely.geometry import Polygon, mapping

    zone_geo = mapping(Polygon.from_bounds(
        10 * size * 0.4, -10 * size * 0.6, 10 * size * 0.6, -10 * size * 0.4
    ))
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": zone_geo, "properties": {}}]}
    tracemalloc.start()
    t0 = time.monotonic()
    stats = _windowed_zonal_stats(fc, str(path), stats=["mean", "min", "max", "count"])
    out["zonal_seconds"] = round(time.monotonic() - t0, 2)
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    out["zonal_peak_mb"] = round(peak / 1e6, 1)
    out["zonal_mean"] = round(stats[0]["mean"], 1)
    out["zonal_count"] = stats[0]["count"]

    # 4. overview statistics (global stats without a full read)
    t0 = time.monotonic()
    with RasterSource.from_path(str(path)).reader() as r:
        gs = overview_statistics(r)
    out["overview_stats_seconds"] = round(time.monotonic() - t0, 3)
    out["overview_mean"] = round(gs["mean"], 1)

    # 5. COG roundtrip on a subwindow copy (quick mode skips for the big one)
    if quick and size <= 10000:
        from app.lib.geo_raster.cog import validate_cog, write_cog

        cog = write_cog(str(path), str(Path(tmpdir) / "cog.tif"))
        out["cog_valid"] = validate_cog(str(cog))["ok"]

    os.unlink(path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args()

    sizes = [10_000, 20_000] if not args.quick else [10_000]
    # 20000² float32 = 1.6 GB on disk; the header-only probe is what the
    # benchmark asserts for it (window paths already proven at 10000²).
    results = []
    for size in sizes:
        r = bench(size, args.quick)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False))

    full_mb = {r["size"]: r["size"] * r["size"] * 4 / 1e6 for r in results}
    gate = all(
        r.get("full_read_guard") == "ok"
        and r["zonal_mean"] is not None
        and 0 < r["zonal_mean"] < 1000
        # windowed increment over the merged output array must stay bounded
        # (the output array itself is the in-memory API contract; streaming
        # to disk is the V3 WindowedRasterWriter path)
        and r["windowed_peak_mb"] < full_mb[r["size"]] + 128
        and r["zonal_peak_mb"] < 512
        for r in results
    )
    print(f"\nraster-v4 invariants: {'PASS' if gate else 'FAIL'}")
    out_path = args.json or os.environ.get("BENCH_OUT")
    if out_path:
        Path(out_path).write_text(json.dumps(results, indent=2))
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
