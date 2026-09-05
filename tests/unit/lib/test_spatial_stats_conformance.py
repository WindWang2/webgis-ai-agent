"""Conformance tests for the global spatial-autocorrelation family (VNext).

These verify the *science* against trusted references — textbook closed-form
anchors and the esda/libpysal reference implementation with identical
weights — not implementation internals:

- Moran: 2×2 checkerboard under rook weights → I = −1 exactly; ≥20-cell
  polygon fixture vs esda.Moran (same Queen row-standardized weights):
  statistic to 1e-10, permutation p within Monte-Carlo tolerance;
- Geary: checkerboard → C = 2 − 2/n (→2); vs esda.Geary to 1e-10 including
  the normality-assumption analytic variance;
- General G: clustered-high fixture significant (p<0.05), CSR (seed 42)
  non-significant, statistic vs esda.G to 1e-10;
- typed scientific errors for degenerate/empty/missing-field inputs;
- determinism: identical payloads on repeated runs; p ∈ [0, 1].
"""
import json

import numpy as np
import pytest

from app.lib.geo_analysis.statistics import (
    geary_c_narrated,
    general_g_narrated,
    moran_i_narrated,
)
from app.lib.geo_processor.core import to_utm_gdf
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    MissingRequiredField,
    NoValidObservations,
    UnsupportedMethod,
)

esda = pytest.importorskip("esda")
libpysal = pytest.importorskip("libpysal")


# ── fixtures ─────────────────────────────────────────────────────────

def _grid_fc(nrows, ncols, cell_deg=0.01, lon0=116.0, lat0=39.0, val_fn=None):
    """Polygon-grid FeatureCollection with *identical* shared vertices.

    Naive per-cell edge arithmetic differs in the last ULP, which breaks
    exact vertex-matching contiguity (queen/rook) — build cells from one
    shared edge-coordinate array.
    """
    xs = [lon0 + i * cell_deg for i in range(ncols + 1)]
    ys = [lat0 + j * cell_deg for j in range(nrows + 1)]
    feats = []
    for r in range(nrows):
        for c in range(ncols):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [xs[c], ys[r]], [xs[c + 1], ys[r]],
                    [xs[c + 1], ys[r + 1]], [xs[c], ys[r + 1]], [xs[c], ys[r]],
                ]]},
                "properties": {"val": float(val_fn(r, c)) if val_fn else 0.0},
            })
    return {"type": "FeatureCollection", "features": feats}


def _points_fc(pts, field="val"):
    feats = [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [xy[0], xy[1]]},
         "properties": {field: float(v)}}
        for xy, v in pts
    ]
    return {"type": "FeatureCollection", "features": feats}


def _gradient_fc(rows=5, cols=4):
    """≥20 cells with a smooth spatial gradient (strong positive autocorr)."""
    return _grid_fc(rows, cols, val_fn=lambda r, c: r * cols + c)


def _utm_gdf_and_values(fc, field="val"):
    gdf, _ = to_utm_gdf(fc)
    gdf = gdf.reset_index(drop=True)
    return gdf, gdf[field].to_numpy(dtype=float)


# ── Moran ────────────────────────────────────────────────────────────

def test_moran_checkerboard_rook_is_minus_one():
    """2×2 checkerboard + rook weights → perfect negative autocorrelation.

    Small-n honesty note: with 4 cells the values {1,1,0,0} admit only 6
    distinct arrangements and 1/3 of the permutation null sits AT the
    observed extreme — so even a perfect checkerboard cannot reach p<0.05
    at n=4. The implementation reports that honestly (p ≈ 0.34, pattern
    "random") instead of fabricating significance.
    """
    fc = _grid_fc(2, 2, val_fn=lambda r, c: float((r + c) % 2))
    res = moran_i_narrated(fc, "val", weights_scheme="rook")
    assert res.success, res.summary
    assert res.data["moran_i"] == pytest.approx(-1.0, abs=1e-12)
    assert res.data["weights"]["scheme"] == "rook"
    # documented small-n behaviour: permutation p stays ≈ (1/3 mass + 1)/100
    assert res.data["p_value"] > 0.05
    assert res.data["pattern"] == "random"

    # Larger checkerboard (6×6) keeps the closed-form anchor and DOES resolve
    rook_fc = _grid_fc(6, 6, val_fn=lambda r, c: float((r + c) % 2))
    res6 = moran_i_narrated(rook_fc, "val", weights_scheme="rook", permutations=499)
    assert res6.success
    assert res6.data["moran_i"] < -0.9
    assert res6.data["p_value"] < 0.05
    assert res6.data["pattern"] == "dispersion"


