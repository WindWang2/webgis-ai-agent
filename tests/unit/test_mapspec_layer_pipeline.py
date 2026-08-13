"""Unit tests for MapSpec Layer Ingestion Pipeline (app/services/mapspec_layer_pipeline.py)."""
import copy
import numpy as np
from app.services.mapspec_layer_pipeline import process_layer_ingestion


def test_process_layer_ingestion_plain_geojson():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                "properties": {}
            }
        ]
    }
    layer = {"id": "l1", "source": "s1"}
    mapspec = {}

    processed_layer, source_entry, suggested_view = process_layer_ingestion(
        mapspec, layer, source_data=geojson
    )

    assert processed_layer["id"] == "l1"
    assert source_entry["inlineData"] == geojson
    assert "profile" in source_entry
    assert source_entry["profile"]["featureCount"] == 1
    # Coordinates alone are not authoritative CRS evidence. Unknown CRS must
    # not silently produce a WGS84 camera target.
    assert suggested_view is None


def test_process_layer_ingestion_analysis_result():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                "properties": {"val": 10}
            }
        ]
    }
    analysis = {
        "success": True,
        "algorithm": "spatial_hotspot",
        "data": geojson,
        "legend_spec": {
            "type": "graduated",
            "field": "val",
            "breaks": [0.0, 10.0, 20.0],
            "palette_colors": ["#0000ff", "#ff0000"]
        }
    }
    layer = {"id": "hotspot_layer", "source": "hotspot_source"}
    mapspec = {}

    processed_layer, source_entry, suggested_view = process_layer_ingestion(
        mapspec, layer, source_data=analysis
    )

    assert processed_layer["id"] == "hotspot_layer"
    assert processed_layer["type"] == "circle"
    assert processed_layer["paint"]["color"]["method"] == "step"
    assert source_entry["inlineData"] == geojson


def test_process_layer_ingestion_raster_payload(tmp_path):
    raster_data = {
        "success": True,
        "algorithm": "ndvi",
        "array": np.array([[0.1, 0.5], [0.8, 0.2]], dtype=float),
        "bounds": [120.0, 30.0, 121.0, 31.0],
        "legend_spec": {"type": "continuous", "min": 0, "max": 1, "palette": "RdYlGn"}
    }
    layer = {"id": "ndvi_layer", "source": "ndvi_source"}
    mapspec = {}

    processed_layer, source_entry, suggested_view = process_layer_ingestion(
        mapspec, layer, source_data=raster_data, session_dir=tmp_path
    )

    assert processed_layer["id"] == "ndvi_layer"
    assert processed_layer["type"] == "raster"
    assert source_entry["type"] == "raster"
    assert "imageRef" in source_entry
    assert source_entry["bounds"] == [120.0, 30.0, 121.0, 31.0]
    assert suggested_view is None  # Rasters do not run GeoJSON auto-profiling


def test_process_layer_ingestion_preserves_existing_view():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                "properties": {}
            }
        ]
    }
    layer = {"id": "l1", "source": "s1"}
    mapspec = {"view": {"center": [0.0, 0.0], "zoom": 1}}

    processed_layer, source_entry, suggested_view = process_layer_ingestion(
        mapspec, layer, source_data=geojson
    )

    # View was already explicitly set (even at origin [0,0]), so no new suggested_view is returned
    assert suggested_view is None


def test_process_layer_ingestion_does_not_mutate_mapspec():
    # Purity invariant (Candidate #3): process_layer_ingestion reads mapspec only
    # to seed the source entry's existing keys and never writes back. MapSpecStore
    # remains the sole write authority. Locks the invariant so a future change
    # can't silently reintroduce aliasing mutation (the friction an earlier
    # review flagged, already prevented by the dict(existing_entry) copy).
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                "properties": {}
            }
        ]
    }
    layer = {"id": "l1", "source": "s1"}
    mapspec = {
        "sources": {"s1": {"type": "geojson", "existing": "KEEP"}},
        "view": {"center": [0.0, 0.0], "zoom": 1},
    }
    before = copy.deepcopy(mapspec)

    processed_layer, source_entry, suggested_view = process_layer_ingestion(
        mapspec, layer, source_data=geojson
    )

    # The mapspec document is byte-for-byte unchanged.
    assert mapspec == before
    # The returned source_entry carries the new data + preserved existing key,
    # proving the entry is a copy, not an alias into mapspec["sources"].
    assert "inlineData" in source_entry
    assert source_entry.get("existing") == "KEEP"
    assert "inlineData" not in mapspec["sources"]["s1"]
