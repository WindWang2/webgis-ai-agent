"""Unit tests for SpatialAnalyzer deepening & operator completeness (ADR-0026)."""
from app.services.spatial_analyzer import SpatialAnalyzer
from app.lib.geo_processor.core import GeoAnalysisResult


def test_spatial_join_operator():
    """Test SpatialAnalyzer.spatial_join with valid point and polygon collections."""
    left_points = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.39, 39.90]},
                "properties": {"name": "P1"},
            }
        ],
    }

    right_polys = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [116.38, 39.89],
                            [116.40, 39.89],
                            [116.40, 39.91],
                            [116.38, 39.91],
                            [116.38, 39.89],
                        ]
                    ],
                },
                "properties": {"district": "Central"},
            }
        ],
    }

    res = SpatialAnalyzer.spatial_join(left_points, right_polys, join_type="inner", predicate="intersects")

    assert isinstance(res, GeoAnalysisResult)
    assert res.success is True
    assert res.data is not None
    assert len(res.data.get("features", [])) == 1
    assert res.data["features"][0]["properties"]["district"] == "Central"


def test_zonal_stats_path_traversal_protection():
    """Test that zonal_stats rejects path traversal attempts."""
    poly = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [116.38, 39.89],
                            [116.40, 39.89],
                            [116.40, 39.91],
                            [116.38, 39.91],
                            [116.38, 39.89],
                        ]
                    ],
                },
                "properties": {},
            }
        ],
    }

    res = SpatialAnalyzer.zonal_stats(poly, "../../etc/passwd")

    assert res.success is False
    assert res.error_type == "ValidationError"
    assert "不在允许的 data_dir 范围内" in res.summary


def test_raster_reclassify_path_security():
    """Test that raster_reclassify rejects invalid raster paths."""
    res = SpatialAnalyzer.raster_reclassify("../../../unauthorized.tif", scheme=[])
    assert res.success is False
    assert res.error_type == "ValidationError"


def test_raster_calculator_path_security():
    """Test that raster_calculator rejects invalid raster paths."""
    res = SpatialAnalyzer.raster_calculator("/etc/passwd", expression="A + 1")
    assert res.success is False
    assert res.error_type == "ValidationError"


def test_raster_resample_path_security():
    """Test that raster_resample rejects invalid raster paths."""
    res = SpatialAnalyzer.raster_resample("../../vsicurl/http://attacker.com/evil.tif", target_resolution=10)
    assert res.success is False
    assert res.error_type == "ValidationError"
