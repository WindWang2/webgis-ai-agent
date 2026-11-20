"""Characterization tests for windowed `raster_calculator` (aligned path).

The aligned (same CRS/transform/shape) and constant paths move to fixed
512×512 window processing (memory O(window) instead of 6-8 full arrays);
the unaligned B-reproject path stays full-array (the tool docs tell users
to resample first). These tests pin pixel-exact output, nodata semantics,
stats, and dtype promotion against an inlined full-array reference.
"""
import os
import uuid

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.geo_analysis.raster_math import raster_calculator


def _reference_calculator(data_a, data_b, out_nodata, nodata_a, nodata_b, expression):
    """Inline copy of the pre-windowed full-array algorithm."""
    import numexpr as ne

    if nodata_a is not None:
        mask_a = data_a != nodata_a
    else:
        mask_a = np.ones(data_a.shape, dtype=bool)
    if nodata_b is not None:
        mask_b = data_b != nodata_b
    else:
        mask_b = np.ones(data_a.shape, dtype=bool)
    mask = mask_a & mask_b
    valid_a = np.where(mask, data_a, 0)
    valid_b = np.where(mask, data_b, 0)
    result = ne.evaluate(expression, local_dict={"A": valid_a, "B": valid_b})
    result = np.where(np.isfinite(result), result, out_nodata)
    result = np.where(mask, result, out_nodata)
    return result


def _write_raster(path, data, nodata=None, transform=None, crs="EPSG:4326", tiled=True):
    h, w = data.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1, dtype=data.dtype,
        crs=crs, transform=transform or from_origin(0, h, 1, 1), nodata=nodata,
        tiled=tiled, blockxsize=32, blockysize=32,
    ) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture()
def aligned_rasters():
    """Two aligned 120×80 rasters (multiple 512-windows not needed; >1 block) with nodata."""
    rng = np.random.default_rng(7)
    a = rng.integers(0, 50, size=(120, 80)).astype(np.float32)
    b = rng.integers(0, 30, size=(120, 80)).astype(np.float32)
    a[5:10, 5:10] = -9999
    b[20:25, 30:35] = -9999
    base = os.path.join(os.path.dirname(__file__), "..", "..", "data")
    pa = _write_raster(os.path.join(base, f"test_calc_a_{uuid.uuid4().hex[:8]}.tif"), a, nodata=-9999)
    pb = _write_raster(os.path.join(base, f"test_calc_b_{uuid.uuid4().hex[:8]}.tif"), b, nodata=-9999)
    yield pa, pb, a, b
    for p in (pa, pb, pa[:-4] + "_calc.tif"):
        try:
            os.unlink(p)
        except OSError:
            pass


def test_calculator_aligned_matches_reference(aligned_rasters):
    pa, pb, a, b = aligned_rasters
    result = raster_calculator(pa, pb, expression="A + B")
    expected = _reference_calculator(a, b, -9999, -9999, -9999, "A + B")
    with rasterio.open(result["output_path"]) as out:
        np.testing.assert_array_equal(out.read(1), expected)
        assert out.profile["compress"] == "lzw"
        assert out.profile["tiled"] is True


def test_calculator_expression_divide(aligned_rasters):
    pa, pb, a, b = aligned_rasters
    result = raster_calculator(pa, pb, expression="(A - B) / (A + B)")
    expected = _reference_calculator(a, b, -9999, -9999, -9999, "(A - B) / (A + B)")
    with rasterio.open(result["output_path"]) as out:
        np.testing.assert_array_equal(out.read(1), expected)
    assert result["pixel_count"] == int((expected != -9999).sum())


def test_calculator_stats_match_reference(aligned_rasters):
    pa, pb, a, b = aligned_rasters
    result = raster_calculator(pa, pb, expression="A * 2 + B")
    expected = _reference_calculator(a, b, -9999, -9999, -9999, "A * 2 + B")
    valid = expected[expected != -9999]
    assert result["min"] == float(valid.min())
    assert result["max"] == float(valid.max())
    assert result["mean"] == pytest.approx(float(valid.mean()))
    assert result["pixel_count"] == int(valid.size)


def test_calculator_constant_path(aligned_rasters):
    pa, _, a, _ = aligned_rasters
    result = raster_calculator(pa, expression="A * 2", constant=0)
    with rasterio.open(pa) as src:
        data_a = src.read(1)
        nodata_a = src.nodata
    expected = _reference_calculator(data_a, np.zeros_like(data_a), nodata_a, nodata_a, None, "A * 2")
    with rasterio.open(result["output_path"]) as out:
        np.testing.assert_array_equal(out.read(1), expected)


def test_calculator_nodata_promotion(aligned_rasters):
    """A no-nodata source gets out_nodata=NaN (#2133); div-by-zero handled."""
    base = os.path.join(os.path.dirname(__file__), "..", "..", "data")
    pa = _write_raster(os.path.join(base, f"test_calc_nn_{uuid.uuid4().hex[:8]}.tif"),
                       np.ones((64, 64), dtype=np.float32))
    pb = _write_raster(os.path.join(base, f"test_calc_nn_b_{uuid.uuid4().hex[:8]}.tif"),
                       np.zeros((64, 64), dtype=np.float32))
    try:
        result = raster_calculator(pa, pb, expression="A / B")
        with rasterio.open(pa) as src:
            a = src.read(1)
        with rasterio.open(pb) as src:
            b = src.read(1)
        expected = _reference_calculator(a, b, np.nan, None, None, "A / B")
        with rasterio.open(result["output_path"]) as out:
            np.testing.assert_array_equal(out.read(1), expected)
            # 输出声明 NaN nodata（#2133：合法零值不被吞）
            assert out.nodata is not None and np.isnan(out.nodata)
        # division by zero → out_nodata (NaN), so nothing is valid
        assert result["pixel_count"] == 0
        assert result["quality_evidence"].get("nodata_disclosure")
    finally:
        for p in (pa, pb):
            try:
                os.unlink(p)
            except OSError:
                pass
        try:
            os.unlink(result["output_path"])
        except OSError:
            pass


def test_calculator_where_expression(aligned_rasters):
    pa, pb, a, b = aligned_rasters
    result = raster_calculator(pa, pb, expression="where(A > 10, A, B)")
    expected = _reference_calculator(a, b, -9999, -9999, -9999, "where(A > 10, A, B)")
    with rasterio.open(result["output_path"]) as out:
        np.testing.assert_array_equal(out.read(1), expected)


def test_calculator_fractional_constant_not_truncated_for_integer_a(tmp_path):
    """#2131: 整型 A + 小数常量——B 常量面不得继承 A 的整型 dtype。"""
    data_a = np.arange(64, dtype=np.uint16).reshape(8, 8) + 100
    pa = str(tmp_path / "u16.tif")
    _write_raster(pa, data_a)
    for expr in ("A * B", "A / B"):
        result = raster_calculator(pa, expression=expr, constant=0.5)
        with rasterio.open(result["output_path"]) as out:
            got = out.read(1)
        a = data_a.astype(np.float64)
        expected = a * 0.5 if expr == "A * B" else a / 0.5
        # 修复前：full_like(uint16, 0.5) → B 全 0 → 乘得全 0 / 除得 inf→nodata
        np.testing.assert_allclose(got, expected, rtol=1e-12)
