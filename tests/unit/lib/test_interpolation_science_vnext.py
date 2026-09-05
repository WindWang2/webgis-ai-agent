"""Interpolation science conformance tests (VNext, ADR-0099).

Conformance anchors for the interpolation domain pack additions:

* IDW — exact-hit at samples, LOOCV metrics equal to an independently
  computed scalar reference (first principles), driver determinism, and the
  additive validation / uncertainty evidence blocks (IDW never claims a
  theoretical variance — LOOCV residual evidence only).
* RBF — node exactness at smoothing=0, LOOCV present/finite, invalid-kernel
  rejection, deterministic repeat, default kernel.
* Universal kriging — exact plane → exact-trend prediction with zero
  variance and the honest ``zero_residual_variance`` disclosure (no fake
  variogram); typed InsufficientSamples below 12 samples; UK ≤ OK·1.10 CV
  RMSE on a trended field.
* Backward compatibility — the ordinary-kriging default path stays
  bit-identical (covered by the untouched suites
  tests/unit/lib/test_kriging_interpolation.py,
  tests/unit/lib/test_idw_interpolation.py,
  tests/unit/gis_harness/test_kriging_vertical_slice.py).
"""
import asyncio
import math

import geopandas as gpd
import h3
import numpy as np
import pytest

from app.lib.geo_analysis.interpolation import idw_loocv, idw_surface
from app.lib.geo_analysis.kriging import (
    cross_validate_kriging,
    kriging_interpolation,
    ols_linear_trend,
    universal_kriging_detrended,
)
from app.lib.geo_analysis.rbf_interpolation import (
    rbf_interpolation,
    rbf_loocv,
    rbf_predict,
)
from app.lib.gis.scientific_errors import InsufficientSamples, UnsupportedMethod

pytestmark = pytest.mark.unit


# ── fixtures ────────────────────────────────────────────────────────────────

def _point_fc(lon, lat, values, field="val", crs_member=None):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(a), float(b)]},
                "properties": {field: float(v)},
            }
            for a, b, v in zip(lon, lat, values)
        ],
    }
    if crs_member:
        fc["crs"] = {"type": "name", "properties": {"name": crs_member}}
    return fc


def _smooth_field_metric(n=50, seed=17, span=5000.0):
    """Projected-metric samples on a smooth field z = sin(x/100)+cos(y/100)."""
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, span, (n, 2))
    z = np.sin(xy[:, 0] / 100.0) + np.cos(xy[:, 1] / 100.0)
    return xy, z


def _reference_idw_loocv(xy, z, power, k_cap=5, exact_m=1e-9):
    """Scalar first-principles LOOCV reference (same contract as idw_loocv:
    metric Euclidean, k=min(5, n-1) neighbourhood cap, exact-hit recovery)."""
    n = len(z)
    preds = np.empty(n)
    for i in range(n):
        d = np.hypot(xy[:, 0] - xy[i, 0], xy[:, 1] - xy[i, 1])
        order = np.argsort(d, kind="stable")
        others = [int(j) for j in order if int(j) != i][: min(k_cap, n - 1)]
        ds = d[others]
        if ds[0] < exact_m:
            preds[i] = z[others[0]]
        else:
            w = 1.0 / (ds ** power)
            preds[i] = float(np.sum(w * z[others]) / np.sum(w))
    e = preds - z
    return {
        "rmse": float(np.sqrt(np.mean(e ** 2))),
        "mae": float(np.mean(np.abs(e))),
        "bias": float(np.mean(e)),
    }


# ── 1. IDW: exact hits + LOOCV reference ────────────────────────────────────

def test_idw_exact_hit_at_samples():
    """Cells centred on sample coordinates recover the sample value exactly
    (idw_surface records bit-identical to the legacy idw_interpolation)."""
    cells = [h3.latlng_to_cell(30.0, 120.0, 8), h3.latlng_to_cell(30.05, 120.05, 8)]
    coords = [h3.cell_to_latlng(c) for c in cells]  # (lat, lng)
    values = [10.0, 20.0]
    fc = _point_fc([c[1] for c in coords], [c[0] for c in coords], values)
    out = idw_surface(fc, "val", resolution=8, cross_validate=False)
    assert len(out["records"]) > 0
    by_cell = {r["h3_index"]: r["value"] for r in out["records"]}
    for cell, v in zip(cells, values):
        assert by_cell[cell] == v
    # cross_validate=False → no validation evidence is claimed
    assert "validation" not in out["metadata"]
    assert "uncertainty" not in out["metadata"]


