"""Hardening tests for raster_calculator (Slice 3).

Covers:
- integer overflow (R-F01): uint16 * uint16 with large values must NOT wrap to
  negative int32 garbage — the result must be the correct positive product.
- NaN nodata mask (R-F03): a raster whose nodata is NaN must exclude those
  pixels (``arr != NaN`` is all-True, so the old comparison treated NaN as
  valid and let ``where()``-style expressions turn it into a valid 0).
- unit behaviour of the new ``_nodata_valid_mask`` / ``_compute_dtype`` helpers.
"""
import os

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.geo_analysis.raster_math import (
    _compute_dtype,
    _nodata_valid_mask,
    raster_calculator,
)


def _write_raster(path, data, nodata=None, crs="EPSG:4326"):
    h, w = data.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1, dtype=data.dtype,
        crs=crs, transform=from_origin(0, h, 0.0001, 0.0001), nodata=nodata,
        tiled=True, blockxsize=32, blockysize=32,
    ) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture()
def _data_dir(tmp_path):
    d = tmp_path / "raster_harden"
    d.mkdir()
    return str(d)


def test_calculator_uint16_multiply_does_not_overflow(_data_dir):
    """uint16 * uint16 with values > sqrt(2^31) previously wrapped to negative
    int32 (60000*60000 -> -694967296) written as valid data (R-F01)."""
    a = np.full((64, 64), 60000, dtype=np.uint16)
    b = np.full((64, 64), 60000, dtype=np.uint16)
    pa = _write_raster(os.path.join(_data_dir, "a.tif"), a)
    pb = _write_raster(os.path.join(_data_dir, "b.tif"), b)

    stats = raster_calculator(pa, pb, expression="A * B")
    assert stats["pixel_count"] > 0
    # Correct product is 3.6e9; the bug produced -694967296.
    assert stats["min"] == pytest.approx(3_600_000_000.0)
    assert stats["max"] == pytest.approx(3_600_000_000.0)
    assert stats["mean"] == pytest.approx(3_600_000_000.0)

    with rasterio.open(stats["output_path"]) as out:
        arr = out.read(1)
    assert arr.min() == pytest.approx(3_600_000_000.0)
    assert (arr > 0).all()  # no wrapped negatives


def test_calculator_nan_nodata_is_masked(_data_dir):
    """A float raster with NaN nodata: NaN pixels must become output nodata and
    be excluded from stats, not silently pass through as valid 0 (R-F03)."""
    a = np.array([[10.0, float("nan")], [float("nan"), 20.0]], dtype=np.float32)
    pa = _write_raster(os.path.join(_data_dir, "nan.tif"), a, nodata=float("nan"))

    # constant=1.0, expression A*B  -> valid pixels = A, NaN pixels = nodata.
    stats = raster_calculator(pa, raster_b=None, expression="A * B", constant=1.0)
    assert stats["pixel_count"] == 2  # the two non-NaN pixels only
    assert stats["min"] == pytest.approx(10.0)
    assert stats["max"] == pytest.approx(20.0)

    with rasterio.open(stats["output_path"]) as out:
        arr = out.read(1)
    out_nodata = out.nodata
    # NaN positions became nodata (out_nodata is NaN here); valid positions
    # kept their value.
    assert arr[0, 0] == pytest.approx(10.0)
    assert arr[1, 1] == pytest.approx(20.0)
    if isinstance(out_nodata, float) and np.isnan(out_nodata):
        assert np.isnan(arr[0, 1])
        assert np.isnan(arr[1, 0])
    else:
        assert arr[0, 1] == out_nodata
        assert arr[1, 0] == out_nodata


# --------------------------------------------------------------------------- #
# Helper unit tests
# --------------------------------------------------------------------------- #
def test_nodata_valid_mask_none_means_all_valid():
    arr = np.array([1.0, 2.0, 3.0])
    assert _nodata_valid_mask(arr, None).all()


