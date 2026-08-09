"""Regression tests for GIS crash bugs (GIS-12, GIS-13, GIS-15) from the
deep-audit-performance-convergence goal.

These are P0 correctness bugs where invalid / empty inputs crashed instead of
returning the intended structured failure result, or where a None crs reached
.upper() in the quality auditor.
"""
import pytest

from app.lib.geo_analysis.statistics import cluster_narrated
from app.lib.geo_analysis.network import calculate_isochrones
from app.lib.geo_analysis.aggregation import spatial_aggregate
from app.services.spatial_quality_service import SpatialQualityEngine


# ---------------------------------------------------------------------------
# GIS-12 — cluster_narrated with non-numeric value_field
# ---------------------------------------------------------------------------

def test_cluster_with_non_numeric_value_field_returns_failure_not_crash():
    """A non-numeric value_field must return a failure result, not raise."""
    features = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"cat": "a"},
                "geometry": {"type": "Point", "coordinates": [116.3 + i * 0.01, 39.9]},
            }
            for i in range(5)
        ],
    }
    # Before the fix this raised AttributeError: 'NoneType' object has no attribute 'empty'
    res = cluster_narrated(features, value_field="cat")
    assert res.success is False
    assert "cat" in (res.summary or "")


# ---------------------------------------------------------------------------
# GIS-13 — to_utm_gdf failure tuple-truthiness
# ---------------------------------------------------------------------------

def test_calculate_isochrones_invalid_network_returns_failure_not_crash():
    """Invalid/empty GeoJSON must return a failure result, not raise."""
    bad = {"type": "FeatureCollection", "features": []}
    facilities = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [116.3, 39.9]}}
        ],
    }
    res = calculate_isochrones(bad, facilities, 10.0)
    assert res.success is False
    assert "Invalid" in (res.summary or "") or "GeoJSON" in (res.summary or "")


def test_spatial_aggregate_invalid_inputs_returns_failure_not_crash():
    bad_points = {"type": "FeatureCollection", "features": []}
    bad_polys = {"type": "FeatureCollection", "features": []}
    res = spatial_aggregate(bad_points, bad_polys)
    assert res.success is False


# ---------------------------------------------------------------------------
# GIS-15 — spatial_quality_service crs=None with geojson crs member
# ---------------------------------------------------------------------------

def test_quality_audit_handles_crs_none_with_geojson_crs_member():
    """audit_dataset must not crash with crs=None when the GeoJSON has a crs member."""
    engine = SpatialQualityEngine()
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1},
                "geometry": {"type": "Point", "coordinates": [116.3, 39.9]},
            }
        ],
    }
    # Before the fix: AttributeError: 'NoneType' object has no attribute 'upper'
    report = engine.audit_dataset(fc, crs=None)
    assert report is not None
    # The GeoJSON crs member should be honored (not default-flagged as MISSING_CRS).
    crs_issues = [i for i in report.issues if i.dimension == "crs"]
    assert not any(i.code == "MISSING_CRS" for i in crs_issues)


def test_quality_audit_defaults_crs_to_4326_when_absent():
    """When the caller signals an unknown CRS and no geojson crs is present,
    flag MISSING_CRS and default to EPSG:4326 (preserving original semantics)."""
    engine = SpatialQualityEngine()
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1},
                "geometry": {"type": "Point", "coordinates": [116.3, 39.9]},
            }
        ],
    }
    report = engine.audit_dataset(fc, crs="UNKNOWN")
    assert report is not None
    crs_issues = [i for i in report.issues if i.dimension == "crs"]
    assert any(i.code == "MISSING_CRS" for i in crs_issues)
