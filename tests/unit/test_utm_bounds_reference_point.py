r"""Test GIS-04: UTM reference point computed from total_bounds instead of union_all.

Verifies that:
1. to_utm_gdf determines UTM zone / polar stereographic CRS from bounding box midpoint
   without invoking expensive $O(N \log N) / O(N^2)$ geometry.union_all().
2. aggregation._metric_zone_area_m2 calculates metric CRS fallback from total_bounds
   without invoking union_all().
"""
from unittest.mock import patch
import geopandas as gpd
from shapely.geometry import box

from app.lib.geo_processor.core import to_utm_gdf
from app.lib.geo_analysis.aggregation import _metric_zone_area_m2


def test_to_utm_gdf_does_not_call_union_all():
    """to_utm_gdf must select UTM zone without topological union_all."""
    # Create multiple polygons across Beijing region (lon ~116.4, lat ~39.9 -> UTM zone 50N, EPSG:32650)
    poly1 = box(116.3, 39.8, 116.4, 39.9)
    poly2 = box(116.4, 39.9, 116.5, 40.0)
    gdf = gpd.GeoDataFrame({"geometry": [poly1, poly2]}, crs="EPSG:4326")

    # Patch union_all on GeoSeries to raise if called
    with patch.object(gpd.GeoSeries, "union_all", side_effect=RuntimeError("union_all should NOT be called")):
        res_gdf, utm_crs = to_utm_gdf(gdf.__geo_interface__)

    assert utm_crs == "EPSG:32650"
    assert res_gdf is not None
    assert str(res_gdf.crs) == "EPSG:32650"


def test_to_utm_gdf_polar_fallback_from_bounds():
    """High latitude features trigger polar stereographic without union_all."""
    poly_arctic = box(-45.0, 85.0, -40.0, 86.0)
    gdf = gpd.GeoDataFrame({"geometry": [poly_arctic]}, crs="EPSG:4326")

    with patch.object(gpd.GeoSeries, "union_all", side_effect=RuntimeError("union_all should NOT be called")):
        res_gdf, utm_crs = to_utm_gdf(gdf.__geo_interface__)

    assert utm_crs == "EPSG:3413"
    assert res_gdf is not None


def test_aggregation_metric_zone_area_m2_fallback_no_union_all():
    """_metric_zone_area_m2 fallback determines metric CRS without union_all."""
    # Web Mercator features where estimate_utm_crs raises, triggering fallback
    p1 = box(12000000.0, -3000000.0, 12001000.0, -2999000.0)
    p2 = box(12002000.0, -2999000.0, 12003000.0, -2998000.0)
    gdf = gpd.GeoDataFrame({"geometry": [p1, p2], "id": [1, 2]}, crs="EPSG:3857")

    with patch.object(gpd.GeoDataFrame, "estimate_utm_crs", side_effect=RuntimeError("fallback test")), \
         patch.object(gpd.GeoSeries, "union_all", side_effect=RuntimeError("union_all should NOT be called")):
        areas, metric_crs, data_class = _metric_zone_area_m2(gdf)

    assert "EPSG:32" in metric_crs
    assert len(areas) == 2
