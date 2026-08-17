"""
单元测试：缓冲区分析 CRS 投影转换测试
"""
import math
import pytest
from shapely.geometry import shape

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


def test_buffer_negative_distance_shrinks_polygon():
    """负距离 buffer 应保留 GIS 语义：多边形向内收缩，而不是静默取绝对值向外扩张。"""
    polygon = {
        "type": "Feature",
        "properties": {"name": "test square"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [121.4700, 31.2300],
                [121.4800, 31.2300],
                [121.4800, 31.2400],
                [121.4700, 31.2400],
                [121.4700, 31.2300],
            ]]
        }
    }

    original_area = shape(polygon["geometry"]).area
    result: AnalysisResult = SpatialAnalyzer.buffer(
        features=[polygon],
        distance=-100,
        unit="m",
        source_crs="EPSG:4326"
    )

    assert result.success is True
    assert result.data is not None
    buffered_geometry = result.data["features"][0]["geometry"]
    assert shape(buffered_geometry).area < original_area


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


def test_buffer_smart_invalid_geojson():
    from app.lib.geo_processor.geometry import buffer_smart
    res = buffer_smart("invalid json", 10)
    assert res.success is False
    assert "invalid" in res.summary.lower()


def test_buffer_smart_single_feature():
    from app.lib.geo_processor.geometry import buffer_smart
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [121.47, 31.23]},
        "properties": {}
    }
    res = buffer_smart(feature, 10)
    assert res.success is True


def test_buffer_smart_km_unit():
    from app.lib.geo_processor.geometry import buffer_smart
    point = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [121.47, 31.23]}
    }
    res = buffer_smart(point, 1, unit="km")
    assert res.success is True


def test_buffer_smart_invalid_geometry():
    from app.lib.geo_processor.geometry import buffer_smart
    feature = {
        "type": "Feature",
        "geometry": None,
        "properties": {}
    }
    res = buffer_smart(feature, 10)
    assert res.success is False
    assert "failed to project" in res.summary.lower()


def test_buffer_smart_exception():
    from app.lib.geo_processor.geometry import buffer_smart
    class BadDict(dict):
        def __bool__(self):
            return True
        def get(self, *args, **kwargs):
            raise RuntimeError("Mock exception")
            
    res = buffer_smart(BadDict(), 10)
    assert res.success is False
    assert res.error_type == "RuntimeError"



def test_buffer_smart_projected_crs_preservation():
    from app.lib.geo_processor.geometry import buffer_smart
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [13523282, 3662284]},
        "properties": {}
    }
    res = buffer_smart(feature, 50, source_crs="EPSG:3857")
    assert res.success is True
    assert res.data["stats"]["reprojected"] is False
    assert res.data["stats"]["working_crs"] == "EPSG:3857"


# ── Issue #524: unit conversion direction for non-metre projected CRS ────────
#
# to_utm_gdf returns an already-projected input UNCHANGED (state-plane feet
# stays in feet), so a "meters" distance must be converted to CRS units by
# DIVIDING by the axis unit_conversion_factor (metres-per-unit). The old code
# multiplied, shrinking a 1000 m buffer in a US-survey-foot CRS to ~92.8 m
# (factor² ≈ 0.093). These tests assert the TRUE geometric radius in metres.


def _ft_to_m_factor(crs_epsg: int) -> float:
    from pyproj import CRS
    axis = CRS.from_epsg(crs_epsg).axis_info[0]
    return float(axis.unit_conversion_factor)


def _area_equivalent_radius_m(res) -> float:
    from shapely.geometry import shape
    geom = shape(res.data["features"][0]["geometry"])
    radius_crs_units = (geom.area / math.pi) ** 0.5
    return radius_crs_units * _ft_to_m_factor(2263)


# (EPSG, a point inside the CRS's area of use, in CRS units)
_FOOT_CRS_POINTS = [
    (2263, [986705.5, 211835.6]),   # NAD83 / New York Long Island (ftUS)
    (2264, [1277217.5, 263201.1]),  # NAD83 / North Carolina (ftUS)
]


