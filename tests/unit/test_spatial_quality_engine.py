"""
Unit tests for SpatialQualityEngine and SpatialRepairPipeline.
Tests cover 5 quality dimensions and safe non-destructive spatial repairs.
"""

import pytest
import copy
from app.services.spatial_quality_service import SpatialQualityEngine, SpatialQualityReport, QualityIssue
from app.services.spatial_repair_pipeline import SpatialRepairPipeline


@pytest.mark.asyncio
async def test_audit_valid_dataset():
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

    report = await SpatialQualityEngine.audit_dataset(geojson, crs="EPSG:4326")
    assert isinstance(report, SpatialQualityReport)
    assert report.total_features == 2
    assert report.overall_status in ["passed", "warning"]
    assert report.issue_summary["blocking"] == 0
    assert report.issue_summary["error"] == 0


@pytest.mark.asyncio
async def test_audit_geometry_dimension():
    # Invalid self-intersecting polygon (bowtie), empty geometry, duplicate geometry
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

    report = await SpatialQualityEngine.audit_dataset(bowtie_geojson)
    codes = [issue.code for issue in report.issues]
    assert "SELF_INTERSECTION" in codes or "INVALID_GEOMETRY" in codes
    assert "EMPTY_GEOMETRY" in codes
    assert "DUPLICATE_GEOMETRY" in codes
    assert report.overall_status == "blocking"


@pytest.mark.asyncio
async def test_audit_sliver_polygon():
    # Extremely thin polygon
    sliver_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0.000001, 100], [0.000002, 0], [0, 0]]],
                },
            }
        ],
    }
    report = await SpatialQualityEngine.audit_dataset(sliver_geojson)
    codes = [i.code for i in report.issues]
    assert "SLIVER_POLYGON" in codes


@pytest.mark.asyncio
async def test_audit_topology_dimension():
    # Overlapping polygons & duplicate feature & dangling endpoints
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
            {
                "type": "Feature",
                "properties": {"id": 2, "category": "B"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[5, 5], [5, 15], [15, 15], [15, 5], [5, 5]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": 3},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[100, 100], [200, 200]],
                },
            },
        ],
    }

    report = await SpatialQualityEngine.audit_dataset(topology_geojson)
    codes = [i.code for i in report.issues]
    assert "DUPLICATE_FEATURE" in codes
    assert "TOPOLOGY_OVERLAP" in codes
    assert "DANGLING_ENDPOINT" in codes


@pytest.mark.asyncio
async def test_audit_crs_dimension():
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
    report = await SpatialQualityEngine.audit_dataset(geojson_missing, crs="")
    codes = [i.code for i in report.issues]
    assert "MISSING_CRS" in codes
    assert "GEO_VS_PROJECTED_MEASUREMENT_WARNING" in codes

    geojson_suspicious = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [500000, 4000000]},
            }
        ],
    }
    report_suspicious = await SpatialQualityEngine.audit_dataset(geojson_suspicious, crs="EPSG:4326")
    codes_susp = [i.code for i in report_suspicious.issues]
    assert "SUSPICIOUS_CRS" in codes_susp or "IMPOSSIBLE_LAT_LON" in codes_susp


@pytest.mark.asyncio
async def test_audit_attributes_dimension():
    attribute_geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": "A1", "age": 20, "score": "high"}, "geometry": {"type": "Point", "coordinates": [1, 1]}},
            {"type": "Feature", "properties": {"id": "A1", "age": None, "score": 95}, "geometry": {"type": "Point", "coordinates": [2, 2]}},
            {"type": "Feature", "properties": {"id": "A2", "age": None, "score": 98}, "geometry": {"type": "Point", "coordinates": [3, 3]}},
            {"type": "Feature", "properties": {"id": "A3", "age": None, "score": 99}, "geometry": {"type": "Point", "coordinates": [4, 4]}},
            {"type": "Feature", "properties": {"id": "A4", "age": 1000, "score": 100}, "geometry": {"type": "Point", "coordinates": [5, 5]}},
        ],
    }

    report = await SpatialQualityEngine.audit_dataset(attribute_geojson)
    codes = [i.code for i in report.issues]
    assert any("NULL" in c for c in codes)


@pytest.mark.asyncio
async def test_audit_spatial_sanity_dimension():
    sanity_geojson = {
        "type": "FeatureCollection",
        "bbox": [100, 100, 0, 0],
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
            },
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [200.0, 95.0]},
            },
        ],
    }

    report = await SpatialQualityEngine.audit_dataset(sanity_geojson)
    codes = [i.code for i in report.issues]
    assert "INVALID_BBOX" in codes
    assert "NULL_ISLAND" in codes
    assert "IMPOSSIBLE_LAT_LON" in codes


@pytest.mark.asyncio
async def test_repair_pipeline_non_destructive():
    original_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"val": "100 ", "id": 1},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 2], [2, 0], [2, 2], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"val": "200", "id": 2},
                "geometry": None,
            },
        ],
    }

    original_copy = copy.deepcopy(original_geojson)

    repaired, logs = await SpatialRepairPipeline.repair_dataset(
        original_geojson,
        ops=["make_valid", "remove_empty", "normalize_geometry_type", "attribute_type_normalization"],
    )

    # Verify non-destructive behavior
    assert original_geojson == original_copy

    # Verify repairs
    assert len(repaired["features"]) == 1
    assert repaired["features"][0]["geometry"]["type"] == "MultiPolygon"
    assert repaired["features"][0]["properties"]["val"] == 100
    assert len(logs) > 0


@pytest.mark.asyncio
async def test_repair_pipeline_operations():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "A", "val": "50"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"name": "A", "val": "50"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"name": "B"},
                "geometry": {
                    "type": "Point",
                    "coordinates": [0.0000001, 0.0000001],
                },
            },
        ],
    }

    repaired, logs = await SpatialRepairPipeline.repair_dataset(
        geojson,
        ops=["deduplicate", "snap_within_tolerance", "attribute_type_normalization"],
        tolerance=1e-5,
    )

    assert len(repaired["features"]) == 2
    assert list(repaired["features"][1]["geometry"]["coordinates"]) == [0.0, 0.0]
    assert "val" in repaired["features"][1]["properties"]
    assert repaired["features"][1]["properties"]["val"] is None
