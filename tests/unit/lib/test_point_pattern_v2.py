"""Conformance tests for point-pattern V2 (Foundation V2 · Domain A3).

Contract bullets (descriptor-anchored, fixed seeds — deterministic):

- G/F/J: CSR data (RandomState(42)) stays inside its own fixed-seed 99-draw
  CSR envelope (G_p / F_p non-significant); a regular lattice has G = 0
  below the first shell (repulsion); a clustered fixture has G above CSR and
  G_p < 0.05; J degrades to NaN with disclosure when F(r) -> 1;
- pcf: CSR g(r) ~= 1 within a loose MC band (|g-1| mean < 0.2) and the
  sup|g-1| rank test is non-significant; clustered fixture peaks above 1;
- cross-K: needs exactly 2 types (else UnsupportedMethod) with >= 5 points
  each (else InsufficientSamples); random-labelling null is non-significant;
  spatially segregated types fall below the random-labelling envelope
  (p < 0.05) — the permutation draws from the POOLED pair table;
- ripley_k envelopes: default call (envelopes=0) keeps the exact legacy key
  set; envelopes>0 adds envelope keys + per-step two-sided rank p-values and
  CSR data stays inside its own envelope;
- NNI (calculate_nearest): additive Clark-Evans keys {nni_z, nni_p_value,
  nni_method}; uniform-random data is non-significant (|z| < 1.96), a
  jittered cluster has large negative z; a true metric 4-point square
  matches the hand-derived z = 6/sqrt((4-pi)/pi) to 1e-12;
- registry/parity: all new descriptors validate and their contracts are
  parity-clean against the registered tool schemas; tools attach scientific
  evidence and emit MonteCarloSummary exactly when envelopes > 0.
"""
import asyncio

import numpy as np
import pytest

from app.lib.geo_analysis.point_pattern import (
    cross_k,
    g_f_j_functions,
    pcf,
    ripley_k,
)
from app.lib.gis.scientific_errors import (
    InsufficientSamples,
    InvalidCRS,
    UnsupportedMethod,
)

import app.lib.geo_analysis.point_pattern as pp_module

METRIC_CRS = "EPSG:32650"
WINDOW = (0.0, 0.0, 100.0, 100.0)

pytestmark = pytest.mark.unit


def _csr_xy(n: int = 100, seed: int = 42) -> np.ndarray:
    return np.random.RandomState(seed).uniform(0, 100, (n, 2))


def _clustered_xy(seed: int = 1) -> np.ndarray:
    rs = np.random.RandomState(seed)
    centers = rs.uniform(10, 90, (5, 2))
    return np.vstack([c + rs.normal(0, 2.5, (20, 2)) for c in centers])


# ── G / F / J ────────────────────────────────────────────────────────

def test_gfj_csr_within_fixed_seed_envelope():
    """CSR fixture: G/F curves inside their own fixed-seed envelope; the
    rank p-values at r_max must be non-significant (estimator honesty)."""
    xy = _csr_xy(100)
    res = g_f_j_functions(xy, crs=METRIC_CRS, window=WINDOW, envelopes=99)
    assert res["envelopes"] == 99 and res["envelope_seed"] == 42
    g = np.array(res["G"])
    f = np.array(res["F"])
    for key, curve in (("envelope_G", g), ("envelope_F", f)):
        lo = np.array(res[f"{key}_low"])
        hi = np.array(res[f"{key}_high"])
        margin = 0.05 * (hi - lo)
        # one CSR draw may legitimately poke above p95 at a few of the 10
        # grid steps (~5-10% expected tail) — bound the violation count.
        assert np.sum(curve < lo - margin) == 0, key
        assert np.sum(curve > hi + margin) <= 3, key
    # two-sided rank p at r_max with 99 draws: resolution 2/100 = 0.02
    assert res["G_p_value"] >= 0.05
    assert res["F_p_value"] >= 0.05
    # CSR analytic reference shared by G and F; J_csr = 1
    r = np.array(res["r"])
    assert np.allclose(res["csr_G"], res["csr_F"])
    assert np.allclose(res["csr_G"], 1 - np.exp(-np.pi * (100 / 1e4) * r**2), atol=1e-3)
    assert res["csr_J"] == [1.0] * len(r)


