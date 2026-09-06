"""Terrain geomorphometry V2 conformance tests (Foundation V2 · A5).

Goldens for openness / geomorphons / Weiss landforms / multi-azimuth
hillshade (app/lib/geo_analysis/terrain.py) and their tool wiring
(app/tools/terrain_analysis.py):

- openness (Yokoyama 2002): flat -> positive and negative exactly 0;
  ridge crest -> high positive openness, negative far below
- geomorphons (Jasiewicz & Stepinski 2013): synthetic peak -> summit,
  pit -> depression, planar ramp -> slope, flat -> flat; class
  distribution sums to the valid cell count
- landform (Weiss 2001): linear ramp centre -> open_slopes(5); synthetic
  ridge crest -> mountain_tops_high_ridges(9)
- hillshade multi-azimuth: single azimuth (315,) equals
  band_math.compute_hillshade bit-for-bit; mean vs min combine; guards
- adversarial: radius caps raise ResourceScaleMismatch BEFORE allocation
  (monkeypatched allocators), all-nodata raises NoValidObservations,
  constant-surface landform raises DegenerateData, determinism double-run
- tools: evidence blocks with backend_selection diagnostics
- registry: terrain-domain algorithm registry + parity validators clean
"""
import math

import numpy as np
import pytest

from app.lib.geo_analysis import terrain as tlib
from app.lib.gis.scientific_errors import (
    DegenerateData,
    NoValidObservations,
    ResourceScaleMismatch,
)

pytestmark = pytest.mark.unit

N = 21


def _ridge(n: int = N, depth: float = 3.0) -> np.ndarray:
    """E-W ridge crest along the centre column: z = 60 - depth*|x - centre|."""
    x = np.arange(n, dtype=float)[None, :]
    return 60.0 - depth * np.abs(x - (n - 1) / 2.0) * np.ones((n, n))


# ── 1. Openness (Yokoyama 2002) ───────────────────────────────────────


def test_openness_flat_zero_and_ridge_crest_high():
    flat = np.zeros((11, 11))
    res, meta = tlib.terrain_openness(flat, 1.0, radius_cells=5)
    assert float(res["positive"][5, 5]) == 0.0
    assert float(res["negative"][5, 5]) == 0.0
    assert meta["method"].startswith("openness")
    assert meta["units"] == "degrees"

    ridge = _ridge()
    res2, _ = tlib.terrain_openness(ridge, 2.0, radius_cells=6)
    pos = float(res2["positive"][10, 10])
    neg = float(res2["negative"][10, 10])
    # The crest looks down the flanks (positive) with nothing above it.
    assert pos > 40.0
    assert pos > neg
    # A valley bottom mirrors the relation.
    valley = 60.0 + _ridge() - 60.0  # inverted ridge
    res3, _ = tlib.terrain_openness(60.0 - _ridge() + 60.0, 2.0, radius_cells=6)
    pos_v = float(res3["positive"][10, 10])
    neg_v = float(res3["negative"][10, 10])
    assert neg_v > pos_v
    del valley


# ── 2. Geomorphons (Jasiewicz & Stepinski 2013) ──────────────────────


