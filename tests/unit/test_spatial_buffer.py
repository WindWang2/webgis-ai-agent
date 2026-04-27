"""
单元测试：缓冲区分析 CRS 投影转换测试
"""
import pytest
import json
import pytest

pytestmark = pytest.mark.heavy

from app.services.spatial_analyzer import SpatialAnalyzer, AnalysisResult


def test_buffer_wgs84_single_point():
    """测试 WGS84 坐标系点的缓冲区分析"""
    # 上海市中心 经纬度: 121.4737, 31.2304
    point = {
        "type": "Feature",
        "properties": {"name": "Shanghai Center"},
        "geometry": {
            "type": "Point",
            "coordinates": [121.4737, 31.2304]
        }
    }
    
    result: AnalysisResult = SpatialAnalyzer.buffer(
        features=[point],
        distance=1000,  # 1000米 = 1公里
        unit="m",
        dissolve=False,
        source_crs="EPSG:4326"
    )
    
    assert result.success is True
    assert result.stats is not None
    assert result.stats["input_count"] == 1
    assert result.stats["output_count"] == 1
    assert result.stats["reprojected"] is True  # WGS84 应该需要重投影
    assert "EPSG:326" in result.stats["working_crs"]  # 北半球应该是 326xx UTM


def test_buffer_utm_already_projected():
    """测试已经在投影坐标系的数据不需要重投影"""
    # UTM 坐标系下的点，单位已经是米
    point = {
        "type": "Feature",
        "properties": {"name": "Test Point"},
        "geometry": {
            "type": "Point",
            "coordinates": [500000, 3450000]
        }
    }
    
    result: AnalysisResult = SpatialAnalyzer.buffer(
        features=[point],
        distance=100,
        unit="m",
        dissolve=False,
        source_crs="EPSG:32651"
    )
    
    assert result.success is True
    assert result.stats["reprojected"] is False  # UTM 已经是投影坐标系


def test_buffer_dissolve():
    """测试缓冲区融合"""
    # 两个相近的点
    point1 = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [121.47, 31.23]}
    }
    point2 = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [121.48, 31.24]}
    }
    
    result: AnalysisResult = SpatialAnalyzer.buffer(
        features=[point1, point2],
        distance=1000,
        unit="m",
        dissolve=True,
        source_crs="EPSG:4326"
    )
    
    assert result.success is True
    assert result.stats["dissolve"] is True
    # dissolve 之后应该只有一个要素
    assert result.data is not None
    features = result.data.get("features", [])
    assert len(features) == 1


def test_buffer_invalid_distance():
    """测试异常情况：距离应该还是能处理，绝对值"""
    point = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [121.47, 31.23]}
    }
    
    result: AnalysisResult = SpatialAnalyzer.buffer(
        features=[point],
        distance=-100,  # 负距离
        unit="m",
        source_crs="EPSG:4326"
    )
    
    # 应该能处理，代码里已经取绝对值了
    assert result.success is True


def test_buffer_empty_features():
    """测试空输入"""
    result: AnalysisResult = SpatialAnalyzer.buffer(
        features=[],
        distance=100,
        unit="m",
        source_crs="EPSG:4326"
    )
    
    assert result.success is False
    assert "empty" in result.error_message.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
