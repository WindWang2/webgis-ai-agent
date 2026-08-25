"""Issue #693 item 9: nearest_neighbor contract {mean_nearest_distance, expected, R, pattern}."""

from app.lib.geo_analysis.statistics import calculate_nearest


def _pts():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.39, 39.9]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4, 39.91]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.41, 39.92]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.42, 39.93]}, "properties": {}},
    ]}


def test_nearest_contract_keys():
    res = calculate_nearest(_pts())
    assert res.success, res.summary
    # Ticket contract
    for k in ("mean_nearest_distance", "expected", "R", "pattern"):
        assert k in res.data, f"missing contract key {k}"
    # Aliases still present
    assert "mean_distance" in res.data
    assert "r_ratio" in res.data
    assert res.data["mean_nearest_distance"] == res.data["mean_distance"]
    assert res.data["R"] == res.data["r_ratio"]
    assert res.data["expected"] > 0


def test_nearest_docstring_covers_contract():
    import inspect
    src = inspect.getsource(calculate_nearest)
    assert "mean_nearest_distance" in src
    assert "expected" in src


def test_nearest_coincident_points_clustered():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4074, 39.9042]}, "properties": {"id": i}}
            for i in range(10)
        ],
    }
    res = calculate_nearest(fc)
    assert res.success
    assert res.data["mean_nearest_distance"] == 0.0
    assert res.data["R"] == 0.0
    assert res.data["pattern"] == "clustered"
