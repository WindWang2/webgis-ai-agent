"""P3 review-fix regressions for app/lib/geo_analysis.

Each test pins one finding from the P3 fix task; every behavioural test here
fails on the pre-fix code (see the task file for the finding locations).
"""
from __future__ import annotations

import sys
import warnings

import numpy as np
import pytest

pytestmark = pytest.mark.unit


# ── aggregation / cokriging / dasymetric ──────────────────────────────

def test_generate_fishnet_invalid_bounds_returns_failure_result():
    from app.lib.geo_analysis.aggregation import generate_fishnet

    for bad in (None, (1.0, 2.0, 3.0), 5):
        res = generate_fishnet(bad, 10.0)
        assert res.success is False
        assert res.error_type == "ValueError"
        assert "bounds" in res.summary


def test_fit_lmc_rejects_nan_rho_from_zero_variance_paired_subset():
    from app.lib.gis.scientific_errors import NumericalInstability
    from app.lib.geo_analysis.cokriging_lmc import fit_lmc

    pts1 = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    z1 = np.array([1.0, 2.0, 3.0])
    pts2 = np.array([[0.0, 0.0], [1.0, 0.0], [100.0, 0.0]])
    z2 = np.array([1.0, 1.0, 9.0])  # NN-paired subset is constant -> rho NaN
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(NumericalInstability):
            fit_lmc(pts1, z1, pts2, z2)


def test_parse_weights_filters_infinite_weights():
    import geopandas as gpd
    from shapely.geometry import Point

    from app.lib.geo_analysis.dasymetric import _parse_weights

    anc = gpd.GeoDataFrame(
        {"w": [1.0, np.inf, -np.inf, -2.0, np.nan]},
        geometry=[Point(0, 0)] * 5, crs="EPSG:32632")
    weights, stats = _parse_weights(anc, "w")
    assert weights == [1.0, 0.0, 0.0, 0.0, 0.0]
    assert stats["nan_weights"] == 3   # +inf, -inf, NaN
    assert stats["clamped_weights"] == 1


# ── ecology ───────────────────────────────────────────────────────────

def test_hsi_meta_discloses_non_finite_variable_counts():
    from app.lib.geo_analysis.ecology import hsi_score_features

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"x": float("nan")},
         "geometry": None},
        {"type": "Feature", "properties": {"x": 20.0}, "geometry": None},
    ]}
    _, meta = hsi_score_features(
        fc, [{"field": "x", "curve": "trapezoid", "params": [10, 15, 25, 30]}])
    assert meta["variables"][0]["non_finite_ignored"] == 1


def test_landscape_metrics_rejects_non_positive_cell_size_x():
    from app.lib.geo_analysis.ecology import landscape_metrics

    with pytest.raises(ValueError, match="cell_size_x"):
        landscape_metrics(np.ones((2, 2)), cell_size=1.0, cell_size_x=0.0)
    with pytest.raises(ValueError, match="cell_size_x"):
        landscape_metrics(np.ones((2, 2)), cell_size=1.0,
                          cell_size_x=float("nan"))


# ── interpolation / compare ───────────────────────────────────────────

def test_antimeridian_halves_keep_bbox_buffer(monkeypatch):
    import app.lib.geo_analysis.interpolation as ip

    calls = []

    def _fake_geo_to_cells(poly, resolution):
        calls.append(poly)
        return {f"cell-{len(calls)}"}

    monkeypatch.setattr(ip.h3, "geo_to_cells", _fake_geo_to_cells)
    lonlat = np.array([[179.5, 10.0], [-179.5, 10.0]])
    cells, _ = ip._target_cells_for_samples(lonlat, 5)
    assert cells == ["cell-1", "cell-2"]
    lons_a = [p[0] for p in calls[0]["coordinates"][0]]
    lons_b = [p[0] for p in calls[1]["coordinates"][0]]
    assert min(lons_a) == pytest.approx(179.5 - 0.009)
    assert max(lons_b) == pytest.approx(-179.5 + 0.009)


def test_compare_metadata_n_skipped_includes_budget_skips():
    from app.lib.geo_analysis.interpolation_compare import (
        compare_interpolation_models,
    )

    feats = []
    for i in range(5):
        for j in range(4):
            feats.append({
                "type": "Feature",
                "properties": {"v": float(i * 4 + j)},
                "geometry": {"type": "Point",
                             "coordinates": [10.0 + i * 0.01, 50.0 + j * 0.01]},
            })
    res = compare_interpolation_models(
        {"type": "FeatureCollection", "features": feats}, "v", cv_budget=20)
    n_skipped = sum(1 for row in res["comparison"] if not row["eligible"])
    assert n_skipped == 4
    assert res["metadata"]["n_skipped"] == n_skipped


