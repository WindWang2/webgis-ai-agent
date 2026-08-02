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
    assert "InsufficientData" in res.error_type
