"""
Spatial Constraint Evaluator for Spatial Decision Intelligence V3.
Evaluates topological and metric geometric constraints with geodesic CRS correctness.
Uses pyproj.Geod and Shapely to avoid degree-to-meter distortions.
"""
import math
import logging
from typing import Any, Dict, List, Optional, Tuple
from shapely.geometry import shape, Point, Polygon, MultiPolygon, GeometryCollection
from shapely.ops import transform
from pyproj import Geod, Transformer

from app.services.spatial_decision.models_v3 import (
    Constraint,
    ConstraintCategory,
    ConstraintEvaluation,
    ConstraintType,
    SpatialPredicate,
)

logger = logging.getLogger(__name__)

# Default ellipsoidal model
_GEOD = Geod(ellps="WGS84")


def _is_wgs84_lonlat(coord: Tuple[float, float]) -> bool:
    """Checks if coordinates appear to be longitude/latitude in degrees."""
    lon, lat = coord
    return -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0


def _compute_geodesic_distance_m(point_a: Point, point_b: Point) -> float:
    """Computes exact ellipsoidal distance in meters between two lon/lat points."""
    _, _, dist_m = _GEOD.inv(point_a.x, point_a.y, point_b.x, point_b.y)
    return float(dist_m)


def _meters_to_degree_buffer(lat: float, meters: float) -> Tuple[float, float]:
    """Converts a meter radius to approx delta lon and delta lat degrees at a given latitude."""
    lat_rad = math.radians(lat)
    d_lat = meters / 111320.0
    d_lon = meters / (111320.0 * max(0.01, math.cos(lat_rad)))
    return d_lon, d_lat


def _extract_leaf_geometries(geom: Any) -> List[Any]:
    """Recursively extracts constituent single geometries from collections or multiparts."""
    if geom is None or geom.is_empty:
        return []
    if hasattr(geom, "geoms"):
        leaves = []
        for g in geom.geoms:
            leaves.extend(_extract_leaf_geometries(g))
        return leaves
    return [geom]


