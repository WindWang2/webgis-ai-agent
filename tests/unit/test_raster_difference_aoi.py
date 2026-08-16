"""Regression tests for the temporal raster fixes (#396).

1. ``raster_difference`` now derives its window from the AOI ∩ raster bounds
   (and validates CRS/transform alignment) instead of silently reading a fixed
   top-left 1024×1024 crop of src1.
2. ``execute_raster_analysis`` computes zonal statistics once and reuses them
   for the trend step instead of re-opening/re-statisticizing every raster.

All tests use small synthetic GeoTIFFs written to a tmp dir — no real data.
"""

import math

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.temporal.raster import TemporalRasterEngine
from app.services.temporal.models import TemporalRasterResult


def _write_raster(path, values, crs="EPSG:4326", transform=None, height=20, width=20):
    transform = transform or from_origin(0.0, float(height), 1.0, 1.0)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float64", crs=crs, transform=transform,
    ) as dst:
        dst.write(np.asarray(values, dtype="float64"), 1)
    return path


@pytest.fixture
def raster_pair(tmp_path):
    """Two aligned 20×20 rasters (pixel = 1°), diff = row index.

    Raster 1: value = col. Raster 2: value = col + row. Difference = row,
    so any window's mean difference equals the mean row of that window.
    """
    rows = np.arange(20.0)
    cols = np.arange(20.0)
    arr1 = np.tile(cols, (20, 1))
    arr2 = arr1 + rows[:, None]
    p1 = _write_raster(str(tmp_path / "t1.tif"), arr1)
    p2 = _write_raster(str(tmp_path / "t2.tif"), arr2)
    return p1, p2


def _aoi(xmin, ymin, xmax, ymax):
    """Eccentric AOI polygon (EPSG:4326) over x∈[xmin,xmax], y∈[ymin,ymax]."""
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]],
        },
    }


# ── AOI-windowed difference ─────────────────────────────────────────────────


def test_raster_difference_uses_aoi_window_not_top_left(raster_pair):
    """An AOI in the east-north quadrant must restrict the difference to those
    pixels: mean difference == mean row of the AOI window (rows 4..9 → 6.5),
    not the whole-raster mean (rows 0..19 → 9.5) the old fixed top-left read
    would have produced."""
    p1, p2 = raster_pair
    engine = TemporalRasterEngine()
    aoi = _aoi(12.0, 10.0, 18.0, 16.0)  # cols 12..17, rows 4..9 (y ∈ [10,16))

    res = engine.raster_difference(p1, p2, aoi_geometry=aoi)

    assert res["pixel_count"] == 6 * 6
    assert res["mean_difference"] == pytest.approx(6.5)
    assert res["min_difference"] == pytest.approx(4.0)
    assert res["max_difference"] == pytest.approx(9.0)


def test_raster_difference_manual_window_equivalence(raster_pair):
    """Difference stats equal a manual windowed read of the same AOI bounds."""
    p1, p2 = raster_pair
    aoi = _aoi(2.0, 2.0, 8.0, 12.0)  # cols 2..7, rows 8..17

    engine = TemporalRasterEngine()
    res = engine.raster_difference(p1, p2, aoi_geometry=aoi)

    with rasterio.open(p1) as s1, rasterio.open(p2) as s2:
        b1 = s1.read(1, window=rasterio.windows.Window(2, 8, 6, 10)).astype(float)
        b2 = s2.read(1, window=rasterio.windows.Window(2, 8, 6, 10)).astype(float)
    diff = b2 - b1
    assert res["pixel_count"] == int(diff.size)
    assert res["mean_difference"] == pytest.approx(float(np.nanmean(diff)))
    assert res["min_difference"] == pytest.approx(float(np.nanmin(diff)))
    assert res["max_difference"] == pytest.approx(float(np.nanmax(diff)))
    assert res["std_difference"] == pytest.approx(float(np.nanstd(diff)))


def test_raster_difference_without_aoi_uses_overlap(raster_pair):
    """Without an AOI the window must cover the overlapping bounds of both
    rasters — for two identical rasters that is the full extent."""
    p1, p2 = raster_pair
    engine = TemporalRasterEngine()
    res = engine.raster_difference(p1, p2)

    assert res["pixel_count"] == 400
    assert res["mean_difference"] == pytest.approx(np.mean(np.arange(20.0)))


