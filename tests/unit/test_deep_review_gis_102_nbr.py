"""GIS-102 regression: local NBR must use SWIR2/B12, never SWIR1/B11.

Deep-review finding: INDEX_BAND_ROLES mapped nbr to ("nir", "swir1") while
band_math.INDEX_FORMULAS expects swir12, and the drift guard compared only
lengths — B11 was silently used as B12. Roles are now name-aligned and the
guard compares names.
"""
import os
import uuid

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.geo_analysis.raster_windowed import (
    INDEX_BAND_ROLES,
    windowed_band_index,
)
from app.services.rs.band_math import INDEX_FORMULAS, compute_index_array


TD = "data/tmp_v3_tests"


def _td():
    os.makedirs(TD, exist_ok=True)
    return TD


def _write_multiband(name, bands, crs="EPSG:4326"):
    path = os.path.join(_td(), name)
    arr = np.stack(bands)
    h, w = arr.shape[1:]
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=len(bands),
        dtype="float32", crs=crs, transform=from_origin(0, h, 1, 1),
    ) as dst:
        dst.write(arr)
    return path


def test_index_roles_match_formula_names_exactly():
    """The drift guard must compare NAMES, not just lengths (GIS-102)."""
    for idx, roles in INDEX_BAND_ROLES.items():
        assert tuple(INDEX_FORMULAS[idx][0]) == tuple(roles), idx
    assert INDEX_BAND_ROLES["nbr"] == ("nir", "swir12")
    assert INDEX_BAND_ROLES["ndwi_gao"] == ("nir", "swir11")


def test_windowed_nbr_with_explicit_b12_matches_online_path():
    nir = np.array([[0.50, 0.40], [0.30, 0.60]], dtype="float32")
    swir2 = np.array([[0.10, 0.05], [0.20, 0.60]], dtype="float32")
    p = _write_multiband(f"nbr102_{uuid.uuid4().hex[:6]}.tif", [nir, swir2])
    try:
        res = windowed_band_index(p, "nbr", band_map={"nir": 1, "swir12": 2})
        with rasterio.open(res["output_path"]) as out:
            arr = out.read(1)
        expected = compute_index_array("nbr", nir=nir.astype(float), swir12=swir2.astype(float))
        valid = np.isfinite(expected)
        np.testing.assert_allclose(arr[valid], expected[valid], rtol=1e-6, atol=1e-6)
        assert arr[~valid].tolist() == pytest.approx([-9999.0] * int((~valid).sum()))
        assert res["band_map"] == {"nir": 1, "swir12": 2}
    finally:
        os.path.exists(p) and os.remove(p)
        os.path.exists(p.replace(".tif", "_nbr.tif")) and os.remove(p.replace(".tif", "_nbr.tif"))


def test_windowed_nbr_rejects_swir1_role():
    """The old role name must not satisfy the B12 contract."""
    nir = np.array([[0.5, 0.4]], dtype="float32")
    swir1 = np.array([[0.1, 0.05]], dtype="float32")
    p = _write_multiband(f"nbr102b_{uuid.uuid4().hex[:6]}.tif", [nir, swir1])
    try:
        with pytest.raises(ValueError, match="swir12"):
            windowed_band_index(p, "nbr", band_map={"nir": 1, "swir1": 2})
    finally:
        os.remove(p)


def test_calculate_index_b11_is_not_accepted_as_nbr():
    """strict band semantics: nbr needs an explicit B12 (swir2_band)."""
    from app.services.nature_resource_analyzer import NatureResourceAnalyzer

    bands = [np.full((2, 2), float(i + 1), dtype="float32") for i in range(12)]
    fname = f"nbr102c_{uuid.uuid4().hex[:6]}.tif"
    p = _write_multiband(fname, bands)
    out_dir = "tmp_v3_tests/idx_out"
    os.makedirs(os.path.join("data", out_dir), exist_ok=True)
    try:
        # Explicit B11 as the SWIR band must NOT satisfy NBR: the preset's B12
        # is still a positional guess and strict mode rejects it.
        rejected = NatureResourceAnalyzer.calculate_index(
            f"tmp_v3_tests/{fname}", "nbr",
            nir_band=8, swir_band=11, output_dir=out_dir,
        )
        assert rejected["success"] is False
        assert rejected["error_type"] == "band_semantics_guess_rejected"
        assert "swir12" in rejected["guessed_roles"]

        # The explicit B12 parameter is honored (role names, not positions).
        ok = NatureResourceAnalyzer.calculate_index(
            f"tmp_v3_tests/{fname}", "nbr",
            nir_band=8, swir2_band=12, output_dir=out_dir,
        )
        assert ok["success"] is True
        assert ok["band_map"] == {"nir": 8, "swir12": 12}
    finally:
        os.remove(p)
        if os.path.exists(os.path.join("data", out_dir)):
            for f in os.listdir(os.path.join("data", out_dir)):
                os.remove(os.path.join("data", out_dir, f))