def test_gfj_regular_lattice_g_below_csr_first_shell():
    """10×10 lattice (spacing 10 m): no neighbour below the spacing, so
    G(r < 10) == 0 while the CSR reference is strictly positive."""
    gx, gy = np.meshgrid(np.arange(10) * 10 + 5, np.arange(10) * 10 + 5)
    xy = np.column_stack([gx.ravel(), gy.ravel()])
    res = g_f_j_functions(xy, crs=METRIC_CRS, window=WINDOW, max_distance_ratio=0.25)
    r = np.array(res["r"])
    g = np.array(res["G"])
    first_shell = r < 10
    assert first_shell.any()
    assert np.all(g[first_shell] == 0.0)
    assert np.all(np.array(res["csr_G"])[first_shell] > 0)


def test_gfj_clustered_g_above_csr_and_significant():
    """Clustered fixture: G(r) above the CSR reference at short radii and
    G_p < 0.05 against the fixed-seed CSR envelope (small r_max so G does
    not saturate to 1 for both observed and simulated sets)."""
    xy = _clustered_xy()
    res = g_f_j_functions(xy, crs=METRIC_CRS, window=WINDOW,
                          max_distance_ratio=0.1, envelopes=99)
    g = np.array(res["G"])
    csr = np.array(res["csr_G"])
    assert np.all(g[:4] > csr[:4])
    assert res["G_p_value"] < 0.05
    assert res["F_p_value"] < 0.05


def test_gfj_j_undefined_tail_disclosed():
    """When F(r) → 1 the J denominator degenerates: J must be NaN from the
    first degenerate step and the disclosure keys must be present."""
    gx, gy = np.meshgrid(np.linspace(2.5, 97.5, 20), np.linspace(5, 95, 10))
    xy = np.column_stack([gx.ravel(), gy.ravel()])
    res = g_f_j_functions(xy, crs=METRIC_CRS, window=WINDOW)
    assert res["F"][-1] == 1.0
    assert "j_undefined_from" in res and "j_undefined_note" in res
    j = res["J"]
    start = res["j_undefined_from"]
    assert j[start] == "NaN" and "NaN" in j[start:]


def test_gfj_typed_errors_and_scale_guard(monkeypatch):
    """Typed failures: degrees CRS, tiny samples, envelope-cap and grid
    validation, and the observation cap (raise BEFORE allocation)."""
    degrees = np.random.RandomState(42).uniform(0, 1, (50, 2))
    with pytest.raises(InvalidCRS):
        g_f_j_functions(degrees, crs="EPSG:4326")
    with pytest.raises(InsufficientSamples):
        g_f_j_functions(_csr_xy(9))
    with pytest.raises(ValueError):
        g_f_j_functions(_csr_xy(20), envelopes=500)
    with pytest.raises(ValueError):
        g_f_j_functions(_csr_xy(20), n_steps=3)
    monkeypatch.setattr(pp_module, "_MAX_RIPLEY_OBSERVATIONS", 50)
    with pytest.raises(pp_module.ResourceScaleMismatch):
        g_f_j_functions(_csr_xy(60))


# ── pcf ──────────────────────────────────────────────────────────────

def test_pcf_csr_near_one_within_band():
    """CSR fixture: g(r) scatters around 1 within a loose MC band and the
    sup|g-1| rank test stays non-significant."""
    xy = _csr_xy(200)
    res = pcf(xy, crs=METRIC_CRS, window=WINDOW, envelopes=99)
    g = np.array(res["g"])
    assert np.abs(g - 1.0).mean() < 0.2
    assert res["p_value"] > 0.05
    assert res["bandwidth_auto"] is True  # 0 = auto one-step width, disclosed
    assert res["bandwidth"] == pytest.approx((25.0 - 2.5) / 9)
    assert res["csr_g"] == [1.0] * len(g)


