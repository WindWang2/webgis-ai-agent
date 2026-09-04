"""Ordinary Kriging vertical slice — core, CRS contract, driver, tool, seams.

Mirrors the IDW reference suite (tests/unit/lib/test_idw_interpolation.py):
deterministic synthetic fixtures, exact-value discriminants, metric-CRS
discriminants, structured error contracts, and the resolver/planner routing
matrix (explicit kriging is never silently swapped for IDW).
"""
import asyncio
import math

import numpy as np
import pytest

from app.lib.geo_analysis.kriging import (
    CV_FOLDS,
    KrigingCrsError,
    KrigingInputError,
    MAX_FIT_POINTS,
    MIN_SAMPLES,
    VariogramFit,
    cross_validate_kriging,
    empirical_variogram,
    fit_variogram,
    kriging_interpolation,
    ordinary_kriging,
    stratified_subsample,
)


def _stationary_field(lon, lat):
    """Gaussian-bump stationary field around Chengdu (no trend)."""
    return (
        5.0 * np.exp(-((lon - 104.0) ** 2 + (lat - 30.65) ** 2) / (2 * 0.06 ** 2))
        + 3.0 * np.exp(-((lon - 104.12) ** 2 + (lat - 30.72) ** 2) / (2 * 0.04 ** 2))
    )


def _points_fc(n=200, seed=11, noise=0.2, field=None, value_field="pm25"):
    rng = np.random.default_rng(seed)
    lon = rng.uniform(103.9, 104.2, n)
    lat = rng.uniform(30.5, 30.8, n)
    z = (field or _stationary_field)(lon, lat) + rng.normal(0, noise, n)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(a), float(b)]},
                "properties": {value_field: float(v)},
            }
            for a, b, v in zip(lon, lat, z)
        ],
    }


def _metric_plane(n=300, seed=7):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 10000, (n, 2))
    z = (
        5.0 * np.exp(-((xy[:, 0] - 3000) ** 2 + (xy[:, 1] - 3000) ** 2) / (2 * 2000 ** 2))
        + 3.0 * np.exp(-((xy[:, 0] - 7000) ** 2 + (xy[:, 1] - 6500) ** 2) / (2 * 1500 ** 2))
        + rng.normal(0, 0.3, n)
    )
    return xy, z


# ── variogram core ──────────────────────────────────────────────────────────


def test_variogram_models_fit_and_rank():
    xy, z = _metric_plane()
    fits = {m: fit_variogram(xy, z, model=m) for m in ("spherical", "exponential", "gaussian")}
    for m, f in fits.items():
        assert f.model == m
        assert f.sill > 0
        assert f.range_m > 0
        assert f.nugget >= 0
        assert f.rss >= 0
        assert f.n_lags >= 4
    auto = fit_variogram(xy, z, model="auto")
    best_manual = min(fits.values(), key=lambda f: f.rss)
    assert auto.model == best_manual.model


def test_variogram_deterministic_under_permutation():
    xy, z = _metric_plane()
    rng = np.random.default_rng(3)
    perm = rng.permutation(len(z))
    f1 = fit_variogram(xy, z, model="spherical")
    f2 = fit_variogram(xy[perm], z[perm], model="spherical")
    assert f1.model == f2.model
    assert abs(f1.sill - f2.sill) < 1e-6
    assert abs(f1.range_m - f2.range_m) < 1e-3
    assert abs(f1.nugget - f2.nugget) < 1e-6


def test_stratified_subsample_bounds_and_spread():
    rng = np.random.default_rng(5)
    xy = rng.uniform(0, 1000, (5000, 2))
    z = rng.normal(0, 1, 5000)
    sxy, sz = stratified_subsample(xy, z, 500)
    assert len(sz) <= 500
    # spread: the kept bbox covers most of the input extent
    assert (sxy[:, 0].max() - sxy[:, 0].min()) > 0.8 * (xy[:, 0].max() - xy[:, 0].min())
    assert (sxy[:, 1].max() - sxy[:, 1].min()) > 0.8 * (xy[:, 1].max() - xy[:, 1].min())
    # determinism
    sxy2, sz2 = stratified_subsample(xy, z, 500)
    np.testing.assert_array_equal(sxy, sxy2)
    np.testing.assert_array_equal(sz, sz2)