def test_moran_matches_esda_queen_weights():
    fc = _gradient_fc(5, 4)
    res = moran_i_narrated(fc, "val", weights_scheme="queen", permutations=499)
    assert res.success, res.summary

    gdf, values = _utm_gdf_and_values(fc)
    w = libpysal.weights.Queen.from_dataframe(gdf, use_index=False)
    w.transform = "r"
    np.random.seed(42)
    ref = esda.Moran(values, w, permutations=499, two_tailed=True)

    assert res.data["moran_i"] == pytest.approx(ref.I, abs=1e-10)
    assert res.data["expected_i"] == pytest.approx(ref.EI, abs=1e-12)
    # Both are permutation p-values of the same statistic under the same
    # weights: Monte-Carlo agreement, not bitwise identity (different RNGs).
    assert res.data["p_value"] < 0.05 and ref.p_sim < 0.05
    assert abs(res.data["p_value"] - float(ref.p_sim)) <= 0.05


def test_moran_deterministic_and_bounded_pvalue():
    fc = _gradient_fc(5, 4)
    r1 = moran_i_narrated(fc, "val", permutations=999)
    r2 = moran_i_narrated(fc, "val", permutations=999)
    assert r1.success and r2.success
    # identical payload dicts (permutation stream is fixed-seeded)
    assert json.dumps(r1.data, sort_keys=True) == json.dumps(r2.data, sort_keys=True)
    assert 0.0 <= r1.data["p_value"] <= 1.0
    # bounded uncertainty block is attached and typed
    unc = r1.data["uncertainty"]
    assert unc["uncertainty_type"] == "statistical_significance"
    assert unc["method"] == "permutation"
    assert unc["permutations"] == 999
    assert unc["alternative"] == "two-sided"


def test_moran_parameter_contract_enforced():
    fc = _points_fc([((116.0 + i * 0.001, 39.0), float(i)) for i in range(8)])
    # permutations is a closed choice set
    with pytest.raises(ValueError, match="permutations"):
        moran_i_narrated(fc, "val", permutations=101)
    # unknown scheme rejected
    with pytest.raises(ValueError, match="weights_scheme"):
        moran_i_narrated(fc, "val", weights_scheme="gaussian")
    # queen/rook on points is an honest UnsupportedMethod, not a silent fallback
    with pytest.raises(UnsupportedMethod) as excinfo:
        moran_i_narrated(fc, "val", weights_scheme="rook")
    assert excinfo.value.correction_hint


def test_moran_distance_band_scheme_runs():
    fc = _gradient_fc(5, 4)
    res = moran_i_narrated(fc, "val", weights_scheme="distance_band")
    assert res.success, res.summary
    assert res.data["weights"]["scheme"] == "distance_band"
    assert res.data["weights"]["threshold_m"] > 0


# ── Geary's C ────────────────────────────────────────────────────────

def test_geary_checkerboard_c_expected_value():
    """6×6 checkerboard + rook weights → C = 2 − 2/n (≈2, the classic cap)."""
    n = 36
    fc = _grid_fc(6, 6, val_fn=lambda r, c: float((r + c) % 2))
    res = geary_c_narrated(fc, "val", weights_scheme="rook")
    assert res.success, res.summary
    assert res.data["gearys_c"] == pytest.approx(2.0 - 2.0 / n, abs=1e-12)
    assert res.data["gearys_c"] == pytest.approx(2.0, abs=0.06)  # "≈ 2"
    assert res.data["pattern"] == "dispersion"


def test_geary_matches_esda_queen_weights():
    fc = _gradient_fc(5, 4)
    res = geary_c_narrated(fc, "val", weights_scheme="queen",
                           permutations=499, analytic_variance=True)
    assert res.success, res.summary

    gdf, values = _utm_gdf_and_values(fc)
    w = libpysal.weights.Queen.from_dataframe(gdf, use_index=False)
    np.random.seed(42)
    ref = esda.Geary(values, w, permutations=499)

    assert res.data["gearys_c"] == pytest.approx(ref.C, abs=1e-10)
    # analytic variance under normality (Cliff-Ord) matches esda's VC_norm
    analytic = res.data["analytic_variance"]
    assert analytic["variance_norm"] == pytest.approx(ref.VC_norm, rel=1e-9)
    assert analytic["z_norm"] == pytest.approx(ref.z_norm, rel=1e-9)
    # permutation agreement (same weights, different RNG streams)
    assert res.data["p_value"] < 0.05 and ref.p_sim < 0.05