def test_raster_difference_aoi_outside_returns_empty(raster_pair):
    """An AOI disjoint from the rasters yields an empty (zero) result, not a
    top-left window's worth of pixels."""
    p1, p2 = raster_pair
    engine = TemporalRasterEngine()
    aoi = _aoi(100.0, 100.0, 110.0, 110.0)
    res = engine.raster_difference(p1, p2, aoi_geometry=aoi)
    assert res["pixel_count"] == 0
    assert res["mean_difference"] == 0.0


# ── Alignment validation ────────────────────────────────────────────────────


def test_raster_difference_crs_mismatch_raises(raster_pair, tmp_path):
    p1, p2 = raster_pair
    p3 = _write_raster(str(tmp_path / "t3_utm.tif"), np.zeros((20, 20)), crs="EPSG:32650")
    engine = TemporalRasterEngine()
    with pytest.raises(ValueError, match="CRS mismatch"):
        engine.raster_difference(p1, p3)


def test_raster_difference_transform_mismatch_raises(raster_pair, tmp_path):
    p1, p2 = raster_pair
    p3 = _write_raster(
        str(tmp_path / "t3_shifted.tif"), np.zeros((20, 20)),
        transform=from_origin(0.5, 20.5, 1.0, 1.0),  # half-pixel shift
    )
    engine = TemporalRasterEngine()
    with pytest.raises(ValueError, match="transform mismatch"):
        engine.raster_difference(p1, p3)


# ── Single statistics pass per request ──────────────────────────────────────


def test_execute_raster_analysis_single_stats_pass(tmp_path, monkeypatch):
    """Each raster is opened once per request (the trend step reuses the
    first statistics pass instead of re-running it)."""
    rows = np.arange(20.0)
    cols = np.arange(20.0)
    base = np.tile(cols, (20, 1))
    paths = []
    for i, offset in enumerate((0.0, 5.0, 10.0)):
        p = str(tmp_path / f"series_{i}.tif")
        _write_raster(p, base + rows[:, None] * 0.0 + offset)
        paths.append(p)

    series = [{"timestamp": f"2026-0{i+1}-01T00:00:00Z", "path": p} for i, p in enumerate(paths)]
    aoi = _aoi(2.0, 2.0, 18.0, 18.0)

    # Count rasterio.open calls per path.
    opens: dict = {}
    real_open = rasterio.open

    def counting_open(path, *args, **kwargs):
        opens[str(path)] = opens.get(str(path), 0) + 1
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(rasterio, "open", counting_open)

    engine = TemporalRasterEngine()
    stats_calls = []
    real_stats = engine.temporal_raster_statistics

    def counting_stats(*a, **k):
        stats_calls.append(1)
        return real_stats(*a, **k)

    monkeypatch.setattr(engine, "temporal_raster_statistics", counting_stats)

    result = engine.execute_raster_analysis(
        raster_series=series,
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-03-01T00:00:00Z",
        aoi_geometry=aoi,
    )

    assert isinstance(result, TemporalRasterResult)
    assert len(result.selected_slices) == 3
    assert result.raster_trend["direction"] == "increasing"
    # statistics computed exactly once (trend reuses it)
    assert len(stats_calls) == 1
    # per-raster opens: 2 for the zonal statistics pass (CRS check + rasterstats
    # internal open) + 1 for the difference read on the two endpoint rasters.
    # The middle raster is NOT opened by the trend step — before the fix it was
    # opened twice more (4 total).
    assert opens[paths[0]] == 3, opens
    assert opens[paths[1]] == 2, opens
    assert opens[paths[2]] == 3, opens


