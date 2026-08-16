"""
Comprehensive Unit Tests for Temporal GIS Runtime Engine.
Tests all 8 core components:
1. TemporalProfiler (profiler.py)
2. TemporalFilterEngine (filter.py)
3. TemporalAggregationEngine (aggregation.py)
4. TemporalChangeEngine (change.py)
5. TemporalTrendEngine (trend.py)
6. SpatiotemporalClusterEngine (spatiotemporal.py)
7. TemporalRasterEngine (raster.py)
8. TemporalEngine (engine.py)
"""

from datetime import datetime, timezone, timedelta
import pytest

from app.services.temporal import (
    TemporalProfiler,
    TemporalFilterEngine,
    TemporalAggregationEngine,
    TemporalChangeEngine,
    TemporalTrendEngine,
    SpatiotemporalClusterEngine,
    TemporalRasterEngine,
    TemporalEngine,
    TemporalFilter,
    TemporalGranularity,
    TemporalFieldType,
    TemporalUnit,
    TemporalMetric,
    SpatiotemporalHotspotResult,
    TemporalChangeResult,
    TemporalTrendResult,
    TemporalRasterResult,
)


@pytest.fixture
def sample_temporal_geojson():
    base_time = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    features = []
    # 10 daily records
    for i in range(10):
        t_iso = (base_time + timedelta(days=i)).isoformat()
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.40 + i * 0.01, 39.90 + i * 0.01]},
            "properties": {
                "id": f"feat_{i}",
                "timestamp": t_iso,
                "temperature": 20.0 + i * 1.5,
                "humidity": 50.0 - i * 0.5,
            }
        })
    return {"type": "FeatureCollection", "features": features}


@pytest.fixture
def sample_st_cluster_geojson():
    base_time = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    features = []
    # Cluster 1: 6 points near Guomao within 10 min
    for i in range(6):
        t_iso = (base_time + timedelta(minutes=i * 2)).isoformat()
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.458 + i * 0.0001, 39.908 + i * 0.0001]},
            "properties": {"id": f"c1_{i}", "timestamp": t_iso, "value": 10 + i}
        })
    # Cluster 2: 5 points near Sanlitun 5 hours later
    for i in range(5):
        t_iso = (base_time + timedelta(hours=5, minutes=i * 3)).isoformat()
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.455 + i * 0.0001, 39.935 + i * 0.0001]},
            "properties": {"id": f"c2_{i}", "timestamp": t_iso, "value": 20 + i}
        })
    # Noise point
    features.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [116.397, 39.908]},
        "properties": {"id": "noise_1", "timestamp": (base_time + timedelta(days=2)).isoformat(), "value": 99}
    })
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# 1. TemporalProfiler Tests
# ---------------------------------------------------------------------------

def test_temporal_profiler_auto_detect(sample_temporal_geojson):
    profiler = TemporalProfiler()
    profile = profiler.profile_dataset(sample_temporal_geojson)

    assert profile.overall_confidence > 0.5
    assert profile.primary_time_field is not None
    assert profile.primary_time_field.field_name == "timestamp"
    assert profile.primary_time_field.field_type in (TemporalFieldType.DATETIME, TemporalFieldType.DATE)
    assert profile.granularity == TemporalGranularity.DAY
    assert profile.temporal_extent is not None
    assert profile.temporal_extent.total_records == 10
    assert profile.temporal_extent.valid_time_records == 10
    assert profile.temporal_extent.missing_time_records == 0


def test_temporal_profiler_gaps():
    profiler = TemporalProfiler()
    base_time = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
    records = [
        {"properties": {"t": (base_time + timedelta(hours=1)).isoformat()}},
        {"properties": {"t": (base_time + timedelta(hours=2)).isoformat()}},
        {"properties": {"t": (base_time + timedelta(hours=3)).isoformat()}},
        # 10 hour gap
        {"properties": {"t": (base_time + timedelta(hours=13)).isoformat()}},
        {"properties": {"t": (base_time + timedelta(hours=14)).isoformat()}},
    ]

    profile = profiler.profile_dataset(records, primary_field="t")
    assert profile.granularity == TemporalGranularity.HOUR
    assert len(profile.detected_gaps) >= 1
    gap = profile.detected_gaps[0]
    assert gap["missing_steps"] >= 9


# ---------------------------------------------------------------------------
# 2. TemporalFilterEngine Tests
# ---------------------------------------------------------------------------