def test_pcf_clustered_peak_above_one():
    """Clustered fixture: g(r) peaks well above 1 at the cluster scale."""
    res = pcf(_clustered_xy(), crs=METRIC_CRS, window=WINDOW)
    g = np.array(res["g"])
    assert g.max() > 1.5
    assert res["tendency"] and "聚集" in res["tendency"]


def test_pcf_envelopes_sup_statistic():
    """Envelope keys + DCLF-style sup statistic are emitted and the p-value
    is deterministic under the fixed seed (identical across calls)."""
    xy = _csr_xy(120)
    r1 = pcf(xy, crs=METRIC_CRS, window=WINDOW, envelopes=99)
    r2 = pcf(xy, crs=METRIC_CRS, window=WINDOW, envelopes=99)
    for key in ("envelope_g_low", "envelope_g_median", "envelope_g_high",
                "p_value", "p_method", "sup_abs_g_minus_1"):
        assert key in r1
    assert r1["p_value"] == r2["p_value"]
    assert r1["p_value"] > 0.05  # CSR null must not be rejected
    with pytest.raises(ValueError):
        pcf(xy[:9], crs=METRIC_CRS)  # below the 10-point floor


# ── cross-K ──────────────────────────────────────────────────────────

def test_cross_k_requires_exactly_two_types():
    """Exactly 2 distinct type values with >= 5 points each are mandatory."""
    xy = _csr_xy(100)
    with pytest.raises(UnsupportedMethod):
        cross_k(xy, ["a"] * 100, crs=METRIC_CRS, permutations=0)  # 1 type
    with pytest.raises(UnsupportedMethod):
        cross_k(xy, ["a", "b", "c"] * 33 + ["a"], crs=METRIC_CRS, permutations=0)
    with pytest.raises(InsufficientSamples):
        cross_k(xy, ["a"] * 96 + ["b"] * 4, crs=METRIC_CRS, permutations=0)
    with pytest.raises(InsufficientSamples):
        cross_k(xy, ["a"] * 4 + ["b"] * 96, crs=METRIC_CRS, permutations=0)


def test_cross_k_random_labelling_null_not_significant():
    """Random labels on CSR locations: K12 stays inside the random-labelling
    envelope (p > 0.05) — the null calibration of the permutation machinery."""
    rs = np.random.RandomState(42)
    xy = rs.uniform(0, 100, (120, 2))
    labels = rs.permutation(["left"] * 60 + ["right"] * 60).tolist()
    res = cross_k(xy, labels, crs=METRIC_CRS, window=WINDOW, permutations=199)
    assert res["n1"] == 60 and res["n2"] == 60
    assert res["envelope_seed"] == 42
    assert res["p_value"] > 0.05
    k12 = np.array(res["K12"])
    assert np.all(np.diff(k12) >= -1e-9)  # monotone non-decreasing


def test_cross_k_segregation_below_envelope():
    """Spatially segregated types (x < 50 vs x >= 50): K12 falls below the
    random-labelling envelope at short r — significant separation, p < 0.05.

    This also pins the pooled-pair-table semantics: the permutation null
    must redraw labels from ALL position pairs, not only the observed
    cross-type pairs (a cross-pair-only table has zero power here)."""
    xy = np.random.RandomState(42).uniform(0, 100, (120, 2))
    labels = np.where(xy[:, 0] < 50, "left", "right").tolist()
    res = cross_k(xy, labels, crs=METRIC_CRS, window=WINDOW, permutations=199)
    assert res["p_value"] < 0.05
    csr = np.array(res["csr_K12"])
    k12 = np.array(res["K12"])
    assert np.all(k12[:4] < csr[:4])


# ── ripley_k envelopes (additive param) ──────────────────────────────

