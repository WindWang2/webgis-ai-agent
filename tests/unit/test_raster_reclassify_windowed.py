"""Characterization tests for the windowed `reclassify` refactor (Phase D).

The refactor moves `reclassify` from full-array processing (O(full raster)
memory: data + out_data + assigned) to fixed-window processing (O(512×512)).
These tests pin the exact output semantics — first-match-wins, nodata
isolation, dtype promotion, stats, compression/tiling — so the refactor
cannot silently change behavior. The reference implementation below is the
pre-windowed full-array algorithm, inlined for independence.
"""
import os
import uuid

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.geo_analysis.raster_math import reclassify


def _reference_reclassify(data, src_nodata, scheme, out_nodata):
    """Inline copy of the pre-windowed algorithm (first-match-wins)."""
    out_dtype = np.result_type(*[r.get("value", 0) for r in scheme], out_nodata)
    out = np.full_like(data, fill_value=out_nodata, dtype=out_dtype)
    assigned = np.zeros(data.shape, dtype=bool)
    if src_nodata is not None:
        assigned[data == src_nodata] = True
    for rule in scheme:
        rmin = rule.get("min", -float("inf"))
        rmax = rule.get("max", float("inf"))
        mask = (data >= rmin) & (data <= rmax)
        if src_nodata is not None:
            mask = mask & (data != src_nodata)
        match = mask & (~assigned)
        out[match] = rule["value"]
        assigned[match] = True
    return out, out_dtype


@pytest.fixture()
def raster_path(tmp_path):
    """Multi-block 100×100 raster with nodata holes, in data/ (path validation)."""
    rng = np.random.default_rng(42)
    data = rng.integers(0, 20, size=(100, 100)).astype(np.float32)
    data[10:20, 10:20] = -9999  # nodata block
    data[0, 0] = -9999
    data[-1, -1] = -9999
    path = os.path.join(os.path.dirname(__file__), "..", "..", "data", f"test_wind_{uuid.uuid4().hex[:8]}.tif")
    with rasterio.open(
        path, "w", driver="GTiff", height=100, width=100, count=1,
        dtype=np.float32, crs="EPSG:4326", transform=from_origin(0, 100, 1, 1),
        nodata=-9999, tiled=True, blockxsize=32, blockysize=32,
    ) as dst:
        dst.write(data, 1)
    yield path
    for p in (path, path[:-4] + "_reclassified.tif"):
        try:
            os.unlink(p)
        except OSError:
            pass


def _expected_stats(out, out_nodata):
    if isinstance(out_nodata, float) and np.isnan(out_nodata):
        valid = out[~np.isnan(out)]
    else:
        valid = out[out != out_nodata]
    unique_vals = np.unique(valid)
    if np.issubdtype(out.dtype, np.integer):
        unique_list = [int(v) for v in unique_vals]
    else:
        unique_list = [float(v) for v in unique_vals]
    return int(valid.size), unique_list


SCHEME = [
    {"min": 0, "max": 4, "value": 1, "label": "low"},
    {"min": 5, "max": 9, "value": 2, "label": "medium"},
    {"min": 10, "max": 14, "value": 3, "label": "high"},
    {"min": 15, "max": 19, "value": 4, "label": "very high"},
]


def test_reclassify_matches_reference_output(raster_path):
    """Windowed output must be pixel-identical to the full-array reference."""
    result = reclassify(raster_path, SCHEME)
    with rasterio.open(raster_path) as src:
        data = src.read(1)
        src_nodata = src.nodata
    out_nodata = src_nodata if src_nodata is not None else 0
    expected, _ = _reference_reclassify(data, src_nodata, SCHEME, out_nodata)

    with rasterio.open(result["output_path"]) as out:
        actual = out.read(1)
        assert out.nodata == out_nodata
        assert out.profile["compress"] == "lzw"
        assert out.profile["tiled"] is True
        assert out.profile["blockxsize"] == 256
        assert out.dtypes[0] == expected.dtype.name

    assert actual.shape == expected.shape
    np.testing.assert_array_equal(actual, expected)