def test_temporal_filter_by_range(sample_temporal_geojson):
    filter_engine = TemporalFilterEngine()
    start = "2026-08-02T00:00:00Z"
    end = "2026-08-05T23:59:59Z"

    res = filter_engine.filter_dataset(
        sample_temporal_geojson,
        time_field="timestamp",
        start=start,
        end=end,
    )

    assert isinstance(res, dict)
    assert "features" in res
    # Days 2026-08-02, 03, 04, 05 -> 4 features
    assert len(res["features"]) == 4


def test_temporal_filter_relative_window(sample_temporal_geojson):
    filter_engine = TemporalFilterEngine()
    ref_time = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)

    # "last_7_days" from 2026-08-10 -> 2026-08-03 to 2026-08-10
    res = filter_engine.filter_dataset(
        sample_temporal_geojson,
        time_field="timestamp",
        relative_window="last_7_days",
        ref_time=ref_time,
    )

    assert len(res["features"]) >= 7


# ---------------------------------------------------------------------------
# 2b. #451: explicit-bounds precedence (tool's spec construction)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_filter_explicit_start_end_returns_matching_features(sample_temporal_geojson):
    """Issue #451: temporal_filter with explicit start_time/end_time built the
    spec WITHOUT an interval; the spec branch shadowed the explicit bounds in
    filter_dataset, so BETWEEN never matched — an always-empty success.
    Constructed exactly as app/tools/temporal_tools.temporal_filter does."""
    engine = TemporalEngine()
    t_filter = TemporalFilter(
        filter_type="range",
        start_time="2026-08-03T00:00:00Z",
        end_time="2026-08-06T12:00:00Z",
    )

    res = await engine.execute_filter(
        dataset=sample_temporal_geojson,
        temporal_field="timestamp",
        t_filter=t_filter,
    )

    feats = res.result_geojson["features"]
    # Daily-noon features 08-03 .. 08-06 inclusive → 4 (was 0 before the fix).
    assert len(feats) == 4
    assert {f["properties"]["id"] for f in feats} == {"feat_2", "feat_3", "feat_4", "feat_5"}


@pytest.mark.asyncio
async def test_execute_filter_start_only(sample_temporal_geojson):
    """Start-only range keeps every feature at/after the start instant."""
    engine = TemporalEngine()
    t_filter = TemporalFilter(filter_type="range", start_time="2026-08-08T00:00:00Z")

    res = await engine.execute_filter(
        dataset=sample_temporal_geojson, temporal_field="timestamp", t_filter=t_filter
    )

    feats = res.result_geojson["features"]
    assert len(feats) == 3  # 08-08, 08-09, 08-10
    assert all(f["properties"]["timestamp"] >= "2026-08-08" for f in feats)


@pytest.mark.asyncio
async def test_execute_filter_end_only(sample_temporal_geojson):
    """End-only range keeps every feature at/before the end instant."""
    engine = TemporalEngine()
    t_filter = TemporalFilter(filter_type="range", end_time="2026-08-03T12:00:00Z")

    res = await engine.execute_filter(
        dataset=sample_temporal_geojson, temporal_field="timestamp", t_filter=t_filter
    )

    feats = res.result_geojson["features"]
    assert len(feats) == 3  # 08-01, 08-02, 08-03 (daily noon)
    assert {f["properties"]["id"] for f in feats} == {"feat_0", "feat_1", "feat_2"}


def test_filter_dataset_relative_window_with_spec(sample_temporal_geojson):
    """The relative_window path (previously the only working shape) must keep
    working when the spec is also present, as execute_filter forwards both."""
    engine = TemporalEngine()
    t_filter = TemporalFilter(filter_type="relative_window", relative_window="last_7_days")
    ref = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)

    res = engine.filter(
        features_or_records=sample_temporal_geojson,
        time_field="timestamp",
        relative_window="last_7_days",
        ref_time=ref,
        filter_spec=t_filter,
    )

    assert len(res["features"]) == 8  # 08-03 12:00 .. 08-10 12:00 inclusive


