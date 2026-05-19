import pytest
import json
from unittest.mock import MagicMock

pytestmark = pytest.mark.heavy

# Test the core logic functions directly
from app.services.spatial_tasks import (
    _do_buffer_analysis, 
    _do_heatmap_generation, 
    _do_spatial_stats
)

def test_heatmap_empty_features():
    """测试空要素下的热力图生成"""
    result = _do_heatmap_generation(features=[], cell_size=500, radius=1000)
    assert result["success"] is False
    assert "No valid point features found" in result["error"]

def test_heatmap_malformed_coordinates():
    """测试包含异常坐标的点要素"""
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.47, 31.23]}}, # Shanghai
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [None, 30]}}, 
        {"type": "Feature", "geometry": None}
    ]
    result = _do_heatmap_generation(features=features, cell_size=500, radius=1000)
    assert result["success"] is True
    # The image field should exist in raster mode
    assert "image" in result["data"]
    assert result["data"]["total_points"] == 1

def test_buffer_heterogeneous_geometries():
    """测试混合几何类型的缓冲区分析"""
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120, 30]}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[120, 30], [121, 31]]}}
    ]
    result = _do_buffer_analysis(features=features, distance=100, unit="m")
    assert result["success"] is True
    assert len(result["data"]["features"]) == 2

def test_spatial_stats_no_geometries():
    """测试无几何对象的空间统计"""
    features = [{"type": "Feature", "geometry": None, "properties": {"a": 1}}]
    result = _do_spatial_stats(features=features)
    assert result["success"] is False
    assert "No valid geometries" in result["error"]

def test_bug_spatial_join_crs_mismatch():
    """测试空间连接是否存在坐标系不匹配的 Bug (已修复)"""
    left_features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.47, 31.23]}, "properties": {"id": 1}}]
    right_features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.48, 31.24]}, "properties": {"val": "Shanghai Point"}}]
    
    from app.lib.geo_analysis.aggregation import spatial_aggregate
    
    result = spatial_aggregate(
        {"type": "FeatureCollection", "features": left_features},
        {"type": "FeatureCollection", "features": right_features}
    )
    
    assert result.success is True

def test_bug_spatial_stats_empty_placeholders():
    """测试空间统计是否已实现 (已修复)"""
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.47, 31.23]}, "properties": {"val": 10}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.48, 31.24]}, "properties": {"val": 20}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.49, 31.25]}, "properties": {"val": 30}}
    ]
    from app.services.spatial_analyzer import SpatialAnalyzer
    result = SpatialAnalyzer.statistics(features, field="val", spatial_stats=True)
    assert result.success is True
    assert "Moran's I" in result.summary


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-s"])
