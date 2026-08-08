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
