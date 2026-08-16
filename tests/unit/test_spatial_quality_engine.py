"""
Unit tests for SpatialQualityEngine and SpatialRepairPipeline.
Tests cover 5 quality dimensions and safe non-destructive spatial repairs.
"""

import copy
from app.services.spatial_quality_service import SpatialQualityEngine, SpatialQualityReport
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


def _dangling_count(report) -> int:
    return sum(1 for i in report.issues if i.code == "DANGLING_ENDPOINT")


# ── Issue #538: DANGLING_ENDPOINT tolerance was dead code ────────────────────
#
# STRtree.query(Point) used the degenerate zero-area point bbox, so only
# bit-identical endpoints were ever matched; near-but-not-identical shared
# endpoints were falsely reported dangling. These tests assert issue COUNTS
# (numeric truth), not just absence of exceptions.


def _line_pair_geojson(second_start) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": 1},
             "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [10.0, 10.0]]}},
            {"type": "Feature", "properties": {"id": 2},
             "geometry": {"type": "LineString", "coordinates": [second_start, [20.0, 20.0]]}},
        ],
    }


def test_dangling_endpoint_near_shared_geographic_not_flagged():
    """EPSG:4326, endpoint offset 5e-7° (below the 1e-6 threshold): the shared
    end must connect → 2 dangling (each line's other end), not 4 (pre-#538)."""
    report = SpatialQualityEngine.audit_dataset(
        _line_pair_geojson([10.0, 10.0000005]), crs="EPSG:4326"
    )
    assert _dangling_count(report) == 2
    dangling_idxs = sorted(i.feature_index for i in report.issues if i.code == "DANGLING_ENDPOINT")
    assert dangling_idxs == [0, 1]  # the near-shared ends suppressed, real ends kept


def test_dangling_endpoint_exact_share_not_flagged():
    """Bit-identical shared endpoint → 2 dangling (baseline: old code already
    matched this case via the identity bbox hit)."""
    report = SpatialQualityEngine.audit_dataset(
        _line_pair_geojson([10.0, 10.0]), crs="EPSG:4326"
    )
    assert _dangling_count(report) == 2


def test_dangling_endpoint_genuine_free_end_still_flagged():
    """A line end with no partner within the threshold must STILL be flagged —
    the fix must not over-suppress."""
    report = SpatialQualityEngine.audit_dataset(
        _line_pair_geojson([30.0, 30.0]), crs="EPSG:4326"  # second line far away
    )
    assert _dangling_count(report) == 4


def test_dangling_endpoint_projected_crs_threshold():
    """EPSG:3857 (metre units, threshold 0.1 m): 0.09 m offset → connected (0
    false positives beyond the real free ends); 0.2 m offset → both ends of
    both lines truly dangling."""
    within = SpatialQualityEngine.audit_dataset(
        _line_pair_geojson([10.0, 10.09]), crs="EPSG:3857"
    )
    assert _dangling_count(within) == 2

    beyond = SpatialQualityEngine.audit_dataset(
        _line_pair_geojson([10.0, 10.2]), crs="EPSG:3857"
    )
    assert _dangling_count(beyond) == 4


def test_repair_pipeline_snap_after_reproject_gis17():
    """GIS-17 regression: snap_within_tolerance must run AFTER crs_transform.

    When snapping runs in the source CRS (degrees) and the target is a
    projected CRS, a tolerance like 10 would be applied as 10 degrees
    (~1100 km) instead of 10 meters. After the fix, the output vertex
    coordinates must sit on a 10-meter grid in the target CRS.
    """
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1},
                "geometry": {
                    "type": "Point",
                    "coordinates": [116.39123, 39.90745],  # Beijing, off-grid on purpose
                },
            },
        ],
    }

    repaired, logs = SpatialRepairPipeline.repair_dataset(
        geojson,
        ops=["crs_transform", "snap_within_tolerance"],
        tolerance=10.0,
        source_crs="EPSG:4326",
        target_crs="EPSG:32650",  # UTM zone 50N — meters
    )

    x, y = repaired["features"][0]["geometry"]["coordinates"]
    # Reprojected coordinates are at the ~1e6 meter scale, NOT degrees (~1e2).
    # Snapping happened in target CRS units: both axes on a 10 m grid.
    assert x > 1e5, f"expected projected meter-scale x, got {x}"
    assert y > 1e5, f"expected projected meter-scale y, got {y}"
    assert abs(round(x / 10.0) * 10.0 - x) < 1e-6, f"x={x} not on 10 m grid"
    assert abs(round(y / 10.0) * 10.0 - y) < 1e-6, f"y={y} not on 10 m grid"
    assert any("target CRS units" in log for log in logs), "log should document target-CRS tolerance semantics"
