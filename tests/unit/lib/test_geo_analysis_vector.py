"""Characterization tests for the shared centroid-extraction helper and the
NumPy-vectorized sites that use it (behavior-preserving sweep).

- extract_centroids must match the legacy per-geometry comprehension exactly,
  including mixed geometry types (Point + Polygon).
- calculate_sde keeps its two-branch semantics: all-geometry centroids when
  fewer than 3 Points, point coordinates otherwise.
- calculate_central_feature still behaves after the sweep + the vectorized
  self-distance zeroing (mean_center ≈ arithmetic mean of inputs for a tiny
  extent; central_feature returns a Point feature).
"""
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, Polygon

from app.lib.geo_analysis._vector import extract_centroids
from app.lib.geo_analysis.statistics import calculate_central_feature, calculate_sde


def _fc(points):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
             "properties": {}}
            for lon, lat in points
        ],
    }


def test_extract_centroids_matches_legacy_comprehension():
    gdf = gpd.GeoDataFrame(
        geometry=[
            Point(0, 0),
            Point(2, 4),
            Polygon([(10, 10), (14, 10), (12, 14)]),
        ],
        crs="EPSG:4326",
    )
    legacy = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
    got = extract_centroids(gdf)
    assert got.shape == (3, 2)
    np.testing.assert_allclose(got, legacy, atol=1e-12)


def test_calculate_sde_mixed_geometry_uses_centroids_branch():
    """<3 Points → the all-geometry centroids branch (swept, still equivalent)."""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120.0, 30.0]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120.1, 30.1]}, "properties": {}},
            {"type": "Feature", "geometry": {
                "type": "Polygon",
                "coordinates": [[[119.9, 29.9], [120.2, 29.9], [120.2, 30.2], [119.9, 30.2], [119.9, 29.9]]],
            }, "properties": {}},
        ],
    }
    res = calculate_sde(geojson)
    assert res.success
    assert res.data["geometry"]["type"] == "Polygon"
    assert "direction" in res.data["properties"]
    assert res.data["properties"]["area_km2"] >= 0


def test_central_feature_mean_center_matches_input_mean():
    pts = [[120.0, 30.0], [120.01, 30.0], [120.005, 30.01], [120.01, 30.02], [120.0, 30.015]]
    res = calculate_central_feature(_fc(pts), method="mean_center")
    assert res.success
    coords = np.array(pts)
    lon, lat = res.data["geometry"]["coordinates"]
    # Tiny extent ⇒ UTM reprojection is near-affine; mean center comes back
    # within ~1e-3° of the arithmetic mean of the inputs.
    assert abs(lon - coords[:, 0].mean()) < 1e-3
    assert abs(lat - coords[:, 1].mean()) < 1e-3


def test_central_feature_min_total_distance_runs():
    pts = [[120.0, 30.0], [120.01, 30.0], [120.005, 30.005], [120.02, 30.01], [120.001, 30.001]]
    res = calculate_central_feature(_fc(pts), method="central_feature")
    assert res.success
    assert res.data["geometry"]["type"] == "Point"
    assert res.data["properties"]["method"] == "central_feature"