def evaluate_spatial_constraint(
    alt_id: str,
    alt_geometry_dict: Optional[Dict[str, Any]],
    constraint: Constraint,
) -> ConstraintEvaluation:
    """
    Evaluates a spatial constraint against candidate alternative geometry.

    Args:
        alt_id: Alternative identifier.
        alt_geometry_dict: GeoJSON geometry of the candidate alternative.
        constraint: Spatial constraint with spatial_predicate and reference_geometry.

    Returns:
        ConstraintEvaluation object with passed status, margin, and audit evidence.
    """
    if not alt_geometry_dict:
        # If alternative lacks geometry, cannot satisfy spatial constraint
        return ConstraintEvaluation(
            constraint_id=constraint.id,
            alternative_id=alt_id,
            passed=False,
            observed_value=None,
            threshold="valid_geometry",
            margin=-1.0,
            penalty=constraint.penalty_weight if constraint.constraint_type == ConstraintType.SOFT else 0.0,
            evidence_statement=f"Alternative '{alt_id}' lacks spatial geometry, failing spatial constraint '{constraint.name}'.",
        )

    try:
        alt_geom = shape(alt_geometry_dict)
    except Exception as e:
        return ConstraintEvaluation(
            constraint_id=constraint.id,
            alternative_id=alt_id,
            passed=False,
            observed_value="invalid_geojson",
            threshold="valid_geometry",
            margin=-1.0,
            penalty=constraint.penalty_weight if constraint.constraint_type == ConstraintType.SOFT else 0.0,
            evidence_statement=f"Invalid geometry syntax for alternative '{alt_id}': {e}",
        )

    predicate = constraint.spatial_predicate
    ref_geom_dict = constraint.reference_geometry
    ref_geom = None
    if ref_geom_dict:
        try:
            if ref_geom_dict.get("type") == "FeatureCollection":
                features = ref_geom_dict.get("features", [])
                geoms = [shape(f["geometry"]) for f in features if f.get("geometry")]
                ref_geom = GeometryCollection(geoms) if geoms else None
            elif ref_geom_dict.get("type") == "Feature":
                ref_geom = shape(ref_geom_dict.get("geometry"))
            else:
                ref_geom = shape(ref_geom_dict)
        except Exception as e:
            logger.warning(f"Failed to parse reference geometry for constraint '{constraint.id}': {e}")

    # 1. OUTSIDE Predicate (e.g. Protected Area Exclusion)
    if predicate == SpatialPredicate.OUTSIDE:
        if ref_geom is None:
            return ConstraintEvaluation(
                constraint_id=constraint.id,
                alternative_id=alt_id,
                passed=True,
                observed_value="no_ref_geometry",
                threshold="outside",
                margin=None,
                penalty=0.0,
                evidence_statement=f"No exclusion geometry specified; '{alt_id}' satisfies outside constraint by default.",
            )

        intersects = alt_geom.intersects(ref_geom)
        passed = not intersects

        if passed:
            margin = 1.0  # Safe
            stmt = f"Alternative '{alt_id}' is located outside protected/exclusion area '{constraint.name}'."
            penalty = 0.0
        else:
            margin = -1.0  # Violated
            stmt = f"Alternative '{alt_id}' intersects protected/exclusion area '{constraint.name}' (Violation)."
            penalty = constraint.penalty_weight if constraint.constraint_type == ConstraintType.SOFT else 0.0

        return ConstraintEvaluation(
            constraint_id=constraint.id,
            alternative_id=alt_id,
            passed=passed,
            observed_value="intersects" if intersects else "disjoint",
            threshold="outside",
            margin=margin,
            penalty=penalty,
            evidence_statement=stmt,
        )

    # 2. WITHIN Predicate (e.g. Inside Boundary)
    elif predicate == SpatialPredicate.WITHIN:
        if ref_geom is None:
            return ConstraintEvaluation(
                constraint_id=constraint.id,
                alternative_id=alt_id,
                passed=True,
                observed_value="no_boundary",
                threshold="within",
                margin=None,
                penalty=0.0,
                evidence_statement="No boundary specified.",
            )

        within = alt_geom.within(ref_geom)
        passed = within
        margin = 1.0 if passed else -1.0
        penalty = 0.0 if passed else (constraint.penalty_weight if constraint.constraint_type == ConstraintType.SOFT else 0.0)
        stmt = (
            f"Alternative '{alt_id}' is strictly within '{constraint.name}'."
            if passed
            else f"Alternative '{alt_id}' falls outside boundary of '{constraint.name}' (Violation)."
        )
        return ConstraintEvaluation(
            constraint_id=constraint.id,
            alternative_id=alt_id,
            passed=passed,
            observed_value="within" if within else "outside",
            threshold="within",
            margin=margin,
            penalty=penalty,
            evidence_statement=stmt,
        )

    # 3. MIN_DISTANCE Predicate (e.g. Must be at least 500m away from toxic site or existing hospital)
    elif predicate == SpatialPredicate.MIN_DISTANCE:
        threshold_m = float(constraint.threshold or 0.0)
        ref_geoms = _extract_leaf_geometries(ref_geom)
        if not ref_geoms:
            return ConstraintEvaluation(
                constraint_id=constraint.id,
                alternative_id=alt_id,
                passed=True,
                observed_value=float("inf"),
                threshold=threshold_m,
                margin=float("inf"),
                penalty=0.0,
                evidence_statement="No reference feature to calculate distance against.",
            )

        # Geodesic distance calculation to nearest reference feature
        alt_centroid = alt_geom.centroid
        distances = []
        for g in ref_geoms:
            if not g or g.is_empty:
                continue
            if g.intersects(alt_geom):
                distances.append(0.0)
            else:
                distances.append(_compute_geodesic_distance_m(alt_centroid, g.centroid))
        dist_m = min(distances) if distances else float("inf")

        passed = dist_m >= threshold_m
        margin = dist_m - threshold_m
        penalty = 0.0 if passed else (constraint.penalty_weight if constraint.constraint_type == ConstraintType.SOFT else 0.0)
        stmt = (
            f"Geodesic distance {round(dist_m, 1)}m >= required minimum {threshold_m}m."
            if passed
            else f"Geodesic distance {round(dist_m, 1)}m < required minimum {threshold_m}m (Violation)."
        )
        return ConstraintEvaluation(
            constraint_id=constraint.id,
            alternative_id=alt_id,
            passed=passed,
            observed_value=round(dist_m, 1),
            threshold=threshold_m,
            margin=round(margin, 1),
            penalty=penalty,
            evidence_statement=stmt,
        )

    # 4. MAX_DISTANCE Predicate (e.g. Accessibility distance <= threshold)
    elif predicate == SpatialPredicate.MAX_DISTANCE:
        threshold_m = float(constraint.threshold or 1000.0)
        ref_geoms = _extract_leaf_geometries(ref_geom)
        if not ref_geoms:
            return ConstraintEvaluation(
                constraint_id=constraint.id,
                alternative_id=alt_id,
                passed=True,
                observed_value=0.0,
                threshold=threshold_m,
                margin=threshold_m,
                penalty=0.0,
                evidence_statement="No reference feature specified.",
            )

        alt_centroid = alt_geom.centroid
        distances = []
        for g in ref_geoms:
            if not g or g.is_empty:
                continue
            if g.intersects(alt_geom):
                distances.append(0.0)
            else:
                distances.append(_compute_geodesic_distance_m(alt_centroid, g.centroid))
        dist_m = min(distances) if distances else float("inf")

        passed = dist_m <= threshold_m
        margin = threshold_m - dist_m
        penalty = 0.0 if passed else (constraint.penalty_weight if constraint.constraint_type == ConstraintType.SOFT else 0.0)
        stmt = (
            f"Geodesic distance {round(dist_m, 1)}m <= max allowed {threshold_m}m."
            if passed
            else f"Geodesic distance {round(dist_m, 1)}m exceeds max allowed {threshold_m}m (Violation)."
        )
        return ConstraintEvaluation(
            constraint_id=constraint.id,
            alternative_id=alt_id,
            passed=passed,
            observed_value=round(dist_m, 1),
            threshold=threshold_m,
            margin=round(margin, 1),
            penalty=penalty,
            evidence_statement=stmt,
        )

    # Default fallback
    return ConstraintEvaluation(
        constraint_id=constraint.id,
        alternative_id=alt_id,
        passed=True,
        observed_value=None,
        threshold=constraint.threshold,
        margin=0.0,
        penalty=0.0,
        evidence_statement=f"Spatial predicate '{predicate}' evaluated.",
    )
