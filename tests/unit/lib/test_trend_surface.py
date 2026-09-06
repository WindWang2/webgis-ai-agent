"""Trend-surface conformance (Foundation V2 · A2).

Contract bullets:

* order-1 OLS recovers the plane z = 2x+3y+1 exactly (residual ≤ 1e-9·scale,
  R² = 1, LOOCV residual dust) — the unit-box scaling is absorbed by the
  coefficients;
* order 2 beats order 1 (LOOCV RMSE) on a genuinely quadratic field;
* targets outside the sample bbox carry ``extrapolated=true`` per record,
  counted in metadata (the model extrapolates globally — flagged, not hidden);
* the feasibility guard (order·(order+3)/2 > n/3) is a typed
  InsufficientSamples rejection; zero-span / rank-deficient designs are
  typed DegenerateData; invalid orders are UnsupportedMethod;
* the driver is deterministic under repeat; NaN-only value fields are
  rejected by the shared parse contract.
"""
import numpy as np
import pytest

from app.lib.geo_analysis.trend_surface import (
    trend_fit_stats,
    trend_loocv,
    trend_predict,
    trend_surface,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    UnsupportedMethod,
)

pytestmark = pytest.mark.unit


def _plane_field(n=30, seed=4):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, 1000.0, (n, 2))
    z = 2.0 * xy[:, 0] + 3.0 * xy[:, 1] + 1.0
    return xy, z


# ── exactness ───────────────────────────────────────────────────────────────

def test_trend_plane_recovered_exactly_order1():
    xy, z = _plane_field()
    targets = np.array([[100.0, 200.0], [999.0, 5.0]])
    out = trend_predict(xy, z, targets, order=1)
    np.testing.assert_allclose(
        out["predictions"], [2 * 100 + 3 * 200 + 1, 2 * 999 + 3 * 5 + 1],
        rtol=0, atol=1e-6,
    )
    assert float(np.max(np.abs(out["residuals"]))) < 1e-6
    stats = trend_fit_stats(xy, z, order=1)
    assert stats["r2"] == pytest.approx(1.0, abs=1e-9)
    assert stats["residual_variance"] < 1e-12
    # OLS trend is exact for planes → LOOCV residual dust only
    loo = trend_loocv(xy, z, order=1)
    assert loo["rmse"] < 1e-6
    assert loo["sample_count"] == 30


def test_trend_order2_beats_order1_on_quadratic_field():
    rng = np.random.default_rng(8)
    xy = rng.uniform(0.0, 1000.0, (60, 2))
    z = (xy[:, 0] / 300.0) ** 2 + 0.5 * xy[:, 1] / 300.0 + rng.normal(0, 0.01, 60)
    loo1 = trend_loocv(xy, z, order=1)
    loo2 = trend_loocv(xy, z, order=2)
    assert loo2["rmse"] < loo1["rmse"]
    stats2 = trend_fit_stats(xy, z, order=2)
    assert stats2["r2"] > 0.99


# ── extrapolation honesty ───────────────────────────────────────────────────

def test_trend_extrapolation_flagged_outside_bbox():
    fc = _driver_fc()
    out = trend_surface(fc, "v", resolution=8, order=1, cross_validate=False)
    m = out["metadata"]
    flagged = [r for r in out["records"] if r["extrapolated"]]
    assert m["extrapolated_count"] == len(flagged)
    assert len(flagged) > 0  # the H3 bbox buffer extends beyond the samples
    assert any(not r["extrapolated"] for r in out["records"])
    # extrapolated cells still carry values (a trend model evaluates globally)
    assert all(np.isfinite(r["value"]) for r in flagged)


# ── typed guards ────────────────────────────────────────────────────────────

def test_trend_order_guard_insufficient_samples():
    xy, z = _plane_field(n=30)
    with pytest.raises(InsufficientSamples, match="项增长"):
        trend_predict(xy[:5], z[:5], xy[:1], order=1)
    # order 3 needs n ≥ 27 by the guard
    with pytest.raises(InsufficientSamples, match="项增长"):
        trend_predict(xy[:26], z[:26], xy[:1], order=3)


def test_trend_zero_span_degenerate():
    rng = np.random.default_rng(2)
    xy = np.column_stack([rng.uniform(0, 100, 12), np.full(12, 5.0)])
    z = rng.normal(size=12)
    with pytest.raises(DegenerateData, match="零跨度"):
        trend_predict(xy, z, xy[:1], order=1)


def test_trend_rank_deficient_design_degenerate():
    # perfectly collinear 2-D samples → rank([1, u, v]) < 3
    t = np.linspace(0, 1, 10)
    xy = np.column_stack([t, 2.0 * t])
    z = t.copy()
    # zero-span guard fires for the unit-box scaler (v span == 0) — either
    # typed rejection is acceptable, both are DegenerateData
    with pytest.raises(DegenerateData):
        trend_predict(xy, z, xy[:1], order=1)


def test_trend_invalid_order_rejected():
    xy, z = _plane_field(n=10)
    with pytest.raises(UnsupportedMethod, match="阶数"):
        trend_predict(xy, z, xy[:1], order=4)


# ── driver ──────────────────────────────────────────────────────────────────

def _driver_fc(ni=5, nj=5, field="v", value_fn=lambda i, j: 2 * i + 3 * j + 1):
    import h3

    feats = []
    for i in range(ni):
        for j in range(nj):
            lat, lon = h3.cell_to_latlng(
                h3.latlng_to_cell(30.0 + i * 0.01, 120.0 + j * 0.01, 9))
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {field: float(value_fn(i, j))},
            })
    return {"type": "FeatureCollection", "features": feats}


def test_trend_driver_deterministic_repeat_and_metadata():
    fc = _driver_fc()
    a = trend_surface(fc, "v", resolution=8, order=1, cross_validate=True)
    b = trend_surface(fc, "v", resolution=8, order=1, cross_validate=True)
    assert a["records"] == b["records"]
    assert a["metadata"] == b["metadata"]
    m = a["metadata"]
    assert m["algorithm"] == "interpolation.trend_surface"
    assert m["order"] == 1 and m["n_params"] == 3
    assert m["r2"] is not None and m["r2"] > 0.99
    assert "residual_variance" in m
    assert m["validation"]["rmse"] is not None
    # unit-box scaling is disclosed
    assert any("单位" in d for d in m["disclosures"])


def test_trend_driver_all_nodata_field_rejected():
    fc = _driver_fc(value_fn=lambda i, j: float("nan"))
    with pytest.raises(ValueError, match="有限"):
        trend_surface(fc, "v", resolution=8, cross_validate=False)
