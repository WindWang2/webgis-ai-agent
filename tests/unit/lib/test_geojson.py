from app.utils.geojson import geojson_bbox


def test_geojson_bbox_deeply_nested_returns_none():
    """Recursion depth guard: structures nested > 50 levels should not crash."""
    node = [0.0, 0.0]
    for _ in range(60):
        node = {"type": "GeometryCollection", "geometries": [node]}
    result = geojson_bbox(node)
    assert result is None


def test_geojson_bbox_normal_feature():
    result = geojson_bbox({"type": "Point", "coordinates": [1.0, 2.0]})
    assert result == [1.0, 2.0, 1.0, 2.0]


def test_geojson_bbox_feature_collection():
    data = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-1, -2]}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [3, 4]}},
        ],
    }
    result = geojson_bbox(data)
    assert result == [-1, -2, 3, 4]