def test_filter_dataset_spec_with_interval_still_honored(sample_temporal_geojson):
    """A filter_spec that DOES carry an interval (no explicit start/end args)
    must keep filtering via the spec branch."""
    from app.services.temporal.models import TimeInterval, TimeInstant

    filter_engine = TemporalFilterEngine()
    spec = TemporalFilter(
        filter_type="range",
        interval=TimeInterval(
            start=TimeInstant.from_datetime(datetime(2026, 8, 3, tzinfo=timezone.utc)),
            end=TimeInstant.from_datetime(datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)),
        ),
    )

    res = filter_engine.filter_dataset(
        sample_temporal_geojson, time_field="timestamp", filter_spec=spec
    )

    assert len(res["features"]) == 4


# ---------------------------------------------------------------------------
# 3. TemporalAggregationEngine Tests
# ---------------------------------------------------------------------------

def test_temporal_aggregation_daily(sample_temporal_geojson):
    agg_engine = TemporalAggregationEngine()
    results = agg_engine.aggregate(
        sample_temporal_geojson,
        time_field="timestamp",
        group_by_unit=TemporalUnit.DAY,
        metrics=[TemporalMetric.COUNT, TemporalMetric.MEAN, TemporalMetric.STDDEV],
        target_fields=["temperature"],
    )

    assert len(results) == 10
    first_bucket = results[0]
    assert first_bucket["count"] == 1
    assert "temperature_mean" in first_bucket
    assert first_bucket["temperature_mean"] == 20.0


def test_temporal_aggregation_monthly(sample_temporal_geojson):
    agg_engine = TemporalAggregationEngine()
    results = agg_engine.aggregate(
        sample_temporal_geojson,
        time_field="timestamp",
        group_by_unit=TemporalUnit.MONTH,
        metrics=[TemporalMetric.COUNT, TemporalMetric.SUM, TemporalMetric.MEAN],
        target_fields=["temperature"],
    )

    assert len(results) == 1
    month_bucket = results[0]
    assert month_bucket["count"] == 10
    assert month_bucket["temperature_count"] == 10
    # Mean of 20.0, 21.5, ..., 33.5 = 26.75
    assert abs(month_bucket["temperature_mean"] - 26.75) < 1e-4


# ---------------------------------------------------------------------------
# 4. TemporalChangeEngine Tests
# ---------------------------------------------------------------------------

def test_temporal_change_engine_multi_snapshot():
    change_engine = TemporalChangeEngine()
    
    snapshot1 = {
        "type": "FeatureCollection",
        "features": [
            {"id": "1", "geometry": {"type": "Point", "coordinates": [116.40, 39.90]}, "properties": {"val": 10}},
            {"id": "2", "geometry": {"type": "Point", "coordinates": [116.41, 39.91]}, "properties": {"val": 20}},
        ]
    }
    snapshot2 = {
        "type": "FeatureCollection",
        "features": [
            {"id": "1", "geometry": {"type": "Point", "coordinates": [116.401, 39.901]}, "properties": {"val": 15}},
            {"id": "2", "geometry": {"type": "Point", "coordinates": [116.411, 39.911]}, "properties": {"val": 25}},
            {"id": "3", "geometry": {"type": "Point", "coordinates": [116.42, 39.92]}, "properties": {"val": 30}},
        ]
    }

    result: TemporalChangeResult = change_engine.compare_snapshots(
        snapshots=[snapshot1, snapshot2],
        snapshot_names_or_times=["2026-01-01", "2026-06-01"],
        numeric_fields=["val"],
    )

    assert result.snapshot_count == 2
    assert len(result.count_deltas) == 1
    assert result.count_deltas[0]["delta"] == 1
    assert len(result.attribute_deltas) >= 1
    assert result.geometry_deltas is not None
    assert result.geometry_deltas["matched_features_count"] == 2


# ---------------------------------------------------------------------------
# 5. TemporalTrendEngine Tests
# ---------------------------------------------------------------------------

def test_temporal_trend_engine_linear_and_anomalies():
    trend_engine = TemporalTrendEngine()
    # Simple linear sequence with 1 anomaly
    values = [10.0, 12.0, 14.0, 16.0, 18.0, 100.0, 22.0, 24.0]
    
    result: TemporalTrendResult = trend_engine.analyze_trend(
        data=values,
        metric_name="traffic_flow",
        moving_avg_window=3,
        z_threshold=2.0,
    )

    assert result.total_points == 8
    assert len(result.moving_average) == 8
    assert result.direction == "increasing"
    assert len(result.anomalies) >= 1
    assert result.anomalies[0]["index"] == 5