def test_idw_loocv_matches_independent_reference():
    """idw_loocv on a smooth field equals the scalar first-principles
    reference (same exponent, same neighbourhood cap, same exact-hit rule)."""
    xy, z = _smooth_field_metric(n=50)
    for power in (1.0, 2.0, 3.5):
        got = idw_loocv(xy, z, power)
        ref = _reference_idw_loocv(xy, z, power)
        assert got["method"] == "loocv"
        assert got["sample_count"] == 50
        for key in ("rmse", "mae", "bias"):
            assert math.isfinite(got[key])
            assert abs(got[key] - ref[key]) < 1e-9, (power, key, got[key], ref[key])


def test_idw_loocv_needs_two_samples():
    with pytest.raises(InsufficientSamples):
        idw_loocv(np.array([[0.0, 0.0]]), np.array([1.0]), 2.0)


def test_idw_power_guard_typed():
    xy, z = _smooth_field_metric(n=8)
    with pytest.raises(UnsupportedMethod) as ei:
        idw_loocv(xy, z, 6.0)
    assert ei.value.correction_hint
    assert isinstance(ei.value, ValueError)


def test_idw_driver_deterministic_repeat():
    rng = np.random.default_rng(9)
    lon = 120.0 + rng.uniform(0, 0.1, 20)
    lat = 30.0 + rng.uniform(0, 0.1, 20)
    vals = rng.uniform(0, 10, 20)
    fc = _point_fc(lon, lat, vals)
    r1 = idw_surface(fc, "val", resolution=8, cross_validate=True)
    r2 = idw_surface(fc, "val", resolution=8, cross_validate=True)
    assert r1["records"] == r2["records"]
    assert r1["metadata"]["validation"] == r2["metadata"]["validation"]
    assert r1["metadata"]["uncertainty"] == r2["metadata"]["uncertainty"]


def test_idw_tool_evidence_blocks():
    """Tool result carries the ADDITIVE validation + uncertainty quantile
    blocks and the scientific_evidence contract (LOOCV residual evidence
    only — never a theoretical variance claim for IDW)."""
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)
    rng = np.random.default_rng(11)
    fc = _point_fc(
        120.0 + rng.uniform(0, 0.1, 20), 30.0 + rng.uniform(0, 0.1, 20),
        rng.uniform(0, 10, 20),
    )
    result = asyncio.run(reg.dispatch("idw_interpolation", {
        "geojson": fc, "value_field": "val", "resolution": 8,
    }))
    assert result["type"] == "FeatureCollection"
    validation = result["validation"]
    assert validation["uncertainty_type"] == "validation_metrics"
    assert validation["method"] == "loocv"
    assert validation["sample_count"] == 20
    for key in ("rmse", "mae", "bias"):
        assert validation[key] is not None and math.isfinite(validation[key])
    uncertainty = result["uncertainty"]
    assert uncertainty["method"] == "loocv_residual_quantiles"
    assert set(uncertainty["quantiles"]) == {"p50", "p90"}
    assert all(math.isfinite(v) for v in uncertainty["quantiles"].values())
    assert uncertainty["quantiles"]["p50"] <= uncertainty["quantiles"]["p90"]

    ev = result["scientific_evidence"]
    assert ev["algorithm"] == "interpolation.idw"
    assert ev["tool"] == "idw_interpolation"
    assert ev["validation"]["method"] == "loocv"
    quant_measures = [
        m for u in ev["uncertainty"] for m in u["measures"]
        if m["measure"] == "quantile"
    ]
    assert {m["value"] for m in quant_measures} == set(uncertainty["quantiles"].values())
    # no theoretical variance anywhere in the IDW evidence
    assert all(
        "variance" not in (m.get("method") or "").lower()
        for u in ev["uncertainty"] for m in u["measures"]
    )


# ── 2. RBF ──────────────────────────────────────────────────────────────────

