"""Test GIS-03: Boundary-to-boundary distance in spatial constraints.

Verifies that:
1. Polygon/LineString features calculate true boundary proximity instead of
   centroid-to-centroid distance, eliminating false safety clearances.
2. Projected coordinates (e.g. EPSG:3857 or UTM) calculate Euclidean distance
   without crashing or producing NaN in pyproj.Geod.
3. Intersecting geometries evaluate distance as 0.0.
"""
from shapely.geometry import box, LineString, Point, mapping

from app.services.spatial_decision.models_v3 import (
    Constraint,
    ConstraintCategory,
    ConstraintType,
    SpatialPredicate,
)
from app.services.spatial_decision.spatial_constraints import evaluate_spatial_constraint


def test_polygon_near_linestring_boundary_distance():
    """Candidate building polygon 50m from a 22km long river must violate 500m min setback.

    Previously, centroid-to-centroid distance was ~11,000m, causing a lethal false pass.
    """
    # Candidate site at lon 120.0, lat 30.0
    building = box(120.0, 30.0, 120.0005, 30.0005)
    # Reference feature: 22km long river stretching northward
    river = LineString([(120.001, 30.0), (120.001, 30.2)])

    constraint = Constraint(
        id="river_setback",
        name="Min 500m from River",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.SPATIAL,
        spatial_predicate=SpatialPredicate.MIN_DISTANCE,
        threshold=500.0,
        reference_geometry=mapping(river),
    )

    eval_res = evaluate_spatial_constraint("Building_Site_1", mapping(building), constraint)
    assert not eval_res.passed, "Building ~50m from river boundary must fail 500m min distance"
    assert eval_res.observed_value < 100.0, f"Distance should be ~48m, got {eval_res.observed_value}"
    assert eval_res.margin < 0.0


def test_polygon_to_polygon_boundary_distance():
    """Two large polygons with far centroids but adjacent boundaries must calculate boundary distance."""
    poly_a = box(120.0, 30.0, 120.001, 30.001)
    poly_b = box(120.0015, 30.0, 120.05, 30.05)

    constraint = Constraint(
        id="reserve_buffer",
        name="Min 200m from Reserve",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.SPATIAL,
        spatial_predicate=SpatialPredicate.MIN_DISTANCE,
        threshold=200.0,
        reference_geometry=mapping(poly_b),
    )

    eval_res = evaluate_spatial_constraint("Site_A", mapping(poly_a), constraint)
    assert not eval_res.passed, "Polygons separated by ~50m must violate 200m buffer"
    assert eval_res.observed_value < 100.0


def test_projected_coordinates_do_not_crash():
    """Features with projected coordinates (e.g. UTM or Web Mercator) must evaluate cleanly without NaN."""
    proj_poly = box(500000, 4000000, 500100, 4000100)
    proj_line = LineString([(500200, 4000000), (500200, 4001000)])

    constraint = Constraint(
        id="metric_min_dist",
        name="Min 50m from Utility Line",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.SPATIAL,
        spatial_predicate=SpatialPredicate.MIN_DISTANCE,
        threshold=50.0,
        reference_geometry=mapping(proj_line),
    )

    eval_res = evaluate_spatial_constraint("Projected_Site", mapping(proj_poly), constraint)
    assert eval_res.passed, "Distance 100m >= 50m threshold should pass"
    assert abs(eval_res.observed_value - 100.0) < 1.0


def test_intersecting_geometries_zero_distance():
    """Intersecting geometries must have 0.0 distance."""
    poly_a = box(120.0, 30.0, 120.01, 30.01)
    poly_b = box(120.005, 30.005, 120.02, 30.02)

    constraint = Constraint(
        id="min_dist",
        name="Min Distance",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.SPATIAL,
        spatial_predicate=SpatialPredicate.MIN_DISTANCE,
        threshold=100.0,
        reference_geometry=mapping(poly_b),
    )

    eval_res = evaluate_spatial_constraint("Site_Overlap", mapping(poly_a), constraint)
    assert not eval_res.passed
    assert eval_res.observed_value == 0.0


def test_point_to_point_distance():
    """Point features calculate geodesic distance accurately."""
    pt_a = Point(116.30, 39.90)
    pt_b = Point(116.301, 39.90)

    constraint = Constraint(
        id="pt_min_dist",
        name="Min 50m from Station",
        constraint_type=ConstraintType.HARD,
        category=ConstraintCategory.SPATIAL,
        spatial_predicate=SpatialPredicate.MIN_DISTANCE,
        threshold=50.0,
        reference_geometry=mapping(pt_b),
    )

    eval_res = evaluate_spatial_constraint("Site_Pt", mapping(pt_a), constraint)
    # ~85m distance >= 50m
    assert eval_res.passed
    assert 80.0 < eval_res.observed_value < 90.0