# ── measurements / phenology / point pattern ───────────────────────────

def test_surface_area_blocked_computation_matches_full_formula(monkeypatch):
    import app.lib.geo_analysis.measurements as meas

    dem = np.random.default_rng(0).normal(0.0, 5.0, (5, 7))
    dem[0, 0] = np.nan
    full, _ = meas.surface_area(dem, 2.0)
    monkeypatch.setattr(meas, "_SURFACE_BLOCK_QUADS", 4)
    blocked, _ = meas.surface_area(dem, 2.0)
    assert blocked["surface_area_m2"] == pytest.approx(
        full["surface_area_m2"], abs=1e-9)
    assert blocked["planar_area_m2"] == full["planar_area_m2"]


def test_temporal_anomaly_all_nan_pixel_emits_no_runtime_warning():
    from app.lib.geo_analysis import phenology, temporal_cube as tc

    stack = np.ones((4, 2, 2))
    stack[:, 0, 0] = np.nan
    cube = tc.build_cube(stack, np.arange(4.0))
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        res = phenology.temporal_anomaly(cube)
    assert np.isnan(res["features"]["change_mean"][0, 0])


def test_k_curve_builds_tree_once(monkeypatch):
    import scipy.spatial

    import app.lib.geo_analysis.point_pattern as pp

    real_tree = scipy.spatial.cKDTree
    builds = {"n": 0}

    class _CountingTree(real_tree):
        def __init__(self, *args, **kwargs):
            builds["n"] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(scipy.spatial, "cKDTree", _CountingTree)
    xy = np.random.default_rng(0).uniform(0.0, 10.0, (20, 2))
    r_grid = np.linspace(0.1, 3.0, 5)
    pp._k_curve(xy, 100.0, (0.0, 0.0, 10.0, 10.0), r_grid)
    assert builds["n"] == 1


# ── raster math / mosaic / ops ────────────────────────────────────────

def test_validate_scheme_rejects_non_numeric_value():
    from app.lib.geo_analysis.raster_math import _validate_scheme

    with pytest.raises(ValueError, match="numeric"):
        _validate_scheme([{"value": "high", "min": 0, "max": 1}])
    _validate_scheme([{"value": 1, "min": 0, "max": 1}])