def test_raster_trend_over_aoi_accepts_precomputed_stats(raster_pair, tmp_path):
    """raster_trend_over_aoi(stats_info=...) must produce the same result as
    the self-computing path but without re-opening the rasters."""
    p1, p2 = raster_pair
    series = [
        {"timestamp": "2026-01-01T00:00:00Z", "path": p1},
        {"timestamp": "2026-02-01T00:00:00Z", "path": p2},
    ]
    engine = TemporalRasterEngine()
    aoi = _aoi(0.0, 0.0, 20.0, 20.0)

    stats = engine.temporal_raster_statistics(series, aoi_geometry=aoi)
    with_precomputed = engine.raster_trend_over_aoi(series, aoi_geometry=aoi, stats_info=stats)
    self_computed = engine.raster_trend_over_aoi(series, aoi_geometry=aoi)

    assert with_precomputed["slope"] == self_computed["slope"]
    assert with_precomputed["direction"] == self_computed["direction"]
    assert with_precomputed["means"] == self_computed["means"]


# ─── #448: no silent 1024×1024 truncation (block-looped full window) ────────


def test_raster_difference_window_larger_than_1024_not_truncated(tmp_path):
    """Issue #448: the difference window must cover the FULL overlap, not the
    top-left 1024×1024 crop. The change here lives entirely outside that crop
    (rows/cols ≥ 1024): the old truncation reported mean 0.0 over 1,048,576
    pixels; the true statistics cover all 4,000,000 pixels."""
    n = 2000
    arr1 = np.zeros((n, n))
    arr2 = np.zeros((n, n))
    arr2[1024:, 1024:] = 10.0
    p1 = _write_raster(str(tmp_path / "big1.tif"), arr1, height=n, width=n)
    p2 = _write_raster(str(tmp_path / "big2.tif"), arr2, height=n, width=n)

    engine = TemporalRasterEngine()
    res = engine.raster_difference(p1, p2)

    diff = arr2 - arr1
    assert res["pixel_count"] == diff.size  # 4,000,000 — not 1,048,576
    assert res["mean_difference"] == pytest.approx(float(np.nanmean(diff)))
    assert res["min_difference"] == pytest.approx(float(np.nanmin(diff)))
    assert res["max_difference"] == pytest.approx(float(np.nanmax(diff)))
    assert res["std_difference"] == pytest.approx(float(np.nanstd(diff)))
    # Result metadata records the true analyzed extent.
    assert res["analyzed_window"] == {
        "col_off": 0, "row_off": 0, "width": n, "height": n,
    }


def test_raster_difference_large_window_equals_bruteforce_with_nans(tmp_path):
    """Equivalence vs brute-force full-window reference on a non-square
    window that spans multiple (partial) blocks, with NaN nodata cells in
    both rasters."""
    rng = np.random.default_rng(42)
    n, m = 1300, 1500
    arr1 = rng.normal(100.0, 5.0, (n, m))
    arr2 = rng.normal(101.5, 5.0, (n, m))
    arr1[rng.random((n, m)) < 0.1] = np.nan  # nodata in t1
    arr2[rng.random((n, m)) < 0.1] = np.nan  # nodata in t2 (partially disjoint)
    p1 = _write_raster(str(tmp_path / "r1.tif"), arr1, height=n, width=m)
    p2 = _write_raster(str(tmp_path / "r2.tif"), arr2, height=n, width=m)

    engine = TemporalRasterEngine()
    res = engine.raster_difference(p1, p2)

    diff = arr2 - arr1
    assert res["pixel_count"] == diff.size
    assert res["mean_difference"] == pytest.approx(float(np.nanmean(diff)), rel=1e-9)
    assert res["min_difference"] == pytest.approx(float(np.nanmin(diff)))
    assert res["max_difference"] == pytest.approx(float(np.nanmax(diff)))
    assert res["std_difference"] == pytest.approx(float(np.nanstd(diff)), rel=1e-6)


def test_raster_difference_large_aoi_window_full_coverage(tmp_path):
    """An AOI larger than 1024 px per side (anchored away from the origin)
    must be fully analyzed — the old code kept the anchor but truncated the
    size, dropping the far side of the AOI."""
    n = 2200
    arr1 = np.zeros((n, n))
    arr2 = np.zeros((n, n))
    arr2[n - 200:, n - 200:] = 5.0  # change in the AOI's far corner only
    p1 = _write_raster(str(tmp_path / "a1.tif"), arr1, height=n, width=n)
    p2 = _write_raster(str(tmp_path / "a2.tif"), arr2, height=n, width=n)

    engine = TemporalRasterEngine()
    aoi = _aoi(0.0, 0.0, float(n), float(n))  # full extent → 2200×2200 window
    res = engine.raster_difference(p1, p2, aoi_geometry=aoi)

    diff = arr2 - arr1
    assert res["pixel_count"] == diff.size
    assert res["mean_difference"] == pytest.approx(float(np.nanmean(diff)))
    assert res["analyzed_window"]["width"] == n
    assert res["analyzed_window"]["height"] == n


