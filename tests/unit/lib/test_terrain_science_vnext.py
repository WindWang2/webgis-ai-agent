"""Terrain science VNext conformance tests (ADR-0099 terrain domain pack).

Trusted-reference fixtures with hand-computed answers for the terrain
science library (app/lib/geo_analysis/terrain.py) and the thin tool wiring
(app/tools/terrain_analysis.py):

- TPI: linear ramp -> exactly 0; center peak -> 10 - 10/9
- TRI: flat -> 0; ramp z=x (cell 1) -> sqrt(6) (3 neighbours +-1, 2 equal)
- roughness: flat -> 0; 7x0 + {3, 6} window -> exactly 2.0
- curvature (Zevenbergen-Thorne): z = x^2 -> profile = +2, plan = 0 exactly
- viewshed: flat DEM -> disk area/window area; conical hill occlusion
- D8: 5x5 bowl z = r^2 -> interior flows toward centre, accumulation 24 (=N-1)
- watershed: bowl centre pour point -> all 25 cells
- contours: ramp z=x interval=1 -> one straight line per integer level
- nodata exclusion, NoValidObservations on all-nodata, window guard,
  determinism (dict equality over repeated calls).
"""
import math

import numpy as np
import pytest

from app.lib.geo_analysis import terrain as tlib
from app.lib.gis.scientific_errors import NoValidObservations

pytestmark = pytest.mark.unit


# ── fixtures ──────────────────────────────────────────────────────────


def _ramp(n: int = 9) -> np.ndarray:
    """z = x (cell 1): linear surface, values 0..n-1 per column."""
    return np.tile(np.arange(n, dtype=float), (n, 1))


def _bowl(n: int = 5) -> np.ndarray:
    """z = r^2 paraboloid centred at (2, 2) — every cell drains to centre."""
    r, c = np.mgrid[0:n, 0:n].astype(float)
    return (r - 2) ** 2 + (c - 2) ** 2