def test_empirical_variogram_pair_budget():
    rng = np.random.default_rng(9)
    n = 4000
    xy = rng.uniform(0, 5000, (n, 2))
    z = rng.normal(0, 1, n)
    lags, gamma, counts = empirical_variogram(xy, z, max_pairs=50_000)
    assert counts.sum() <= 50_000 + n  # last row may overshoot by < one row
    assert len(lags) >= 4
    assert (gamma >= -1e-9).all()


# ── ordinary kriging prediction + uncertainty ───────────────────────────────


def test_ok_exact_interpolation_at_samples():
    xy, z = _metric_plane()
    v = fit_variogram(xy, z, model="spherical")
    res = ordinary_kriging(xy, z, xy[:60], v, k=12)
    np.testing.assert_allclose(res.predictions, z[:60], atol=0.05)
    # exact interpolation at sample sites → near-zero variance there
    assert res.variances.max() < 1e-3


def test_ok_uncertainty_grows_away_from_samples():
    xy, z = _metric_plane()
    v = fit_variogram(xy, z, model="spherical")
    center = np.array([[5000.0, 5000.0]])
    far = np.array([[45000.0, 45000.0]])
    near_var = ordinary_kriging(xy, z, center, v, k=12).variances[0]
    far_var = ordinary_kriging(xy, z, far, v, k=12).variances[0]
    assert far_var > near_var
    assert far_var > 0


def test_ok_accuracy_beats_naive_mean_on_stationary_field():
    """Kriging CV must at least beat the field-mean predictor."""
    xy, z = _metric_plane()
    cv = cross_validate_kriging(xy, z, model="spherical")
    assert cv.rmse is not None
    mean_rmse = float(np.sqrt(np.mean((z - z.mean()) ** 2)))
    assert cv.rmse < mean_rmse, (
        f"kriging CV rmse {cv.rmse:.3f} should beat mean-baseline {mean_rmse:.3f}"
    )
    assert cv.mae is not None and cv.bias is not None


def test_ok_gaussian_oscillation_is_bounded():
    """The gaussian-model ill-conditioning must be stabilized: predictions
    stay within the sill-scaled clamp, degraded cells are counted."""
    xy, z = _metric_plane(seed=7)
    v = fit_variogram(xy, z, model="gaussian")
    rng = np.random.default_rng(1)
    grid = rng.uniform(0, 10000, (2000, 2))
    res = ordinary_kriging(xy, z, grid, v, k=12)
    lo = z.min() - 3.0 * math.sqrt(v.sill) - 1e-6
    hi = z.max() + 3.0 * math.sqrt(v.sill) + 1e-6
    assert res.predictions.min() >= lo
    assert res.predictions.max() <= hi
    assert (res.variances >= 0).all()


def test_cv_declines_below_min_samples():
    xy, z = _metric_plane(n=15)
    cv = cross_validate_kriging(xy, z)
    assert cv.rmse is None
    assert "无法进行可靠的交叉验证" in cv.note


# ── CRS contract ────────────────────────────────────────────────────────────


def test_declared_crs_rejected_structured():
    fc = _points_fc()
    with pytest.raises(KrigingCrsError) as ei:
        kriging_interpolation(fc, "pm25", declared_crs="EPSG:9999")
    assert "EPSG:9999" in str(ei.value)
    assert "静默" in str(ei.value)


@pytest.mark.parametrize("declared", ["EPSG:4326", "EPSG:4490", None])
def test_degree_crs_route_to_projected(declared):
    fc = _points_fc(n=150, seed=3)
    out = kriging_interpolation(
        fc, "pm25", resolution=6, declared_crs=declared, cross_validate=False
    )
    m = out["metadata"]
    assert m["working_crs"].startswith("EPSG:326") or m["working_crs"].startswith("EPSG:327")
    assert m["declared_crs"] == (declared or "EPSG:4326")
    assert len(out["records"]) > 0


