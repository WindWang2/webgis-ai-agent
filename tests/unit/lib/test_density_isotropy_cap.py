"""Regression tests for #384: KDE meter bandwidth is isotropic, both KDE
tools share one kernel/bandwidth logic, and evaluation input is capped.

The old code scaled the *data covariance* by a scalar factor, so a requested
isotropic meter bandwidth turned into an elliptical kernel shaped like the
point cloud (a 1000 m request on a ~8.5 km × 1 km corridor became
~1819 m × 184 m), and ``kde_surface`` / ``kde_contours`` used different std
statistics (per-axis mean vs pooled). The evaluation also had no input-point
cap: gaussian_kde costs O(n_points × n_cells), so a 1M-point input on the
100k-cell grid (~1e11 evaluations) never returned.
"""
import numpy as np
import pytest
from scipy.stats import multivariate_normal

from app.lib.geo_analysis import density as density_mod
from app.lib.geo_analysis.density import (
    _cap_kde_points,
    _fit_kde,
    kde_contours,
    kde_surface,
)
from app.lib.geo_processor.core import to_utm_gdf


def _anisotropic_fc(n=400, seed=7):
    """Corridor-shaped cloud (WGS84): sigma_x ~8.5 km, sigma_y ~1 km.

    Points are drawn directly in degrees around (116.4, 39.9) with spans
    scaled to meters (1 deg lon ~85 km, 1 deg lat ~111 km at this latitude);
    the tools reproject to UTM where the anisotropy is preserved.
    """
    rng = np.random.default_rng(seed)
    xs = 116.40 + rng.normal(0, 0.1, n)   # ~8.5 km per 0.1 deg lon
    ys = 39.90 + rng.normal(0, 0.009, n)  # ~1.0 km per 0.009 deg lat
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(x), float(y)]},
                "properties": {"v": float(i)},
            }
            for i, (x, y) in enumerate(zip(xs, ys))
        ],
    }


def _utm_coords(fc):
    gdf, _ = to_utm_gdf(fc)
    return np.column_stack((gdf.geometry.x.values, gdf.geometry.y.values))


# ── isotropic meter bandwidth ───────────────────────────────────────────────


def test_fixture_is_anisotropic():
    """The test cloud must genuinely be corridor-shaped, or the isotropy
    assertions below prove nothing."""
    stds = _utm_coords(_anisotropic_fc()).std(axis=0)
    assert stds.max() / stds.min() > 5


def test_explicit_bandwidth_kernel_isotropic_on_anisotropic_cloud():
    """A requested 1000 m bandwidth must yield a strictly isotropic kernel of
    1000 m on BOTH axes even for an anisotropic cloud (old behavior:
    ~1819 m × 184 m)."""
    coords = _utm_coords(_anisotropic_fc())
    h = 1000.0
    kde, bw, _clamped = _fit_kde(coords.T, h)
    assert bw == pytest.approx(h)
    # The kernel Cholesky factor is exactly h·I -> covariance h²·I on both
    # axes, independent of the cloud's covariance.
    assert np.allclose(kde.cho_cov, h * np.eye(2), atol=1e-9)
    # Behavioral check: the evaluated density equals the analytic isotropic
    # Gaussian mixture (N(x_i, h²I)) at probe offsets along both axes.
    center = coords.mean(axis=0)
    probes = center + np.array([
        [0, 0], [h, 0], [-h, 0], [0, h], [0, -h],
        [2 * h, 0], [0, 2 * h], [h, h], [-h, h],
    ])
    expected = np.mean(
        [multivariate_normal.pdf(probes, mean=p, cov=h ** 2 * np.eye(2)) for p in coords],
        axis=0,
    )
    assert np.allclose(kde(probes.T), expected, rtol=1e-9, atol=1e-15)


def test_auto_bandwidth_kernel_isotropic():
    """Scott auto mode also produces an isotropic kernel (std = the reported
    effective bandwidth), not a covariance-shaped ellipse."""
    coords = _utm_coords(_anisotropic_fc())
    kde, bw, _clamped = _fit_kde(coords.T, 0)
    assert bw > 0
    assert np.allclose(kde.cho_cov, bw * np.eye(2), atol=1e-9)


def test_both_tools_share_kernel_bandwidth_logic(monkeypatch):
    """kde_surface and kde_contours must route through the same fitter with
    the same data and bandwidth — one std/bandwidth logic for both tools."""
    fc = _anisotropic_fc()
    calls = []
    real_fit = density_mod._fit_kde

    def spy(kde_data, bandwidth, weights=None):
        calls.append((kde_data.copy(), bandwidth, weights))
        return real_fit(kde_data, bandwidth, weights)

    monkeypatch.setattr(density_mod, "_fit_kde", spy)
    res_s = kde_surface(fc, bandwidth=1000, cell_size=5000)
    res_c = kde_contours(fc, bandwidth=1000, levels=6)
    assert res_s.success and res_c.success
    assert len(calls) == 2
    data_s, bw_s, w_s = calls[0]
    data_c, bw_c, w_c = calls[1]
    assert bw_s == bw_c == 1000
    assert w_s is None and w_c is None
    assert np.array_equal(data_s, data_c)
    assert res_s.data["bandwidth_m"] == 1000


# ── evaluation point cap ────────────────────────────────────────────────────


def test_cap_noop_below_threshold():
    """Under the cap the arrays are returned untouched (no subsample)."""
    n = density_mod._MAX_KDE_POINTS - 10
    data = np.zeros((2, n))
    w = np.zeros(n)
    d2, w2, total, used = _cap_kde_points(data, w)
    assert d2 is data and w2 is w
    assert (total, used) == (n, n)


def test_cap_real_threshold_boundary():
    """Threshold boundary without running a million points: n = cap + 1
    subsamples to exactly the cap with evenly spaced indices, keeping weights
    row-aligned with their points."""
    cap = density_mod._MAX_KDE_POINTS
    data = np.arange(cap + 1, dtype=float)[None, :].repeat(2, axis=0)
    w = np.arange(cap + 1, dtype=float)
    d2, w2, total, used = _cap_kde_points(data, w)
    assert (total, used) == (cap + 1, cap)
    assert d2.shape == (2, cap) and w2.shape == (cap,)
    idx = np.linspace(0, cap, cap).astype(int)  # 0 .. cap inclusive, cap picks
    assert np.array_equal(d2[0], idx.astype(float))
    assert np.array_equal(w2, idx.astype(float))


def test_kde_surface_no_sampling_note_small_input():
    """Small inputs pass through unchanged: no sampling note on the envelope."""
    res = kde_surface(_anisotropic_fc(n=200), bandwidth=1000, cell_size=5000)
    assert res.success
    assert "sampled_points" not in res.data


def test_kde_surface_point_cap_reported(monkeypatch):
    """End-to-end cap path: an oversized input is subsampled and the sampling
    is reported on the envelope (no unbounded n_points × n_cells evaluation)."""
    monkeypatch.setattr(density_mod, "_MAX_KDE_POINTS", 3000)
    res = kde_surface(_anisotropic_fc(n=4000), bandwidth=1000, cell_size=5000)
    assert res.success
    assert res.data["sampled_points"] == {"used": 3000, "total": 4000}


def test_kde_contours_point_cap_reported(monkeypatch):
    monkeypatch.setattr(density_mod, "_MAX_KDE_POINTS", 3000)
    res = kde_contours(_anisotropic_fc(n=4000), bandwidth=1000, levels=6)
    assert res.success
    assert res.data["sampled_points"] == {"used": 3000, "total": 4000}