# ---------------------------------------------------------------------------
# 5b. #452: NaN handling in the trend pipeline
# ---------------------------------------------------------------------------


def test_trend_single_nan_matches_cleaned_series():
    """Issue #452: a single NaN made compute_sens_slope return NaN, the OLS
    r² clamp turn NaN into a perfect 1.0 fit, direction fall to "stable", and
    anomalies vanish. [1,2,NaN,4,5,6] must yield the same slope/r²/direction
    as [1,2,4,5,6] plus a dropped_nan count."""
    trend_engine = TemporalTrendEngine()

    result = trend_engine.analyze_trend([1.0, 2.0, float("nan"), 4.0, 5.0, 6.0])
    ref = trend_engine.analyze_trend([1.0, 2.0, 4.0, 5.0, 6.0])

    assert result.slope == ref.slope
    assert result.intercept == ref.intercept
    assert result.r_squared == ref.r_squared
    assert result.direction == ref.direction == "increasing"
    assert result.slope == result.slope  # finite, not NaN
    assert 0.0 <= result.r_squared < 1.0  # not the spurious perfect fit
    assert result.dropped_nan == 1
    assert result.total_points == 5
    assert result.values == [1.0, 2.0, 4.0, 5.0, 6.0]


def test_trend_all_nan_returns_explicit_empty():
    """Adversarial: all-NaN input → explicit empty result with the dropped
    count, not NaN stats dressed as a confident fit."""
    trend_engine = TemporalTrendEngine()
    result = trend_engine.analyze_trend([float("nan")] * 4)

    assert result.total_points == 0
    assert result.dropped_nan == 4
    assert result.slope == 0.0
    assert result.direction == "stable"


def test_trend_single_finite_value_after_drop():
    """Adversarial: one finite value survives → stable, no crash."""
    trend_engine = TemporalTrendEngine()
    result = trend_engine.analyze_trend([5.0, float("nan"), float("nan")])

    assert result.total_points == 1
    assert result.dropped_nan == 2
    assert result.direction == "stable"
    assert result.r_squared == 0.0


def test_trend_drops_inf_too():
    """Adversarial: ±Inf values are non-finite and must be dropped like NaN."""
    trend_engine = TemporalTrendEngine()
    result = trend_engine.analyze_trend([1.0, float("inf"), 3.0, float("-inf"), 5.0])

    assert result.dropped_nan == 2
    assert result.values == [1.0, 3.0, 5.0]
    assert result.direction == "increasing"


def test_trend_dict_series_with_nan_metric_values():
    """Dict input shape (temporal_trend tool): NaN metric values dropped with
    timestamps kept aligned to the surviving points."""
    trend_engine = TemporalTrendEngine()
    data = [
        {"timestamp": "2026-01-01T00:00:00Z", "temp": 10.0},
        {"timestamp": "2026-01-02T00:00:00Z", "temp": float("nan")},
        {"timestamp": "2026-01-03T00:00:00Z", "temp": 12.0},
    ]
    result = trend_engine.analyze_trend(data=data, metric_name="temp")

    assert result.dropped_nan == 1
    assert result.total_points == 2
    assert result.values == [10.0, 12.0]
    assert result.timestamps == ["2026-01-01T00:00:00+00:00", "2026-01-03T00:00:00+00:00"]


def test_compute_sens_slope_direct_nan_input():
    """Direct seam calls with NaN behave like the cleaned input."""
    from app.services.temporal.trend import TemporalTrendEngine as TTE

    dirty = TTE.compute_sens_slope([1.0, float("nan"), 3.0, 5.0, 7.0, 9.0])
    clean = TTE.compute_sens_slope([1.0, 3.0, 5.0, 7.0, 9.0])
    assert dirty == clean
    assert dirty == 2.0  # not NaN


def test_compute_linear_regression_direct_nan_input():
    """Direct seam: NaN input yields finite (slope, intercept, r²); the old
    clamp turned the NaN r² into a perfect 1.0."""
    from app.services.temporal.trend import TemporalTrendEngine as TTE

    slope, intercept, r2 = TTE.compute_linear_regression([1.0, float("nan"), 2.9, 4.2, 4.8])
    ref_slope, ref_intercept, ref_r2 = TTE.compute_linear_regression([1.0, 2.9, 4.2, 4.8])
    assert (slope, intercept, r2) == (ref_slope, ref_intercept, ref_r2)
    assert slope == slope  # not NaN
    assert 0.0 <= r2 <= 1.0 and r2 != 1.0


