"""
Unit tests for ST-DBSCAN (Spatio-Temporal DBSCAN) operator.
"""
from datetime import datetime, timezone, timedelta

import pytest
from app.lib.geo_analysis.statistics import st_dbscan_narrated
from app.services.spatial_analyzer import SpatialAnalyzer


def create_sample_st_geojson():
    base_time = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    features = []
    
    # Cluster 1: Beijing Guomao (39.908, 116.458), 6 points within 10 minutes & 100m
    for i in range(6):
        t_iso = (base_time + timedelta(minutes=i * 2)).isoformat()
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.458 + i * 0.0001, 39.908 + i * 0.0001]},
            "properties": {"id": f"c1_{i}", "timestamp": t_iso, "value": 10 + i}
        })
        
    # Cluster 2: Beijing Sanlitun (39.935, 116.455), 5 points within 15 minutes & 150m, 5 hours later
    for i in range(5):
        t_iso = (base_time + timedelta(hours=5, minutes=i * 3)).isoformat()
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.455 + i * 0.0001, 39.935 + i * 0.0001]},
            "properties": {"id": f"c2_{i}", "timestamp": t_iso, "value": 20 + i}
        })
        
    # Noise point: Far away in time & space
    features.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [116.397, 39.908]}, # Tiananmen
        "properties": {"id": "noise_1", "timestamp": (base_time + timedelta(days=1)).isoformat(), "value": 99}
    })
    
    return {"type": "FeatureCollection", "features": features}


def test_st_dbscan_narrated_basic():
    geojson = create_sample_st_geojson()
    res = st_dbscan_narrated(
        geojson,
        eps1_spatial_meters=1000.0,
        eps2_temporal_seconds=3600.0,
        min_samples=5,
        timestamp_field="timestamp"
    )
    assert res.success is True
    assert res.data is not None
    stats = res.data.get("cluster_stats", {})
    assert stats["total_clusters"] == 2
    assert stats["clustered_points"] == 11
    assert stats["noise_points"] == 1
    assert stats["temporal_span_hours"] > 0
    assert "ST-DBSCAN identified 2 spatio-temporal cluster(s)" in res.summary


def test_st_dbscan_spatial_analyzer_seam():
    geojson = create_sample_st_geojson()
    res = SpatialAnalyzer.st_dbscan(
        geojson,
        eps1_spatial_meters=1000.0,
        eps2_temporal_seconds=3600.0,
        min_samples=5
    )
    assert res.success is True
    features = res.data.get("features", [])
    assert len(features) == 12
    # Check cluster_id property is present on output features
    cluster_ids = [f["properties"]["cluster_id"] for f in features]
    assert -1 in cluster_ids
    assert 0 in cluster_ids
    assert 1 in cluster_ids


def test_st_dbscan_insufficient_data():
    geojson = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.45, 39.90]}, "properties": {"timestamp": "2026-08-01T12:00:00Z"}}
    ]}
    res = st_dbscan_narrated(geojson, min_samples=5)
    assert res.success is False
    # b6dbeaa1：statistics narrated 族的 legacy "InsufficientData" 迁移为
    # 类型化 ScientificError code（INSUFFICIENT_SAMPLES）+ correction_hint。
    assert res.error_type == "INSUFFICIENT_SAMPLES"
    assert res.correction_hint  # typed failure 必须带可执行修正提示


def _numeric_epoch_geojson():
    """Numeric epoch-second timestamps (repo time contract: seconds)."""
    base = 1_787_000_000.0          # 2026-08-01T ~ UTC, epoch seconds
    features = []
    for i in range(6):              # cluster 1: 6 pts, 10 min span, tight space
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [116.458 + i * 0.0001, 39.908 + i * 0.0001]},
            "properties": {"id": f"c1_{i}", "ts": base + i * 120.0, "value": 10 + i},
        })
    for i in range(5):              # cluster 2: 5 pts, 15 min span, 2 h later
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [116.455 + i * 0.0001, 39.935 + i * 0.0001]},
            "properties": {"id": f"c2_{i}", "ts": base + 7200.0 + i * 180.0,
                           "value": 20 + i},
        })
    features.append({               # noise: far in time (1 day) and space
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [116.397, 39.908]},
        "properties": {"id": "noise_1", "ts": base + 86400.0, "value": 99},
    })
    return {"type": "FeatureCollection", "features": features}


def test_st_dbscan_numeric_epoch_seconds_not_nanoseconds():
    """P2 修复：数值型 epoch 按**秒**解析（pd.to_datetime 默认纳秒会把秒级
    字段静默变成 1970 年，temporal_span 与 eps2 比较随之失真）。"""
    res = st_dbscan_narrated(
        _numeric_epoch_geojson(),
        eps1_spatial_meters=1000.0, eps2_temporal_seconds=3600.0,
        min_samples=5, timestamp_field="ts",
    )
    assert res.success is True, res.summary
    stats = res.data["cluster_stats"]
    # 24 h span（noise +24h 为最远时刻）—— 纳秒解析会给 ~0
    assert stats["temporal_span_hours"] == pytest.approx(24.0, abs=0.01)
    assert stats["total_clusters"] == 2
    assert stats["clustered_points"] == 11
    assert stats["noise_points"] == 1


def test_st_dbscan_param_guards():
    """P2 修复：eps1/eps2/min_samples 非法时类型化拒绝（旧行为：
    min_samples<1 逃逸 sklearn 原始 ValueError；eps≤0 静默返回全噪声）。"""
    gj = create_sample_st_geojson()
    for kwargs in (
        {"eps1_spatial_meters": 0.0},
        {"eps1_spatial_meters": -5.0},
        {"eps2_temporal_seconds": 0.0},
        {"min_samples": 0},
        {"min_samples": -1},
    ):
        params = dict(eps1_spatial_meters=1000.0, eps2_temporal_seconds=3600.0,
                      min_samples=5, timestamp_field="timestamp")
        params.update(kwargs)
        res = st_dbscan_narrated(gj, **params)
        assert res.success is False, kwargs
        assert res.error_type == "ValueError", kwargs