def test_geomorphons_peak_pit_ramp_classes_and_distribution():
    r, c = np.mgrid[0:N, 0:N].astype(float)
    peak = -((r - 10) ** 2 + (c - 10) ** 2) * 5.0
    g, meta = tlib.geomorphons(peak, 1.0, lookup_radius_cells=5, flatten=1.0)
    assert int(g["classes"][10, 10]) == 2  # summit
    assert meta["class_codes"]["2"] == "summit"

    g2, _ = tlib.geomorphons(-peak, 1.0, lookup_radius_cells=5, flatten=1.0)
    assert int(g2["classes"][10, 10]) == 10  # depression

    ramp = np.tile(np.arange(N, dtype=float) * 3.0, (N, 1))
    g3, _ = tlib.geomorphons(ramp, 1.0, lookup_radius_cells=5, flatten=1.0)
    assert int(g3["classes"][10, 10]) == 6  # slope (dual 135-225 degree runs)

    g4, meta4 = tlib.geomorphons(np.zeros((N, N)), 1.0,
                                 lookup_radius_cells=5, flatten=1.0)
    assert int(g4["classes"][10, 10]) == 1  # flat
    # Distribution sums to the valid cell count (honest accounting).
    assert sum(meta4["class_distribution"].values()) == meta4["cells_valid"]
    assert meta4["decision_table"].startswith("dn==8 summit")
    # far parameter skips the near field without crashing determinism.
    g5, _ = tlib.geomorphons(ramp, 1.0, lookup_radius_cells=5,
                             flatten=1.0, far=2.0)
    assert int(g5["classes"][10, 10]) == 6
    with pytest.raises(ValueError, match="far"):
        tlib.geomorphons(ramp, 1.0, lookup_radius_cells=5, flatten=1.0, far=5.0)


# ── 3. Weiss 2001 landform classification ─────────────────────────────


def test_landform_ramp_open_slope_and_ridge_class():
    ramp = np.tile(np.arange(N, dtype=float), (N, 1))
    lf, meta = tlib.landform_classification(
        ramp, 1.0, tpi_window_small=3, tpi_window_large=9)
    # Linear surface: TPI == 0 at both scales, middle percentile -> open slope.
    assert int(lf["classes"][10, 10]) == 5
    assert meta["class_codes"]["5"] == "open_slopes"
    assert meta["tpi_window_includes_center"] is True

    # Sharp linear-flank ridge: the crest is a positive TPI anomaly at both
    # scales (a broad parabola would classify as mesa instead — documented).
    x = np.abs(np.arange(N, dtype=float)[None, :] - 10.0)
    ridge = 60.0 - 4.0 * x - 0.05 * (np.mgrid[0:N, 0:N][0] - 10.0) ** 2
    lf2, _ = tlib.landform_classification(
        ridge, 1.0, tpi_window_small=3, tpi_window_large=11)
    assert int(lf2["classes"][10, 10]) == 9  # mountain tops / high ridges
    assert lf2["classes"].max() <= 10 and lf2["classes"].min() >= 0
    # Window guards: large < small rejected, even windows rejected.
    with pytest.raises(ValueError, match="tpi_window_large"):
        tlib.landform_classification(ramp, 1.0, tpi_window_small=9,
                                     tpi_window_large=3)
    with pytest.raises(ValueError, match="window"):
        tlib.landform_classification(ramp, 1.0, tpi_window_small=4)


# ── 4. Multi-azimuth hillshade ────────────────────────────────────────


def _demo_dem() -> np.ndarray:
    r, c = np.mgrid[0:12, 0:12].astype(float)
    return r * 2.0 + c * 3.0 + np.sin(r * c)


def test_hillshade_multiazimuth_single_equals_band_math():
    from app.services.rs.band_math import compute_hillshade

    z = _demo_dem()
    mine, meta = tlib.hillshade_multiazimuth(
        z, 2.0, cell_size_x=1.5, altitude=40.0, azimuths=(315.0,),
        combine="mean")
    reference = compute_hillshade(z, 2.0, azimuth=315, altitude=40,
                                  cell_size_x=1.5)
    # Bit-for-bit equality with the #379 compass-fix truth source.
    assert np.array_equal(mine, reference)
    assert meta["truth_source"].startswith("app/services/rs/band_math.py")
    assert meta["azimuths"] == [315.0]


