"""Conformance tests for the point-pattern module (Ripley K / quadrat χ²).

Science anchors, not internals:

- CSR fixture (RandomState(42), uniform in a 100×100 m window): K(r) stays
  inside a fixed-seed 99-replicate CSR simulation envelope of the same
  estimator — the edge-corrected estimator must be unbiased under the null;
- perfectly regular grid: K(r) < πr² below ~1.5 spacings (repulsion) and
  never wildly above CSR at any inspected radius;
- degrees input is rejected (InvalidCRS) — metric coordinates are a
  methodological requirement;
- all points in one quadrant of a fixed 2×2 window: p < 0.05 (reject CSR),
  while a CSR sample is non-significant;
- full determinism: identical payloads across runs.
"""
import json

import numpy as np
import pytest

from app.lib.geo_analysis.point_pattern import quadrat_test, ripley_k
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    InvalidCRS,
    ResourceScaleMismatch,
)

WINDOW = (0.0, 0.0, 100.0, 100.0)
METRIC_CRS = "EPSG:32650"


# ── Ripley's K ───────────────────────────────────────────────────────

def test_ripley_k_csr_within_simulation_envelope():
    """Observed K on a CSR sample must sit inside the CSR envelope of the
    same fixed-seed simulator (99 replicates) — the estimator's own null
    behaviour defines the tolerance band."""
    rs = np.random.RandomState(42)
    xy = rs.uniform(0, 100, (100, 2))
    obs = ripley_k(xy, crs=METRIC_CRS, n_steps=10, max_distance_ratio=0.25,
                   window=WINDOW)

    sim_rs = np.random.RandomState(7)  # independent of the sample seed
    ks = np.array([
        ripley_k(sim_rs.uniform(0, 100, (100, 2)), crs=METRIC_CRS,
                 n_steps=10, max_distance_ratio=0.25, window=WINDOW)["K"]
        for _ in range(99)
    ])
    lo = ks.min(axis=0)
    hi = ks.max(axis=0)

    k_obs = np.array(obs["K"])
    # small guard band: the observed draw is one more CSR sample, the
    # envelope is the min/max of 99 others — allow touching the edges.
    margin = 0.05 * (hi - lo)
    assert np.all(k_obs >= lo - margin), (k_obs, lo)
    assert np.all(k_obs <= hi + margin), (k_obs, hi)
    # L function and CSR reference are consistent with K
    assert np.allclose(obs["L"], np.sqrt(np.array(obs["K"]) / np.pi), atol=1e-3)
    assert np.allclose(obs["csr_K"], np.pi * np.array(obs["r"]) ** 2, atol=1e-2)


def test_ripley_k_regular_grid_below_csr():
    """10×10 regular lattice shows repulsion against the CSR reference.

    K̂ on a lattice is a step function: at exact shell radii (10, √200, …)
    an entire neighbour shell enters at once and may legitimately exceed
    the smooth πr² parabola. The honest anchors are therefore:

    1. below the 10 m spacing K(r) == 0 (perfect repulsion, far below CSR);
    2. between shells (≈11.6–14.1 m, past the axis-shell crossover) the
       plateau K stays below the grown parabola;
    3. the estimator never explodes above CSR (sanity bound).
    """
    gx, gy = np.meshgrid(np.arange(10) * 10 + 5, np.arange(10) * 10 + 5)
    xy = np.column_stack([gx.ravel(), gy.ravel()])
    res = ripley_k(xy, crs=METRIC_CRS, window=WINDOW)
    k = np.array(res["K"])
    csr = np.array(res["csr_K"])
    r = np.array(res["r"])
    # (1) no pair at all below the spacing — the extreme regular case
    assert np.all(k[r < 10] == 0.0)
    # (2) between the first and second shells: K < πr²
    between = (r > 11.7) & (r < 14.1)
    assert between.any()
    assert np.all(k[between] < csr[between])
    # hand-checked first-shell plateau: axis pairs only, edge-corrected
    # Σ1/w ≈ 419 → K ≈ 10000/9900·419 ≈ 423 m²
    assert k[3] == pytest.approx(423.4, abs=1.0)
    # (3) sanity bound across the whole grid (shell jumps stay modest)
    assert np.all(k <= csr * 1.35)