def _rbf_fixture(n=30, seed=23):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, 5000.0, (n, 2))
    z = np.sin(xy[:, 0] / 500.0) + np.cos(xy[:, 1] / 500.0)
    return xy, z


def test_rbf_exactness_at_nodes_smoothing_zero():
    """smoothing=0 + thin_plate_spline reproduces the sample values exactly."""
    xy, z = _rbf_fixture(n=30)
    preds = rbf_predict(xy, z, xy, kernel="thin_plate_spline", smoothing=0.0)
    rel = np.abs(preds - z) / np.maximum(np.abs(z), 1e-9)
    assert rel.max() < 1e-6


def test_rbf_loocv_present_and_finite():
    xy, z = _rbf_fixture(n=30)
    loocv = rbf_loocv(xy, z)
    assert loocv["method"] == "loocv"
    assert loocv["sample_count"] == 30
    for key in ("rmse", "mae", "bias"):
        assert math.isfinite(loocv[key])
    # driver-level: validation + uncertainty blocks present and finite
    rng = np.random.default_rng(3)
    fc = _point_fc(
        120.0 + rng.uniform(0, 0.05, 30), 30.0 + rng.uniform(0, 0.05, 30),
        rng.uniform(0, 10, 30),
    )
    out = rbf_interpolation(fc, "val", resolution=6, cross_validate=True)
    v = out["metadata"]["validation"]
    assert v["method"] == "loocv" and v["rmse"] is not None and math.isfinite(v["rmse"])
    q = out["metadata"]["uncertainty"]["quantiles"]
    assert math.isfinite(q["p50"]) and math.isfinite(q["p90"]) and q["p50"] <= q["p90"]


def test_rbf_invalid_kernel_rejected():
    xy, z = _rbf_fixture(n=12)
    with pytest.raises(UnsupportedMethod):
        rbf_predict(xy, z, xy[:3], kernel="gaussian")
    with pytest.raises(UnsupportedMethod) as ei:
        rbf_predict(xy, z, xy[:3], kernel="bogus_kernel")
    assert "thin_plate_spline" in ei.value.correction_hint
    fc = _point_fc([104.0 + i * 0.01 for i in range(12)],
                   [30.0 + i * 0.01 for i in range(12)],
                   list(range(12)))
    with pytest.raises(UnsupportedMethod):
        rbf_interpolation(fc, "val", kernel="gaussian", resolution=6)


def test_rbf_driver_deterministic_repeat():
    rng = np.random.default_rng(5)
    n = 200
    fc = _point_fc(
        120.0 + rng.uniform(0, 0.1, n), 30.0 + rng.uniform(0, 0.1, n),
        rng.uniform(0, 10, n),
    )
    r1 = rbf_interpolation(fc, "val", resolution=6, cross_validate=False)
    r2 = rbf_interpolation(fc, "val", resolution=6, cross_validate=False)
    assert len(r1["records"]) > 0
    assert r1["records"] == r2["records"]
    assert r1["metadata"] == r2["metadata"]


def test_rbf_default_kernel_thin_plate_spline():
    rng = np.random.default_rng(7)
    fc = _point_fc(
        120.0 + rng.uniform(0, 0.05, 30), 30.0 + rng.uniform(0, 0.05, 30),
        rng.uniform(0, 10, 30),
    )
    out = rbf_interpolation(fc, "val", resolution=6, cross_validate=False)
    assert out["metadata"]["kernel"] == "thin_plate_spline"
    assert out["metadata"]["smoothing"] == 0.0
    assert out["metadata"]["neighbors"] == min(32, out["metadata"]["n_samples"])


def test_rbf_rejects_too_few_samples_typed():
    fc = _point_fc([120.0, 120.01], [30.0, 30.01], [1.0, 2.0])
    with pytest.raises(InsufficientSamples):
        rbf_interpolation(fc, "val", resolution=6)


# ── 3. Universal kriging ────────────────────────────────────────────────────

def _project_metric(lonlat, crs):
    gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]), crs="EPSG:4326"
    ).to_crs(crs)
    return np.column_stack((gdf.geometry.x.values, gdf.geometry.y.values))