def test_detect_anomalies_with_nan_keeps_original_indices():
    """Anomalies are computed over the valid subset while reported indices
    stay those of the original series."""
    from app.services.temporal.trend import TemporalTrendEngine as TTE

    values = [10.0] * 10 + [float("nan")] + [100.0]  # outlier at index 11
    anomalies = TTE.detect_anomalies(values, z_threshold=2.0)

    assert len(anomalies) == 1
    assert anomalies[0]["index"] == 11
    assert anomalies[0]["value"] == 100.0


# ---------------------------------------------------------------------------
# 6. SpatiotemporalClusterEngine Tests
# ---------------------------------------------------------------------------

def test_spatiotemporal_cluster_engine(sample_st_cluster_geojson):
    engine = SpatiotemporalClusterEngine()
    result: SpatiotemporalHotspotResult = engine.cluster(
        sample_st_cluster_geojson,
        eps1_spatial_meters=1000.0,
        eps2_temporal_seconds=3600.0,
        min_samples=5,
        timestamp_field="timestamp",
    )

    assert result.success is True
    assert result.total_clusters == 2
    assert result.clustered_points == 11
    assert result.noise_points == 1
    assert len(result.clusters) == 2


# ---------------------------------------------------------------------------
# 7. TemporalRasterEngine Tests
# ---------------------------------------------------------------------------

def test_temporal_raster_engine_mock():
    raster_engine = TemporalRasterEngine()

    raster_series = [
        {"timestamp": "2026-01-01T00:00:00Z", "data": [[10, 20], [30, 40]]},
        {"timestamp": "2026-02-01T00:00:00Z", "data": [[15, 25], [35, 45]]},
        {"timestamp": "2026-03-01T00:00:00Z", "data": [[20, 30], [40, 50]]},
    ]

    result: TemporalRasterResult = raster_engine.execute_raster_analysis(
        raster_series=raster_series,
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-03-01T00:00:00Z",
    )

    assert len(result.selected_slices) == 3
    assert result.raster_statistics is not None
    assert len(result.raster_statistics["series_statistics"]) == 3
    assert result.raster_difference is not None
    # Difference (T3 - T1) = [[10, 10], [10, 10]] -> mean = 10.0
    assert result.raster_difference["mean_difference"] == 10.0
    assert result.raster_trend is not None
    assert result.raster_trend["direction"] == "increasing"


# ---------------------------------------------------------------------------
# 7b. #454: temporal_raster operation wiring / #458: skipped slices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_temporal_raster_operation_selects_pipeline():
    """Issue #454: the temporal_raster tool's `operation` argument was
    accepted by the schema and engine wrapper but never consumed — every
    value produced the identical full pipeline. Distinct operations must now
    select distinct branches."""
    engine = TemporalEngine()
    series = [
        {"timestamp": "2026-01-01T00:00:00Z", "data": [[10.0, 10.0], [10.0, 10.0]]},
        {"timestamp": "2026-02-01T00:00:00Z", "data": [[20.0, 20.0], [20.0, 20.0]]},
        {"timestamp": "2026-03-01T00:00:00Z", "data": [[30.0, 30.0], [30.0, 30.0]]},
    ]

    res_diff = await engine.execute_temporal_raster(raster_series=series, operation="difference")
    res_trend = await engine.execute_temporal_raster(raster_series=series, operation="trend")
    res_mean = await engine.execute_temporal_raster(raster_series=series, operation="mean")
    res_all = await engine.execute_temporal_raster(raster_series=series, operation="all")

    payload_diff = res_diff.raster_series[0]
    payload_trend = res_trend.raster_series[0]
    payload_mean = res_mean.raster_series[0]
    payload_all = res_all.raster_series[0]

    # difference: stats + difference, no trend
    assert payload_diff["raster_difference"] is not None
    assert payload_diff["raster_difference"]["mean_difference"] == 20.0
    assert payload_diff["raster_trend"] is None
    # trend: stats + trend, no difference
    assert payload_trend["raster_trend"] is not None
    assert payload_trend["raster_trend"]["direction"] == "increasing"
    assert payload_trend["raster_difference"] is None
    # mean: statistics only
    assert payload_mean["raster_statistics"] is not None
    assert payload_mean["raster_difference"] is None
    assert payload_mean["raster_trend"] is None
    # all: the full pipeline (previous effective behavior)
    assert payload_all["raster_difference"] is not None
    assert payload_all["raster_trend"] is not None
    # Two different operation values produce different results.
    assert payload_diff != payload_trend


