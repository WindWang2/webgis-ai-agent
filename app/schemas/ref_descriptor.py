"""Ref Descriptor — lightweight metadata computed once at ref creation.

ADR: Large Map Performance V3
Eliminates the need to re-scan 100k features on every descriptor request and
allows frontend to decide GeoJSON vs MVT without downloading the full payload.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RefDescriptor:
    """Lightweight ref metadata computed once at store() time.
    
    Attributes:
        ref_id: The canonical ref identifier
        feature_count: Total feature count (0 for non-FC data)
        point_count: Count of Point geometry features
        geometry_types: Set of geometry type strings present in the data
        bbox: [min_lon, min_lat, max_lon, max_lat] or None
        mvt_capable: True if the FC has vector geometry (Point/Line/Polygon)
            servable by the MVT encoder (app/services/mvt.py)
        estimated_bytes: Rough size estimate (feature-count heuristic; exact
            byte count is not computed to avoid blocking the store() hot path)
        content_hash: Reserved; not computed on the hot path (see compute_descriptor)
    """
    ref_id: str
    feature_count: int
    point_count: int
    geometry_types: List[str]
    bbox: Optional[List[float]]
    mvt_capable: bool
    estimated_bytes: int
    content_hash: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize to dict for SSE/JSON responses."""
        return {
            "ref_id": self.ref_id,
            "feature_count": self.feature_count,
            "point_count": self.point_count,
            "geometry_types": self.geometry_types,
            "bbox": self.bbox,
            "mvt_capable": self.mvt_capable,
            "estimated_bytes": self.estimated_bytes,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RefDescriptor":
        """Deserialize from stored dict."""
        return cls(
            ref_id=d["ref_id"],
            feature_count=d.get("feature_count", 0),
            point_count=d.get("point_count", 0),
            geometry_types=d.get("geometry_types", []),
            bbox=d.get("bbox"),
            mvt_capable=d.get("mvt_capable", False),
            estimated_bytes=d.get("estimated_bytes", 0),
            content_hash=d.get("content_hash"),
        )


def compute_descriptor(ref_id: str, data) -> RefDescriptor:
    """Compute descriptor from raw data at store time.
    
    Handles three shapes:
    - FeatureCollection dict
    - Wrapped: {"geojson": FeatureCollection}
    - Tool result: {"type": "...", "geojson": FeatureCollection}
    
    Returns descriptor with all fields populated. For non-FC data,
    feature_count/point_count/geometry_types will be zero/empty.
    """
    import json
    
    # Extract FeatureCollection
    fc = data
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict):
            fc = nested
    
    feature_count = 0
    point_count = 0
    geometry_types = set()
    bbox_coords = []
    
    if isinstance(fc, dict) and fc.get("type") == "FeatureCollection":
        features = fc.get("features", [])
        feature_count = len(features)
        
        for feature in features:
            if not isinstance(feature, dict):
                continue
            geometry = feature.get("geometry")
            if not geometry or not isinstance(geometry, dict):
                continue
            
            geom_type = geometry.get("type")
            if geom_type:
                geometry_types.add(geom_type)
                if geom_type == "Point":
                    point_count += 1
                    coords = geometry.get("coordinates")
                    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                        if coords[0] is not None and coords[1] is not None:
                            bbox_coords.append((coords[0], coords[1]))
                elif geom_type in ("LineString", "MultiPoint"):
                    for coords in (geometry.get("coordinates") or []):
                        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                            if coords[0] is not None and coords[1] is not None:
                                bbox_coords.append((coords[0], coords[1]))
                elif geom_type in ("Polygon", "MultiLineString"):
                    for ring in (geometry.get("coordinates") or []):
                        for coords in ring:
                            if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                                if coords[0] is not None and coords[1] is not None:
                                    bbox_coords.append((coords[0], coords[1]))
                elif geom_type == "MultiPolygon":
                    for polygon in (geometry.get("coordinates") or []):
                        for ring in polygon:
                            for coords in ring:
                                if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                                    if coords[0] is not None and coords[1] is not None:
                                        bbox_coords.append((coords[0], coords[1]))
    
    # Compute bbox
    bbox = None
    if bbox_coords:
        lons = [c[0] for c in bbox_coords]
        lats = [c[1] for c in bbox_coords]
        bbox = [min(lons), min(lats), max(lons), max(lats)]
    elif isinstance(fc, dict) and isinstance(fc.get("bbox"), list) and len(fc.get("bbox", [])) == 4:
        # Honor existing bbox member if present
        bbox = fc["bbox"]
    
    # Estimate bytes: cheap heuristic — 100 bytes/feature base + raw FC overhead.
    # Avoids serialising the entire payload just for an estimate (O(n) blocked).
    if feature_count > 0:
        estimated_bytes = feature_count * 100 + 1024
    else:
        try:
            estimated_bytes = len(json.dumps(data, ensure_ascii=False))
        except Exception:
            estimated_bytes = len(str(data))
    
    # content_hash intentionally omitted from hot-path compute: two full
    # json.dumps + SHA256 of a 30MB payload block the event loop in store().
    # Checkpoint already hashes independently for its own dedup.
    content_hash = None
    
    return RefDescriptor(
        ref_id=ref_id,
        feature_count=feature_count,
        point_count=point_count,
        geometry_types=sorted(list(geometry_types)),
        bbox=bbox,
        # V3: MVT encoder supports Point/Line/Polygon; any FC with vector
        # features is tile-capable (GeometryCollection members are silently
        # skipped in the encoder but that's handled at render time).
        mvt_capable=(feature_count > 0 and bool(
            geometry_types - {"GeometryCollection"}
        )),
        estimated_bytes=estimated_bytes,
        content_hash=content_hash,
    )
