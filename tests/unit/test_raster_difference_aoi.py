"""Regression tests for the temporal raster fixes (#396).

1. ``raster_difference`` now derives its window from the AOI ∩ raster bounds
   (and validates CRS/transform alignment) instead of silently reading a fixed
   top-left 1024×1024 crop of src1.
2. ``execute_raster_analysis`` computes zonal statistics once and reuses them
   for the trend step instead of re-opening/re-statisticizing every raster.

All tests use small synthetic GeoTIFFs written to a tmp dir — no real data.
"""

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
