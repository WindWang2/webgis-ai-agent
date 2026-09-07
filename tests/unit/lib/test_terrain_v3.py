"""Terrain V3 conformance tests（Terrain V3 批次）.

Hand-computed goldens for the Terrain V3 additions
(app/lib/geo_analysis/terrain.py) and their thin tool wiring
(app/tools/terrain_analysis.py):

- horizon_angle: flat DEM -> every azimuth exactly 0; idealized ridge wall
  (z = 10 on row 2) blocks azimuth 0 at arctan(10/d) by hand; rays stop at
  nodata (documented edge policy); anisotropic cell sizes honoured
- sky_view_factor (Steyn 1980): flat -> SVF = 1.0 exactly (1e-12); single
  deep pit -> SVF < 0.05 at the pit while the surrounding flat stays 1
  (a hole below the horizon plane does not obstruct the sky); azimuth-count
  invariance; SVF equals mean cos^2(psi) over the shared horizon rasters
- d8 flat_routing="epsilon": flat-bottomed bowl keeps interior sinks under
  the default (code 0) and drains fully to the rim outlet with epsilon
  (Barnes 2014 fill machinery); accumulation conservation
  (sum of acc over outlets == number of flowing cells); default path is
  bit-identical to the historical behavior (golden direction codes)
- guards: cell-count cap fires before allocation (monkeypatched
  allocators), radius cap, azimuth/n_azimuth validation
- determinism: dict equality + array equality over repeated calls
- tools: evidence blocks + contract violations on real GeoTIFFs
- registry: validate() -> [] and tool-parameter parity clean for terrain
"""
import math

import numpy as np
import pytest

from app.lib.geo_analysis import terrain as tlib
from app.lib.gis.scientific_errors import ResourceScaleMismatch

pytestmark = pytest.mark.unit


# ── fixtures ──────────────────────────────────────────────────────────


def _wall_dem(n: int = 9, height: float = 10.0, row: int = 2) -> np.ndarray:
    """Idealized ridge: flat 0 with one full-width wall row at `height`."""
    z = np.zeros((n, n))
    z[row, :] = height
    return z


