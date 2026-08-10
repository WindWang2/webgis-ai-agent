"""
Spatial Data Quality Engine: Audits GeoJSON datasets across 5 dimensions:
1. Geometry (invalid, self-intersection, empty, duplicate, ring/sliver check)
2. Topology (overlap, gap, duplicate features, near-duplicate vertices, dangling endpoints)
3. CRS (missing CRS, suspicious CRS, geo-vs-projected measurement warning)
4. Attributes (schema, null ratio, duplicate primary IDs, type inconsistencies, numeric outliers > 3 std dev)
5. Spatial sanity (invalid bbox, Null Island (0,0), impossible lat/lon (>90/180), extreme coordinates)

Emits structured SpatialQualityReport with explicit severity levels (info, warning, error, blocking).
"""

import math
import logging
from typing import List, Dict, Any, Optional, Set, Tuple
from pydantic import BaseModel, Field
import numpy as np

from shapely.geometry import (
    shape,
    Polygon,
    MultiPolygon,
    LineString,
    MultiLineString,
    Point,
)
from shapely.validation import explain_validity
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)


class QualityIssue(BaseModel):
    dimension: str      # "geometry", "topology", "crs", "attributes", "spatial_sanity"
    code: str           # Issue identification code
    level: str          # "info", "warning", "error", "blocking"
    message: str
    feature_index: Optional[int] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class SpatialQualityReport(BaseModel):
    dataset_id: str = "dataset"
    total_features: int = 0
    issue_summary: Dict[str, int] = Field(
        default_factory=lambda: {"info": 0, "warning": 0, "error": 0, "blocking": 0}
    )
    issues: List[QualityIssue] = Field(default_factory=list)
    overall_status: str = "passed"  # "passed", "warning", "blocking"

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class SpatialQualityEngine:
    @classmethod
    def audit_dataset(
        cls,
        geojson_data: Dict[str, Any],
        crs: str = "EPSG:4326",
        dataset_id: Optional[str] = None,
    ) -> SpatialQualityReport:
        """
        Audits a GeoJSON dataset across all 5 spatial quality dimensions.
        """
        if dataset_id is None:
            dataset_id = geojson_data.get("name") or geojson_data.get("id") or "dataset"

        features = geojson_data.get("features", [])
        if not isinstance(features, list) and geojson_data.get("type") == "Feature":
            features = [geojson_data]

        total_features = len(features)
        issues: List[QualityIssue] = []

        # ----------------------------------------------------
        # Dimension 3: CRS Checks
        # ----------------------------------------------------
        geojson_crs = geojson_data.get("crs")
        # GIS-15: if the caller passed crs=None but the GeoJSON carries a `crs`
        # member, fall back to that before any .upper() call. The GeoJSON spec
        # default when no crs is declared anywhere is EPSG:4326.
        if not crs:
            if isinstance(geojson_crs, dict):
                props = geojson_crs.get("properties") or {}
                crs = props.get("name") or props.get("code") or "EPSG:4326"
            else:
                crs = "EPSG:4326"
        if not geojson_crs and crs.upper() in ["UNKNOWN", "MISSING"]:
            issues.append(
                QualityIssue(
                    dimension="crs",
                    code="MISSING_CRS",
                    level="info",
                    message="Dataset is missing explicit CRS definition. Defaulting to EPSG:4326.",
                )
            )
            crs = "EPSG:4326"

        is_geographic = crs.upper() in ["EPSG:4326", "WGS84", "CRS84", "EPSG:4490"]
        if is_geographic:
            issues.append(
                QualityIssue(
                    dimension="crs",
                    code="GEO_VS_PROJECTED_MEASUREMENT_WARNING",
                    level="info",
                    message="Geographic CRS (EPSG:4326) detected. Spatial area/distance operations will produce degree-based values rather than metric measurements.",
                )
            )

        # ----------------------------------------------------
        # Dimension 5: Spatial Sanity - Header BBOX Check
        # ----------------------------------------------------
        declared_bbox = geojson_data.get("bbox")
        if declared_bbox:
            if not isinstance(declared_bbox, (list, tuple)) or len(declared_bbox) < 4:
                issues.append(
                    QualityIssue(
                        dimension="spatial_sanity",
                        code="INVALID_BBOX",
                        level="warning",
                        message="Top-level GeoJSON bbox format is invalid.",
                        details={"bbox": declared_bbox},
                    )
                )
            else:
                minx, miny, maxx, maxy = declared_bbox[:4]
                if minx > maxx or miny > maxy:
                    issues.append(
                        QualityIssue(
                            dimension="spatial_sanity",
                            code="INVALID_BBOX",
                            level="warning",
                            message=f"Top-level GeoJSON bbox bounds are inverted: [{minx}, {miny}, {maxx}, {maxy}].",
                            details={"bbox": declared_bbox},
                        )
                    )

        parsed_geometries: List[Tuple[int, Any, Dict[str, Any]]] = []

        # ----------------------------------------------------
        # Dimension 1 & 5: Feature Geometry & Spatial Sanity
        # ----------------------------------------------------
        for idx, feat in enumerate(features):
            if not isinstance(feat, dict):
                issues.append(
                    QualityIssue(
                        dimension="geometry",
                        code="INVALID_FEATURE_FORMAT",
                        level="blocking",
                        message=f"Feature {idx} is not a valid JSON dictionary.",
                        feature_index=idx,
                    )
                )
                continue

            geom_raw = feat.get("geometry")

            # Geometry: Empty / Null Check
            if geom_raw is None:
                issues.append(
                    QualityIssue(
                        dimension="geometry",
                        code="EMPTY_GEOMETRY",
                        level="blocking",
                        message=f"Feature {idx} has null or missing geometry.",
                        feature_index=idx,
                    )
                )
                continue

            try:
                geom = shape(geom_raw)
            except Exception as e:
                issues.append(
                    QualityIssue(
                        dimension="geometry",
                        code="INVALID_GEOMETRY_SYNTAX",
                        level="blocking",
                        message=f"Feature {idx} geometry cannot be parsed: {e}",
                        feature_index=idx,
                    )
                )
                continue

            if geom.is_empty:
                issues.append(
                    QualityIssue(
                        dimension="geometry",
                        code="EMPTY_GEOMETRY",
                        level="error",
                        message=f"Feature {idx} has an empty geometry.",
                        feature_index=idx,
                    )
                )
                continue

            props = feat.get("properties") or {}
            parsed_geometries.append((idx, geom, props))

            # 1a. Invalid geometry & self-intersection
            if not geom.is_valid:
                reason = explain_validity(geom)
                is_self_intersect = "Self-intersection" in reason or "self-intersection" in reason
                issues.append(
                    QualityIssue(
                        dimension="geometry",
                        code="SELF_INTERSECTION" if is_self_intersect else "INVALID_GEOMETRY",
                        level="blocking" if is_self_intersect or "Nested shells" in reason else "error",
                        message=f"Feature {idx} has invalid geometry: {reason}",
                        feature_index=idx,
                        details={"reason": reason},
                    )
                )
            elif isinstance(geom, (LineString, MultiLineString)) and not geom.is_simple:
                issues.append(
                    QualityIssue(
                        dimension="geometry",
                        code="SELF_INTERSECTION",
                        level="error",
                        message=f"Feature {idx} LineString contains self-intersections.",
                        feature_index=idx,
                    )
                )

            # 1b. Ring check for Polygons
            if isinstance(geom, Polygon):
                rings = [geom.exterior] + list(geom.interiors)
                for ring in rings:
                    coords = list(ring.coords)
                    if len(coords) < 4:
                        issues.append(
                            QualityIssue(
                                dimension="geometry",
                                code="RING_CHECK_FAILED",
                                level="error",
                                message=f"Feature {idx} polygon ring has fewer than 4 coordinates ({len(coords)}).",
                                feature_index=idx,
                            )
                        )
                    elif coords[0] != coords[-1]:
                        issues.append(
                            QualityIssue(
                                dimension="geometry",
                                code="RING_CHECK_FAILED",
                                level="error",
                                message=f"Feature {idx} polygon ring is not closed (start != end).",
                                feature_index=idx,
                            )
                        )
            elif isinstance(geom, MultiPolygon):
                for p_sub in geom.geoms:
                    rings = [p_sub.exterior] + list(p_sub.interiors)
                    for ring in rings:
                        coords = list(ring.coords)
                        if len(coords) < 4:
                            issues.append(
                                QualityIssue(
                                    dimension="geometry",
                                    code="RING_CHECK_FAILED",
                                    level="error",
                                    message=f"Feature {idx} multi-polygon ring has fewer than 4 coordinates.",
                                    feature_index=idx,
                                )
                            )
                        elif coords[0] != coords[-1]:
                            issues.append(
                                QualityIssue(
                                    dimension="geometry",
                                    code="RING_CHECK_FAILED",
                                    level="error",
                                    message=f"Feature {idx} multi-polygon ring is not closed.",
                                    feature_index=idx,
                                )
                            )

            # 1c. Sliver Check
            if isinstance(geom, (Polygon, MultiPolygon)):
                area = geom.area
                perimeter = geom.length
                if area > 0 and perimeter > 0:
                    iso_ratio = (perimeter ** 2) / area
                    if iso_ratio > 1000.0:
                        issues.append(
                            QualityIssue(
                                dimension="geometry",
                                code="SLIVER_POLYGON",
                                level="warning",
                                message=f"Feature {idx} is a sliver polygon with high perimeter-to-area ratio ({iso_ratio:.1f}).",
                                feature_index=idx,
                                details={"area": area, "perimeter": perimeter, "isoperimetric_ratio": iso_ratio},
                            )
                        )

            # 5a. Extreme coordinates / Impossible Lat/Lon / Null Island
            bounds = geom.bounds  # (minx, miny, maxx, maxy)
            minx, miny, maxx, maxy = bounds

            if any(math.isnan(c) or math.isinf(c) for c in bounds) or max(abs(minx), abs(miny), abs(maxx), abs(maxy)) > 1e10:
                issues.append(
                    QualityIssue(
                        dimension="spatial_sanity",
                        code="EXTREME_COORDINATES",
                        level="blocking",
                        message=f"Feature {idx} contains extreme or NaN/Inf coordinates.",
                        feature_index=idx,
                        details={"bounds": bounds},
                    )
                )

            if is_geographic:
                if abs(minx) > 180 or abs(maxx) > 180 or abs(miny) > 90 or abs(maxy) > 90:
                    issues.append(
                        QualityIssue(
                            dimension="spatial_sanity",
                            code="IMPOSSIBLE_LAT_LON",
                            level="blocking",
                            message=f"Feature {idx} has coordinates exceeding WGS84 latitude/longitude bounds [-180, 180], [-90, 90].",
                            feature_index=idx,
                            details={"bounds": bounds},
                        )
                    )
                    issues.append(
                        QualityIssue(
                            dimension="crs",
                            code="SUSPICIOUS_CRS",
                            level="warning",
                            message=f"Feature {idx} coordinates exceed 180 degrees in EPSG:4326; dataset may be in a projected CRS.",
                            feature_index=idx,
                            details={"bounds": bounds},
                        )
                    )

            # Null Island check (0,0)
            centroid = geom.centroid
            if abs(centroid.x) < 1e-7 and abs(centroid.y) < 1e-7:
                issues.append(
                    QualityIssue(
                        dimension="spatial_sanity",
                        code="NULL_ISLAND",
                        level="warning",
                        message=f"Feature {idx} is located at or very near Null Island (0, 0).",
                        feature_index=idx,
                        details={"centroid": (centroid.x, centroid.y)},
                    )
                )

        # Check declared bbox vs calculated bounds
        if declared_bbox and len(declared_bbox) >= 4 and parsed_geometries:
            d_minx, d_miny, d_maxx, d_maxy = declared_bbox[:4]
            c_minx = min(g.bounds[0] for _, g, _ in parsed_geometries)
            c_miny = min(g.bounds[1] for _, g, _ in parsed_geometries)
            c_maxx = max(g.bounds[2] for _, g, _ in parsed_geometries)
            c_maxy = max(g.bounds[3] for _, g, _ in parsed_geometries)

            if abs(d_minx - c_minx) > 1e-4 or abs(d_miny - c_miny) > 1e-4 or abs(d_maxx - c_maxx) > 1e-4 or abs(d_maxy - c_maxy) > 1e-4:
                issues.append(
                    QualityIssue(
                        dimension="spatial_sanity",
                        code="INVALID_BBOX",
                        level="warning",
                        message="Declared GeoJSON bbox does not match actual calculated dataset bounding box.",
                        details={
                            "declared_bbox": [d_minx, d_miny, d_maxx, d_maxy],
                            "calculated_bbox": [c_minx, c_miny, c_maxx, c_maxy],
                        },
                    )
                )

        # ----------------------------------------------------
        # Dimension 1 (cont): Duplicate Geometries
        # ----------------------------------------------------
        seen_wkb: Dict[bytes, int] = {}
        for idx, geom, _ in parsed_geometries:
            wkb = geom.wkb
            if wkb in seen_wkb:
                orig_idx = seen_wkb[wkb]
                issues.append(
                    QualityIssue(
                        dimension="geometry",
                        code="DUPLICATE_GEOMETRY",
                        level="warning",
                        message=f"Feature {idx} has duplicate geometry identical to feature {orig_idx}.",
                        feature_index=idx,
                        details={"duplicate_of": orig_idx},
                    )
                )
            else:
                seen_wkb[wkb] = idx

        # ----------------------------------------------------
        # Dimension 2: Topology Checks
        # ----------------------------------------------------
        if parsed_geometries:
            valid_shapes = [g for _, g, _ in parsed_geometries]
            idx_map = [idx for idx, _, _ in parsed_geometries]
            props_map = [props for _, _, props in parsed_geometries]

            tree = STRtree(valid_shapes)
            line_endpoints: List[Tuple[Point, int]] = []

            for i, (f_idx_i, geom_i, props_i) in enumerate(parsed_geometries):
                # Collect line endpoints for dangling endpoint checks
                if isinstance(geom_i, LineString):
                    coords = list(geom_i.coords)
                    if coords:
                        line_endpoints.append((Point(coords[0]), f_idx_i))
                        line_endpoints.append((Point(coords[-1]), f_idx_i))
                elif isinstance(geom_i, MultiLineString):
                    for ls in geom_i.geoms:
                        coords = list(ls.coords)
                        if coords:
                            line_endpoints.append((Point(coords[0]), f_idx_i))
                            line_endpoints.append((Point(coords[-1]), f_idx_i))

                candidates = tree.query(geom_i)
                for cand_pos in candidates:
                    j = int(cand_pos)
                    if j <= i:
                        continue
                    f_idx_j = idx_map[j]
                    geom_j = valid_shapes[j]
                    props_j = props_map[j]

                    # Topology: Duplicate Feature
                    if geom_i.equals(geom_j):
                        if props_i == props_j:
                            issues.append(
                                QualityIssue(
                                    dimension="topology",
                                    code="DUPLICATE_FEATURE",
                                    level="warning",
                                    message=f"Feature {f_idx_j} is a duplicate feature (identical geometry and attributes to feature {f_idx_i}).",
                                    feature_index=f_idx_j,
                                    details={"duplicate_of": f_idx_i},
                                )
                            )

                    # Topology: Overlap check for Polygons
                    if isinstance(geom_i, (Polygon, MultiPolygon)) and isinstance(geom_j, (Polygon, MultiPolygon)):
                        if geom_i.overlaps(geom_j) or (geom_i.intersects(geom_j) and geom_i.intersection(geom_j).area > 1e-7):
                            issues.append(
                                QualityIssue(
                                    dimension="topology",
                                    code="TOPOLOGY_OVERLAP",
                                    level="error",
                                    message=f"Feature {f_idx_i} overlaps with feature {f_idx_j}.",
                                    feature_index=f_idx_i,
                                    details={"overlaps_with": f_idx_j},
                                )
                            )

                    # Topology: Gap check (adjacent polygons with tiny gap)
                    # GIS-16 (deep-audit round 4): the thresholds were absolute
                    # DEGREES (1e-5 ≈ 1.1 m at the equator), which became a
                    # no-op for projected CRS (1e-5 m = 10 microns never
                    # matches). Scale by CRS: geographic keeps degree
                    # thresholds (~1.1 m gap / ~0.11 m near-duplicate); a
                    # projected CRS uses the equivalent METER thresholds.
                    dist = geom_i.distance(geom_j)
                    gap_threshold = 1e-5 if is_geographic else 1.0  # ~1.1 m at equator
                    dup_threshold = 1e-6 if is_geographic else 0.1  # ~0.11 m at equator
                    if 0 < dist < gap_threshold:
                        issues.append(
                            QualityIssue(
                                dimension="topology",
                                code="TOPOLOGY_GAP",
                                level="warning",
                                message=f"Small gap ({dist:.6f}) detected between feature {f_idx_i} and feature {f_idx_j}.",
                                feature_index=f_idx_i,
                                details={"gap_distance": dist, "adjacent_feature": f_idx_j},
                            )
                        )

                    # Topology: Near-duplicate vertices between features
                    if 0 < dist < dup_threshold:
                        issues.append(
                            QualityIssue(
                                dimension="topology",
                                code="NEAR_DUPLICATE_VERTICES",
                                level="warning",
                                message=f"Near-duplicate vertices detected between feature {f_idx_i} and feature {f_idx_j} (distance {dist:.8f}).",
                                feature_index=f_idx_i,
                                details={"distance": dist, "other_feature": f_idx_j},
                            )
                        )

            # Dangling endpoints check
            if line_endpoints:
                pt_geoms = [pt for pt, _ in line_endpoints]
                pt_tree = STRtree(pt_geoms)
                # GIS-16: same CRS-aware threshold as the gap checks.
                dup_threshold = 1e-6 if is_geographic else 0.1
                for pt_idx, (pt, f_idx) in enumerate(line_endpoints):
                    near_pts = pt_tree.query(pt)
                    connected = False
                    for candidate_idx in near_pts:
                        cand_idx_int = int(candidate_idx)
                        if cand_idx_int == pt_idx:
                            continue
                        if pt.distance(pt_geoms[cand_idx_int]) < dup_threshold:
                            connected = True
                            break
                    if not connected:
                        issues.append(
                            QualityIssue(
                                dimension="topology",
                                code="DANGLING_ENDPOINT",
                                level="warning",
                                message=f"Feature {f_idx} has a dangling line endpoint at ({pt.x}, {pt.y}).",
                                feature_index=f_idx,
                                details={"endpoint": (pt.x, pt.y)},
                            )
                        )

        # ----------------------------------------------------
        # Dimension 4: Attributes Checks
        # ----------------------------------------------------
        if features:
            feature_prop_keys = [
                set(f.get("properties", {}).keys())
                for f in features
                if isinstance(f, dict) and isinstance(f.get("properties"), dict)
            ]
            if feature_prop_keys:
                all_keys = set().union(*feature_prop_keys)
                common_keys = set.intersection(*feature_prop_keys) if feature_prop_keys else set()

                if len(common_keys) < len(all_keys):
                    issues.append(
                        QualityIssue(
                            dimension="attributes",
                            code="INCONSISTENT_SCHEMA",
                            level="info",
                            message="Dataset features have inconsistent property schemas across records.",
                            details={"all_keys": list(all_keys), "missing_keys": list(all_keys - common_keys)},
                        )
                    )

                null_counts = {k: 0 for k in all_keys}
                type_map: Dict[str, Set[type]] = {k: set() for k in all_keys}
                numeric_values: Dict[str, List[Tuple[int, float]]] = {k: [] for k in all_keys}
                id_values: Dict[str, List[Tuple[int, Any]]] = {}

                primary_id_fields = {"id", "ID", "fid", "gid", "uuid", "key", "pk"}

                for idx, feat in enumerate(features):
                    if not isinstance(feat, dict):
                        continue
                    props = feat.get("properties") or {}

                    if "id" in feat:
                        id_values.setdefault("top_level_id", []).append((idx, feat["id"]))

                    for k in all_keys:
                        val = props.get(k)
                        if val is None or val == "" or (isinstance(val, float) and math.isnan(val)):
                            null_counts[k] += 1
                        else:
                            type_map[k].add(type(val))
                            if isinstance(val, (int, float)) and not isinstance(val, bool):
                                numeric_values[k].append((idx, float(val)))
                            if k.lower() in primary_id_fields:
                                id_values.setdefault(k, []).append((idx, val))

                # Null ratio check (> 50%)
                for k, count in null_counts.items():
                    null_ratio = count / total_features
                    if null_ratio > 0.5:
                        issues.append(
                            QualityIssue(
                                dimension="attributes",
                                code="HIGH_NULL_RATIO",
                                level="warning",
                                message=f"Attribute '{k}' has a high null ratio ({null_ratio:.1%}).",
                                details={"attribute": k, "null_ratio": null_ratio, "null_count": count},
                            )
                        )

                # Duplicated primary IDs
                for id_field, val_list in id_values.items():
                    seen_ids: Dict[Any, int] = {}
                    for idx, val in val_list:
                        if val in seen_ids:
                            orig_idx = seen_ids[val]
                            issues.append(
                                QualityIssue(
                                    dimension="attributes",
                                    code="DUPLICATE_PRIMARY_KEY",
                                    level="error",
                                    message=f"Duplicate primary ID '{val}' found in field '{id_field}' at feature {idx} (first seen at feature {orig_idx}).",
                                    feature_index=idx,
                                    details={"field": id_field, "duplicate_value": val, "original_index": orig_idx},
                                )
                            )
                        else:
                            seen_ids[val] = idx

                # Type inconsistencies
                for k, types in type_map.items():
                    non_numeric_types = {t for t in types if t not in (int, float)}
                    if len(types) > 1 and (len(non_numeric_types) > 1 or (non_numeric_types and (int in types or float in types))):
                        type_names = [t.__name__ for t in types]
                        issues.append(
                            QualityIssue(
                                dimension="attributes",
                                code="TYPE_INCONSISTENCY",
                                level="warning",
                                message=f"Attribute '{k}' has inconsistent data types across features: {type_names}.",
                                details={"attribute": k, "types": type_names},
                            )
                        )

                # Numeric outliers (> 3 std dev)
                for k, vals in numeric_values.items():
                    if len(vals) >= 3:
                        num_array = np.array([v for _, v in vals])
                        mean = float(np.mean(num_array))
                        std = float(np.std(num_array))
                        if std > 1e-8:
                            for idx, val in vals:
                                z_score = abs(val - mean) / std
                                if z_score > 3.0:
                                    issues.append(
                                        QualityIssue(
                                            dimension="attributes",
                                            code="NUMERIC_OUTLIER",
                                            level="warning",
                                            message=f"Attribute '{k}' value {val} at feature {idx} is a numeric outlier ({z_score:.2f} std dev from mean).",
                                            feature_index=idx,
                                            details={"attribute": k, "value": val, "z_score": z_score, "mean": mean, "std": std},
                                        )
                                    )

        # ----------------------------------------------------
        # Summary & Status
        # ----------------------------------------------------
        summary = {"info": 0, "warning": 0, "error": 0, "blocking": 0}
        for issue in issues:
            summary[issue.level] = summary.get(issue.level, 0) + 1

        if summary["blocking"] > 0 or summary["error"] > 0:
            overall_status = "blocking"
        elif summary["warning"] > 0:
            overall_status = "warning"
        else:
            overall_status = "passed"

        return SpatialQualityReport(
            dataset_id=dataset_id,
            total_features=total_features,
            overall_status=overall_status,
            issue_summary=summary,
            issues=issues,
        )