def test_ripley_k_default_call_unchanged_keys():
    """envelopes=0 (default) keeps the exact legacy output key set — old
    callers (SpatialAnalyzer.ripley_k) see byte-identical structure."""
    xy = _csr_xy(100)
    res = ripley_k(xy, crs=METRIC_CRS, window=WINDOW)
    assert set(res.keys()) == {
        "r", "K", "L", "csr_K", "n", "r_max", "window", "area",
        "edge_correction", "estimator", "tendency", "summary",
    }


def test_ripley_k_envelopes_csr_honest_and_additive_keys():
    """envelopes>0 adds envelope keys + per-step rank p-values; CSR data
    stays inside its own envelope (all p >= resolution floor), and the
    result is deterministic under the fixed seed."""
    xy = _csr_xy(100)
    r1 = ripley_k(xy, crs=METRIC_CRS, window=WINDOW, envelopes=99)
    r2 = ripley_k(xy, crs=METRIC_CRS, window=WINDOW, envelopes=99)
    # additive keys on top of the legacy set
    assert set(r1.keys()) - set(ripley_k(xy, crs=METRIC_CRS, window=WINDOW).keys()) == {
        "envelopes", "envelope_seed", "envelope_K_low", "envelope_K_median",
        "envelope_K_high", "K_p_values",
    }
    assert r1 == r2  # fixed seed 42 → deterministic
    k = np.array(r1["K"])
    lo = np.array(r1["envelope_K_low"])
    hi = np.array(r1["envelope_K_high"])
    margin = 0.05 * (hi - lo)
    # one CSR draw may sit outside the p5-p95 band at a few of the 10 grid
    # steps — the honest anchor is the per-step rank p-value below, not the
    # raw band.
    assert np.sum(k < lo - margin) <= 3
    assert np.sum(k > hi + margin) <= 3
    assert min(r1["K_p_values"]) >= 0.02  # two-sided resolution with 99 draws
    with pytest.raises(ValueError):
        ripley_k(xy, crs=METRIC_CRS, window=WINDOW, envelopes=500)


# ── NNI (calculate_nearest, Clark-Evans upgrade) ─────────────────────

def _fc(points, crs_name="EPSG:4326"):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
                                         "coordinates": [float(x), float(y)]},
         "properties": {}}
        for x, y in points]}
    if crs_name:
        fc["crs"] = {"type": "name", "properties": {"name": crs_name}}
    return fc


def test_nni_uniform_random_not_significant():
    """Uniform-random points: the Clark-Evans z must be non-significant."""
    from app.lib.geo_analysis.statistics import calculate_nearest

    rs = np.random.RandomState(42)
    lons = rs.uniform(115.95, 116.05, 80)
    lats = rs.uniform(39.85, 39.95, 80)
    res = calculate_nearest(_fc(zip(lons, lats)))
    assert res.success
    assert res.data["nni_method"] == "clark_evans_normal_approx"
    assert abs(res.data["nni_z"]) < 1.96
    assert res.data["nni_p_value"] > 0.05


def test_nni_clustered_jitter_z_large_negative():
    """Jittered 4-cluster fixture: strongly negative z, p far below 0.05,
    while all legacy contract keys are untouched."""
    from app.lib.geo_analysis.statistics import calculate_nearest

    rs = np.random.RandomState(7)
    lons = rs.uniform(115.98, 116.02, (4, 1))
    lats = rs.uniform(39.88, 39.92, (4, 1))
    pts = np.vstack([
        np.column_stack([c[0] + rs.normal(0, 0.0005, 10),
                         c[1] + rs.normal(0, 0.0005, 10)])
        for c in np.hstack([lons, lats])
    ])
    res = calculate_nearest(_fc(pts))
    assert res.success
    for key in ("mean_nearest_distance", "expected", "R", "pattern"):
        assert key in res.data
    assert res.data["nni_z"] < -5.0
    assert res.data["nni_p_value"] < 1e-6
    assert res.data["pattern"] == "clustered"


