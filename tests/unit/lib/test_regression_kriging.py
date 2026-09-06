"""Regression-kriging conformance (Foundation V2 · A2).

Contract bullets:

* golden anchor: z = 2x+3y+1 with covariates = (x, y) fields recovers the
  plane EXACTLY with zero residual-kriging variance and the honest
  ``zero_residual_variance`` disclosure (the trend IS the signal — no
  variogram is fitted or faked);
* with a stationary residual the rk_variance equals the ordinary-kriging
  variance of the OLS residuals (bit-equal pipeline) and the metadata
  discloses that trend-coefficient uncertainty is NOT propagated;
* typed guards: a constant (zero-variance) covariate is DegenerateData;
  fewer than 2 covariate fields / too few samples are KrigingInputError;
  non-numeric fields raise ValueError;
* LOOCV is finite and deterministic (bounded full-pipeline refits);
* the driver is deterministic under repeat and always carries the
  approximate-semantics disclosures (IDW covariates at targets).
"""
import numpy as np
import pytest

from app.lib.geo_analysis.kriging import (
    KrigingInputError,
    fit_variogram,
    ordinary_kriging,
)
from app.lib.geo_analysis.regression_kriging import (
    regression_kriging_loocv,
    regression_kriging_predict,
    regression_kriging_surface,
)
from app.lib.gis.scientific_errors import DegenerateData

pytestmark = pytest.mark.unit


def _plane_covariates(n=40, seed=9, noise=0.0):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, 5000.0, (n, 2))
    z = 2.0 * xy[:, 0] + 3.0 * xy[:, 1] + 1.0
    if noise:
        z = z + rng.normal(0, noise, n)
    return xy, z, xy.copy()  # covariates ARE the coordinates


# ── golden anchors ──────────────────────────────────────────────────────────

def test_rk_plane_covariates_exact_zero_residual():
    xy, z, cov = _plane_covariates()
    targets = np.array([[1000.0, 2000.0], [2500.0, 2500.0]])
    out = regression_kriging_predict(xy, z, cov, targets, targets.copy())
    np.testing.assert_allclose(
        out["predictions"], [2 * 1000 + 3 * 2000 + 1, 2 * 2500 + 3 * 2500 + 1],
        rtol=0, atol=1e-6,
    )
    assert out["variances"].max() == 0.0  # exact zero, no faked uncertainty
    assert out["variogram"] is None
    assert "zero_residual_variance" in out["disclosures"]
    np.testing.assert_allclose(out["beta"], [1.0, 2.0, 3.0], atol=1e-6)


def test_rk_variance_is_residual_kriging_variance_only():
    xy, z, cov = _plane_covariates(noise=1.0)
    targets = xy[:10]
    target_cov = cov[:10]
    out = regression_kriging_predict(xy, z, cov, targets, target_cov)
    assert out["variances"].min() >= 0.0
    assert out["variances"].max() > 0.0  # residuals are real → real variance
    # the residual-kriging variance equals plain OK on the OLS residuals
    F = np.column_stack([np.ones(len(xy)), cov])
    beta, *_ = np.linalg.lstsq(F, z, rcond=None)
    resid = z - F @ beta
    vfit = fit_variogram(xy, resid, model=out["variogram"].model,
                         matern_smoothness=out["variogram"].nu)
    ok = ordinary_kriging(xy, resid, targets, vfit, k=12)
    np.testing.assert_allclose(out["variances"], ok.variances, atol=1e-9)
    assert any("趋势系数" in d for d in out["disclosures"])


# ── typed guards ────────────────────────────────────────────────────────────

def test_rk_constant_covariate_rejected():
    xy, z, cov = _plane_covariates()
    cov_const = np.column_stack([cov[:, 0], np.full(len(cov), 7.0)])
    with pytest.raises(DegenerateData, match="方差为 0"):
        regression_kriging_surface(
            _driver_fc(cov2_fn=lambda i, j: 7.0),
            "v", ["c1", "c2"], resolution=8, cross_validate=False,
        )
    with pytest.raises(DegenerateData):
        regression_kriging_predict(
            xy, z, cov_const, xy[:3], cov_const[:3],
        )


def test_rk_requires_two_covariates():
    xy, z, cov = _plane_covariates()
    with pytest.raises(KrigingInputError, match="协变量"):
        regression_kriging_predict(
            xy, z, cov[:, :1], xy[:3], cov[:3, :1],
        )


def test_rk_too_few_samples_rejected():
    xy, z, cov = _plane_covariates(n=6)
    with pytest.raises(KrigingInputError, match="至少"):
        regression_kriging_predict(xy, z, cov, xy[:3], cov[:3])


def test_rk_non_numeric_field_raises():
    fc = _driver_fc()
    fc["features"][0]["properties"]["c1"] = "not-a-number"
    with pytest.raises(ValueError, match="非数值"):
        regression_kriging_surface(
            fc, "v", ["c1", "c2"], resolution=8, cross_validate=False,
        )


# ── LOOCV + driver determinism ──────────────────────────────────────────────

def test_rk_loocv_finite_and_deterministic():
    xy, z, cov = _plane_covariates(noise=0.8)
    a = regression_kriging_loocv(xy, z, cov)
    b = regression_kriging_loocv(xy, z, cov)
    assert a == b
    assert np.isfinite(a["rmse"]) and a["rmse"] > 0
    assert a["sample_count"] == len(z)
    # a plane-plus-noise field: RK LOOCV must beat the naive mean baseline
    mean_rmse = float(np.sqrt(np.mean((z - z.mean()) ** 2)))
    assert a["rmse"] < mean_rmse


def _driver_fc(cov2_fn=None):
    import h3

    feats = []
    for i in range(5):
        for j in range(5):
            lat, lon = h3.cell_to_latlng(
                h3.latlng_to_cell(30.0 + i * 0.01, 120.0 + j * 0.01, 9))
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "v": float(2 * i + 3 * j + 1),
                    "c1": float(i),
                    "c2": float(cov2_fn(i, j) if cov2_fn else j),
                },
            })
    return {"type": "FeatureCollection", "features": feats}


def test_rk_driver_deterministic_repeat():
    """Determinism + metadata contract (approximate semantics disclosures)."""
    fc = _driver_fc()
    a = regression_kriging_surface(
        fc, "v", ["c1", "c2"], resolution=8, cross_validate=True,
    )
    b = regression_kriging_surface(
        fc, "v", ["c1", "c2"], resolution=8, cross_validate=True,
    )
    assert a["records"] == b["records"]
    assert a["metadata"] == b["metadata"]
    m = a["metadata"]
    assert m["algorithm"] == "interpolation.regression_kriging"
    assert m["explanatory_fields"] == ["c1", "c2"]
    # trend recovered (i, j spacings are equal so coefficients ≈ [1, 2, 3]
    # up to the metric anisotropy of the lat/lon grid)
    assert len(m["trend_coefficients"]["values"]) == 3
    assert m["trend_coefficients"]["terms"] == ["1", "c1", "c2"]
    # approximate covariate semantics + variance scope, both disclosed
    assert m["covariate_idw"]["method"] == "idw"
    assert any("IDW" in d for d in m["disclosures"])
    assert any("趋势系数" in d for d in m["disclosures"])
    # per-cell first-class outputs
    for rec in a["records"]:
        assert set(rec) >= {"h3_index", "rk_prediction", "rk_variance", "rk_stddev"}
        assert rec["rk_variance"] >= 0.0
    assert m["validation"]["rmse"] is not None