def _pit_dem(n: int = 9, depth: float = -50.0) -> np.ndarray:
    """Flat 0 with a single very deep pit at the centre."""
    z = np.zeros((n, n))
    z[n // 2, n // 2] = depth
    return z


def _flat_bottom_bowl(n: int = 11) -> np.ndarray:
    """Bowl with a perfectly flat pan: z = 2 for r <= 3, paraboloid walls
    (z = r^2) outside. The pan (29 cells) is a flat-bottomed depression
    whose spill elevation is the surrounding wall base."""
    r, c = np.mgrid[0:n, 0:n].astype(float)
    rr = (r - 5) ** 2 + (c - 5) ** 2
    return np.where(rr <= 9.0, 2.0, rr)


# ── 1. Horizon angle ──────────────────────────────────────────────────


def test_horizon_flat_all_zeros():
    flat = np.zeros((9, 9))
    res, meta = tlib.horizon_angle(flat, 1.0)
    assert res["azimuths"] == [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0]
    assert set(res["horizon"]) == {"0", "45", "90", "135", "180", "225", "270", "315"}
    for arr in res["horizon"].values():
        assert np.all(arr == 0.0)  # flat ground -> exactly 0 on every azimuth
    assert np.all(res["max"] == 0.0)
    assert meta["units"] == "degrees"
    assert "unobstructed" in meta["edge_policy"]
    assert meta["cells_valid"] == 81


def test_horizon_ridge_blocks_specific_azimuths():
    # Wall (z = 10) at row 2, observer at (5, 4), cell 1 m.
    res, _ = tlib.horizon_angle(_wall_dem(), 1.0, max_search_radius=10)
    # North ray (az 0) crosses the wall at distance 3 -> arctan(10/3).
    north = math.degrees(math.atan(10.0 / 3.0))
    assert res["horizon"]["0"][5, 4] == pytest.approx(north, rel=1e-12)
    # South ray runs along the flat row -> exactly 0.
    assert res["horizon"]["180"][5, 4] == 0.0
    # Cross-azimuth max picks up the wall.
    assert res["max"][5, 4] == pytest.approx(north, rel=1e-12)
    # Observer one cell south of the wall sees it at arctan(10/1).
    assert res["horizon"]["0"][3, 4] == pytest.approx(
        math.degrees(math.atan(10.0)), rel=1e-12)
    # North of the wall the obstruction appears on the south ray instead.
    assert res["horizon"]["180"][1, 4] == pytest.approx(
        math.degrees(math.atan(10.0)), rel=1e-12)
    assert res["horizon"]["0"][1, 4] == 0.0
    # NE ray (rounded 1-cell steps): first wall hit at k = 4 -> offset
    # (dr, dc) = (-3, +3), metric distance 3*sqrt(2) -> arctan(10 / (3*sqrt(2))).
    ne = math.degrees(math.atan(10.0 / (3.0 * math.sqrt(2.0))))
    assert res["horizon"]["45"][5, 4] == pytest.approx(ne, rel=1e-12)
    # Anisotropic metric distances: a west wall column with cx = 2 m puts
    # the wall at 2 steps * 2 m = 4 m, not 2 m (east-west distances use cx;
    # the wall lies west of the observer -> azimuth 270).
    ew = np.zeros((9, 9))
    ew[:, 2] = 10.0
    res_ew, _ = tlib.horizon_angle(ew, 1.0, cell_size_x=2.0, max_search_radius=10)
    assert res_ew["horizon"]["270"][4, 4] == pytest.approx(
        math.degrees(math.atan(10.0 / 4.0)), rel=1e-12)
    res_iso, _ = tlib.horizon_angle(ew, 1.0, max_search_radius=10)
    assert res_iso["horizon"]["270"][4, 4] == pytest.approx(
        math.degrees(math.atan(10.0 / 2.0)), rel=1e-12)


def test_horizon_nodata_stops_ray():
    dem = _wall_dem()
    dem[3, 4] = -9999.0
    dem[4, 4] = -9999.0
    res, meta = tlib.horizon_angle(dem, 1.0, max_search_radius=10, nodata=-9999.0)
    # The ray due north from (5, 4) stops at the first nodata sample (k = 1):
    # the wall behind the data gap is NOT seen on that azimuth (truncated
    # ray = unobstructed)...
    assert res["horizon"]["0"][5, 4] == 0.0
    # ...but the NE ray (no data gap on its column) still sees the wall, so
    # the cross-azimuth max is the NE hand value, not 0.
    ne = math.degrees(math.atan(10.0 / (3.0 * math.sqrt(2.0))))
    assert res["horizon"]["45"][5, 4] == pytest.approx(ne, rel=1e-12)
    assert res["max"][5, 4] == pytest.approx(ne, rel=1e-12)
    # The neighbouring column has no gap -> the north ray still sees the wall.
    assert res["horizon"]["0"][5, 3] == pytest.approx(
        math.degrees(math.atan(10.0 / 3.0)), rel=1e-12)
    # Nodata cells are NaN outputs.
    assert np.isnan(res["horizon"]["0"][4, 4])
    assert np.isnan(res["max"][3, 4])
    assert meta["cells_valid"] == 81 - 2
    # All-nodata input is rejected by the prepare contract.
    from app.lib.gis.scientific_errors import NoValidObservations

    with pytest.raises(NoValidObservations):
        tlib.horizon_angle(np.full((5, 5), -9999.0), 1.0, nodata=-9999.0)


# ── 2. Sky view factor (Steyn 1980) ───────────────────────────────────


def test_svf_flat_exact_one():
    flat = np.zeros((9, 9))
    for n_az in (4, 16, 64):
        res, meta = tlib.sky_view_factor(flat, 1.0, n_azimuths=n_az)
        assert np.all(np.abs(res["svf"] - 1.0) <= 1e-12)
        assert meta["units"].startswith("ratio")
        assert meta["n_azimuths"] == n_az
        assert len(res["azimuths"]) == n_az
        # First azimuth is due north; equal angular spacing.
        assert res["azimuths"][0] == 0.0
        if n_az == 4:
            assert res["azimuths"] == [0.0, 90.0, 180.0, 270.0]


def test_svf_deep_pit_small_and_azimuth_invariance():
    pit = _pit_dem()
    res8, _ = tlib.sky_view_factor(pit, 1.0, n_azimuths=8)
    res16, _ = tlib.sky_view_factor(pit, 1.0, n_azimuths=16)
    res64, _ = tlib.sky_view_factor(pit, 1.0, n_azimuths=64)
    # From inside the deep pit every azimuth is obstructed by the near rim:
    # SVF is small at the pit cell for any azimuth count...
    for res in (res8, res16, res64):
        assert 0.0 < res["svf"][4, 4] < 0.05
    # ...and the estimate is azimuth-count invariant to well under 1%.
    assert abs(res8["svf"][4, 4] - res16["svf"][4, 4]) < 1e-3
    assert abs(res16["svf"][4, 4] - res64["svf"][4, 4]) < 1e-3
    # The flat ring AROUND the pit keeps a fully open sky: the pit lies
    # below the horizon plane (negative angles clamp at 0).
    assert abs(res16["svf"][4, 3] - 1.0) <= 1e-12
    assert abs(res16["svf"][0, 0] - 1.0) <= 1e-12


def test_svf_matches_steyn_formula_from_horizon():
    wall = _wall_dem()
    hres, _ = tlib.horizon_angle(wall, 1.0, max_search_radius=10)
    sres, smeta = tlib.sky_view_factor(wall, 1.0, n_azimuths=8, max_search_radius=10)
    # Same azimuth set (8 evenly spaced from north) -> the SVF must equal the
    # Steyn 1980 mean of cos^2(psi) over the shared horizon rasters.
    expected = np.zeros((9, 9))
    for arr in hres["horizon"].values():
        expected += np.cos(np.radians(arr)) ** 2
    expected /= 8.0
    assert smeta["method"].startswith("sky view factor (Steyn 1980)")
    assert np.allclose(sres["svf"], expected, atol=1e-12)
    # Blocked azimuths shrink the SVF below 1 only where the wall is seen.
    assert sres["svf"][5, 4] < 1.0
    # A cell IN the wall row looks along/over flat ground on every azimuth.
    assert abs(sres["svf"][2, 4] - 1.0) <= 1e-12
    # The shared ray walk produces bit-identical horizon rasters.
    for key, arr in sres["horizon"].items():
        assert np.array_equal(arr, hres["horizon"][key]), key


# ── 3. Guards ─────────────────────────────────────────────────────────


def test_horizon_and_svf_guards(monkeypatch):
    dem = np.zeros((5, 5))
    # Radius cap fires before any allocation (openness-consistent envelope).
    with pytest.raises(ResourceScaleMismatch):
        tlib.horizon_angle(dem, 1.0, max_search_radius=101)
    with pytest.raises(ResourceScaleMismatch):
        tlib.sky_view_factor(dem, 1.0, max_search_radius=101)
    # Type/range validation of the ray and azimuth parameters.
    for bad in (0, -5, 2.5, True):
        with pytest.raises(ValueError, match="max_search_radius"):
            tlib.horizon_angle(dem, 1.0, max_search_radius=bad)
    with pytest.raises(ValueError, match="azimuths"):
        tlib.horizon_angle(dem, 1.0, azimuths=())
    with pytest.raises(ValueError, match="azimuths"):
        tlib.horizon_angle(dem, 1.0, azimuths=(400.0,))
    with pytest.raises(ValueError, match="azimuths"):
        tlib.horizon_angle(dem, 1.0, azimuths=(-1.0, 45.0))
    with pytest.raises(ValueError, match="azimuths"):
        tlib.horizon_angle(dem, 1.0, azimuths=tuple(65 * (10.0,)))
    with pytest.raises(ValueError, match="n_azimuths"):
        tlib.sky_view_factor(dem, 1.0, n_azimuths=3)
    with pytest.raises(ValueError, match="n_azimuths"):
        tlib.sky_view_factor(dem, 1.0, n_azimuths=65)
    # Cell-count guard fires BEFORE allocation: shrink the cap and booby-trap
    # the allocators — reaching any allocation explodes the test.
    def _boom(*args, **kwargs):
        raise AssertionError("allocator reached before scale guard")

    monkeypatch.setattr(tlib, "MAX_HYDRO_CELLS", 16)
    for name in ("zeros", "empty", "full"):
        monkeypatch.setattr(tlib.np, name, _boom)
    with pytest.raises(ResourceScaleMismatch) as exc:
        tlib.horizon_angle(dem, 1.0)
    assert exc.value.estimated == "25 cells"
    assert exc.value.limit == "16 cells"
    with pytest.raises(ResourceScaleMismatch):
        tlib.sky_view_factor(dem, 1.0)


# ── 4. D8 epsilon flat routing ────────────────────────────────────────


def test_epsilon_flat_routing_bowl_and_default_unchanged():
    dem = _flat_bottom_bowl(11)
    valid = np.ones_like(dem, dtype=bool)
    interior = np.zeros_like(valid)
    interior[1:-1, 1:-1] = True

    # Default call == explicit flat_routing="none", bit for bit.
    d_default, m_default = tlib.d8_flow(dem, 1.0)
    d_none, m_none = tlib.d8_flow(dem, 1.0, flat_routing="none")
    for key in ("direction", "receiver", "valid", "dem"):
        assert np.array_equal(d_default[key], d_none[key]), key
    assert m_default == m_none
    assert m_default["flat_routing"] == "none"

    # Historical golden behaviour is unchanged on a paraboloid bowl:
    # centre is the single pit (code 0), diagonal steps dominate.
    r, c = np.mgrid[0:5, 0:5].astype(float)
    paraboloid = (r - 2) ** 2 + (c - 2) ** 2
    d5, _ = tlib.d8_flow(paraboloid, 1.0)
    assert int(d5["direction"][2, 2]) == 0
    assert int(d5["direction"][1, 1]) == 2    # SE
    assert int(d5["direction"][1, 3]) == 8    # SW
    assert int(d5["direction"][3, 1]) == 128  # NE
    acc5, _ = tlib.flow_accumulation(d5)
    assert int(acc5[2, 2]) == 24              # N - 1

    # Flat-bottomed bowl, default routing: the whole flat pan (29 cells)
    # is interior sinks (code 0) — documented "flats are sinks" semantics.
    pan = dem == 2.0
    assert int(pan.sum()) == 29
    sinks_none = valid & interior & (d_none["direction"] == 0)
    assert int((sinks_none & pan).sum()) == 29

    # flat_routing="epsilon": the Barnes 2014 epsilon fill gives a strictly
    # drainable surface — no interior sinks remain and the whole bowl
    # drains toward the rim outlet.
    d_eps, m_eps = tlib.d8_flow(dem, 1.0, flat_routing="epsilon")
    sinks_eps = valid & interior & (d_eps["direction"] == 0)
    assert int(sinks_eps.sum()) == 0
    assert (d_eps["direction"][pan] != 0).all()
    assert m_eps["flat_routing"] == "epsilon"
    assert m_eps["flat_epsilon"] == tlib.DEFAULT_FLAT_EPSILON
    assert m_eps["filled_cell_count"] >= 29
    # Receivers are strictly lower on the routing surface (the epsilon-filled
    # DEM returned as result["dem"]) — the topological accumulation invariant.
    flat_idx = np.arange(dem.size)
    recv_ok = (d_eps["receiver"] < 0) | (
        d_eps["dem"].ravel()[d_eps["receiver"]]
        < d_eps["dem"].ravel()[flat_idx])
    assert recv_ok.all()
    # Following the chain from the pan centre reaches a rim outlet, strictly
    # descending on the filled surface at every hop.
    w = dem.shape[1]
    cur = 5 * w + 5
    hops = 0
    while d_eps["receiver"][cur] >= 0:
        nxt = int(d_eps["receiver"][cur])
        assert d_eps["dem"].ravel()[nxt] < d_eps["dem"].ravel()[cur]
        cur = nxt
        hops += 1
        assert hops <= dem.size
    rr, cc = divmod(cur, w)
    assert rr in (0, 10) or cc in (0, 10)  # outlet sits on the grid boundary

    # Accumulation conservation for both modes: every valid cell contributes
    # exactly one unit, transferred until an outlet — so the accumulated
    # totals over outlet cells sum to the number of flowing cells.
    for d8 in (d_none, d_eps):
        acc, _ = tlib.flow_accumulation(d8)
        flowing = valid.ravel() & (d8["receiver"] >= 0)
        outlets = valid.ravel() & (d8["receiver"] < 0)
        assert int(flowing.sum()) + int(outlets.sum()) == int(valid.sum())
        assert int(acc.ravel()[outlets].sum()) == int(flowing.sum())

    # Parameter guards.
    with pytest.raises(ValueError, match="flat_routing"):
        tlib.d8_flow(dem, 1.0, flat_routing="gable")
    with pytest.raises(ValueError, match="flat_epsilon"):
        tlib.d8_flow(dem, 1.0, flat_routing="epsilon", flat_epsilon=0.0)


def test_epsilon_routing_rim_outlet_hand_case():
    # Minimal hand case: flat 7x7 plane with one pit at the centre. Pure
    # fill (epsilon machinery, default none) leaves the pit a sink; with
    # epsilon the pit drains to the boundary.
    plane = np.zeros((7, 7))
    plane[3, 3] = -5.0
    d_none, _ = tlib.d8_flow(plane, 2.0)
    assert int(d_none["direction"][3, 3]) == 0  # pit stays a sink
    d_eps, m_eps = tlib.d8_flow(plane, 2.0, flat_routing="epsilon")
    assert int(d_eps["direction"][3, 3]) != 0   # pit now drains
    # Chain from the pit terminates on the grid boundary.
    w = 7
    cur = 3 * w + 3
    while d_eps["receiver"][cur] >= 0:
        cur = int(d_eps["receiver"][cur])
    rr, cc = divmod(cur, w)
    assert rr in (0, 6) or cc in (0, 6)
    # With the epsilon variant every interior cell (including the zero
    # flats) is lifted by the monotone-drainage gradient: 49 - 24 border
    # cells = 25 lifted cells; the pit itself dominates the fill depth and
    # result["dem"] carries the filled routing surface.
    assert m_eps["filled_cell_count"] == 25
    lift = d_eps["dem"] - plane
    assert float(lift.max()) == pytest.approx(5.0, abs=1e-3)
    assert float(lift[3, 3]) == pytest.approx(5.0, abs=1e-3)
    # Default-mode arrays on this fixture are bit-identical to the explicit
    # "none" call (historical behaviour untouched).
    d_default, m_default = tlib.d8_flow(plane, 2.0)
    d_none2, m_none2 = tlib.d8_flow(plane, 2.0, flat_routing="none")
    assert np.array_equal(d_default["receiver"], d_none2["receiver"])
    assert m_default == m_none2


# ── 5. Determinism ────────────────────────────────────────────────────


def test_determinism_horizon_svf_epsilon():
    dem = _flat_bottom_bowl(11)
    h1, hm1 = tlib.horizon_angle(dem, 2.0, max_search_radius=8)
    h2, hm2 = tlib.horizon_angle(dem, 2.0, max_search_radius=8)
    assert hm1 == hm2
    assert np.array_equal(h1["max"], h2["max"])
    for key in h1["horizon"]:
        assert np.array_equal(h1["horizon"][key], h2["horizon"][key]), key

    s1, sm1 = tlib.sky_view_factor(dem, 2.0, n_azimuths=8, max_search_radius=8)
    s2, sm2 = tlib.sky_view_factor(dem, 2.0, n_azimuths=8, max_search_radius=8)
    assert sm1 == sm2 and np.array_equal(s1["svf"], s2["svf"])

    e1, em1 = tlib.d8_flow(dem, 2.0, flat_routing="epsilon")
    e2, em2 = tlib.d8_flow(dem, 2.0, flat_routing="epsilon")
    assert em1 == em2
    for key in ("direction", "receiver", "valid", "dem"):
        assert np.array_equal(e1[key], e2[key]), key


# ── 6. Tool wiring (thin wrappers + evidence) ─────────────────────────


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
def flat_tif(tmp_path):
    dem = np.zeros((21, 21))
    tf = (10.0, 0.0, 0.0, 0.0, -10.0, 10.0 * 21)
    path = tmp_path / "flat.tif"
    _write_tif(path, dem, tf, crs="EPSG:32650")
    return path


@pytest.fixture()
def bowl_tif(tmp_path):
    n = 21
    dem = _flat_bottom_bowl(n)
    tf = (10.0, 0.0, 0.0, 0.0, -10.0, 10.0 * n)
    path = tmp_path / "flat_bottom_bowl.tif"
    _write_tif(path, dem, tf, crs="EPSG:32650")
    return path


@pytest.fixture()
def wall_tif(tmp_path):
    dem = _wall_dem(n=21, row=4)
    tf = (10.0, 0.0, 0.0, 0.0, -10.0, 10.0 * 21)
    path = tmp_path / "wall.tif"
    _write_tif(path, dem, tf, crs="EPSG:32650")
    return path


def test_v3_tools_evidence(flat_tif, bowl_tif, wall_tif, terrain_tools):
    call = terrain_tools._tools

    # Horizon angle tool: a truly flat DEM -> every azimuth mean is exactly 0.
    hor = call["horizon_angle_analysis"](str(flat_tif), max_search_radius=10)
    assert hor["success"] is True
    assert hor["azimuths"] == [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0]
    assert all(v == 0.0 for v in hor["azimuth_mean_degrees"].values())
    assert hor["max_horizon"]["statistics"]["max"] == pytest.approx(0.0)
    assert len(hor["max_horizon"]["sample"]) <= 64
    ev = hor["scientific_evidence"]
    assert ev["algorithm"] == "terrain.horizon_angle"
    assert ev["tool"] == "horizon_angle_analysis"
    assert ev["reproducibility"]["deterministic"] is True
    assert ev["parameters_applied"]["max_search_radius"] == 10
    # Wall DEM: most cells sit south of the wall, so the north-horizon mean
    # dominates the south-horizon mean.
    hor_wall = call["horizon_angle_analysis"](
        str(wall_tif), azimuths="0,180", max_search_radius=10)
    assert hor_wall["azimuth_mean_degrees"]["0"] > hor_wall["azimuth_mean_degrees"]["180"]

    # SVF tool: pan bottom sees an (almost) fully open sky on this fixture;
    # the wall DEM tops are fully open at the far field.
    svf = call["sky_view_factor_analysis"](str(bowl_tif), max_search_radius=10)
    assert svf["success"] is True
    assert 0.0 <= svf["svf"]["statistics"]["min"] <= svf["svf"]["statistics"]["max"] <= 1.0
    assert svf["scientific_evidence"]["algorithm"] == "terrain.sky_view_factor"
    assert any("Steyn" in a for a in svf["scientific_evidence"]["assumptions"])
    svf_wall = call["sky_view_factor_analysis"](
        str(wall_tif), n_azimuths=8, max_search_radius=10)
    assert svf_wall["scientific_evidence"]["parameters_applied"]["n_azimuths"] == 8
    # Contract violations surface as parameter_contract_violation.
    with pytest.raises(ValueError, match="parameter_contract_violation"):
        call["sky_view_factor_analysis"](str(bowl_tif), n_azimuths=3)
    with pytest.raises(ValueError, match="parameter_contract_violation"):
        call["horizon_angle_analysis"](str(bowl_tif), max_search_radius=101)

    # flow_analysis flat_routing: default leaves the pan as interior sinks,
    # epsilon drains it — disclosed in meta and evidence.
    n = 21
    flow = call["flow_analysis"]
    res_none = flow(str(bowl_tif), "flow_direction")
    assert res_none["success"] is True
    assert res_none["meta"]["flat_routing"] == "none"
    assert res_none["statistics"]["sink_or_outlet_cells"] >= 1

    res_eps = flow(str(bowl_tif), "flow_direction", flat_routing="epsilon")
    assert res_eps["meta"]["flat_routing"] == "epsilon"
    assert res_eps["meta"]["filled_cell_count"] >= 1
    assert res_eps["scientific_evidence"]["parameters_applied"]["flat_routing"] == "epsilon"
    acc_none = flow(str(bowl_tif), "flow_accumulation")
    acc_eps = flow(str(bowl_tif), "flow_accumulation", flat_routing="epsilon")
    assert acc_none["success"] and acc_eps["success"]
    # Both products stay available under either routing mode.
    assert "max_accumulation" in acc_none["statistics"]
    assert "max_accumulation" in acc_eps["statistics"]
    with pytest.raises(ValueError, match="parameter_contract_violation"):
        flow(str(bowl_tif), "flow_direction", flat_routing="gable")


# ── 7. Registry / parity ──────────────────────────────────────────────


def test_v3_registry_and_parity_clean():
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    issues = get_algorithm_registry().validate()
    assert issues == []
    from app.services.gis_harness.registry_validation import (
        validate_algorithm_tool_parameter_parity,
    )

    parity = [
        issue for issue in validate_algorithm_tool_parameter_parity()
        if "terrain." in issue
    ]
    assert parity == []


# ── science-v3 审计修复回归（水文组合链 P0）─────────────────────────────
def test_depression_fill_persists_dem_for_downstream_hydrology(
        bowl_tif, terrain_tools, tmp_path):
    """fill(persist_filled=True) → 填充面落盘 → D∞/D8 消费填充面无洼地哨兵。

    science-v3 审计 F2/P0：depression_fill 此前不持久化填充面，
    「fill(epsilon)→D∞」组合在工具层不可执行。
    """
    call = terrain_tools._tools
    out = call["depression_fill"](
        str(bowl_tif), epsilon=0.01, persist_filled=True)
    assert out["success"] is True
    filled_path = out["filled_raster_path"]
    assert str(filled_path).endswith("_filled.tif")

    import rasterio
    with rasterio.open(str(filled_path)) as src:
        filled = src.read(1)
    # 填充面 ≥ 原 DEM 且内部洼地已被填平（epsilon 抬升后单调可排）
    dinf, _ = tlib.dinf_flow_direction(filled, 10.0, cell_size_x=10.0)
    valid = dinf["valid"]
    no_flow = valid & (dinf["angle"] == tlib._DINF_NO_FLOW)
    # 填充后内部无平地/洼地哨兵；残余 no-flow 只能是栅格边界出口
    # （边界=排水口的既定语义）。
    rr, cc = np.nonzero(no_flow)
    assert all(r in (0, filled.shape[0] - 1) or c in (0, filled.shape[1] - 1)
               for r, c in zip(rr, cc))

    # 默认行为不变：不传 persist_filled 不落盘
    out2 = call["depression_fill"](str(bowl_tif), epsilon=0.01)
    assert "filled_raster_path" not in out2
