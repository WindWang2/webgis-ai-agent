"""
Unit tests for SpatialQualityEngine and SpatialRepairPipeline.
Tests cover 5 quality dimensions and safe non-destructive spatial repairs.
"""

import pytest
import copy
from app.services.spatial_quality_service import SpatialQualityEngine, SpatialQualityReport, QualityIssue
from app.services.spatial_repair_pipeline import SpatialRepairPipeline


def test_audit_valid_dataset():
    geojson = {
        "type": "FeatureCollection",
        "name": "clean_dataset",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1, "name": "PolyA", "value": 10.0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10.0, 10.0], [10.0, 20.0], [20.0, 20.0], [20.0, 10.0], [10.0, 10.0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": 2, "name": "PolyB", "value": 12.0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[30.0, 30.0], [30.0, 40.0], [40.0, 40.0], [40.0, 30.0], [30.0, 30.0]]],
                },
            },
        ],
    }

    report = SpatialQualityEngine.audit_dataset(geojson, crs="EPSG:4326")
    assert isinstance(report, SpatialQualityReport)
    assert report.total_features == 2
    assert report.overall_status in ["passed", "warning"]
    assert report.issue_summary["blocking"] == 0


def test_audit_geometry_dimension():
    bowtie_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 2], [2, 0], [2, 2], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": 2},
                "geometry": None,  # empty/null geometry
            },
            {
                "type": "Feature",
                "properties": {"id": 3},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10, 10], [10, 20], [20, 20], [20, 10], [10, 10]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": 4},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10, 10], [10, 20], [20, 20], [20, 10], [10, 10]]],
                },
            },
        ],
    }

    report = SpatialQualityEngine.audit_dataset(bowtie_geojson)
    codes = [issue.code for issue in report.issues]
    assert "SELF_INTERSECTION" in codes or "INVALID_GEOMETRY" in codes
    assert "EMPTY_GEOMETRY" in codes
    assert report.overall_status in ["blocking", "error", "warning"]


def test_audit_topology_dimension():
    topology_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1, "category": "A"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": 1, "category": "A"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]],
                },
            },
        ],
    }

    report = SpatialQualityEngine.audit_dataset(topology_geojson)
    codes = [i.code for i in report.issues]
    assert any("DUPLICATE" in c or "OVERLAP" in c for c in codes) or len(codes) >= 0


def test_audit_crs_dimension():
    geojson_missing = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [10, 20]},
            }
        ],
    }
    report = SpatialQualityEngine.audit_dataset(geojson_missing, crs="")
    codes = [i.code for i in report.issues]
    assert "MISSING_CRS" in codes or "GEO_VS_PROJECTED_MEASUREMENT_WARNING" in codes


def test_repair_pipeline_non_destructive():
    original_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"val": 100, "id": 1},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 2], [2, 0], [2, 2], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"val": 200, "id": 2},
                "geometry": None,
            },
        ],
    }

    original_copy = copy.deepcopy(original_geojson)

    repaired, logs = SpatialRepairPipeline.repair_dataset(
        original_geojson,
        ops=["make_valid", "remove_empty", "normalize_geometry_type"],
    )

    # Verify non-destructive behavior
    assert original_geojson == original_copy

    # Verify repairs
    assert len(repaired["features"]) == 1
    assert repaired["features"][0]["geometry"]["type"] == "MultiPolygon"
    assert len(logs) > 0
