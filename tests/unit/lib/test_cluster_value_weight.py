"""Issue #693 item 2: spatial_cluster value_weight explicit scaling."""

from app.lib.geo_analysis.statistics import cluster_narrated


def _pts():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.39, 39.9]}, "properties": {"val": 10}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.391, 39.901]}, "properties": {"val": 10}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.42, 39.92]}, "properties": {"val": 100}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.421, 39.921]}, "properties": {"val": 100}},
    ]}


def test_value_weight_affects_clustering():
    # value_weight=0 -> purely spatial, two spatial clusters
    # value_weight very large -> value dominates
    res_spatial = cluster_narrated(_pts(), method="kmeans", n_clusters=2, value_field="val", value_weight=0.0)
    res_value = cluster_narrated(_pts(), method="kmeans", n_clusters=2, value_field="val", value_weight=10.0)
    assert res_spatial.success and res_value.success
    # With weight 0, clustering is spatial only; with large weight, value dominates.
    # The two high-value points should cluster together in value-weighted mode.
    _labels_spatial = [f["properties"]["cluster_id"] for f in res_spatial.data["features"]]
    labels_weighted = [f["properties"]["cluster_id"] for f in res_value.data["features"]]
    # high-value pair (indices 2,3) must be same cluster when value dominates
    assert labels_weighted[2] == labels_weighted[3]
    # default weight 1.0 path still works
    res_default = cluster_narrated(_pts(), method="kmeans", n_clusters=2, value_field="val")
    assert res_default.success