def test_ripley_k_clustered_above_csr():
    rs = np.random.RandomState(1)
    centers = rs.uniform(10, 90, (5, 2))
    xy = np.vstack([c + rs.normal(0, 2.5, (20, 2)) for c in centers])
    res = ripley_k(xy, crs=METRIC_CRS, window=WINDOW)
    k = np.array(res["K"])
    csr = np.array(res["csr_K"])
    assert np.all(k[:5] > csr[:5])  # clustered at short radii


def test_ripley_k_rejects_geographic_degrees():
    xy = np.random.RandomState(42).uniform(0, 1, (50, 2))  # degree-scale coords
    with pytest.raises(InvalidCRS) as excinfo:
        ripley_k(xy, crs="EPSG:4326")
    assert "reproject" in excinfo.value.correction_hint
    # small samples and degenerate windows are typed failures too
    with pytest.raises(InsufficientSamples):
        ripley_k(xy[:5], crs=METRIC_CRS)
    with pytest.raises(DegenerateData):
        ripley_k(np.zeros((20, 2)), crs=METRIC_CRS)
    with pytest.raises(ResourceScaleMismatch):
        ripley_k(np.zeros((20001, 2)) + np.arange(20001)[:, None] * 0.001,
                 crs=METRIC_CRS, window=(0, 0, 100, 100))


def test_ripley_k_deterministic_and_bounded():
    rs = np.random.RandomState(42)
    xy = rs.uniform(0, 100, (100, 2))
    r1 = ripley_k(xy, crs=METRIC_CRS, window=WINDOW)
    r2 = ripley_k(xy, crs=METRIC_CRS, window=WINDOW)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    assert len(r1["r"]) == 10 and len(r1["K"]) == 10  # bounded series
    # parameter bounds are enforced
    with pytest.raises(ValueError, match="n_steps"):
        ripley_k(xy, crs=METRIC_CRS, n_steps=64)
    with pytest.raises(ValueError, match="max_distance_ratio"):
        ripley_k(xy, crs=METRIC_CRS, max_distance_ratio=0.9)


# ── Quadrat χ² test ──────────────────────────────────────────────────

def test_quadrat_single_quadrant_rejects_csr():
    """All points in one quadrant of a FIXED 2×2 window → p<0.05, clustered.

    The fixed window matters: with a data-derived bbox the concentration
    would be self-normalized away.
    """
    rs = np.random.RandomState(3)
    xy = rs.uniform(0, 50, (40, 2))
    res = quadrat_test(xy, crs=METRIC_CRS, grid_rows=2, grid_cols=2,
                       window=WINDOW)
    assert res["p_value"] < 0.05
    assert res["pattern"] == "clustered"
    assert res["variance_mean_ratio"] > 1.0
    # closed-form anchor: counts [40, 0, 0, 0], E = 10 → χ² = 120, df = 3
    assert res["chi2"] == pytest.approx(120.0)
    assert res["df"] == 3


def test_quadrat_csr_not_significant():
    rs = np.random.RandomState(42)
    xy = rs.uniform(0, 100, (100, 2))
    res = quadrat_test(xy, crs=METRIC_CRS, window=WINDOW)
    assert res["p_value"] >= 0.05
    assert res["pattern"] == "random"
    # determinism + typed failures
    res2 = quadrat_test(xy, crs=METRIC_CRS, window=WINDOW)
    assert json.dumps(res, sort_keys=True) == json.dumps(res2, sort_keys=True)
    with pytest.raises(InvalidCRS):
        quadrat_test(xy, crs="EPSG:4326")
    with pytest.raises(InsufficientSamples):
        quadrat_test(xy[:3], crs=METRIC_CRS)
    with pytest.raises(ValueError, match="grid_rows"):
        quadrat_test(xy, crs=METRIC_CRS, grid_rows=11)
    # points outside a fixed window are refused, not silently dropped
    with pytest.raises(ValueError, match="outside"):
        quadrat_test(np.array([[150.0, 50.0]] * 10), crs=METRIC_CRS,
                     window=WINDOW)