def test_universal_kriging_exact_plane_zero_residual_variance():
    """Data exactly on the plane z = 2 + 3x + 5y (metric space): the OLS
    residuals are zero to machine precision → the honest UK answer is the
    exact trend prediction with variance 0 and the zero_residual_variance
    disclosure — no variogram is fitted or faked."""
    rng = np.random.default_rng(2)
    xy = rng.uniform(200000.0, 260000.0, (40, 2))
    z = 2.0 + 3.0 * xy[:, 0] + 5.0 * xy[:, 1]

    # core path
    res = universal_kriging_detrended(xy, z, xy[:5])
    assert res.disclosures == ["zero_residual_variance"]
    assert res.variogram is None
    assert float(np.abs(res.variances).max()) == 0.0
    assert np.abs(res.predictions - z[:5]).max() / np.abs(z[:5]).max() < 1e-6

    # driver path (declared EPSG:3857 → metric pass-through)
    fc = _point_fc(xy[:, 0], xy[:, 1], z, crs_member="urn:ogc:def:crs:EPSG::3857")
    out = kriging_interpolation(
        fc, "val", resolution=6, method="universal", cross_validate=False,
        declared_crs="EPSG:3857",
    )
    meta = out["metadata"]
    assert meta["disclosures"] == ["zero_residual_variance"]
    assert meta["variogram"] is None
    assert meta["method"] == "universal"
    beta = meta["drift"]["coefficients"]
    assert beta == pytest.approx([2.0, 3.0, 5.0], abs=1e-4)
    assert meta["variance_range"] == [0.0, 0.0]

    # predictions exact at cell centres (reprojected to the working CRS)
    cell_lonlat = np.array([h3.cell_to_latlng(r["h3_index"]) for r in out["records"]])
    cell_metric = _project_metric(
        np.column_stack([cell_lonlat[:, 1], cell_lonlat[:, 0]]), "EPSG:3857"
    )
    truth = 2.0 + 3.0 * cell_metric[:, 0] + 5.0 * cell_metric[:, 1]
    got = np.array([r["value"] for r in out["records"]])
    rel = np.abs(got - truth) / np.maximum(np.abs(truth), 1e-9)
    assert rel.max() < 1e-6
    assert all(r["kriging_variance"] == 0.0 for r in out["records"])


def test_universal_kriging_insufficient_samples_typed():
    rng = np.random.default_rng(13)
    fc = _point_fc(
        104.0 + rng.uniform(0, 0.1, 11), 30.0 + rng.uniform(0, 0.1, 11),
        rng.uniform(0, 10, 11),
    )
    with pytest.raises(InsufficientSamples) as ei:
        kriging_interpolation(fc, "val", method="universal", resolution=6)
    assert ei.value.scientific_code == "INSUFFICIENT_SAMPLES"
    assert isinstance(ei.value, ValueError)
    assert ei.value.correction_hint
    # ordinary kriging still accepts 11 samples (≥ MIN_SAMPLES=8)
    ok = kriging_interpolation(
        fc, "val", method="ordinary", resolution=6, cross_validate=False
    )
    assert len(ok["records"]) > 0


def test_ok_uk_loocv_on_trended_field():
    """Plane + noise (seed 42): per-fold-refit UK CV must not lose to OK by
    more than the 10% band (on a strongly trended field it should win)."""
    rng = np.random.default_rng(42)
    xy = rng.uniform(0, 10000, (40, 2))
    z = 2.0 + 3.0 * xy[:, 0] / 1000.0 + 5.0 * xy[:, 1] / 1000.0 \
        + rng.normal(0, 1.0, 40)
    cv_ok = cross_validate_kriging(xy, z, method="ordinary")
    cv_uk = cross_validate_kriging(xy, z, method="universal")
    assert cv_ok.rmse is not None and cv_uk.rmse is not None
    assert cv_uk.rmse <= cv_ok.rmse * 1.10, (
        f"UK rmse {cv_uk.rmse:.3f} vs OK {cv_ok.rmse:.3f}"
    )
    assert cv_uk.folds >= 1


# ── 4/5/6. driver determinism (UK) + evidence contract (kriging) ────────────