def test_metric_crs_pass_through_3857():
    import geopandas as gpd

    fc = _points_fc(n=150, seed=3)
    # re-project the fixture coordinates to 3857 and declare it
    feats = fc["features"]
    g = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(
            [f["geometry"]["coordinates"][0] for f in feats],
            [f["geometry"]["coordinates"][1] for f in feats],
        ),
        crs="EPSG:4326",
    ).to_crs("EPSG:3857")
    fc_3857 = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(x), float(y)]},
                "properties": f["properties"],
            }
            for f, x, y in zip(feats, g.geometry.x, g.geometry.y)
        ],
    }
    out = kriging_interpolation(
        fc_3857, "pm25", resolution=6, declared_crs="EPSG:3857", cross_validate=False
    )
    m = out["metadata"]
    assert m["working_crs"] == "EPSG:3857"
    # bbox must be the real lon/lat extent of the fixture (Chengdu)
    assert 103.0 < m["bbox"][0] < 105.0
    assert 30.0 < m["bbox"][1] < 31.0


def test_degree_distance_discriminant():
    """Degree-space distances must NOT feed the variogram: the same physical
    field expressed at two latitudes yields materially different raw degree
    spans, but the projected fit must stay comparable (range in metres)."""
    rng = np.random.default_rng(21)
    vfits = []
    for lat0 in (30.0, 60.0):
        n = 250
        lon = rng.uniform(0.5, 1.5, n)
        lat = lat0 + rng.uniform(-0.5, 0.5, n)
        z = np.exp(-((lon - 1.0) ** 2 + (lat - lat0) ** 2) / (2 * 0.2 ** 2)) + rng.normal(0, 0.05, n)
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(a), float(b)]},
                 "properties": {"v": float(vv)}}
                for a, b, vv in zip(lon, lat, z)
            ],
        }
        out = kriging_interpolation(fc, "v", resolution=6, cross_validate=False)
        vfits.append(out["metadata"]["variogram"])
    # both fits are in METRES; a degree-space bug would shrink the
    # high-latitude range by ~cos(60°)=0.5
    ratio = vfits[1]["range_meters"] / max(vfits[0]["range_meters"], 1e-9)
    assert 0.7 < ratio < 1.4, f"projected range must be latitude-invariant, got ratio={ratio:.2f}"


# ── driver contracts ────────────────────────────────────────────────────────


def test_driver_two_first_class_outputs_and_metadata():
    fc = _points_fc()
    out = kriging_interpolation(fc, "pm25", resolution=7)
    m = out["metadata"]
    recs = out["records"]
    assert len(recs) > 0
    for r in recs:
        assert set(r) >= {"h3_index", "value", "kriging_variance", "kriging_stddev"}
        assert r["kriging_stddev"] >= 0
    for key in (
        "algorithm", "declared_crs", "working_crs", "bbox", "resolution",
        "n_samples", "n_fit_samples", "variogram", "cross_validation",
        "value_range", "variance_range", "value_field",
    ):
        assert key in m, f"metadata missing {key}"
    assert m["algorithm"] == "interpolation.kriging"
    assert m["n_fit_samples"] <= MAX_FIT_POINTS
    cv = m["cross_validation"]
    assert cv is not None and cv["n_samples"] == m["n_samples"]


def test_driver_rejects_too_few_points():
    fc = _points_fc(n=MIN_SAMPLES - 1, seed=2)
    with pytest.raises(KrigingInputError, match="至少"):
        kriging_interpolation(fc, "pm25")


def test_driver_rejects_bad_model_name():
    fc = _points_fc(n=50, seed=2)
    with pytest.raises(KrigingInputError, match="variogram_model"):
        kriging_interpolation(fc, "pm25", variogram_model="cubic")


def test_driver_missing_field_raises():
    fc = _points_fc(n=50, seed=2)
    with pytest.raises(ValueError, match="nope"):
        kriging_interpolation(fc, "nope")


# ── tool wrapper ────────────────────────────────────────────────────────────


def _tool_registry():
    from app.tools.registry import ToolRegistry
    from app.tools.advanced_spatial import register_advanced_spatial_tools

    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)
    return reg


def test_kriging_tool_registered_with_heavy_policy():
    reg = _tool_registry()
    assert "kriging_interpolation" in reg.list_tools()
    meta = reg.metadata("kriging_interpolation")
    assert meta.get("cost") == "heavy"
    assert meta.get("execution_policy") in ("celery", "thread")