def test_nodata_valid_mask_scalar_nodata():
    arr = np.array([1.0, -9999.0, 3.0])
    mask = _nodata_valid_mask(arr, -9999.0)
    assert mask.tolist() == [True, False, True]


def test_nodata_valid_mask_nan_nodata():
    arr = np.array([1.0, float("nan"), 3.0])
    mask = _nodata_valid_mask(arr, float("nan"))
    assert mask.tolist() == [True, False, True]


def test_compute_dtype_promotes_integers_to_float64():
    for dt in (np.uint8, np.uint16, np.int16, np.int32):
        arr = np.array([1, 2, 3], dtype=dt)
        out = _compute_dtype(arr)
        assert out.dtype == np.float64, dt
        # values preserved
        assert out.tolist() == [1.0, 2.0, 3.0]


def test_compute_dtype_preserves_float():
    arr = np.array([1.0, 2.0], dtype=np.float32)
    assert _compute_dtype(arr).dtype == np.float32


def test_nodata_valid_mask_excludes_undeclared_nan():
    """A float raster whose declared nodata is None / a scalar but which still
    contains NaN must exclude those NaN pixels (review B silent-correctness)."""
    arr = np.array([1.0, float("nan"), 3.0])
    # nodata=None: NaN must still be masked out
    assert _nodata_valid_mask(arr, None).tolist() == [True, False, True]
    # nodata=-9999 scalar: NaN is not the declared nodata but still invalid
    assert _nodata_valid_mask(arr, -9999.0).tolist() == [True, False, True]


def test_calculator_undeclared_nan_becomes_nodata(_data_dir):
    """An undeclared NaN pixel (nodata=None) must not pass through a where()
    expression as a valid 0; it must become output nodata (review B)."""
    a = np.array([[10.0, float("nan")], [3.0, 4.0]], dtype=np.float32)
    pa = _write_raster(os.path.join(_data_dir, "undeclared.tif"), a)  # nodata=None
    stats = raster_calculator(pa, raster_b=None, expression="A * B", constant=1.0)
    # Only the 3 finite pixels count; the NaN pixel is excluded.
    assert stats["pixel_count"] == 3
    with rasterio.open(stats["output_path"]) as out:
        arr = out.read(1)
    # The NaN position is the output nodata (NaN by default since #2133),
    # not a computed value.
    assert np.isnan(arr[0, 1])
    assert np.isnan(out.nodata)


def test_calculator_unaligned_nodata_footprint(_data_dir):
    """Unaligned B without nodata tag: outside B's footprint must be nodata,
    not A+0 (#931)."""
    import tempfile
    import os as _os
    from rasterio.transform import from_bounds

    with tempfile.TemporaryDirectory() as tmpdir:
        pa = _os.path.join(tmpdir, "a.tif")
        pb = _os.path.join(tmpdir, "b.tif")
        data_a = np.full((10, 10), 10.0, dtype=np.float32)
        data_b = np.full((5, 5), 5.0, dtype=np.float32)
        tr_a = from_bounds(0, 0, 100, 100, 10, 10)
        tr_b = from_bounds(0, 50, 50, 100, 5, 5)
        with rasterio.open(pa, "w", driver="GTiff", height=10, width=10, count=1, dtype=np.float32, crs="EPSG:3857", transform=tr_a, nodata=-9999.0) as dst:
            dst.write(data_a, 1)
        with rasterio.open(pb, "w", driver="GTiff", height=5, width=5, count=1, dtype=np.float32, crs="EPSG:3857", transform=tr_b) as dst:
            dst.write(data_b, 1)
        res = raster_calculator(raster_a=pa, raster_b=pb, expression="A + B")
        with rasterio.open(res["output_path"]) as out:
            arr = out.read(1)
            assert arr[0, 0] == 15.0
            assert arr[9, 9] == out.nodata


# ── #2132 / #2133 回归 ───────────────────────────────────────────────