def test_kriging_driver_deterministic_repeat():
    """UK driver outputs identical across repeats (and the ordinary default
    path stays bit-identical — covered by the untouched trusted suites)."""
    rng = np.random.default_rng(19)
    xy = rng.uniform(200000.0, 260000.0, (20, 2))
    z = 2.0 + 3.0 * xy[:, 0] / 10000.0 + 5.0 * xy[:, 1] / 10000.0 \
        + rng.normal(0, 0.1, 20)
    fc = _point_fc(xy[:, 0], xy[:, 1], z, crs_member="urn:ogc:def:crs:EPSG::3857")
    r1 = kriging_interpolation(
        fc, "val", resolution=6, method="universal", cross_validate=False,
        declared_crs="EPSG:3857",
    )
    r2 = kriging_interpolation(
        fc, "val", resolution=6, method="universal", cross_validate=False,
        declared_crs="EPSG:3857",
    )
    assert r1["records"] == r2["records"]
    assert r1["metadata"] == r2["metadata"]
    # ordinary default determinism on the same fixture (metric-declared —
    # the raw 3857 metres must never be re-read as degrees)
    o1 = kriging_interpolation(
        fc, "val", resolution=6, cross_validate=False, declared_crs="EPSG:3857",
    )
    o2 = kriging_interpolation(
        fc, "val", resolution=6, cross_validate=False, declared_crs="EPSG:3857",
    )
    assert o1["records"] == o2["records"]
    assert o1["metadata"] == o2["metadata"]
    assert o1["metadata"]["algorithm"] == "interpolation.kriging"
    assert o1["metadata"]["method"] == "ordinary"


def test_kriging_tool_structured_validation_block():
    """Kriging tool result carries a structured validation metrics block
    inside scientific_evidence (k-fold RMSE/MAE/bias/R² with fold count)."""
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)
    rng = np.random.default_rng(29)
    fc = _point_fc(
        104.0 + rng.uniform(0, 0.15, 40), 30.5 + rng.uniform(0, 0.15, 40),
        rng.uniform(0, 10, 40),
    )
    result = asyncio.run(reg.dispatch("kriging_interpolation", {
        "geojson": fc, "value_field": "val", "resolution": 6,
    }))
    assert result["type"] == "FeatureCollection"
    assert "克里金" in result["summary"]
    ev = result["scientific_evidence"]
    assert ev["algorithm"] == "interpolation.kriging"
    assert ev["tool"] == "kriging_interpolation"
    validation = ev["validation"]
    assert validation["uncertainty_type"] == "validation_metrics"
    assert validation["method"] == "k_fold"
    assert validation["rmse"] is not None and math.isfinite(validation["rmse"])
    assert validation["folds"] >= 1
    assert validation["sample_count"] == 40
    assert ev["uncertainty"] and ev["uncertainty"][0]["uncertainty_type"] == "raster_uncertainty"


def test_kriging_tool_universal_routes_to_uk_descriptor():
    """method="universal" routes the evidence block to the
    interpolation.universal_kriging descriptor (and the zero-residual
    degenerate summary discloses honestly instead of faking a variogram)."""
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)
    rng = np.random.default_rng(2)
    xy = rng.uniform(200000.0, 260000.0, (40, 2))
    z = 2.0 + 3.0 * xy[:, 0] + 5.0 * xy[:, 1]
    fc = _point_fc(xy[:, 0], xy[:, 1], z, crs_member="urn:ogc:def:crs:EPSG::3857")
    result = asyncio.run(reg.dispatch("kriging_interpolation", {
        "geojson": fc, "value_field": "val", "resolution": 6,
        "method": "universal", "cross_validate": False,
    }))
    ev = result["scientific_evidence"]
    assert ev["algorithm"] == "interpolation.universal_kriging"
    meta = result["kriging_metadata"]
    assert meta["disclosures"] == ["zero_residual_variance"]
    assert meta["variogram"] is None
    assert "零残差退化" in result["summary"]


def test_ols_linear_trend_recovers_plane():
    xy = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [2.0, 3.0], [4.0, 1.0]])
    z = 2.0 + 3.0 * xy[:, 0] + 5.0 * xy[:, 1]
    beta, resid = ols_linear_trend(xy, z)
    assert beta == pytest.approx([2.0, 3.0, 5.0], abs=1e-9)
    assert np.abs(resid).max() < 1e-9
