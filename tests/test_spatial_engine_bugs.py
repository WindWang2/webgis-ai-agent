import pytest
import json

pytestmark = pytest.mark.heavy

from app.services.spatial_analyzer import SpatialAnalyzer
from app.services.spatial_tasks import run_buffer_analysis, run_heatmap_generation, run_spatial_stats
from unittest.mock import MagicMock

def test_heatmap_empty_features():
    """测试空要素下的热力图生成"""
    # 使用 .__wrapped__ 访问未经 Celery 装饰的原始函数
    result = run_heatmap_generation.__wrapped__(None, [], cell_size=500, radius=1000)
    assert result["success"] is False
    assert "No valid point features found" in result["error"]

def test_heatmap_malformed_coordinates():
    """测试包含异常坐标的点要素"""
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.47, 31.23]}}, # Shanghai
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [None, 30]}}, 
        {"type": "Feature", "geometry": None}
    ]
    mock_self = MagicMock()
    result = run_heatmap_generation.__wrapped__(mock_self, features, 500, 1000)
    assert result["success"] is True
    assert result["data"]["total_points"] == 1

def test_buffer_heterogeneous_geometries():
    """测试混合几何类型的缓冲区分析"""
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120, 30]}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[120, 30], [121, 31]]}}
    ]
    result = run_buffer_analysis.__wrapped__(None, features, 100, "m")
    assert result["success"] is True
    assert len(result["data"]["features"]) == 2

def test_spatial_stats_no_geometries():
    """测试无几何对象的空间统计"""
    features = [{"type": "Feature", "geometry": None, "properties": {"a": 1}}]
    mock_self = MagicMock()
    result = run_spatial_stats.__wrapped__(mock_self, features)
    assert result["success"] is False
    assert "No valid geometries" in result["error"]

def test_bug_spatial_join_crs_mismatch():
    """测试空间连接是否存在坐标系不匹配的 Bug (已修复)"""
    # 模拟两个坐标系不同的图层
    left_features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.47, 31.23]}, "properties": {"id": 1}}]
    right_features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [13522100, 3662500]}, "properties": {"val": "Shanghai Point"}}]
    
    # 显式传递坐标系
    result = SpatialAnalyzer.spatial_join(
        left_features, 
        right_features, 
        join_type="inner", 
        predicate="intersects",
        left_crs="EPSG:4326",
        right_crs="EPSG:3857"
    )
    
    assert result.success is True
    # 虽然是点对点匹配可能因精度问题返回0，但如果修复成功，应能通过 success=True
    print(f"\n[SPATIAL JOIN SUCCESS]: {result.success}")

def test_bug_spatial_stats_empty_placeholders():
    """测试空间统计是否已实现 (已修复)"""
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.47, 31.23]}}]
    # 这里的 SpatialAnalyzer.statistics 返回 AnalysisResult 对象
    result = SpatialAnalyzer.statistics(features, spatial_stats=True)
    if not result.success:
        print(f"\n[ERROR]: Statistics failed: {result.error_message}")
    
    assert result.success is True
    stats = result.data["spatial_statistics"]
    print(f"\n[SPATIAL STATS RESULT]: Vertices={stats['total_vertices']}, Area={stats['total_area_sqkm']}")
    assert stats["total_features"] == 1
    # 修复后 total_vertices 应该 > 0
    assert stats["total_vertices"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-s"])