@pytest.mark.asyncio
async def test_execute_temporal_raster_invalid_operation_raises():
    engine = TemporalEngine()
    series = [{"timestamp": "2026-01-01T00:00:00Z", "data": [[1.0]]}]
    with pytest.raises(ValueError, match="operation"):
        await engine.execute_temporal_raster(raster_series=series, operation="quantum")


def test_raster_trend_over_aoi_skips_missing_slices(tmp_path):
    """Issue #458: a missing/failed raster contributed mean 0.0 to the trend
    (`.get("mean", 0.0)` on empty statistics) — fabricating points that drag
    the slope toward 0 and dress a real decline as 'stable'. Missing slices
    must be skipped and listed."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    def _write(path, value):
        with rasterio.open(
            path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float64",
            crs="EPSG:4326", transform=from_origin(0.0, 4.0, 1.0, 1.0),
        ) as dst:
            dst.write(np.full((4, 4), value), 1)
        return path

    p1 = _write(str(tmp_path / "s1.tif"), 10.0)
    p2 = str(tmp_path / "missing.tif")  # never written → failed statistics
    p3 = _write(str(tmp_path / "s3.tif"), 30.0)

    series = [
        {"timestamp": "2026-01-01T00:00:00Z", "path": p1},
        {"timestamp": "2026-02-01T00:00:00Z", "path": p2},
        {"timestamp": "2026-03-01T00:00:00Z", "path": p3},
    ]
    engine = TemporalRasterEngine()
    stats = engine.temporal_raster_statistics(series)
    assert stats["series_statistics"][1]["statistics"] == {}  # the failed slice

    res = engine.raster_trend_over_aoi(series, stats_info=stats)

    # Trend computed over the 2 valid slices only (means 10 → 30, slope 20).
    assert res["means"] == [10.0, 30.0]
    assert res["slope"] == 20.0
    assert res["direction"] == "increasing"
    # The skip is listed explicitly.
    assert len(res["skipped_slices"]) == 1
    assert res["skipped_slices"][0]["index"] == 1
    assert res["skipped_slices"][0]["path"] == p2


def test_raster_trend_over_aoi_all_slices_missing():
    """Adversarial: every slice failed → empty series, no fabricated zeros,
    and direction "unknown" (not "stable" — nothing was fit)."""
    series = [
        {"timestamp": "2026-01-01T00:00:00Z", "path": "/nonexistent/a.tif"},
        {"timestamp": "2026-02-01T00:00:00Z", "path": "/nonexistent/b.tif"},
    ]
    engine = TemporalRasterEngine()
    res = engine.raster_trend_over_aoi(series)

    assert res["means"] == []
    assert res["slope"] == 0.0
    assert res["direction"] == "unknown"  # pre-#541: fabricated "stable"
    assert len(res["skipped_slices"]) == 2


def test_raster_trend_over_aoi_all_valid_no_skips(tmp_path):
    """No missing slices → no skipped_slices key noise, identical means."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    series = []
    for i, value in enumerate((10.0, 12.0, 14.0)):
        path = str(tmp_path / f"v{i}.tif")
        with rasterio.open(
            path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float64",
            crs="EPSG:4326", transform=from_origin(0.0, 4.0, 1.0, 1.0),
        ) as dst:
            dst.write(np.full((4, 4), value), 1)
        series.append({"timestamp": f"2026-0{i+1}-01T00:00:00Z", "path": path})

    engine = TemporalRasterEngine()
    res = engine.raster_trend_over_aoi(series)

    assert res["means"] == [10.0, 12.0, 14.0]
    assert "skipped_slices" not in res


# ── Issue #541: no-AOI default must cover the scene, not a unit square ───────


def _write_constant_raster(path, value, origin=(116.0, 40.0), size=4):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    with rasterio.open(
        path, "w", driver="GTiff", height=size, width=size, count=1,
        dtype="float64", crs="EPSG:4326",
        transform=from_origin(origin[0], origin[1], 1.0, 1.0),
    ) as dst:
        dst.write(np.full((size, size), float(value)), 1)
    return path