def test_hillshade_multiazimuth_mean_min_and_validation():
    z = _demo_dem()
    mean2, _ = tlib.hillshade_multiazimuth(
        z, 2.0, altitude=45.0, azimuths=(315.0, 135.0), combine="mean")
    min2, _ = tlib.hillshade_multiazimuth(
        z, 2.0, altitude=45.0, azimuths=(315.0, 135.0), combine="min")
    one_a, _ = tlib.hillshade_multiazimuth(
        z, 2.0, altitude=45.0, azimuths=(315.0,), combine="mean")
    one_b, _ = tlib.hillshade_multiazimuth(
        z, 2.0, altitude=45.0, azimuths=(135.0,), combine="mean")
    manual_mean = (one_a + one_b) / 2.0
    assert np.allclose(mean2, manual_mean, atol=1e-9)
    assert np.all(min2 <= mean2 + 1e-9)
    # Parameter guards.
    with pytest.raises(ValueError, match="altitude"):
        tlib.hillshade_multiazimuth(z, 2.0, altitude=95.0)
    with pytest.raises(ValueError, match="azimuths"):
        tlib.hillshade_multiazimuth(z, 2.0, azimuths=())
    with pytest.raises(ValueError, match="combine"):
        tlib.hillshade_multiazimuth(z, 2.0, combine="max")


# ── 5. Adversarial inputs & guards ───────────────────────────────────


def test_geomorphometry_guards_radius_caps_and_nodata(monkeypatch):
    dem = np.zeros((5, 5))
    constant = np.full((6, 6), 4.0)  # built before the allocator patch
    all_nodata = np.full((5, 5), -9999.0)

    with pytest.raises(NoValidObservations):
        tlib.terrain_openness(all_nodata, 1.0, radius_cells=3, nodata=-9999.0)
    with pytest.raises(NoValidObservations):
        tlib.geomorphons(all_nodata, 1.0, lookup_radius_cells=3, nodata=-9999.0)
    with pytest.raises(NoValidObservations):
        tlib.landform_classification(all_nodata, 1.0, nodata=-9999.0)
    with pytest.raises(DegenerateData):
        tlib.landform_classification(constant, 1.0)

    # Radius caps fire BEFORE allocation (monkeypatched allocators explode).
    def _boom(*args, **kwargs):
        raise AssertionError("allocator reached before scale guard")

    monkeypatch.setattr(tlib, "MAX_HYDRO_CELLS", 8)
    for name in ("zeros", "empty", "full"):
        monkeypatch.setattr(tlib.np, name, _boom)
    with pytest.raises(ResourceScaleMismatch):
        tlib.terrain_openness(dem, 1.0, radius_cells=101)
    with pytest.raises(ResourceScaleMismatch):
        tlib.geomorphons(dem, 1.0, lookup_radius_cells=129)
    with pytest.raises(ResourceScaleMismatch):
        tlib.landform_classification(dem, 1.0)
    monkeypatch.undo()
    # NaN cells propagate to NaN outputs (invalid, not zero).
    z = _ridge()
    z[10, 10] = np.nan
    res, _ = tlib.terrain_openness(z, 2.0, radius_cells=4)
    assert math.isnan(float(res["positive"][10, 10]))
    g, gmeta = tlib.geomorphons(z, 2.0, lookup_radius_cells=4, flatten=1.0)
    assert int(g["classes"][10, 10]) == 0  # 0 = invalid cell
    # All valid cells are classified; the NaN cell is excluded.
    assert sum(gmeta["class_distribution"].values()) == gmeta["cells_valid"]


# ── 6. Determinism ────────────────────────────────────────────────────