def test_raster_calculator_exits_b_reader_with_live_exception(
        tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin

    import app.lib.geo_analysis.raster_math as rm

    transform = from_origin(0, 4, 1, 1)
    paths = []
    for name, value in (("a.tif", 1.0), ("b.tif", 2.0)):
        path = str(tmp_path / name)
        with rasterio.open(path, "w", driver="GTiff", height=4, width=4,
                           count=1, dtype="float32", crs="EPSG:32632",
                           transform=transform) as dst:
            dst.write(np.full((4, 4), value, dtype="float32"), 1)
        paths.append(path)

    class _FakeCM:
        def __init__(self):
            self.exit_args = None

        def __enter__(self):
            return (object(), None)

        def __exit__(self, *args):
            self.exit_args = args
            return False

    cm = _FakeCM()

    def _boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(rm, "aligned_reader", lambda *a, **k: cm)
    monkeypatch.setattr(rm, "iter_bounded_windows", _boom)
    with pytest.raises(ValueError, match="boom"):
        rm.raster_calculator(paths[0], paths[1], "A + B")
    assert cm.exit_args is not None
    assert cm.exit_args[0] is ValueError


def test_mosaic_nodata_prefill_handles_column_blocks(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin

    import app.lib.geo_analysis.raster_mosaic as mosaic

    transform = from_origin(0, 4, 1, 1)
    tile_path = str(tmp_path / "tile.tif")
    with rasterio.open(tile_path, "w", driver="GTiff", height=4, width=6,
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=transform, nodata=-1.0) as dst:
        dst.write(np.full((4, 6), 7.0, dtype="float32"), 1)

    monkeypatch.setattr(mosaic, "_MOSAIC_FILL_BLOCK_COLS", 2)
    out = str(tmp_path / "mosaic.tif")
    mosaic.mosaic_tiles(
        [{"path": tile_path,
          "core_window": {"col_off": 2, "row_off": 0, "width": 2, "height": 4}}],
        out_path=out, width=6, height=4, crs="EPSG:4326",
        transform=list(transform)[:6])
    with rasterio.open(out) as src:
        arr = src.read(1)
    assert (arr[:, 0] == -1.0).all()
    assert (arr[:, 1] == -1.0).all()
    assert (arr[:, 4] == -1.0).all()
    assert (arr[:, 5] == -1.0).all()
    np.testing.assert_array_equal(arr[:, 2:4],
                                  np.full((4, 2), 7.0, dtype="float32"))


def test_windowed_zonal_stats_rejects_unrecognized_input_shapes(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    import app.lib.geo_analysis.raster_ops as ops

    raster = str(tmp_path / "r.tif")
    with rasterio.open(raster, "w", driver="GTiff", height=4, width=4,
                       count=1, dtype="float32", crs="EPSG:32632",
                       transform=from_origin(0, 4, 1, 1)) as dst:
        dst.write(np.ones((4, 4), dtype="float32"), 1)

    with pytest.raises(ValueError, match="FeatureCollection"):
        ops._windowed_zonal_stats(
            {"type": "FeatureCollection"}, raster, stats=["mean"])
    with pytest.raises(ValueError, match="must be a dict"):
        ops._windowed_zonal_stats([1, 2], raster, stats=["mean"])
    assert ops._windowed_zonal_stats(
        {"type": "FeatureCollection", "features": []}, raster,
        stats=["mean"]) == []


# ── RS cube pipeline / samples / v3 ──────────────────────────────────

def _pipeline_descriptors():
    from app.lib.geo_analysis import rs_cube_descriptor as rcd

    grid = {"crs": "EPSG:32650", "width": 4, "height": 4,
            "transform": (10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0)}
    opt_times = [f"2024-0{m}-15" for m in range(3, 9)]
    opt_assets = []
    for t in opt_times:
        opt_assets.append({"ref": f"ref:raster/s2-{t}", "time_iso": t,
                           "role": "optical", "band": "nir"})
        opt_assets.append({"ref": f"ref:raster/s2-red-{t}", "time_iso": t,
                           "role": "optical", "band": "red"})
    optical = rcd.build_cube_descriptor(
        cube_id="cube-opt-p3", grid=grid, assets=opt_assets,
        source_version="S2_TEST")
    sar_assets = [{"ref": f"ref:raster/s1-{m}", "time_iso": f"2024-0{m}-17",
                   "role": "sar", "polarization": "vv"} for m in range(3, 9)]
    sar = rcd.build_cube_descriptor(
        cube_id="cube-sar-p3", grid=grid, assets=sar_assets,
        source_version="S1_TEST")
    return optical, sar


def _pipeline_stacks():
    import datetime as dt

    rng = np.random.default_rng(11)
    opt = np.clip(rng.normal(0.5, 0.1, (6, 4, 4)), 0, 1)
    sar = rng.normal(-12.0, 1.0, (6, 4, 4))
    times_opt = np.array([
        dt.datetime(2024, m, 15, tzinfo=dt.timezone.utc).timestamp()
        for m in range(3, 9)])
    times_sar = np.array([
        dt.datetime(2024, m, 17, tzinfo=dt.timezone.utc).timestamp()
        for m in range(3, 9)])
    return opt, times_opt, sar, times_sar


def _run_pipeline(optical, sar, opt, times_opt, sar_stack, times_sar, **kw):
    from app.lib.geo_analysis import rs_cube_pipeline as rcp

    kw.setdefault("optical_gap_codes", [None] * 6)
    return rcp.run_temporal_cube_pipeline(
        optical, sar, optical_stack=opt, optical_times_sec=times_opt,
        sar_stack=sar_stack, sar_times_sec=times_sar, tolerance_days=5, **kw)


def test_pipeline_rejects_valid_sentinel_as_optical_gap_code():
    optical, sar = _pipeline_descriptors()
    opt, times_opt, sar_stack, times_sar = _pipeline_stacks()
    with pytest.raises(ValueError, match="GAP_CODES"):
        _run_pipeline(optical, sar, opt, times_opt, sar_stack, times_sar,
                      optical_gap_codes=["valid"] + [None] * 5)


def test_pipeline_rejects_unknown_sar_gap_code():
    optical, sar = _pipeline_descriptors()
    opt, times_opt, sar_stack, times_sar = _pipeline_stacks()
    with pytest.raises(ValueError, match="GAP_CODES"):
        _run_pipeline(optical, sar, opt, times_opt, sar_stack, times_sar,
                      sar_gap_codes=["typo"] + [None] * 5)


def test_pipeline_passes_descriptor_nodata_to_optical_features():
    optical, sar = _pipeline_descriptors()
    optical.nodata = -9999.0
    opt, times_opt, sar_stack, times_sar = _pipeline_stacks()
    opt[:, 0, 0] = -9999.0
    out = _run_pipeline(optical, sar, opt, times_opt, sar_stack, times_sar)
    assert np.isnan(out["optical_features"]["features"]["p50"][0, 0])


def test_polygon_centroid_is_area_centroid_not_vertex_mean():
    from app.lib.geo_analysis.rs_samples import _polygon_centroid

    ring = [[0, 0], [10, 0], [10, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    cx, cy = _polygon_centroid([ring])
    assert (cx, cy) == pytest.approx((5.0, 5.0))


def test_attach_polygon_samples_converts_feature_arrays_once():
    from app.lib.geo_analysis.rs_samples import attach_polygon_samples

    class _Tracker:
        def __init__(self, arr):
            self.arr = arr
            self.calls = 0

        def __array__(self, dtype=None, copy=None):
            self.calls += 1
            return np.asarray(self.arr, dtype=dtype)

    tracker = _Tracker(np.zeros((4, 4)))
    grid = {"transform": (1, 0, 0, 0, -1, 4), "height": 4, "width": 4}
    polys = [
        {"type": "Polygon",
         "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
         "properties": {"id": "a"}},
        {"type": "Polygon",
         "coordinates": [[[2, 0], [3, 0], [3, 1], [2, 1], [2, 0]]],
         "properties": {"id": "b"}},
    ]
    out = attach_polygon_samples({"f": tracker}, grid, polys)
    assert [r["cell_count"] for r in out["records"]] == [1, 1]
    assert tracker.calls == 1


def test_robust_normalize_checks_reference_scale(monkeypatch):
    import app.lib.geo_analysis.rs_v3 as rv
    from app.lib.gis.scientific_errors import ResourceScaleMismatch

    monkeypatch.setattr(rv, "RS_SCALE_LIMIT_CELLS", 6)
    with pytest.raises(ResourceScaleMismatch):
        rv.robust_normalize(
            np.ones((1, 2, 3)), method="percentile_match",
            reference=np.ones((1, 2, 4)))


def test_robust_normalize_rejects_all_nodata_band():
    import app.lib.geo_analysis.rs_v3 as rv
    from app.lib.gis.scientific_errors import DegenerateData

    rng = np.random.default_rng(0)
    stack = np.stack([np.full((2, 3), np.nan), rng.normal(0, 1, (2, 3))])
    with pytest.raises(DegenerateData, match=r"波段 \[0\]"):
        rv.robust_normalize(stack, method="percentile_stretch")


def test_cloud_qc_all_nan_red_nir_raises_no_valid_observations():
    import app.lib.geo_analysis.rs_v3 as rv
    from app.lib.gis.scientific_errors import NoValidObservations

    nan = np.full((3, 3), np.nan)
    with pytest.raises(NoValidObservations):
        rv.cloud_qc_basic(nan, nan.copy())


def test_rx_anomaly_fraction_uses_valid_pixel_denominator():
    import app.lib.geo_analysis.rs_v3 as rv

    g = np.full((2, 3, 3), np.nan)
    g[:, 0, 0] = [100.0, 120.0]
    g[:, 0, 1] = [4.0, 6.0]
    g[:, 1, 0] = [5.0, 7.0]
    g[:, 1, 1] = [4.5, 5.5]
    g[:, 1, 2] = [5.2, 4.8]
    g[:, 2, 0] = [6.0, 5.0]
    g[:, 2, 1] = [4.9, 5.1]
    g[:, 2, 2] = [6.1, 5.9]
    res = rv.rx_anomaly(g, threshold_sigma=0.1)
    assert res["n_valid_pixels"] == 8
    assert int(res["anomaly_mask"].sum()) > 0
    assert res["anomaly_fraction"] == pytest.approx(
        int(res["anomaly_mask"].sum()) / res["n_valid_pixels"])


# ── sar calibration / regression / EHA ───────────────────────────────

def test_remove_thermal_noise_rejects_negative_lut_even_with_nan():
    from app.lib.geo_analysis.sar_calibration import remove_thermal_noise

    lut = np.array([[2.0, np.nan, -1.0]])
    with pytest.raises(ValueError, match="负值"):
        remove_thermal_noise(np.full((1, 3), 5.0), noise_lut=lut)


def test_gwr_perfect_fit_emits_null_aic_not_neg_inf(monkeypatch):
    import app.lib.geo_analysis.spatial_regression as sr

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {"y": float(i), "x": float(i % 3)},
         "geometry": {"type": "Point",
                      "coordinates": [10.0 + i * 0.01, 50.0]}}
        for i in range(10)
    ]}

    def _fake_fit(coords, y, x_mat, bandwidth, leave_self_out):
        n, p = x_mat.shape
        return np.zeros((n, p)), np.asarray(y, float).copy(), 0.0, float(p)

    def _fake_r2(coords, y, x_mat, betas, bandwidth):
        return np.ones(len(y))

    monkeypatch.setattr(sr, "_gwr_local_fit", _fake_fit)
    monkeypatch.setattr(sr, "_gwr_local_r2", _fake_r2)
    res = sr.gwr_regression_narrated(fc, "y", ["x"], bandwidth=5)
    assert res.success is True
    assert res.data["aic"] is None
    assert res.data["aicc"] is None
    assert "AICc=不可用" in res.summary


def test_eha_checkpoints_cover_period_and_bin_loops():
    from app.lib.cancellation import CURRENT_TOKEN, OperationCancelled
    from app.lib.geo_analysis.spatiotemporal_eha import (
        emerging_hotspot_narrated,
    )

    class _CancelAfter:
        def __init__(self, after):
            self.after = after
            self.calls = 0

        @property
        def cancelled(self):
            self.calls += 1
            return self.calls > self.after

        reason = "test cancel"
        job_id = "p3-eha"

    coords = np.array([[i * 10.0, j * 10.0]
                       for i in range(3) for j in range(3)])
    counts = np.random.default_rng(0).poisson(5, (9, 4)).astype(float)

    for after in (0, 5):  # 0 -> period loop, 5 -> first bin checkpoint
        token = _CancelAfter(after)
        ctx = CURRENT_TOKEN.set(token)
        try:
            with pytest.raises(OperationCancelled):
                emerging_hotspot_narrated(counts, coords)
        finally:
            CURRENT_TOKEN.reset(ctx)


# ── statistics / diagnostics / terrain / smoothing ───────────────────

def _point_fc(values, field="v"):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {field: float(v)},
         "geometry": {"type": "Point",
                      "coordinates": [10.0 + (i % 4) * 0.01,
                                      50.0 + (i // 4) * 0.01]}}
        for i, v in enumerate(values)
    ]}


def test_bivariate_local_moran_import_error_is_typed(monkeypatch):
    from app.lib.geo_analysis.statistics import bivariate_local_moran_narrated

    monkeypatch.setitem(sys.modules, "esda", None)
    res = bivariate_local_moran_narrated(
        _point_fc([1, 2, 3, 4]), "v", "v", permutations=99)
    assert res.success is False
    assert res.error_type == "ImportError"


def test_hotspot_accepts_none_distance_band():
    from app.lib.geo_analysis.statistics import hotspot_narrated

    res = hotspot_narrated(_point_fc([1, 2, 3, 4, 5, 6]), "v",
                           distance_band=None)
    assert res.success is True


def test_local_join_count_expected_fp_uses_focal_family():
    from app.lib.geo_analysis.statistics_v3_diag import (
        local_join_count_narrated,
    )

    values = [1, 1, 0, 0]
    feats = []
    for i in range(2):
        for j in range(2):
            feats.append({
                "type": "Feature",
                "properties": {"bin": values[i * 2 + j]},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [j, i], [j + 1, i], [j + 1, i + 1], [j, i + 1], [j, i],
                ]]},
            })
    res = local_join_count_narrated(
        {"type": "FeatureCollection", "features": feats}, "bin",
        weights_scheme="rook")
    assert res.data["n_ones"] == 2
    assert res.data["expected_false_positives"] == pytest.approx(0.1)


