"""Hardening tests for KDE (Slice 7).

Covers degenerate-input handling (C-5), NaN-weight alignment (C-6), and
large dynamic-range weights no longer clamped by point repetition (C-7).
"""
import numpy as np

from app.lib.geo_analysis.density import kde_contours, kde_surface


def _pt_fc(pts, field="w"):
    feats = []
    for xy, props in pts:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [xy[0], xy[1]]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": feats}


def _scattered(n=12, seed=1, w=1.0):
    rng = np.random.default_rng(seed)
    return [
        ((116.40 + rng.uniform(-0.02, 0.02), 39.90 + rng.uniform(-0.02, 0.02)), {"w": w})
        for _ in range(n)
    ]


# --------------------------------------------------------------------------- #
# C-5: degenerate (coincident) input -> structured error, not LinAlgError crash
# --------------------------------------------------------------------------- #
def test_kde_surface_degenerate_coincident_points_errors():
    fc = _pt_fc([((116.40, 39.90), {}) for _ in range(5)])  # all identical
    res = kde_surface(fc)
    assert not res.success
    assert res.error_type == "NumericalError"


def test_kde_contours_degenerate_coincident_points_errors():
    fc = _pt_fc([((116.40, 39.90), {}) for _ in range(6)])
    res = kde_contours(fc)
    assert not res.success
    assert res.error_type == "NumericalError"


def test_kde_surface_collinear_points_ok():
    # Collinear points do NOT break gaussian_kde (the bandwidth inflates the
    # covariance); they produce a valid ridge surface. Only coincident points
    # are degenerate. Assert it succeeds, not errors.
    fc = _pt_fc([((116.40 + i * 0.001, 39.90), {"w": 1.0}) for i in range(8)])
    res = kde_surface(fc)
    assert res.success


# --------------------------------------------------------------------------- #
# C-6: NaN weight values don't crash the weighted path
# --------------------------------------------------------------------------- #
def test_kde_surface_nan_weight_filtered():
    pts = _scattered(10)  # every point has w=1.0
    pts[0] = (pts[0][0], {"w": float("nan")})  # one NaN weight among valid ones
    fc = _pt_fc(pts)
    res = kde_surface(fc, value_field="w")
    assert res.success
    assert np.isfinite(res.data["stats"]["max_density"])


# --------------------------------------------------------------------------- #
# C-7: large dynamic-range weights don't clamp or OOM (native weights)
# --------------------------------------------------------------------------- #
def test_kde_surface_large_dynamic_range_weights():
    pts = _scattered(10)
    # One very heavy point (1e6) among unit weights.
    pts[0] = (pts[0][0], {"w": 1e6})
    for p in pts[1:]:
        p[1]["w"] = 1.0
    fc = _pt_fc(pts)
    res = kde_surface(fc, value_field="w")
    assert res.success
    # The heavy point should dominate: max density is finite and meaningful.
    assert np.isfinite(res.data["stats"]["max_density"])
    assert res.data["stats"]["max_density"] > 0


# --------------------------------------------------------------------------- #
# Sanity: unweighted KDE still works
# --------------------------------------------------------------------------- #
def test_kde_surface_unweighted_works():
    res = kde_surface(_pt_fc(_scattered(15)))
    assert res.success
    assert res.data["count"] >= 1