def test_kriging_tool_end_to_end_shape():
    reg = _tool_registry()
    fc = _points_fc(n=120, seed=13)
    result = asyncio.run(
        reg.dispatch("kriging_interpolation", {
            "geojson": fc, "value_field": "pm25", "resolution": 6,
            "cross_validate": False,
        })
    )
    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) > 0
    p0 = result["features"][0]["properties"]
    assert "pm25" in p0 and "kriging_stddev" in p0 and "kriging_variance" in p0
    # second first-class surface
    unc = result["uncertainty"]
    assert unc["type"] == "FeatureCollection"
    assert len(unc["features"]) == len(result["features"])
    assert "kriging_stddev" in unc["features"][0]["properties"]
    # metadata + honest summary
    assert result["kriging_metadata"]["algorithm"] == "interpolation.kriging"
    assert "克里金" in result["summary"]


def test_kriging_tool_fc_crs_member_4490_honored():
    reg = _tool_registry()
    fc = _points_fc(n=120, seed=13)
    fc["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4490"}}
    result = asyncio.run(
        reg.dispatch("kriging_interpolation", {
            "geojson": fc, "value_field": "pm25", "resolution": 6, "cross_validate": False,
        })
    )
    assert result["kriging_metadata"]["declared_crs"] == "EPSG:4490"


def test_kriging_tool_invalid_crs_structured_error():
    """Unsupported declared CRS surfaces as the registry's structured error
    envelope (code VALIDATION_ERROR) carrying the CRS rejection message —
    never a silent WGS84 fallback."""
    reg = _tool_registry()
    fc = _points_fc(n=120, seed=13)
    result = asyncio.run(
        reg.dispatch("kriging_interpolation", {
            "geojson": fc, "value_field": "pm25", "declared_crs": "EPSG:31370",
        })
    )
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert result.get("code") == "VALIDATION_ERROR"
    assert "EPSG:31370" in result.get("message", "")
    assert "静默" in result.get("message", "")


# ── resolver + planner routing matrix ───────────────────────────────────────


def test_resolver_default_prefers_idw_hint_pins_kriging():
    r = _resolver()
    tools = set(_tool_registry().list_tools())
    prof = {"geometryTypes": ["Point"], "featureCount": 200}
    default = r.resolve("spatial_interpolation", profile=prof, available_tools=tools)
    assert default.algorithm == "interpolation.idw"
    hinted = r.resolve(
        "spatial_interpolation", profile=prof, available_tools=tools,
        algorithm_hint="interpolation.kriging",
    )
    assert hinted.algorithm == "interpolation.kriging"
    assert "explicit_request=interpolation.kriging" in hinted.reason


def test_resolver_kriging_min_features_falls_back_with_evidence():
    r = _resolver()
    tools = set(_tool_registry().list_tools())
    prof = {"geometryTypes": ["Point"], "featureCount": 6}
    res = r.resolve(
        "spatial_interpolation", profile=prof, available_tools=tools,
        algorithm_hint="interpolation.kriging",
    )
    assert res.algorithm == "interpolation.idw"
    assert any("insufficient_features:interpolation.kriging" in x for x in res.rejected)


def _resolver():
    from app.lib.gis.algorithm_resolver import AlgorithmResolver
    return AlgorithmResolver()


@pytest.mark.parametrize(
    "query,expected_algo",
    [
        ("用克里金插值成都PM2.5站点数据", "interpolation.kriging"),
        ("对成都气温站点做插值", "interpolation.idw"),
    ],
)
def test_planner_routes_interpolation_queries(query, expected_algo):
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    intent = resolve_map_request_intent(query)
    assert intent.task == "raster_distribution"
    plan = MapProductPlanner().plan_from_intent(intent, use_memo=False)
    assert plan.recipe_id == "raster_distribution"
    sel = [s for s in plan.algorithm_selections if s.capability == "spatial_interpolation"]
    assert sel, "interpolation capability must be planned for interpolation queries"
    assert sel[0].algorithm == expected_algo