def test_landform_percentile_starts_at_zero(monkeypatch):
    import app.lib.geo_analysis.terrain as terrain

    dem = (9.0 - np.arange(10, dtype=float)).reshape(2, 5)
    tpi = np.full((2, 5), -0.1)
    tpi[0, 0] = 0.9  # neutral s/l everywhere except the first pixel
    monkeypatch.setattr(
        terrain, "topographic_position_index",
        lambda z, window=3, nodata=None: (tpi.copy(), {}))
    res, _ = terrain.landform_classification(
        dem, 1.0, elevation_tolerance=0.1)
    # Lowest elevation pixel: p=0 (was 1/(n-1)=0.111 > tol before the fix).
    assert res["classes"][1, 4] == 4


def test_moving_average_even_window_is_centered_and_full_length():
    from app.lib.geo_analysis.ts_smoothing import smooth_gapfill

    v = np.array([0.0, 0.0, 10.0, 0.0, 0.0, 0.0])
    smoothed, _, meta = smooth_gapfill(
        v, method="moving_average", window_length=4, fill="none")
    assert len(smoothed) == len(v)
    assert meta["window_length"] == 3
    assert smoothed[1] == pytest.approx(smoothed[3])
    assert smoothed[0] == pytest.approx(0.0)
    assert smoothed[-1] == pytest.approx(0.0)