def test_calculator_output_names_unique_per_expression(_data_dir):
    """#2132: 同一输入不同 expression 不得覆盖彼此产物。"""
    a = np.arange(16, dtype=np.float32).reshape(4, 4) + 1.0
    pa = _write_raster(os.path.join(_data_dir, "uniq_calc.tif"), a)
    r1 = raster_calculator(pa, expression="A * 2", constant=0)
    r2 = raster_calculator(pa, expression="A + 100", constant=0)
    assert r1["output_path"] != r2["output_path"]
    with rasterio.open(r1["output_path"]) as out:
        np.testing.assert_array_equal(out.read(1), a * 2)
    with rasterio.open(r2["output_path"]) as out:
        np.testing.assert_array_equal(out.read(1), a + 100)


def test_reclassify_output_names_unique_per_scheme(_data_dir):
    """#2132: 同一输入不同 scheme 不得覆盖彼此产物。"""
    from app.lib.geo_analysis.raster_math import reclassify
    a = np.array([[1, 2], [3, 4]], dtype=np.float32)
    pa = _write_raster(os.path.join(_data_dir, "uniq_recl.tif"), a)
    s1 = [{"min": 1, "max": 2, "value": 10}, {"min": 3, "max": 4, "value": 20}]
    s2 = [{"min": 1, "max": 4, "value": 99}]
    r1 = reclassify(pa, s1)
    r2 = reclassify(pa, s2)
    assert r1["output_path"] != r2["output_path"]
    with rasterio.open(r1["output_path"]) as out:
        assert out.read(1)[0, 0] == 10
    with rasterio.open(r2["output_path"]) as out:
        assert out.read(1)[0, 0] == 99


def test_resample_output_names_unique_per_resolution(_data_dir):
    """#2132: 同一输入不同分辨率不得覆盖彼此产物。"""
    a = np.arange(64, dtype=np.float32).reshape(8, 8)
    pa = _write_raster(os.path.join(_data_dir, "uniq_resamp.tif"), a)
    from app.lib.geo_analysis.raster_math import resample_raster
    r1 = resample_raster(pa, target_resolution=2.0)
    r2 = resample_raster(pa, target_resolution=4.0)
    assert r1["output_path"] != r2["output_path"]
    assert r1["new_shape"] != r2["new_shape"]


def test_calculator_legal_zero_results_survive(_data_dir):
    """#2133: 无 nodata 源默认 out_nodata=NaN——A−B 相等的合法零值不被吞。"""
    a = np.array([[5.0, 3.0], [3.0, 5.0]], dtype=np.float32)
    pa = _write_raster(os.path.join(_data_dir, "zeros.tif"), a)  # nodata=None
    stats = raster_calculator(pa, expression="A - B", constant=3.0)
    with rasterio.open(stats["output_path"]) as out:
        arr = out.read(1)
        assert out.nodata is not None and np.isnan(out.nodata)
    # (0,1) 与 (1,0) 的合法零值结果是有效像元，不是 nodata
    assert arr[0, 1] == 0.0 and arr[1, 0] == 0.0
    assert stats["pixel_count"] == 4


def test_reclassify_zero_class_survives_and_sentinel_disclosed(_data_dir):
    """#2133: value=0 类别在无 nodata 源上不被当 nodata；哨兵选择进披露。"""
    from app.lib.geo_analysis.raster_math import reclassify
    a = np.array([[1, 2], [3, 99]], dtype=np.float32)
    pa = _write_raster(os.path.join(_data_dir, "recl_zero.tif"), a)
    scheme = [{"min": 1, "max": 1, "value": 0}, {"min": 3, "max": 3, "value": 1}]
    result = reclassify(pa, scheme)
    with rasterio.open(result["output_path"]) as out:
        arr = out.read(1)
        nd = out.nodata
    # (0,0) 判进 value=0 类，保持有效 0（修复前：默认 nodata=0 会吞掉它）
    assert arr[0, 0] == 0
    # 未匹配像元 (1,1) 写成哨兵而不是 0
    assert arr[1, 1] == nd
    assert nd != 0
    assert "nodata_disclosure" in result