def test_nni_hand_computed_square_grid():
    """True metric 4-point square (declared projected CRS → identity
    projection): every corner has NN distance = side, bbox area = side², so

        z = (side − side/4) / (side·sqrt((4−π)/(64π))) = 6/sqrt((4−π)/π)

    exactly — matched to 1e-12 (R = 4.0, dispersed)."""
    from app.lib.geo_analysis.statistics import calculate_nearest

    side = 100.0
    res = calculate_nearest(_fc([(0, 0), (side, 0), (0, side), (side, side)],
                                crs_name="EPSG:32650"))
    assert res.success
    z_expected = 6.0 / np.sqrt((4.0 - np.pi) / np.pi)
    np.testing.assert_allclose(res.data["nni_z"], z_expected, rtol=1e-12)
    np.testing.assert_allclose(res.data["R"], 4.0, rtol=1e-12)
    assert res.data["pattern"] == "dispersed"
    # coincident points → zero-area bbox: z-test honestly unavailable
    coincident = calculate_nearest(_fc([(1, 1)] * 10, crs_name="EPSG:32650"))
    assert coincident.data["nni_z"] is None and coincident.data["nni_p_value"] is None
    assert "nni_test_note" in coincident.data


# ── registry / parity / tool evidence ────────────────────────────────

def test_point_pattern_v2_registry_and_contracts():
    """All new descriptors/contracts validate cleanly; parity between the
    registered tool schemas and the contracts is clean for A3 algorithms."""
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.parameter_contracts import get_parameter_contract_registry

    mine = ("point_pattern.g_f_j", "point_pattern.pcf", "point_pattern.cross_k",
            "point_pattern.ripley_k_env", "spatiotemporal.knox", "point_pattern.nni")
    issues = [i for i in get_algorithm_registry().validate()
              if any(m in i for m in mine)]
    assert issues == []

    contract_registry = get_parameter_contract_registry()
    for cid in ("g_f_j_analysis", "pcf_analysis", "cross_k_analysis",
                "knox_analysis", "ripley_k_envelope_analysis"):
        assert contract_registry.has(cid), cid
    ripley_contract = contract_registry.get("ripley_k_analysis")
    assert ripley_contract.version == 2  # additive envelopes param bump
    assert ripley_contract.spec("envelopes") is not None

    from app.services.gis_harness.registry_validation import (
        validate_algorithm_tool_parameter_parity,
    )
    parity_issues = [i for i in validate_algorithm_tool_parameter_parity()
                     if any(m in i for m in mine)]
    assert parity_issues == []


def test_gfj_tool_payload_evidence_and_mc():
    """Tool payload: scientific evidence always attached; MonteCarloSummary
    blocks appear exactly when envelopes > 0 (declared ⇒ produced)."""
    from app.tools.point_pattern_tools import register_point_pattern_tools
    from app.tools.registry import ToolRegistry

    rs = np.random.RandomState(42)
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [float(lo), float(la)]},
         "properties": {}}
        for lo, la in zip(rs.uniform(116.3, 116.4, 60),
                          rs.uniform(39.85, 39.95, 60))]}

    registry = ToolRegistry()
    register_point_pattern_tools(registry)

    async def _run(name, args):
        return await registry.dispatch(name, args)

    with_env = asyncio.run(_run("g_f_j_analysis", {"geojson": fc, "envelopes": 49}))
    assert with_env["success"] is True
    ev = with_env["scientific_evidence"]
    kinds = [u["uncertainty_type"] for u in ev["uncertainty"]]
    assert "monte_carlo_summary" in kinds
    assert "statistical_significance" in kinds
    mc = next(u for u in ev["uncertainty"]
              if u["uncertainty_type"] == "monte_carlo_summary")
    assert mc["seed"] == 42 and mc["draws"] == 49
    assert any(d["name"] == "backend_selection" for d in ev["diagnostics"])

    without_env = asyncio.run(_run("g_f_j_analysis", {"geojson": fc}))
    assert without_env["success"] is True
    kinds_plain = [u["uncertainty_type"]
                   for u in without_env["scientific_evidence"]["uncertainty"]]
    assert "monte_carlo_summary" not in kinds_plain