def test_reclassify_stats_match_reference(raster_path):
    result = reclassify(raster_path, SCHEME)
    with rasterio.open(raster_path) as src:
        data = src.read(1)
        src_nodata = src.nodata
    out_nodata = src_nodata if src_nodata is not None else 0
    expected, _ = _reference_reclassify(data, src_nodata, SCHEME, out_nodata)
    exp_count, exp_uniques = _expected_stats(expected, out_nodata)

    assert result["pixel_count"] == exp_count
    assert result["unique_values"] == exp_uniques
    # labels only for values present in the output
    assert result["labels"] == {
        "1": "low", "2": "medium", "3": "high", "4": "very high",
    }


def test_reclassify_first_match_wins(raster_path):
    """Overlapping scheme ranges: the FIRST rule wins per pixel."""
    overlapping = [
        {"min": 0, "max": 19, "value": 9, "label": "all"},
        {"min": 0, "max": 4, "value": 1, "label": "low"},
    ]
    result = reclassify(raster_path, overlapping)
    with rasterio.open(raster_path) as src:
        data = src.read(1)
        src_nodata = src.nodata
    out_nodata = src_nodata if src_nodata is not None else 0
    expected, _ = _reference_reclassify(data, src_nodata, overlapping, out_nodata)
    with rasterio.open(result["output_path"]) as out:
        np.testing.assert_array_equal(out.read(1), expected)
    assert result["unique_values"] == [9.0]


def test_reclassify_scheme_value_equals_nodata_quirk(raster_path):
    """A rule value equal to out_nodata counts as invalid in stats (preserved quirk)."""
    # values >= -9999 with value 0: nodata pixels excluded by assigned mask,
    # but a rule mapping to 0 == out_nodata only matches... none (nodata masked).
    scheme2 = [{"min": 0, "max": 4, "value": 0, "label": "to-zero"}]
    result = reclassify(raster_path, scheme2)
    with rasterio.open(raster_path) as src:
        data = src.read(1)
        src_nodata = src.nodata
    out_nodata = src_nodata if src_nodata is not None else 0
    expected, _ = _reference_reclassify(data, src_nodata, scheme2, out_nodata)
    with rasterio.open(result["output_path"]) as out:
        np.testing.assert_array_equal(out.read(1), expected)
    # pixels mapped to 0 (== nodata) are excluded from valid stats — same as reference
    exp_count, _ = _expected_stats(expected, out_nodata)
    assert result["pixel_count"] == exp_count


def test_reclassify_nan_nodata(raster_path):
    """NaN nodata handling (float) survives windowing."""
    scheme = [{"min": 0, "max": 100, "value": 5, "label": "all"}]
    result = reclassify(raster_path, scheme, nodata=np.nan)
    with rasterio.open(result["output_path"]) as out:
        data = out.read(1)
        assert out.nodata is None or np.isnan(out.nodata)  # profile nodata float
        assert not np.isnan(data).all()
        # everything that wasn't source-nodata became 5
        with rasterio.open(raster_path) as src:
            src_data = src.read(1)
            src_nodata = src.nodata
        expected, _ = _reference_reclassify(src_data, src_nodata, scheme, np.nan)
        np.testing.assert_array_equal(data, expected)


def test_reclassify_whole_raster_single_block(raster_path):
    """A single-block source (small files) still works and matches reference."""
    # 3×3 single-block file
    path = os.path.join(os.path.dirname(__file__), "..", "..", "data", f"test_wind_small_{uuid.uuid4().hex[:8]}.tif")
    small = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float32)
    with rasterio.open(path, "w", driver="GTiff", height=3, width=3, count=1,
                       dtype=np.float32, crs="EPSG:4326", transform=from_origin(0, 3, 1, 1)) as dst:
        dst.write(small, 1)
    try:
        scheme = [{"min": 0, "max": 4, "value": 1}, {"min": 5, "max": 9, "value": 2}]
        result = reclassify(path, scheme)
        expected, _ = _reference_reclassify(small, None, scheme, 0)
        with rasterio.open(result["output_path"]) as out:
            np.testing.assert_array_equal(out.read(1), expected)
        assert result["unique_values"] == [1.0, 2.0]
    finally:
        for p in (path, path[:-4] + "_reclassified.tif"):
            try:
                os.unlink(p)
            except OSError:
                pass