def test_geary_constant_and_empty_inputs_typed_errors():
    pts = [((116.0 + i * 0.001, 39.0), 5.0) for i in range(8)]
    # constant field → degenerate (zero variance)
    with pytest.raises(DegenerateData):
        geary_c_narrated(_points_fc(pts), "val")
    # empty input → typed, no silent success
    with pytest.raises(NoValidObservations):
        geary_c_narrated({"type": "FeatureCollection", "features": []}, "val")
    # missing field
    with pytest.raises(MissingRequiredField):
        geary_c_narrated(_points_fc(pts), "wrong_field")
    # small-n → insufficient samples
    with pytest.raises(InsufficientSamples):
        geary_c_narrated(_points_fc(pts[:2]), "val")


# ── Getis-Ord General G ──────────────────────────────────────────────

def _clustered_high_pts():
    """High-value points concentrated in one tight cluster + low-value ring."""
    rng = np.random.default_rng(9)
    pts = []
    for _ in range(18):  # high cluster
        pts.append(((116.39 + rng.normal(0, 3e-4), 39.90 + rng.normal(0, 3e-4)),
                    float(rng.uniform(80, 100))))
    for _ in range(18):  # low values spread out
        ang = rng.uniform(0, 2 * np.pi)
        pts.append(((116.39 + 0.05 * np.cos(ang) * 2, 39.90 + 0.05 * np.sin(ang) * 2),
                    float(rng.uniform(0, 5))))
    return _points_fc(pts)


def test_general_g_clustered_high_significant():
    res = general_g_narrated(_clustered_high_pts(), "val", permutations=499)
    assert res.success, res.summary
    assert res.data["general_g"] > res.data["expected_g"]
    assert res.data["p_value"] < 0.05
    assert res.data["pattern"] == "clustered_high"
    # the clustered-low reading must be disclosed as low-value clustering
    assert "clustered-low" in res.summary or "clustered-high" in res.summary


def test_general_g_csr_not_significant():
    rs = np.random.RandomState(42)
    pts = [((116.3 + rs.uniform(0, 0.1), 39.9 + rs.uniform(0, 0.1)),
            float(rs.uniform(1, 100))) for _ in range(40)]
    res = general_g_narrated(_points_fc(pts), "val", permutations=499)
    assert res.success, res.summary
    assert res.data["p_value"] >= 0.05
    assert res.data["pattern"] == "random"
    # determinism: same fixed permutation seed → identical payload
    res2 = general_g_narrated(_points_fc(pts), "val", permutations=499)
    assert json.dumps(res.data, sort_keys=True) == json.dumps(res2.data, sort_keys=True)


def test_general_g_matches_esda_and_rejects_negative():
    rs = np.random.RandomState(7)
    pts = [((116.3 + rs.uniform(0, 0.05), 39.9 + rs.uniform(0, 0.05)),
            float(rs.uniform(1, 50))) for _ in range(30)]
    fc = _points_fc(pts)
    band = 800.0  # metres — explicit for both implementations
    res = general_g_narrated(fc, "val", distance_band=band, permutations=499)
    assert res.success, res.summary

    gdf, values = _utm_gdf_and_values(fc)
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    w = libpysal.weights.DistanceBand(coords, threshold=band, binary=True,
                                      silence_warnings=True)
    np.random.seed(42)
    ref = esda.G(values, w, permutations=499)
    assert res.data["general_g"] == pytest.approx(ref.G, abs=1e-10)
    assert res.data["expected_g"] == pytest.approx(ref.EG, abs=1e-12)

    # signed values are an honest method mismatch, not a silent computation
    neg = _points_fc([((116.0 + i * 0.001, 39.0), float(i - 4)) for i in range(8)])
    with pytest.raises(UnsupportedMethod) as excinfo:
        general_g_narrated(neg, "val")
    assert excinfo.value.correction_hint
