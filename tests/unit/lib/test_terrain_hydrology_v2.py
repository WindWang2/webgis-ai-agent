"""Terrain hydrology V2 conformance tests (Foundation V2 · A5).

Hand-computed goldens for the terrain hydrology extension
(app/lib/geo_analysis/terrain.py) and its thin tool wiring
(app/tools/terrain_analysis.py):

- sink_fill: single pit filled to the spill elevation exactly
  (volume 5.0, one lifted cell); epsilon variant drains monotonically
- D-infinity (Tarboton 1997): plane z = -x + y/2 -> exact downslope angle
  atan2(-0.5, 1) and magnitude sqrt(1.25); bowl pit -> -1.0 sentinel;
  accumulation sum equals the D8 total on the symmetric bowl
- flow_length: straight channel -> downstream = (n-1) * cell exactly
- Strahler: hand-built binary confluence -> 1+1 = 2 with exact distribution
- watershed morphometry: straight-channel basin -> area/perimeter/basin
  length/form factor/elongation/relief/drainage density all hand-computed
- TWI/SPI: uniform slope + uniform accumulation -> constant goldens;
  tan(beta) floor clamps flat TWI at ln(SCA / 1e-6)
- LS: McCool m-table boundaries (0.5/1/3/5/10 %) exact; desmet_govers on a
  uniform slope vs the hand formula at 1e-10
- adversarial: all-nodata raises NoValidObservations, NaN excluded, 1xN and
  non-2D rejected, scale guards fire BEFORE allocation (monkeypatched
  allocators), double-run determinism (dict equality)
- tools: evidence blocks with backend_selection diagnostics on a real GeoTIFF
- registry: algorithm registry + tool-parameter parity validators clean
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


# ── fixtures ──────────────────────────────────────────────────────────


def _pit_dem() -> np.ndarray:
    """Flat 7x7 with one pit (-5) at (3, 3); spill elevation is 0."""
    z = np.zeros((7, 7))
    z[3, 3] = -5.0
    return z


def _plane_dem(n: int = 9) -> np.ndarray:
    """z = -x + y/2 (west/south high): downslope bearing east-north-east."""
    r, c = np.mgrid[0:n, 0:n].astype(float)
    return -c + 0.5 * (n - 1 - r)


def _bowl(n: int = 9) -> np.ndarray:
    r, c = np.mgrid[0:n, 0:n].astype(float)
    ctr = (n - 1) / 2.0
    return (r - ctr) ** 2 + (c - ctr) ** 2


# ── 1. Priority-Flood sink fill ───────────────────────────────────────


def test_fill_depressions_single_pit_spill_elevation_exact():
    filled, meta = tlib.fill_depressions(_pit_dem(), 2.0)
    # The pit fills exactly to the spill elevation (flat 0.0 surface).
    assert float(filled[3, 3]) == 0.0
    assert float(filled.max()) == 0.0 and float(filled.min()) == 0.0
    assert meta["algorithm"] == "terrain.sink_fill"
    assert meta["filled_cell_count"] == 1
    assert meta["filled_volume"] == pytest.approx(5.0)
    assert meta["max_fill_depth"] == pytest.approx(5.0)
    # Flat DEM: nothing to fill.
    flat_filled, flat_meta = tlib.fill_depressions(np.zeros((6, 6)), 1.0)
    assert flat_meta["filled_cell_count"] == 0
    assert np.array_equal(flat_filled, np.zeros((6, 6)))
    # epsilon must be non-negative.
    with pytest.raises(ValueError, match="epsilon"):
        tlib.fill_depressions(_pit_dem(), 1.0, epsilon=-0.5)


def test_fill_depressions_volume_and_epsilon_monotone():
    z = _pit_dem()
    filled, meta = tlib.fill_depressions(z, 2.0, epsilon=0.5)
    # Volume bookkeeping: sum of (filled - z) over lifted cells.
    lift = filled - z
    assert meta["filled_volume"] == pytest.approx(float(lift.sum()))
    assert meta["filled_cell_count"] == int(np.count_nonzero(lift > 0.0))
    # Epsilon surface is monotonically drainable: every interior cell has a
    # strictly lower in-grid neighbour (its priority-flood parent, epsilon
    # below it); only the boundary ring drains off-grid (d8 code 0 = outlet).
    d8, _ = tlib.d8_flow(filled, 2.0)
    interior = np.zeros_like(d8["valid"])
    interior[1:-1, 1:-1] = True
    core_sinks = d8["valid"] & interior & (d8["direction"] == 0)
    assert int(core_sinks.sum()) == 0
    # Pure fill (epsilon=0) leaves the filled flat as interior flats/sinks
    # (documented honesty: no invented routing without epsilon).
    filled0, _ = tlib.fill_depressions(z, 2.0)
    d8_0, _ = tlib.d8_flow(filled0, 2.0)
    assert int((d8_0["valid"] & interior & (d8_0["direction"] == 0)).sum()) == 25
    # Determinism: meta dicts compare equal across runs.
    _, meta_again = tlib.fill_depressions(z, 2.0, epsilon=0.5)
    assert meta == meta_again


# ── 2. D-infinity flow (Tarboton 1997) ────────────────────────────────


def test_dinf_ramp_direction_angle_exact():
    # z = -x + y/2 (west high, south-of-centre high): downslope bearing is
    # (east, -north/2) -> angle atan2(-0.5, 1) mod 2*pi, magnitude sqrt(1.25).
    z = _plane_dem(9)
    res, meta = tlib.dinf_flow_direction(z, 1.0)
    expected_angle = math.atan2(-0.5, 1.0) % (2.0 * math.pi)
    expected_slope = math.sqrt(1.25)
    interior = res["angle"][1:-1, 1:-1]
    assert np.allclose(interior, expected_angle, atol=1e-10)
    assert np.allclose(res["slope"][1:-1, 1:-1], expected_slope, atol=1e-10)
    assert meta["method"].startswith("D-infinity")
    # East-facing ramp z = -x: downslope due east (angle 0), receivers = E.
    east = np.tile(np.arange(9, dtype=float), (9, 1))
    res_e, _ = tlib.dinf_flow_direction(-east, 1.0)
    assert res_e["angle"][4, 4] == 0.0
    assert res_e["receiver_a"][4 * 9 + 4] == 4 * 9 + 5  # east neighbour
    assert res_e["frac_a"][4 * 9 + 4] == 1.0
    # Anisotropic cells: z = -x with cx=1, cy=2 still drains due east.
    res_a, _ = tlib.dinf_flow_direction(-east, 2.0, cell_size_x=1.0)
    assert res_a["angle"][4, 4] == 0.0
    assert res_a["slope"][4, 4] == pytest.approx(1.0)


def test_dinf_pit_sentinel_and_accumulation_sum_matches_d8():
    bowl = _bowl(9)
    dinf, _ = tlib.dinf_flow_direction(bowl, 1.0)
    # Pit: no strictly positive descent -> angle sentinel -1, slope 0.
    assert dinf["angle"][4, 4] == tlib._DINF_NO_FLOW
    assert dinf["slope"][4, 4] == 0.0
    # All interior cells split flow between two receivers summing to 1.
    flowing = (dinf["receiver_a"] >= 0)
    assert np.allclose(
        dinf["frac_a"][flowing] + dinf["frac_b"][flowing], 1.0, atol=1e-12)

    d8, _ = tlib.d8_flow(bowl, 1.0)
    d8_acc, _ = tlib.flow_accumulation(d8)
    dinf_acc, acc_meta = tlib.dinf_flow_accumulation(dinf)
    # Property on the symmetric bowl: total accumulated flow is conserved
    # (same transfers, fractional vs single routing).
    assert float(dinf_acc.sum()) == pytest.approx(float(d8_acc.sum()))
    assert float(dinf_acc.max()) == pytest.approx(float(d8_acc.max()))
    assert "fractional" in acc_meta["counting_convention"]


# ── 3. Flow length ────────────────────────────────────────────────────


def test_flow_length_straight_channel_golden():
    # Two parallel straight channels (one per row): z = n - c drains due
    # east; cell (r, n-1) is the boundary outlet of each channel.
    n = 8
    z = np.tile(n - np.arange(n, dtype=float), (2, 1))
    d8, _ = tlib.d8_flow(z, 2.0)
    fl, meta = tlib.flow_length(d8, "downstream", 2.0)
    assert fl[0, 0] == (n - 1) * 2.0  # straight channel golden
    assert fl[1, 0] == (n - 1) * 2.0
    assert fl[0, n - 1] == 0.0        # outlet
    assert fl[1, n - 1] == 0.0
    assert meta["mode"] == "downstream"
    # Upstream (MAX convention): ridge source at each channel head.
    flu, meta_u = tlib.flow_length(d8, "upstream", 2.0)
    assert flu[0, 0] == 0.0 and flu[1, 0] == 0.0
    assert flu[0, n - 1] == (n - 1) * 2.0
    assert flu[1, n - 1] == (n - 1) * 2.0
    assert meta_u["length_convention"].startswith(
        "downstream: sum of metric cell-to-cell steps")
    # Mode guard.
    with pytest.raises(ValueError, match="mode"):
        tlib.flow_length(d8, "sideways", 2.0)


# ── 4. Streams + Strahler order ───────────────────────────────────────


def _hand_d8() -> dict:
    """Hand-built 3x5 network: two order-1 headwaters join -> order 2 trunk.

    Streams (row 1 is the trunk): (0,0)->(1,0)<-(2,0), (1,0)->(1,1)->...->
    (1,4). All non-stream receivers are -1; accum is supplied explicitly.
    """
    h, w = 3, 5
    receiver = np.full(h * w, -1, dtype=np.int64)
    stream_cells = [(0, 0), (2, 0), (1, 0), (1, 1), (1, 2), (1, 3), (1, 4)]
    for r, c in [(0, 0), (2, 0)]:
        receiver[r * w + c] = 1 * w + 0          # headwaters -> (1,0)
    for c in range(0, 4):
        receiver[1 * w + c] = 1 * w + (c + 1)    # trunk drains east
    z = np.full((h, w), 50.0)
    for i, (r, c) in enumerate(stream_cells):
        z[r, c] = 10.0 - i                        # strictly decreasing chain
    valid = np.ones((h, w), dtype=bool)
    direction = np.zeros((h, w), dtype=np.int16)
    return {
        "direction": direction,
        "receiver": receiver,
        "valid": valid,
        "dem": z,
    }


def test_strahler_confluence_orders_exact():
    d8 = _hand_d8()
    threshold = 3.0
    acc = np.zeros((3, 5))
    for r, c in [(0, 0), (2, 0), (1, 0), (1, 1), (1, 2), (1, 3), (1, 4)]:
        acc[r, c] = threshold
    order, meta = tlib.stream_order(d8, acc, threshold)
    # Binary confluence: 1 + 1 -> 2; trunk keeps order 2 to the outlet.
    assert int(order[0, 0]) == 1 and int(order[2, 0]) == 1
    assert int(order[1, 0]) == 2
    for c in range(1, 5):
        assert int(order[1, c]) == 2
    # Non-stream cells are 0.
    assert int(order[0, 2]) == 0
    assert meta["order_distribution"] == {"1": 2, "2": 5}
    assert meta["max_order"] == 2
    assert meta["stream_cells"] == 7

    # extract_streams: mask = accum >= threshold.
    mask, s_meta = tlib.extract_streams(acc, threshold)
    assert mask.sum() == 7
    assert s_meta["threshold"] == threshold
    with pytest.raises(ValueError, match="threshold"):
        tlib.stream_order(d8, acc, 0.5)
    with pytest.raises(ValueError, match="shape"):
        tlib.stream_order(d8, np.zeros((4, 4)), threshold)


# ── 5. Watershed morphometry ──────────────────────────────────────────


def test_watershed_morphometry_circular_basin_golden():
    # Straight-channel basin: a 3x7 grid where every cell drains east;
    # pouring at (1, 6) captures exactly row 1 (7 cells, one channel).
    n = 7
    z = np.tile((10.0 - np.arange(n, dtype=float)), (3, 1))
    d8, _ = tlib.d8_flow(z, 5.0)
    metrics, meta = tlib.watershed_morphometry(
        z, d8, (1.0, 6.0), 5.0, stream_threshold=1.0)
    assert metrics["cell_count"] == 7
    assert metrics["area_m2"] == pytest.approx(7 * 25.0)
    assert metrics["area_km2"] == pytest.approx(7 * 25.0 / 1e6)
    # Perimeter of the 1x7 strip: 14 vertical cx edges + 2 horizontal cy edges.
    assert metrics["perimeter_m"] == pytest.approx(14 * 5.0 + 2 * 5.0)
    # Basin length = MAX upstream flow length = 6 steps * 5 m (channel head).
    assert metrics["basin_length_m"] == pytest.approx(30.0)
    assert metrics["form_factor"] == pytest.approx(175.0 / 900.0, abs=1e-8)
    assert metrics["elongation_ratio"] == pytest.approx(
        2.0 * math.sqrt(175.0 / math.pi) / 30.0, abs=1e-8)
    # Relief: z from 10 (head) down to 4 (outlet).
    assert metrics["relief_m"] == pytest.approx(6.0)
    assert metrics["relief_ratio"] == pytest.approx(0.2)
    # Drainage density, hand-computed: with threshold=1 the head cell has
    # accumulation 0 and is not a stream -> 5 in-basin stream segments
    # of 5 m each = 25 m over 175 m2 -> 1000 * 25 / 175 km/km2.
    assert metrics["stream_length_m"] == pytest.approx(25.0)
    assert metrics["drainage_density_km_per_km2"] == pytest.approx(
        1000.0 * 25.0 / 175.0, abs=1e-8)
    assert meta["basin_length_convention"].startswith("max upstream flow length")
    # Without a threshold the density is honestly None.
    metrics0, meta0 = tlib.watershed_morphometry(z, d8, (1.0, 6.0), 5.0)
    assert metrics0["drainage_density_km_per_km2"] is None
    # Determinism.
    metrics_again, meta_again = tlib.watershed_morphometry(
        z, d8, (1.0, 6.0), 5.0, stream_threshold=1.0)
    assert metrics_again == metrics and meta_again == meta


# ── 6. TWI / SPI ──────────────────────────────────────────────────────


def test_twi_spi_uniform_golden_and_floor():
    slope = np.full((5, 5), 10.0)          # degrees
    accum = np.full((5, 5), 99.0)          # upstream cells (self excluded)
    sca = (99.0 + 1.0) * (2.0 * 2.0) / 2.0  # contour width = cell_size = 2
    twi, meta = tlib.topographic_wetness_index(slope, accum, 2.0)
    assert np.allclose(twi, math.log(sca / math.tan(math.radians(10.0))),
                       atol=1e-12)
    assert meta["sca_convention"].startswith("SCA = (accum + 1)")
    assert meta["tan_beta_floor"] == 1e-6
    # Flat slope: tan(beta) clamps at the floor -> documented upper bound.
    twi_flat, _ = tlib.topographic_wetness_index(np.zeros((5, 5)), accum, 2.0)
    assert np.allclose(twi_flat, math.log(sca / 1e-6), atol=1e-12)
    spi, _ = tlib.stream_power_index(slope, accum, 2.0)
    assert np.allclose(spi, sca * math.tan(math.radians(10.0)), atol=1e-12)
    # Aligned-shape guard.
    with pytest.raises(ValueError, match="aligned|shape|match"):
        tlib.topographic_wetness_index(slope, np.zeros((4, 4)), 2.0)


# ── 7. USLE LS factor ─────────────────────────────────────────────────


def test_ls_factor_mccool_table_and_desmet_govers_hand():
    slopes_pct = np.array([[0.5], [1.0], [3.0], [5.0], [10.0]])
    m_expected = [0.2, 0.3, 0.4, 0.5, 0.5]  # McCool 1987 table boundaries
    lam = 100.0
    ls, meta = tlib.ls_factor(slopes_pct, lam, 1.0)
    for i, pct in enumerate(slopes_pct.ravel()):
        theta = math.atan(pct / 100.0)
        expected = (lam / 22.13) ** m_expected[i] * (
            65.41 * math.sin(theta) ** 2 + 4.56 * math.sin(theta) + 0.065)
        assert ls[i, 0] == pytest.approx(expected, rel=1e-12)
    assert "mccool" in meta["formula"]
    assert "0.2" in meta["m_table"]

    # desmet_govers: uniform 5% slope, uniform accumulation, cell 2 m.
    slope = np.full((5, 5), 5.0)
    accum = np.full((5, 5), 99.0)
    sca = (99.0 + 1.0) * (2.0 * 2.0) / 2.0
    theta = math.atan(0.05)
    expected = (0.5 + 1.0) * (sca / 22.13) ** 0.5 * (
        math.sin(theta) / 0.0896) ** 1.3
    ls_dg, _ = tlib.ls_factor(slope, 1.0, 2.0, method="desmet_govers",
                              slope_units="percent", flow_accum=accum)
    assert np.allclose(ls_dg, expected, atol=1e-10)
    # desmet_govers without flow_accum is rejected.
    with pytest.raises(ValueError, match="flow_accum"):
        tlib.ls_factor(slope, 1.0, 2.0, method="desmet_govers")
    with pytest.raises(ValueError, match="method"):
        tlib.ls_factor(slope, 1.0, 2.0, method="renard")


# ── 8. Adversarial inputs & guards ───────────────────────────────────


def test_hydrology_nodata_adversarial_and_guards(monkeypatch):
    all_nodata = np.full((5, 5), -9999.0)
    with pytest.raises(NoValidObservations):
        tlib.fill_depressions(all_nodata, 1.0, nodata=-9999.0)
    with pytest.raises(NoValidObservations):
        tlib.dinf_flow_direction(all_nodata, 1.0, nodata=-9999.0)
    # 1xN / non-2D inputs are rejected by the prepare contract.
    with pytest.raises(NoValidObservations):
        tlib.fill_depressions(np.zeros((1, 8)), 1.0)
    with pytest.raises(NoValidObservations):
        tlib.dinf_flow_direction(np.zeros(6), 1.0)
    # 2x2 minimum is accepted (contract floor).
    filled2, meta2 = tlib.fill_depressions(np.array([[1.0, 0.0], [0.5, 0.2]]), 1.0)
    assert meta2["cells_valid"] == 4
    # NaN pit is excluded from filling (invalid cells never lift).
    z = _pit_dem()
    z[3, 3] = np.nan
    _, meta_nan = tlib.fill_depressions(z, 2.0)
    assert meta_nan["cells_valid"] == 48
    assert meta_nan["filled_cell_count"] == 0

    # Scale guards fire BEFORE any allocation: shrink the cell cap and
    # monkeypatch the allocators — any allocation attempt explodes.
    dem = np.zeros((5, 5))  # built before the allocator patch
    constant_dem = np.full((6, 6), 3.0)

    def _boom(*args, **kwargs):
        raise AssertionError("allocator reached before scale guard")

    monkeypatch.setattr(tlib, "MAX_HYDRO_CELLS", 16)
    for name in ("zeros", "empty", "full"):
        monkeypatch.setattr(tlib.np, name, _boom)
    with pytest.raises(ResourceScaleMismatch) as exc:
        tlib.fill_depressions(dem, 1.0)
    assert exc.value.estimated == "25 cells"
    assert exc.value.limit == "16 cells"
    with pytest.raises(ResourceScaleMismatch):
        tlib.dinf_flow_direction(dem, 1.0)
    # Ray-walk radius caps fire before allocation at any cap.
    with pytest.raises(ResourceScaleMismatch):
        tlib.terrain_openness(dem, 1.0, radius_cells=101)
    with pytest.raises(ResourceScaleMismatch):
        tlib.geomorphons(dem, 1.0, lookup_radius_cells=129)
    # Restore the allocators before the remaining functional assertions.
    monkeypatch.undo()
    # DegenerateData: constant surface has zero TPI spread.
    with pytest.raises(DegenerateData):
        tlib.landform_classification(constant_dem, 1.0)


# ── 9. Determinism ────────────────────────────────────────────────────


def test_hydrology_determinism_double_run():
    bowl = _bowl(11)
    f1, m1 = tlib.fill_depressions(bowl, 2.0, epsilon=0.1)
    f2, m2 = tlib.fill_depressions(bowl, 2.0, epsilon=0.1)
    assert m1 == m2 and np.array_equal(f1, f2)

    d1, dm1 = tlib.dinf_flow_direction(bowl, 2.0)
    d2, dm2 = tlib.dinf_flow_direction(bowl, 2.0)
    assert dm1 == dm2
    for key in ("angle", "slope", "receiver_a", "receiver_b", "frac_a", "frac_b"):
        assert np.array_equal(d1[key], d2[key]), key

    d8, _ = tlib.d8_flow(bowl, 2.0)
    acc, _ = tlib.flow_accumulation(d8)
    o1, om1 = tlib.stream_order(d8, acc, 5.0)
    o2, om2 = tlib.stream_order(d8, acc, 5.0)
    assert om1 == om2 and np.array_equal(o1, o2)

    m3, mm3 = tlib.watershed_morphometry(bowl, d8, (5.0, 5.0), 2.0,
                                         stream_threshold=4.0)
    m4, mm4 = tlib.watershed_morphometry(bowl, d8, (5.0, 5.0), 2.0,
                                         stream_threshold=4.0)
    assert m3 == m4 and mm3 == mm4


# ── 10. Tool wiring (thin wrappers + evidence) ────────────────────────


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
def bowl_tif(tmp_path):
    n = 21
    r, c = np.mgrid[0:n, 0:n].astype(float)
    ctr = (n - 1) / 2.0
    bowl = (r - ctr) ** 2 + (c - ctr) ** 2
    tf = (10.0, 0.0, 0.0, 0.0, -10.0, 10.0 * n)
    path = tmp_path / "bowl.tif"
    _write_tif(path, bowl, tf, crs="EPSG:32650")
    return path


@pytest.fixture()
def plane_tif(tmp_path):
    # z = col: monotone east-draining plane, no depressions at any epsilon.
    plane = np.tile(np.arange(21, dtype=float), (21, 1))
    tf = (10.0, 0.0, 0.0, 0.0, -10.0, 210.0)
    path = tmp_path / "plane.tif"
    _write_tif(path, plane, tf, crs="EPSG:32650")
    return path


def test_tool_hydrology_tools_evidence(bowl_tif, plane_tif, terrain_tools):
    n = 21
    call = terrain_tools._tools

    # Plane DEM drains off-grid: nothing to fill.
    res = call["depression_fill"](str(plane_tif))
    assert res["success"] is True
    assert res["filled_cell_count"] == 0
    # Paraboloid DEM: the interior below the lowest border exit (100) is one
    # closed depression -> the centre lifts 0 -> 100 (spill-level golden).
    res_bowl = call["depression_fill"](str(bowl_tif))
    assert res_bowl["success"] is True
    assert res_bowl["max_fill_depth"] == pytest.approx(100.0)
    assert res_bowl["filled_cell_count"] >= 1
    ev = res["scientific_evidence"]
    assert ev["algorithm"] == "terrain.sink_fill"
    assert ev["tool"] == "depression_fill"
    assert any(d["name"] == "backend_selection" for d in ev["diagnostics"])
    assert ev["reproducibility"]["deterministic"] is True
    assert any("Priority-Flood" in a or "Barnes" in a for a in ev["assumptions"])

    dinf = call["dinf_flow_analysis"](str(bowl_tif), "flow_accumulation")
    assert dinf["success"] is True
    assert dinf["statistics"]["max_accumulation"] == pytest.approx(n * n - 1)
    assert dinf["scientific_evidence"]["algorithm"] == "terrain.dinf_flow"
    dinf_dir = call["dinf_flow_analysis"](str(bowl_tif), "flow_direction")
    assert dinf_dir["statistics"]["no_flow_cells"] == 1  # the single pit
    assert dinf_dir["statistics"]["valid_cells"] == n * n

    flen = call["flow_length_analysis"](str(bowl_tif), "downstream")
    assert flen["success"] is True
    assert flen["max_length_m"] > 0.0
    assert flen["scientific_evidence"]["parameters_applied"]["mode"] == "downstream"

    streams = call["stream_network"](str(bowl_tif), 50.0, "stream_order")
    assert streams["success"] is True
    assert streams["scientific_evidence"]["algorithm"] == "terrain.strahler"
    assert streams["statistics"]["order_distribution"]
    mask = call["stream_network"](str(bowl_tif), 50.0, "stream_mask")
    assert mask["scientific_evidence"]["algorithm"] == "terrain.streams"

    centre = 10.0 * n / 2.0
    morph = call["watershed_morphometry_analysis"](
        str(bowl_tif), centre, centre, 50.0)
    assert morph["success"] is True
    assert morph["metrics"]["cell_count"] == n * n
    assert morph["scientific_evidence"]["algorithm"] == "terrain.morphometry"
    # Contract violation surfaces as parameter_contract_violation.
    with pytest.raises(ValueError, match="parameter_contract_violation"):
        call["stream_network"](str(bowl_tif), 0.0)


def test_tool_wetness_and_ls_tools_evidence(bowl_tif, terrain_tools):
    call = terrain_tools._tools
    twi = call["topographic_index"](str(bowl_tif), "twi")
    assert twi["success"] is True
    assert twi["scientific_evidence"]["algorithm"] == "terrain.twi"
    assert any("Horn" in t for t in twi["scientific_evidence"]["transformations_applied"])
    spi = call["topographic_index"](str(bowl_tif), "spi")
    assert spi["scientific_evidence"]["algorithm"] == "terrain.spi"

    ls = call["ls_factor_analysis"](str(bowl_tif), "mccool", 100.0)
    assert ls["success"] is True
    assert ls["scientific_evidence"]["algorithm"] == "terrain.ls_factor"
    ls_dg = call["ls_factor_analysis"](str(bowl_tif), "desmet_govers", 100.0)
    assert ls_dg["success"] is True
    with pytest.raises(ValueError, match="parameter_contract_violation"):
        call["ls_factor_analysis"](str(bowl_tif), "rusle", 100.0)


# ── 11. Registry / parity ─────────────────────────────────────────────


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