def test_geomorphometry_determinism_double_run():
    ridge = _ridge()
    r1, m1 = tlib.terrain_openness(ridge, 2.0, radius_cells=6)
    r2, m2 = tlib.terrain_openness(ridge, 2.0, radius_cells=6)
    assert m1 == m2
    assert np.array_equal(r1["positive"], r2["positive"])
    assert np.array_equal(r1["negative"], r2["negative"])

    ramp = np.tile(np.arange(N, dtype=float) * 3.0, (N, 1))
    g1, gm1 = tlib.geomorphons(ramp, 1.0, lookup_radius_cells=5, flatten=1.0)
    g2, gm2 = tlib.geomorphons(ramp, 1.0, lookup_radius_cells=5, flatten=1.0)
    assert gm1 == gm2 and np.array_equal(g1["classes"], g2["classes"])

    l1, lm1 = tlib.landform_classification(ramp, 1.0)
    l2, lm2 = tlib.landform_classification(ramp, 1.0)
    assert lm1 == lm2 and np.array_equal(l1["classes"], l2["classes"])

    h1, hm1 = tlib.hillshade_multiazimuth(ramp, 1.0)
    h2, hm2 = tlib.hillshade_multiazimuth(ramp, 1.0)
    assert hm1 == hm2 and np.array_equal(h1, h2)


# ── 7. Tool wiring (thin wrappers + evidence) ─────────────────────────


def _write_tif(path, arr, transform, crs=None, nodata=None):
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(
        str(path), "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
        count=1, dtype="float64", crs=crs, transform=Affine(*transform),
        nodata=nodata,
    ) as dst:
        dst.write(arr.astype("float64"), 1)


@pytest.fixture()
def terrain_tools(monkeypatch):
    from app.tools.registry import ToolRegistry
    from app.tools import terrain_analysis as ta

    monkeypatch.setattr(ta, "validate_data_path", lambda p: str(p))
    registry = ToolRegistry()
    ta.register_terrain_tools(registry)
    return registry


@pytest.fixture()
def ridge_tif(tmp_path):
    n = 21
    x = np.arange(n, dtype=float)[None, :] - (n - 1) / 2.0
    ridge = 60.0 - 0.4 * x ** 2 * np.ones((n, n))
    tf = (10.0, 0.0, 0.0, 0.0, -10.0, 10.0 * n)
    path = tmp_path / "ridge.tif"
    _write_tif(path, ridge, tf, crs="EPSG:32650")
    return path


def test_tool_geomorphometry_tools_evidence(ridge_tif, terrain_tools):
    call = terrain_tools._tools

    open_res = call["terrain_openness_analysis"](str(ridge_tif), 6, 16)
    assert open_res["success"] is True
    assert open_res["scientific_evidence"]["algorithm"] == "terrain.openness"
    assert open_res["positive_openness"]["statistics"]["max"] > 0.0
    assert any(d["name"] == "backend_selection"
               for d in open_res["scientific_evidence"]["diagnostics"])

    geo = call["geomorphon_analysis"](str(ridge_tif), 5, 1.0, 0.0)
    assert geo["success"] is True
    assert geo["scientific_evidence"]["algorithm"] == "terrain.geomorphons"
    assert sum(geo["class_distribution"].values()) == geo["meta"]["cells_valid"]

    land = call["landform_classify"](str(ridge_tif), 3, 11, 0.1)
    assert land["success"] is True
    assert land["scientific_evidence"]["algorithm"] == "terrain.landform"
    assert land["class_distribution"]

    shade = call["multiazimuth_hillshade"](str(ridge_tif), 45.0, "315,135", "mean")
    assert shade["success"] is True
    assert shade["scientific_evidence"]["algorithm"] == "terrain.hillshade_multi"
    assert shade["azimuths"] == [315.0, 135.0]
    # Contract violation surfaces as parameter_contract_violation.
    with pytest.raises(ValueError, match="parameter_contract_violation"):
        call["multiazimuth_hillshade"](str(ridge_tif), 95.0)


# ── 8. Registry / parity ──────────────────────────────────────────────


def test_algorithm_registry_and_parity_clean():
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    issues = [
        issue for issue in get_algorithm_registry().validate()
        if "terrain." in issue
    ]
    assert issues == []
    from app.services.gis_harness.registry_validation import (
        validate_algorithm_tool_parameter_parity,
    )

    parity = [
        issue for issue in validate_algorithm_tool_parameter_parity()
        if "terrain." in issue
    ]
    assert parity == []