@pytest.mark.parametrize(
    "query",
    ["查看成都DEM高程分布", "成都小学分布情况", "计算成都NDVI植被指数"],
)
def test_planner_does_not_overplan_interpolation(query):
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    intent = resolve_map_request_intent(query)
    plan = MapProductPlanner().plan_from_intent(intent, use_memo=False)
    caps = [s.capability for s in plan.algorithm_selections]
    assert "spatial_interpolation" not in caps, (
        f"non-interpolation query must not plan spatial_interpolation: {query}"
    )


# ── numerics-review regressions (nuggety data, antimeridian, CRS member) ────


def test_ok_survives_nugget_dominated_data():
    """Review #1/#2: with real noise the fitted nugget is non-zero — the OK
    system must use the CANONICAL construction (nugget in every off-diagonal
    γ(h), γ₀ included, zero diagonal). The buggy diagonal-only placement had
    CV RMSE ~4.95 (worse than the mean baseline) and variance >> sill."""
    rng = np.random.default_rng(7)
    n = 300
    xy = rng.uniform(0, 10000, (n, 2))
    z = (
        5.0 * np.exp(-((xy[:, 0] - 3000) ** 2 + (xy[:, 1] - 3000) ** 2) / (2 * 2000 ** 2))
        + rng.normal(0, 1.5, n)
    )
    for model in ("spherical", "exponential", "gaussian"):
        v = fit_variogram(xy, z, model=model)
        assert v.nugget > 0.1, f"fixture must fit a real nugget ({model})"
        cv = cross_validate_kriging(xy, z, model=model)
        mean_rmse = float(np.sqrt(np.mean((z - z.mean()) ** 2)))
        assert cv.rmse is not None and cv.rmse < mean_rmse, (
            f"{model}: kriging CV ({cv.rmse:.2f}) must beat the mean baseline "
            f"({mean_rmse:.2f}) on nuggety data"
        )
        # variance bounded by the (fitted) total sill + nugget scale
        res = ordinary_kriging(xy, z, xy[:60], v, k=12)
        assert res.variances.max() <= (v.sill + v.nugget) * 1.5 + 1e-6


def test_driver_antimeridian_split_bbox():
    """Review #5: a dataset straddling ±180° must produce cells (IDW-parity
    split bbox), not the misleading 'polyfill returned 0 cells' rejection."""
    rng = np.random.default_rng(5)
    feats = []
    for _ in range(60):
        lon = rng.uniform(179.5, 180.0) if rng.random() < 0.5 else rng.uniform(-180.0, -179.5)
        lat = rng.uniform(-16.0, -15.0)  # avoid the polar guard
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"v": float(rng.uniform(0, 10))},
        })
    fc = {"type": "FeatureCollection", "features": feats}
    out = kriging_interpolation(fc, "v", resolution=6, cross_validate=False)
    assert len(out["records"]) > 0, "antimeridian dataset must produce cells"


def test_tool_rejects_unknown_fc_crs_member():
    """Review #6: an FC-level crs member naming an unsupported CRS is a
    structured rejection — never a silent lon/lat read of projected data."""
    import asyncio

    reg = _tool_registry()
    fc = _points_fc(n=120, seed=13)
    fc["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::22523"}}
    result = asyncio.run(
        reg.dispatch("kriging_interpolation", {
            "geojson": fc, "value_field": "pm25", "cross_validate": False,
        })
    )
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert "22523" in result.get("message", "")


def test_tool_accepts_wgs84_urn_crs_member():
    import asyncio

    reg = _tool_registry()
    fc = _points_fc(n=120, seed=13)
    fc["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}}
    result = asyncio.run(
        reg.dispatch("kriging_interpolation", {
            "geojson": fc, "value_field": "pm25", "resolution": 6, "cross_validate": False,
        })
    )
    assert result["kriging_metadata"]["declared_crs"] == "EPSG:4326"


def test_cv_reports_folds_actually_used():
    """Review #7: fold count in the report = folds that produced metrics."""
    xy, z = _metric_plane(n=40)  # folds capped by n//4 → 10? no: min(5, 10) = 5
    cv = cross_validate_kriging(xy, z, model="spherical")
    assert cv.rmse is not None
    assert 1 <= cv.folds <= 5
