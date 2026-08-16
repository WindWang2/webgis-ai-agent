"""Issue #537 regression tests — NDVI nodata validity + output header contract.

``NatureResourceAnalyzer.calculate_ndvi`` previously counted zero-filled
``denom==0`` pixels as valid NDVI 0.0 (halving the reported mean) and wrote
raw NaN cells under a header declaring nodata=-9999. Both must now be fixed:

- stats are computed over the NaN-masked truth only;
- every output cell equals the declared nodata value exactly where the input
  was invalid (no NaN under a -9999 header, no 0.0 counted valid).
"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.nature_resource_analyzer import NatureResourceAnalyzer


def _write_input(path, red, nir, nodata=None):
    """Write a 2-band GeoTIFF (band 1 = red, band 2 = nir)."""
    rows, cols = np.asarray(red).shape
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=2,
        dtype="float64", crs="EPSG:4326",
        transform=from_origin(116.0, 40.0, 1.0, 1.0),
        nodata=nodata,
    ) as dst:
        dst.write(np.asarray(red, dtype="float64"), 1)
        dst.write(np.asarray(nir, dtype="float64"), 2)
    return path


@pytest.fixture
def allow_tmp_paths(monkeypatch):
    """calculate_ndvi validates input/output paths against ./data — keep the
    integration test fully under pytest's tmp dir instead of the repo data/."""
    monkeypatch.setattr(
        "app.utils.path.validate_data_path",
        lambda p, data_dir="./data": p,
    )


def _half_zero_bands():
    """16×16 bands: top half red=200/nir=800 (NDVI 0.6), bottom half 0/0
    (undeclared zero nodata, S2-L2A style)."""
    n = 16
    red = np.full((n, n), 200.0)
    nir = np.full((n, n), 800.0)
    red[n // 2:] = 0.0
    nir[n // 2:] = 0.0
    return red, nir, n


@pytest.mark.parametrize("declared_nodata", [None, -9999.0])
def test_calculate_ndvi_stats_and_bytes_match_nan_truth(tmp_path, allow_tmp_paths, declared_nodata):
    """Issue #537: stats equal the NaN-masked truth (mean 0.6, NOT the halved
    0.3), and re-opening the output shows every invalid cell equals the
    declared nodata -9999 exactly (no NaN under a -9999 header)."""
    red, nir, n = _half_zero_bands()
    if declared_nodata is not None:
        red[n // 2:] = declared_nodata
        nir[n // 2:] = declared_nodata
    tif = _write_input(str(tmp_path / "input.tif"), red, nir, nodata=declared_nodata)

    res = NatureResourceAnalyzer.calculate_ndvi(
        tif, red_band=1, nir_band=2, output_dir=str(tmp_path / "out"),
    )
    assert res["success"] is True, res.get("error")

    # Numeric truth over the 128 valid pixels: NDVI = (800-200)/(800+200) = 0.6.
    assert res["stats"]["mean"] == pytest.approx(0.6, abs=1e-9)
    assert res["stats"]["min"] == pytest.approx(0.6, abs=1e-9)
    assert res["stats"]["max"] == pytest.approx(0.6, abs=1e-9)

    with rasterio.open(res["result_path"]) as out:
        assert out.nodata == -9999  # header declares -9999
        data = out.read(1)
        assert np.all(np.isclose(data[: n // 2], 0.6))          # valid half kept
        assert np.all(data[n // 2:] == -9999.0)                  # invalid half == declared nodata
        assert not np.isnan(data).any()                          # no NaN under -9999 header


def test_calculate_ndvi_zero_fill_no_longer_counts_as_valid(tmp_path, allow_tmp_paths):
    """Mixed image where the zero block sits in the MIDDLE: stats must ignore
    it entirely (valid_pixel count 128, mean 0.6), not average it in."""
    red, nir, _ = _half_zero_bands()
    tif = _write_input(str(tmp_path / "input.tif"), red, nir)

    res = NatureResourceAnalyzer.calculate_ndvi(
        tif, red_band=1, nir_band=2, output_dir=str(tmp_path / "out"),
    )
    assert res["success"] is True, res.get("error")
    assert res["stats"]["mean"] == pytest.approx(0.6, abs=1e-9)