@pytest.mark.parametrize("crs_epsg,point", _FOOT_CRS_POINTS)
def test_buffer_smart_state_plane_feet_distance_in_meters(crs_epsg, point):
    """A 1000 m buffer on a foot-based state-plane CRS must yield a circle
    whose area-equivalent radius is ~1000 m (±1%) — pre-#524 it was ~92.8 m."""
    from app.lib.geo_processor.geometry import buffer_smart
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": point},
        "properties": {},
    }
    res = buffer_smart(feature, 1000, unit="m", source_crs=f"EPSG:{crs_epsg}")

    assert res.success is True, res.summary
    assert res.data["stats"]["reprojected"] is False  # projected input kept
    geom = shape(res.data["features"][0]["geometry"])
    radius_crs_units = (geom.area / math.pi) ** 0.5
    radius_m = radius_crs_units * _ft_to_m_factor(crs_epsg)
    assert 990.0 <= radius_m <= 1010.0, f"radius {radius_m} m outside 1000±1%"


def test_buffer_smart_state_plane_feet_legacy_crs_member():
    """Same contract via the legacy GeoJSON ``crs`` member (no source_crs=)."""
    from app.lib.geo_processor.geometry import buffer_smart
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:2263"}},
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [986705.5, 211835.6]},
            "properties": {},
        }],
    }
    res = buffer_smart(fc, 1000, unit="m")

    assert res.success is True, res.summary
    radius_m = _area_equivalent_radius_m(res)
    assert 990.0 <= radius_m <= 1010.0, f"radius {radius_m} m outside 1000±1%"


def test_buffer_smart_state_plane_feet_km_unit():
    """1 km on the same foot CRS → ~1000 m radius (km→m conversion still ok)."""
    from app.lib.geo_processor.geometry import buffer_smart
    point = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [986705.5, 211835.6]},
        "properties": {},
    }
    res = buffer_smart(point, 1, unit="km", source_crs="EPSG:2263")

    assert res.success is True, res.summary
    radius_m = _area_equivalent_radius_m(res)
    assert 990.0 <= radius_m <= 1010.0, f"radius {radius_m} m outside 1000±1%"


def test_buffer_smart_state_plane_feet_polygon_ring_width():
    """Polygon buffered by 1000 m in a foot CRS: the output area must match a
    reference shapely buffer of the same polygon by exactly (1000 m converted
    to CRS units) — pre-#524 the area was ~factor² smaller."""
    from app.lib.geo_processor.geometry import buffer_smart

    epsg = 2263
    factor = _ft_to_m_factor(epsg)
    half = 500.0  # ftUS
    polygon = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [986705.5 - half, 211835.6 - half],
                [986705.5 + half, 211835.6 - half],
                [986705.5 + half, 211835.6 + half],
                [986705.5 - half, 211835.6 + half],
                [986705.5 - half, 211835.6 - half],
            ]],
        },
        "properties": {},
    }
    res = buffer_smart(polygon, 1000, unit="m", source_crs=f"EPSG:{epsg}")
    assert res.success is True, res.summary

    out_geom = shape(res.data["features"][0]["geometry"])
    # Reference: the same square buffered by the true 1000 m width in ftUS.
    reference = shape(polygon["geometry"]).buffer(1000.0 / factor)
    assert out_geom.area == pytest.approx(reference.area, rel=0.01)


# ── Issue #599: output must declare its CRS for projected input ────────────
#
# buffer_smart returns projected input in its ORIGINAL CRS (EPSG:3857 metres
# stay metres). Downstream consumers (MVT/geojson_bbox/frontend) read bare
# GeoJSON as WGS84, so the output FeatureCollection must carry a legacy `crs`
# member — pre-fix the projected coordinates were emitted undecorated and
# silently misplaced. WGS84 output must NOT carry the member (RFC 7946).


def test_buffer_smart_output_declares_crs_for_projected_input():
    from app.lib.geo_processor.geometry import buffer_smart
    point = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [13523282, 3662284]},
        "properties": {},
    }
    res = buffer_smart(point, 50, source_crs="EPSG:3857")
    assert res.success is True
    assert res.data["crs"] == {"type": "name", "properties": {"name": "EPSG:3857"}}

    # WGS84 input/output must not gain a crs member (RFC 7946).
    res2 = buffer_smart({"type": "Point", "coordinates": [116.4, 39.9]}, 100)
    assert res2.success is True
    assert "crs" not in res2.data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

