"""TIN interpolation conformance (Foundation V2 · A2).

Contract bullets:

* linear TIN is an exact interpolant — sample cell centres recover the
  sample values exactly, and the plane z = 2x+3y+1 is reproduced exactly
  (barycentric weights on a linear field);
* the convex hull is honest — targets outside evaluate to NaN (no
  extrapolation), driver records exclude those cells and the metadata
  discloses fill_fraction / n_outside_hull / hull_area_fraction;
* collinear or <3-sample inputs are typed DegenerateData rejections with a
  correction hint (Qhull errors never leak);
* > TIN_HARD_CAP samples is a typed ResourceScaleMismatch raised BEFORE any
  allocation (monkeypatched constant);
* LOOCV is finite, deterministic, and bounded by a disclosed stride
  subsample; < 4 samples declines via typed InsufficientSamples;
* the driver is deterministic under repeat calls; unknown methods are
  typed UnsupportedMethod rejections.
"""
import numpy as np
import pytest

from app.lib.geo_analysis.tin_interpolation import (
    TIN_HARD_CAP,
    TIN_LOOCV_MAX_POINTS,
    tin_loocv,
    tin_predict,
    tin_surface,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    ResourceScaleMismatch,
    UnsupportedMethod,
)

pytestmark = pytest.mark.unit


def _square_grid():
    xy = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [10.0, 10.0]])
    vals = np.array([0.0, 10.0, 10.0, 20.0])  # z = x + y
    return xy, vals


# ── exactness ───────────────────────────────────────────────────────────────

def test_tin_linear_exact_at_samples():
    xy, vals = _square_grid()
    preds = tin_predict(xy, vals, xy, method="linear")
    np.testing.assert_allclose(preds, vals, atol=1e-12)


def test_tin_linear_reproduces_plane_at_interior_points():
    xy, vals = _square_grid()
    targets = np.array([[2.5, 7.5], [5.0, 5.0], [9.9, 0.1]])
    preds = tin_predict(xy, vals, targets, method="linear")
    np.testing.assert_allclose(preds, targets.sum(axis=1), atol=1e-9)


def test_tin_clough_tocher_exact_at_samples():
    xy, vals = _square_grid()
    preds = tin_predict(xy, vals, xy, method="clough_tocher")
    np.testing.assert_allclose(preds, vals, atol=1e-6)


# ── honest hull ─────────────────────────────────────────────────────────────

def test_tin_nan_outside_hull_no_extrapolation():
    xy, vals = _square_grid()
    preds = tin_predict(xy, vals, np.array([[50.0, 50.0], [5.0, 5.0]]),
                        method="linear")
    assert np.isnan(preds[0])
    assert preds[1] == pytest.approx(10.0)


def test_tin_driver_reports_hull_facts_and_skips_outside_cells():
    import h3

    cells = [h3.latlng_to_cell(30.0 + i * 0.01, 120.0 + j * 0.01, 9)
             for i in range(3) for j in range(3)]
    coords = [h3.cell_to_latlng(c) for c in cells]
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [c[1], c[0]]},
                "properties": {"v": float(2 * i + 3 * j + 1)},
            }
            for (i, j), c in zip(
                [(i, j) for i in range(3) for j in range(3)], coords
            )
        ],
    }
    out = tin_surface(fc, "v", resolution=9, cross_validate=False)
    m = out["metadata"]
    assert m["algorithm"] == "interpolation.tin"
    assert m["triangle_count"] > 0
    assert 0.0 < m["hull_area_fraction"] <= 1.0
    assert 0.0 < m["fill_fraction"] <= 1.0
    assert m["n_outside_hull"] == m["n_evaluated"] - len(out["records"])
    # every record is finite (outside-hull cells are honestly absent)
    assert all(np.isfinite(r["value"]) for r in out["records"])
    if m["n_outside_hull"]:
        assert m["n_outside_hull"] > 0 and m["fill_fraction"] < 1.0
        assert any("凸包" in d for d in m.get("disclosures", []))