def test_temporal_raster_statistics_no_aoi_covers_scene(tmp_path):
    """Issue #541: without an AOI the statistics must cover the raster's OWN
    scene (true mean 42.0) — the old (0,0)-(1,1) unit-square default missed
    this 116-117E/39-40N raster entirely and fabricated all-zero stats."""
    path = _write_constant_raster(str(tmp_path / "scene.tif"), 42.0)
    engine = TemporalRasterEngine()
    res = engine.temporal_raster_statistics([
        {"timestamp": "2026-01-01T00:00:00Z", "path": path},
    ])

    stats = res["series_statistics"][0]["statistics"]
    assert stats["mean"] == pytest.approx(42.0, abs=1e-9)
    assert stats["min"] == pytest.approx(42.0, abs=1e-9)
    assert stats["max"] == pytest.approx(42.0, abs=1e-9)
    assert stats["std"] == pytest.approx(0.0, abs=1e-9)


def test_raster_trend_no_aoi_uses_scene_means(tmp_path):
    """Issue #541: end-to-end trend without an AOI fits the SCENE means
    (42 → 52 → 62), not fabricated zeros; direction is increasing with the
    true slope 10."""
    paths = [_write_constant_raster(str(tmp_path / f"s{i}.tif"), v)
             for i, v in enumerate((42.0, 52.0, 62.0))]
    series = [
        {"timestamp": f"2026-0{i+1}-01T00:00:00Z", "path": p}
        for i, p in enumerate(paths)
    ]
    engine = TemporalRasterEngine()
    res = engine.raster_trend_over_aoi(series)

    assert res["means"] == [42.0, 52.0, 62.0]
    assert res["direction"] == "increasing"
    assert res["slope"] == pytest.approx(10.0, abs=1e-6)


def test_temporal_raster_non_intersecting_aoi_skips_not_fabricates(tmp_path):
    """Issue #541: an AOI disjoint from the raster must behave like a missing
    slice (empty stats, skip in the trend) — not produce all-zero statistics
    that the old code fed to the trend as real data."""
    path = _write_constant_raster(str(tmp_path / "far.tif"), 42.0)
    series = [{"timestamp": "2026-01-01T00:00:00Z", "path": path}]
    away_aoi = {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Polygon",
                     "coordinates": [[[200.0, 200.0], [210.0, 200.0],
                                      [210.0, 210.0], [200.0, 210.0], [200.0, 200.0]]]},
    }
    engine = TemporalRasterEngine()

    stats = engine.temporal_raster_statistics(series, aoi_geometry=away_aoi)
    assert stats["series_statistics"][0]["statistics"] == {}

    trend = engine.raster_trend_over_aoi(series, aoi_geometry=away_aoi)
    assert trend["means"] == []
    assert trend["direction"] == "unknown"
    assert len(trend["skipped_slices"]) == 1


# ---------------------------------------------------------------------------
# 8. Unified TemporalEngine Orchestrator Seam Tests
# ---------------------------------------------------------------------------

def test_unified_temporal_engine_facade(sample_temporal_geojson, sample_st_cluster_geojson):
    engine = TemporalEngine()

    # 1. Profile
    profile = engine.profile(sample_temporal_geojson)
    assert profile.overall_confidence > 0.5

    # 2. Filter
    filtered = engine.filter(sample_temporal_geojson, relative_window="last_7_days", ref_time=datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert len(filtered["features"]) > 0

    # 3. Aggregate
    agg = engine.aggregate(sample_temporal_geojson, group_by_unit=TemporalUnit.DAY, target_fields=["temperature"])
    assert len(agg) == 10

    # 4. Trend
    trend = engine.analyze_trend([10, 12, 14, 16, 18, 20])
    assert trend.direction == "increasing"

    # 5. Spatiotemporal Cluster
    st_result = engine.cluster_spatiotemporal(sample_st_cluster_geojson)
    assert st_result.success is True
    assert st_result.total_clusters == 2

    # 6. Raster Series
    raster_res = engine.analyze_raster_series([
        {"timestamp": "2026-01-01T00:00:00Z", "data": [[1, 2], [3, 4]]},
        {"timestamp": "2026-02-01T00:00:00Z", "data": [[5, 6], [7, 8]]},
    ])
    assert raster_res.raster_difference["mean_difference"] == 4.0
