"""Unit tests for MapSpec Layer Ingestion Pipeline (app/services/mapspec_layer_pipeline.py)."""
import pytest
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
    assert suggested_view is not None
    assert suggested_view["center"] == [120.0, 30.0]


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
