"""
Temporal Change Engine.
Compares multi-snapshot temporal changes (T1 vs T2 vs Tn) across feature counts,
attribute numeric deltas, and geometry/spatial centroid displacement.
"""

import math
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.temporal.models import TemporalChangeResult
from app.services.temporal.profiler import parse_value_to_instant

logger = logging.getLogger(__name__)


def compute_centroid(geometry: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Helper to compute basic centroid (lon, lat) of Point/Polygon geometries.

    Limitation: Polygon centroid is the arithmetic mean of the exterior ring
    vertices (holes are flattened into the same mean). True area-weighted
    centroids of polygons with holes differ; for precise metrics use shapely
    area centroids on projected geometries.
    """
    if not geometry or not isinstance(geometry, dict):
        return None
    gtype = geometry.get("type", "")
    coords = geometry.get("coordinates", [])

    if gtype == "Point":
        return float(coords[0]), float(coords[1])
    elif gtype == "Polygon" and coords:
        ring = coords[0]
        if not ring:
            return None
        lons = [pt[0] for pt in ring]
        lats = [pt[1] for pt in ring]
        return sum(lons) / len(lons), sum(lats) / len(lats)
    elif gtype == "MultiPolygon" and coords:
        lons, lats = [], []
        for poly in coords:
            for ring in poly:
                for pt in ring:
                    lons.append(pt[0])
                    lats.append(pt[1])
        if lons:
            return sum(lons) / len(lons), sum(lats) / len(lats)
    return None


def haversine_distance(coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
    """Calculates distance between two (lon, lat) coordinates in meters."""
    lon1, lat1 = coord1
    lon2, lat2 = coord2
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


class TemporalChangeEngine:
    """
    Analyzes temporal change across multiple snapshot datasets (T1, T2, ... Tn).
    """

    def compare_snapshots(
        self,
        snapshots: List[Dict[str, Any]],
        snapshot_names_or_times: Optional[List[str]] = None,
        numeric_fields: Optional[List[str]] = None,
        id_field: str = "id",
        time_field: Optional[str] = None,
    ) -> TemporalChangeResult:
        """
        Compares multi-snapshot data.
        'snapshots' can be a list of GeoJSON FeatureCollections or lists of feature dicts.
        """
        if not snapshots:
            return TemporalChangeResult(snapshot_count=0)

        # Prepare parsed snapshot records
        parsed_snapshots: List[Dict[str, Any]] = []

        for idx, snap in enumerate(snapshots):
            features = snap.get("features", snap) if isinstance(snap, dict) and "features" in snap else (snap if isinstance(snap, list) else [])
            t_label = None
            if snapshot_names_or_times and idx < len(snapshot_names_or_times):
                t_label = snapshot_names_or_times[idx]

            # Try extracting time label from features if not given
            if not t_label and time_field and features:
                for f in features:
                    props = f.get("properties", f) if isinstance(f, dict) else {}
                    val = props.get(time_field)
                    if val is not None:
                        inst = parse_value_to_instant(val, field_name_hint=time_field)
                        if inst:
                            t_label = inst[0].iso_string
                            break

            t_label = t_label or f"T{idx + 1}"
            parsed_snapshots.append({
                "index": idx,
                "label": t_label,
                "features": features,
            })

        time_points = [s["label"] for s in parsed_snapshots]
        count_deltas: List[Dict[str, Any]] = []

        # Count deltas between consecutive snapshots
        for i in range(len(parsed_snapshots) - 1):
            s1 = parsed_snapshots[i]
            s2 = parsed_snapshots[i + 1]
            c1 = len(s1["features"])
            c2 = len(s2["features"])
            diff = c2 - c1
            pct = (diff / c1 * 100.0) if c1 > 0 else (100.0 if c2 > 0 else 0.0)

            count_deltas.append({
                "from": s1["label"],
                "to": s2["label"],
                "count_from": c1,
                "count_to": c2,
                "delta": diff,
                "pct_change": round(pct, 2),
            })

        # Attribute deltas
        if not numeric_fields:
            # Auto-detect numeric fields from first snapshot
            sample_features = parsed_snapshots[0]["features"]
            num_keys = set()
            for f in sample_features[:20]:
                props = f.get("properties", f) if isinstance(f, dict) else {}
                for k, v in props.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        num_keys.add(k)
            numeric_fields = list(num_keys)

        attribute_deltas: List[Dict[str, Any]] = []
        for field in numeric_fields:
            field_snapshots_stats = []
            for s in parsed_snapshots:
                vals = []
                for f in s["features"]:
                    props = f.get("properties", f) if isinstance(f, dict) else {}
                    v = props.get(field)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        vals.append(float(v))

                mean_val = (sum(vals) / len(vals)) if vals else 0.0
                sum_val = sum(vals) if vals else 0.0
                field_snapshots_stats.append({"mean": mean_val, "sum": sum_val, "count": len(vals)})

            # consecutive attribute deltas
            consecutive_changes = []
            for i in range(len(field_snapshots_stats) - 1):
                st1 = field_snapshots_stats[i]
                st2 = field_snapshots_stats[i + 1]
                mean_diff = st2["mean"] - st1["mean"]
                pct_change = (mean_diff / abs(st1["mean"]) * 100.0) if st1["mean"] != 0 else 0.0
                consecutive_changes.append({
                    "from": parsed_snapshots[i]["label"],
                    "to": parsed_snapshots[i + 1]["label"],
                    "mean_from": round(st1["mean"], 4),
                    "mean_to": round(st2["mean"], 4),
                    "mean_delta": round(mean_diff, 4),
                    "pct_change": round(pct_change, 2),
                })

            attribute_deltas.append({
                "field": field,
                "snapshot_stats": field_snapshots_stats,
                "consecutive_changes": consecutive_changes,
            })

        # Feature matching and geometry deltas
        geometry_deltas: Optional[Dict[str, Any]] = None
        feature_changes: List[Dict[str, Any]] = []

        if len(parsed_snapshots) >= 2:
            s1_features = parsed_snapshots[0]["features"]
            sn_features = parsed_snapshots[-1]["features"]

            # Map features by id_field
            id_map_s1 = {}
            for f in s1_features:
                props = f.get("properties", f) if isinstance(f, dict) else {}
                fid = props.get(id_field) or f.get("id")
                if fid is not None:
                    id_map_s1[str(fid)] = f

            shifted_distances: List[float] = []

            for f_end in sn_features:
                props_end = f_end.get("properties", f_end) if isinstance(f_end, dict) else {}
                fid = props_end.get(id_field) or f_end.get("id")
                if fid is not None and str(fid) in id_map_s1:
                    f_start = id_map_s1[str(fid)]

                    # Check centroid shift
                    geom1 = f_start.get("geometry") if isinstance(f_start, dict) else None
                    geom2 = f_end.get("geometry") if isinstance(f_end, dict) else None

                    c1 = compute_centroid(geom1) if geom1 else None
                    c2 = compute_centroid(geom2) if geom2 else None

                    dist = 0.0
                    if c1 and c2:
                        dist = haversine_distance(c1, c2)
                        shifted_distances.append(dist)

                    feature_changes.append({
                        "id": str(fid),
                        "centroid_shift_meters": round(dist, 2),
                    })

            if shifted_distances:
                geometry_deltas = {
                    "matched_features_count": len(shifted_distances),
                    "mean_centroid_shift_meters": round(sum(shifted_distances) / len(shifted_distances), 2),
                    "max_centroid_shift_meters": round(max(shifted_distances), 2),
                }

        return TemporalChangeResult(
            snapshot_count=len(snapshots),
            time_points=time_points,
            count_deltas=count_deltas,
            attribute_deltas=attribute_deltas,
            geometry_deltas=geometry_deltas,
            feature_changes=feature_changes if feature_changes else None,
        )