def test_raster_difference_all_nan_large_window(tmp_path):
    """Adversarial: a large window whose diff is entirely NaN must not crash
    the block loop (NaN stats, full pixel count)."""
    n = 1200
    arr = np.full((n, n), np.nan)
    p1 = _write_raster(str(tmp_path / "nan1.tif"), arr, height=n, width=n)
    p2 = _write_raster(str(tmp_path / "nan2.tif"), arr, height=n, width=n)

    engine = TemporalRasterEngine()
    res = engine.raster_difference(p1, p2)

    assert res["pixel_count"] == n * n
    assert math.isnan(res["mean_difference"])


# ─── #523: declared nodata sentinels must be masked, not counted ────────────


def _write_raster_nodata(path, values, nodata):
    """Write a GeoTIFF whose header declares ``nodata`` (raw values kept)."""
    rows, cols = np.asarray(values).shape
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1,
        dtype="float64", crs="EPSG:4326",
        transform=from_origin(0.0, float(rows), 1.0, 1.0),
        nodata=nodata,
    ) as dst:
        dst.write(np.asarray(values, dtype="float64"), 1)
    return path


def _half_nodata_pair(tmp_path, nodata, tag):
    """Two 16×16 aligned rasters: top half real, bottom half nodata.

    arr1 top = base, arr2 top = base+1 → truth diff = 1 over the 128 valid
    pixels; bottom halves carry the declared sentinel in both files. The base
    is chosen to differ from the sentinel (nodata=0 case must not collide).
    """
    n = 16
    base = 100.0 if nodata == 0 else 0.0
    a1 = np.full((n, n), base)
    a2 = np.full((n, n), base + 1.0)
    a1[n // 2:] = nodata
    a2[n // 2:] = nodata
    p1 = _write_raster_nodata(str(tmp_path / f"{tag}_t1.tif"), a1, nodata)
    p2 = _write_raster_nodata(str(tmp_path / f"{tag}_t2.tif"), a2, nodata)
    return p1, p2, a1, a2


def _brute_force_diff_truth(a1, a2, nodata):
    """Reference masked diff stats over pixels valid in BOTH rasters."""
    valid = (a1 != nodata) & (a2 != nodata) & ~np.isnan(a1 - a2)
    diff = a2 - a1
    return diff[valid], valid


@pytest.mark.parametrize("nodata", [-9999.0, 0.0])
def test_raster_difference_streamed_masks_declared_nodata(tmp_path, nodata):
    """Issue #523: with half of each raster declared nodata, the difference
    statistics must equal the brute-force truth over the valid half only —
    not the polluted mean 0.5 / min 0.0 the NaN-only mask produced (two-nodata
    pixels counted as diff=0)."""
    p1, p2, a1, a2 = _half_nodata_pair(tmp_path, nodata, "s")
    engine = TemporalRasterEngine()
    res = engine.raster_difference(p1, p2)

    valid_diff, valid = _brute_force_diff_truth(a1, a2, nodata)
    assert res["pixel_count"] == a1.size  # total pixels (documented semantics)
    assert res["mean_difference"] == pytest.approx(float(np.mean(valid_diff)), abs=1e-12)
    assert res["min_difference"] == pytest.approx(float(np.min(valid_diff)), abs=1e-12)
    assert res["max_difference"] == pytest.approx(float(np.max(valid_diff)), abs=1e-12)
    assert res["std_difference"] == pytest.approx(float(np.std(valid_diff)), abs=1e-12)


def test_raster_difference_in_memory_honors_nodata_key():
    """Issue #523: the in-memory dict contract accepts a per-side ``nodata``
    key; sentinel pixels are masked the same way as the streamed branch."""
    n = 16
    nodata = -9999.0
    a1 = np.zeros((n, n))
    a2 = np.ones((n, n))
    a1[n // 2:] = nodata
    a2[n // 2:] = nodata

    engine = TemporalRasterEngine()
    res = engine.raster_difference(
        {"data": a1.tolist(), "nodata": nodata},
        {"data": a2.tolist(), "nodata": nodata},
    )

    valid_diff, _ = _brute_force_diff_truth(a1, a2, nodata)
    assert res["pixel_count"] == a1.size
    assert res["mean_difference"] == pytest.approx(float(np.mean(valid_diff)), abs=1e-12)
    assert res["min_difference"] == pytest.approx(float(np.min(valid_diff)), abs=1e-12)
    assert res["max_difference"] == pytest.approx(float(np.max(valid_diff)), abs=1e-12)
    assert res["std_difference"] == pytest.approx(float(np.std(valid_diff)), abs=1e-12)


def test_raster_difference_single_side_nodata_no_garbage(tmp_path):
    """Issue #523: a pixel whose SECOND raster is nodata (arr2=-9999, arr1=1)
    previously contributed -9999 - 1 = -10000 to min; the nodata mask must
    keep min/max within the valid region's range."""
    n = 16
    nodata = -9999.0
    a1 = np.zeros((n, n))
    a2 = np.zeros((n, n))
    a1[n // 2:] = 1.0          # t1 bottom = real value 1
    a2[n // 2:] = nodata       # t2 bottom = nodata → those pixels are invalid
    p1 = _write_raster_nodata(str(tmp_path / "ss_t1.tif"), a1, nodata)
    p2 = _write_raster_nodata(str(tmp_path / "ss_t2.tif"), a2, nodata)

    engine = TemporalRasterEngine()
    res = engine.raster_difference(p1, p2)

    valid_diff, _ = _brute_force_diff_truth(a1, a2, nodata)
    # top half: diff 0 everywhere → truth min == max == 0
    assert valid_diff.size == n * n // 2
    assert res["min_difference"] == pytest.approx(0.0, abs=1e-12)
    assert res["max_difference"] == pytest.approx(0.0, abs=1e-12)
    assert res["mean_difference"] == pytest.approx(0.0, abs=1e-12)


def test_raster_difference_streamed_in_memory_agree_with_nodata(tmp_path):
    """Issue #523: the streamed (GeoTIFF) and in-memory (dict) branches must
    agree on sentinel-masked statistics for the same logical arrays."""
    p1, p2, a1, a2 = _half_nodata_pair(tmp_path, -9999.0, "par")
    engine = TemporalRasterEngine()

    streamed = engine.raster_difference(p1, p2)
    in_memory = engine.raster_difference(
        {"data": a1.tolist(), "nodata": -9999.0},
        {"data": a2.tolist(), "nodata": -9999.0},
    )

    for key in ("mean_difference", "min_difference", "max_difference", "std_difference"):
        assert streamed[key] == pytest.approx(in_memory[key], abs=1e-12), key
    assert streamed["pixel_count"] == in_memory["pixel_count"]


def test_temporal_raster_statistics_in_memory_masks_nodata():
    """Issue #523 sibling: temporal_raster_statistics' in-memory fallback must
    exclude declared nodata from the stats (was nan-only)."""
    engine = TemporalRasterEngine()
    data = [[1.0, 1.0], [-9999.0, -9999.0]]
    res = engine.temporal_raster_statistics([
        {"timestamp": "2026-01-01T00:00:00Z", "data": data, "nodata": -9999.0},
    ])
    stats = res["series_statistics"][0]["statistics"]
    assert stats["mean"] == pytest.approx(1.0, abs=1e-12)
    assert stats["min"] == pytest.approx(1.0, abs=1e-12)
    assert stats["max"] == pytest.approx(1.0, abs=1e-12)
    assert stats["std"] == pytest.approx(0.0, abs=1e-12)

    # all-invalid array → empty statistics (skipped downstream, not zeros)
    res_all = engine.temporal_raster_statistics([
        {"timestamp": "2026-01-01T00:00:00Z", "data": [[-9999.0, -9999.0]], "nodata": -9999.0},
    ])
    assert res_all["series_statistics"][0]["statistics"] == {}