def _cone_hill(n: int = 41, peak: float = 50.0) -> np.ndarray:
    r, c = np.mgrid[0:n, 0:n].astype(float)
    return np.maximum(0.0, peak - np.hypot(r - n // 2, c - n // 2))


# ── 1. TPI ────────────────────────────────────────────────────────────


def test_tpi_linear_ramp_zero_and_center_peak_positive():
    tpi, meta = tlib.topographic_position_index(_ramp(9), window=3)
    # Linear surface: window mean == centre value -> TPI identically 0.
    assert np.all(tpi[1:-1, 1:-1] == 0.0)
    assert np.all(np.isfinite(tpi[1:-1, 1:-1]))
    assert meta["window_includes_center"] is True
    assert meta["cells_valid"] == 81

    flat = np.zeros((9, 9))
    flat[4, 4] = 10.0
    tpi2, _ = tlib.topographic_position_index(flat, window=3)
    assert tpi2[4, 4] == pytest.approx(10.0 - 10.0 / 9.0)
    assert tpi2[4, 4] > 0
    # Ring neighbours share the elevated window mean -> slightly negative.
    assert tpi2[4, 3] < 0


# ── 2. TRI ────────────────────────────────────────────────────────────


def test_tri_flat_zero_and_ramp_hand_computed():
    tri, _ = tlib.terrain_ruggedness_index(np.full((9, 9), 123.0))
    assert float(np.nanmax(tri)) == 0.0

    # z = x, cell 1: 8 neighbours of an interior cell are 3 x (-1), 2 x 0,
    # 3 x (+1) diffs -> sum of squares = 6 -> TRI = sqrt(6).
    tri, meta = tlib.terrain_ruggedness_index(_ramp(9))
    assert float(tri[4, 4]) == math.sqrt(6)
    assert np.all(tri[1:-1, 1:-1] == math.sqrt(6))
    assert meta["cells_valid"] == 81


# ── 3. Roughness ──────────────────────────────────────────────────────


def test_roughness_flat_zero_and_hand_fixture_exact():
    ro, _ = tlib.roughness(np.full((9, 9), 7.5), window=3)
    assert float(np.nanmax(ro)) == 0.0

    # Hand fixture: window at (4,4) covers 7 zeros + one 3 + one 6:
    # mean = 1, E[z^2] = 45/9 = 5, var = 4 -> std = 2 (exact in float64:
    # every intermediate sum is a small exact integer).
    z = np.zeros((9, 9))
    z[5, 4] = 3.0
    z[5, 5] = 6.0
    ro, meta = tlib.roughness(z, window=3)
    assert ro[4, 4] == 2.0
    assert ro[4, 4] == float(np.std(z[3:6, 3:6]))
    assert meta["window"] == 3


# ── 4. Curvature (Zevenbergen & Thorne 1987) ─────────────────────────


def test_curvature_quadratic_convention_exact():
    # z = x^2, cell 1: Dxx = (z[j-1] - 2 z[j] + z[j+1]) = 2 exactly.
    # Gradient (2x, 0) points along x -> profile = Dxx = +2 (convex),
    # plan (contour direction) = Dyy = 0.
    x2 = np.tile(np.arange(9, dtype=float) ** 2, (9, 1))
    curv, meta = tlib.surface_curvature(x2, 1.0)
    profile, plan = curv["profile"], curv["plan"]
    for i in range(1, 8):
        assert np.all(profile[i, 1:8] == 2.0), i
        assert np.all(plan[i, 1:8] == 0.0), i
    assert "cell^-2" in meta["units"]
    assert "profile > 0 convex" in meta["sign_convention"]

    # Constant plane: gradient zero -> curvature undefined (NaN), the
    # band_math flat-aspect convention.
    curv_flat, _ = tlib.surface_curvature(np.full((9, 9), 5.0), 1.0)
    assert np.all(np.isnan(curv_flat["profile"]))
    assert np.all(np.isnan(curv_flat["plan"]))


# ── 5. Viewshed ───────────────────────────────────────────────────────


def test_viewshed_flat_disk_fraction_and_cone_occlusion():
    n = 41
    r, c = np.mgrid[0:n, 0:n].astype(float)
    dist = np.hypot(r - 20, c - 20)

    # Flat DEM: every cell within max_distance is visible -> the visible
    # fraction equals the disk area / window area (discretisation ~2%).
    res, meta = tlib.viewshed(
        np.full((n, n), 100.0), 1.0, observer=(20, 20),
        observer_height=2.0, max_distance=10.0)
    assert res["visible_fraction"] == pytest.approx(
        math.pi * 10 ** 2 / (n * n), abs=0.02)
    assert np.array_equal(res["visible"], dist <= 10.0)
    assert meta["earth_curvature_refraction"].startswith("not applied")

    # Conical hill z = max(0, 50 - r): observer W of the peak sees the
    # near slope but the cell straight behind the summit is occluded.
    cone = _cone_hill(n=41, peak=50.0)
    res2, meta2 = tlib.viewshed(
        cone, 1.0, observer=(20, 5), observer_height=2.0, max_distance=100.0)
    assert bool(res2["visible"][20, 7])
    assert not res2["visible"][20, 35]
    # The summit itself is always visible from anywhere on its flanks.
    assert res2["visible"][20, 20]
    assert 0.0 < res2["visible_fraction"] < 1.0


# ── 6. D8 flow direction + accumulation ───────────────────────────────


def test_d8_bowl_flow_toward_center_accumulation_24():
    bowl = _bowl(5)
    d8, meta = tlib.d8_flow(bowl, 1.0)
    direction = d8["direction"]
    # Pit: no strictly lower neighbour -> 0 (sink).
    assert direction[2, 2] == 0
    # Every interior cell routes to a neighbour strictly closer to centre;
    # diagonal steps dominate (drop 2 over sqrt(2) beats drop 1 over 1).
    decode = {code: (dr, dc) for _, code, dr, dc in tlib._D8_NEIGHBORS}
    for i in range(1, 4):
        for j in range(1, 4):
            if (i, j) == (2, 2):
                continue
            dr, dc = decode[int(direction[i, j])]
            assert (i + dr - 2) ** 2 + (j + dc - 2) ** 2 < (i - 2) ** 2 + (j - 2) ** 2
    assert int(direction[1, 1]) == 2   # SE
    assert int(direction[1, 3]) == 8   # SW
    assert int(direction[3, 1]) == 128  # NE
    assert "1=E" in meta["encoding"]

    acc, acc_meta = tlib.flow_accumulation(d8)
    # All 24 non-centre cells drain into the pit: accumulation = N - 1
    # (upstream contributors, self excluded — documented convention).
    assert int(acc[2, 2]) == 24
    assert int(acc.max()) == 24
    assert acc_meta["counting_convention"].startswith("number of upstream")


# ── 7. Watershed ──────────────────────────────────────────────────────


def test_watershed_bowl_all_cells():
    bowl = _bowl(5)
    d8, _ = tlib.d8_flow(bowl, 1.0)
    mask, meta = tlib.upstream_watershed(d8, [(2, 2)])
    assert mask.shape == (5, 5)
    assert int(mask.sum()) == 25
    assert bool(mask.all())
    assert meta["includes_pour_point"] is True

    # World-coordinate pour point convenience wrapper: transform
    # (a=10, e=-10, c=0, f=50) maps cell (2, 2) centre to (25, 25).
    mask2, meta2 = tlib.watershed(bowl, 10.0, [(25.0, 25.0)],
                                  transform=(10.0, 0.0, 0.0, 0.0, -10.0, 50.0))
    assert np.array_equal(mask2, mask)
    assert meta2["pour_cells"] == [(2, 2)]


# ── 8. Contours ───────────────────────────────────────────────────────


def test_contours_ramp_interval_levels_and_world_coords():
    ramp = np.tile(np.arange(10, dtype=float), (10, 1))  # z = x in 0..9
    tf = (1.0, 0.0, 0.0, 0.0, -1.0, 10.0)                # x = col, y = 10 - row
    fc, meta = tlib.extract_contours(ramp, transform=tf, interval=1.0)

    # One straight vertical line per integer level; the degenerate level 9
    # (== vmax) draws no geometry -> exactly 9 features with levels 0..8.
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 9
    levels = [f["properties"]["level"] for f in fc["features"]]
    assert levels == [float(v) for v in range(9)]
    for feat in fc["features"]:
        assert feat["geometry"]["type"] == "LineString"
        coords = feat["geometry"]["coordinates"]
        assert len(coords) >= 2
        lvl = feat["properties"]["level"]
        # Straight line at world x == level, spanning rows 0..9 -> y 10..1.
        assert all(pt[0] == lvl for pt in coords)
        ys = [pt[1] for pt in coords]
        assert min(ys) == pytest.approx(1.0)
        assert max(ys) == pytest.approx(10.0)
    assert meta["levels_drawn"] == [float(v) for v in range(9)]

    # Explicit levels take priority; fractional level lands between cells.
    fc2, meta2 = tlib.extract_contours(ramp, transform=tf, levels=[3.5])
    assert meta2["levels_policy"] == "explicit levels"
    assert len(fc2["features"]) == 1
    assert fc2["features"][0]["properties"]["level"] == 3.5
    assert all(pt[0] == 3.5 for pt in fc2["features"][0]["geometry"]["coordinates"])


# ── 9. Nodata semantics ───────────────────────────────────────────────


def test_nodata_excluded_and_all_nodata_raises():
    ramp = _ramp(9).copy()
    ramp[:, 6:] = -999.0
    tpi, meta = tlib.topographic_position_index(ramp, window=3, nodata=-999.0)
    assert meta["cells_valid"] == 81 - 27
    assert np.all(np.isfinite(tpi) == (ramp != -999.0))
    # Nodata cells never contribute to neighbour statistics on the seam.
    assert np.isfinite(tpi[:, 5]).all()

    allnodata = np.full((9, 9), -999.0)
    with pytest.raises(NoValidObservations):
        tlib.topographic_position_index(allnodata, window=3, nodata=-999.0)
    with pytest.raises(NoValidObservations):
        tlib.terrain_ruggedness_index(allnodata, nodata=-999.0)
    with pytest.raises(NoValidObservations):
        tlib.roughness(allnodata, window=3, nodata=-999.0)
    with pytest.raises(NoValidObservations):
        tlib.surface_curvature(allnodata, 1.0, nodata=-999.0)
    with pytest.raises(NoValidObservations):
        tlib.viewshed(allnodata, 1.0, observer=(4, 4), nodata=-999.0)
    with pytest.raises(NoValidObservations):
        tlib.d8_flow(allnodata, 1.0, nodata=-999.0)
    with pytest.raises(NoValidObservations):
        tlib.extract_contours(allnodata, nodata=-999.0)
    # NaN counts as invalid even without an explicit nodata value.
    with pytest.raises(NoValidObservations):
        tlib.topographic_position_index(np.full((5, 5), np.nan))


# ── 10. Determinism ───────────────────────────────────────────────────


def test_determinism_repeated_calls_identical():
    cone = _cone_hill(21, peak=30.0)
    r1, m1 = tlib.viewshed(cone, 2.0, observer=(5, 5),
                           observer_height=2.0, max_distance=40.0)
    r2, m2 = tlib.viewshed(cone, 2.0, observer=(5, 5),
                           observer_height=2.0, max_distance=40.0)
    assert m1 == m2
    assert r1["visible_fraction"] == r2["visible_fraction"]
    assert np.array_equal(r1["visible"], r2["visible"])

    tf = (2.0, 0.0, 0.0, 0.0, -2.0, 42.0)
    fc1, cm1 = tlib.extract_contours(cone, transform=tf, n_levels=6)
    fc2, cm2 = tlib.extract_contours(cone, transform=tf, n_levels=6)
    assert fc1 == fc2
    assert cm1 == cm2

    d8a, dma = tlib.d8_flow(cone, 2.0)
    acc_a, am_a = tlib.flow_accumulation(d8a)
    d8b, dmb = tlib.d8_flow(cone, 2.0)
    acc_b, am_b = tlib.flow_accumulation(d8b)
    assert dma == dmb and am_a == am_b
    assert np.array_equal(d8a["direction"], d8b["direction"])
    assert np.array_equal(acc_a, acc_b)

    t1, tm1 = tlib.topographic_position_index(cone, window=5)
    t2, tm2 = tlib.topographic_position_index(cone, window=5)
    assert tm1 == tm2 and np.array_equal(t1, t2)


# ── 11. Window guard ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad_window", [103, 101 + 2, 4, 2, 0, -3, 101.5])
def test_window_guard_rejects_out_of_range(bad_window):
    dem = _ramp(9)
    with pytest.raises(ValueError, match="window"):
        tlib.topographic_position_index(dem, window=bad_window)
    with pytest.raises(ValueError, match="window"):
        tlib.roughness(dem, window=bad_window)


# ── Tool wiring (thin wrapper + scientific evidence) ─────────────────


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
    """Register terrain tools on a fresh registry; bypass data_dir path gate."""
    from app.tools.registry import ToolRegistry
    from app.tools import terrain_analysis as ta

    monkeypatch.setattr(ta, "validate_data_path", lambda p: str(p))
    registry = ToolRegistry()
    ta.register_terrain_tools(registry)
    return registry


def _bowl_tif(tmp_path, n=9, crs="EPSG:32650"):
    """z = r^2 bowl over an n x n grid, 10 m cells, origin (0, 10n)."""
    r, c = np.mgrid[0:n, 0:n].astype(float)
    ctr = (n - 1) / 2.0
    bowl = (r - ctr) ** 2 + (c - ctr) ** 2
    tf = (10.0, 0.0, 0.0, 0.0, -10.0, 10.0 * n)
    path = tmp_path / "bowl.tif"
    _write_tif(path, bowl, tf, crs=crs)
    return path


def test_terrain_derivatives_tool_evidence(tmp_path, terrain_tools):
    path = _bowl_tif(tmp_path)
    call = terrain_tools._tools["terrain_derivatives"]
    res = call(str(path), "tpi")
    assert res["success"] is True
    assert set(res["statistics"]) >= {"min", "max", "mean", "std"}
    assert len(res["sample"]) <= 64 and len(res["sample"][0]) <= 64
    assert res["meta"]["algorithm"] == "terrain.tpi"
    ev = res["scientific_evidence"]
    assert ev["algorithm"] == "terrain.tpi"
    assert ev["tool"] == "terrain_derivatives"
    assert ev["inputs"]["artifact_type"] == "terrain_surface"
    assert ev["reproducibility"]["deterministic"] is True
    assert ev["parameters_applied"]["window"] == 3
    assert ev["assumptions"]

    curv = call(str(path), "profile_curvature")
    assert curv["scientific_evidence"]["algorithm"] == "terrain.curvature"
    assert curv["meta"]["derivative"] == "profile_curvature"

    with pytest.raises(ValueError, match="parameter_contract_violation"):
        call(str(path), "not_a_derivative")


def test_viewshed_tool_geographic_disclosure(tmp_path, terrain_tools):
    # Geographic (degree) DEM: the cos(lat) metric conversion must be
    # disclosed in evidence transformations_applied.
    n = 9
    r, c = np.mgrid[0:n, 0:n].astype(float)
    cone = np.maximum(0.0, 50.0 - np.hypot(r - 4, c - 4))
    tf = (0.001, 0.0, 10.0, 0.0, -0.001, 10.01)
    path = tmp_path / "cone_geo.tif"
    _write_tif(path, cone, tf, crs="EPSG:4326")
    call = terrain_tools._tools["viewshed_analysis"]
    res = call(str(path), 10.0045, 10.0055, observer_height=2.0, max_distance=500.0)
    assert res["success"] is True
    assert 0.0 <= res["visible_fraction"] <= 1.0
    assert res["visible_area_m2"] >= 0.0
    ev = res["scientific_evidence"]
    assert ev["algorithm"] == "terrain.viewshed"
    assert ev["inputs"]["crs_class"] == "geographic"
    assert any("cos(" in t for t in ev["transformations_applied"])
    # No earth curvature / atmospheric refraction is disclosed.
    assert any("折射" in a for a in ev["assumptions"])


def test_flow_and_watershed_tools(tmp_path, terrain_tools):
    path = _bowl_tif(tmp_path, n=9)
    flow = terrain_tools._tools["flow_analysis"]
    res = flow(str(path), "flow_accumulation")
    assert res["success"] is True
    n = 9
    assert res["statistics"]["max_accumulation"] == n * n - 1
    assert res["scientific_evidence"]["algorithm"] == "terrain.flow"

    fd = flow(str(path), "flow_direction")
    counts = fd["statistics"]["direction_counts"]
    assert counts["0"] == 1  # the single pit
    assert sum(counts.values()) == n * n

    ws = terrain_tools._tools["watershed_delineation"]
    # Bowl centre cell centre -> world (45, 45).
    wres = ws(str(path), 45.0, 45.0)
    assert wres["cell_count"] == n * n
    assert wres["area_m2"] == pytest.approx(n * n * 100.0)
    # Whole-grid watershed: the boundary is the raster border ring (32 cells).
    assert len(wres["boundary_sample"]) == 4 * (n - 1)
    xs = [pt[0] for pt in wres["boundary_sample"]]
    ys = [pt[1] for pt in wres["boundary_sample"]]
    assert min(xs) >= 0.0 and max(xs) <= 90.0
    assert min(ys) >= 0.0 and max(ys) <= 90.0
    assert wres["scientific_evidence"]["algorithm"] == "terrain.watershed"


def test_extract_contours_tool(tmp_path, terrain_tools):
    ramp = np.tile(np.arange(10, dtype=float), (10, 1))
    tf = (10.0, 0.0, 0.0, 0.0, -10.0, 100.0)
    path = tmp_path / "ramp.tif"
    _write_tif(path, ramp, tf, crs="EPSG:32650")
    call = terrain_tools._tools["extract_contours"]
    res = call(str(path), levels=[2.0, 5.0])
    assert res["success"] is True
    assert res["feature_count"] == 2
    lvls = {f["properties"]["level"] for f in res["contours"]["features"]}
    assert lvls == {2.0, 5.0}
    for feat in res["contours"]["features"]:
        assert all(pt[0] == 10.0 * feat["properties"]["level"]
                   for pt in feat["geometry"]["coordinates"])
    ev = res["scientific_evidence"]
    assert ev["algorithm"] == "terrain.contours"
    assert ev["parameters_applied"]["explicit_levels"] is True
    # Default path: n_levels equal-interval (interval/levels absent).
    res2 = call(str(path), n_levels=4)
    assert res2["feature_count"] >= 1
    assert res2["meta"]["levels_policy"] == "n_levels equal-interval"