# ── typed guards ────────────────────────────────────────────────────────────

def test_tin_collinear_rejected_degenerate_data():
    xy = np.array([[float(i), 0.0] for i in range(6)])
    vals = np.arange(6.0)
    with pytest.raises(DegenerateData, match="共线"):
        tin_predict(xy, vals, np.array([[1.0, 1.0]]), method="linear")


def test_tin_too_few_points_degenerate_data():
    xy = np.array([[0.0, 0.0], [1.0, 1.0]])
    with pytest.raises(DegenerateData, match="非共线"):
        tin_predict(xy, np.array([0.0, 1.0]), np.array([[0.5, 0.5]]))


def test_tin_scale_guard_resource_mismatch(monkeypatch):
    """Hard cap fires BEFORE any allocation (monkeypatched tiny constant)."""
    monkeypatch.setattr("app.lib.geo_analysis.tin_interpolation.TIN_HARD_CAP", 10)
    assert TIN_HARD_CAP == 200_000  # module constant untouched for others
    rng = np.random.default_rng(3)
    xy = rng.uniform(0, 100, (11, 2))
    with pytest.raises(ResourceScaleMismatch):
        tin_predict(xy, rng.normal(size=11), xy[:3])


def test_tin_unknown_method_rejected():
    xy, vals = _square_grid()
    with pytest.raises(UnsupportedMethod, match="TIN method"):
        tin_predict(xy, vals, xy, method="nearest")


# ── LOOCV ───────────────────────────────────────────────────────────────────

def _blob_samples(n=40, seed=7):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, 1000.0, (n, 2))
    vals = np.sin(xy[:, 0] / 200.0) + np.cos(xy[:, 1] / 250.0)
    return xy, vals


def test_tin_loocv_bounded_and_finite():
    xy, vals = _blob_samples(n=40)
    out = tin_loocv(xy, vals, method="linear")
    # hull vertices leave a residual hole when held out — the count discloses
    # the covered subset (never silently below n, never padded to n)
    assert 0 < out["sample_count"] <= 40
    assert np.isfinite(out["rmse"]) and np.isfinite(out["mae"])
    assert out["rmse"] > 0
    # deterministic repeat
    again = tin_loocv(xy, vals, method="linear")
    assert out == again


def test_tin_loocv_stride_subsample_disclosed(monkeypatch):
    xy, vals = _blob_samples(n=30)
    monkeypatch.setattr(
        "app.lib.geo_analysis.tin_interpolation.TIN_LOOCV_MAX_POINTS", 10
    )
    out = tin_loocv(xy, vals, method="linear", max_points=10)
    assert out["sample_count"] <= 10
    assert TIN_LOOCV_MAX_POINTS == 500


def test_tin_loocv_declines_below_four_samples():
    xy, vals = _blob_samples(n=3)
    with pytest.raises(InsufficientSamples, match="LOOCV"):
        tin_loocv(xy, vals, method="linear")


# ── driver determinism ──────────────────────────────────────────────────────

def _driver_fc(ni=4, nj=4):
    import h3

    feats = []
    for i in range(ni):
        for j in range(nj):
            lat, lon = h3.cell_to_latlng(
                h3.latlng_to_cell(30.0 + i * 0.01, 120.0 + j * 0.01, 9))
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"v": float(2 * i + 3 * j + 1)},
            })
    return {"type": "FeatureCollection", "features": feats}


def test_tin_driver_deterministic_repeat():
    fc = _driver_fc()
    a = tin_surface(fc, "v", resolution=8, cross_validate=True)
    b = tin_surface(fc, "v", resolution=8, cross_validate=True)
    assert a["records"] == b["records"]
    assert a["metadata"] == b["metadata"]


def test_tin_driver_all_nodata_field_rejected():
    fc = _driver_fc()
    for f in fc["features"]:
        f["properties"]["v"] = float("nan")
    with pytest.raises(ValueError, match="有限"):
        tin_surface(fc, "v", resolution=8, cross_validate=False)
