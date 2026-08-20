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
        raster_capable: True if the payload is a raster source (dict with
            file_path or path key) servable by the raster tile endpoint.
        estimated_bytes: Rough size estimate (feature-count heuristic; exact
            byte count is not computed to avoid blocking the store() hot path)
        content_hash: Reserved, always None. Computing a stable hash would
            require json.dumps + sha256 of the full payload on the store hot
            path (30 MB → seconds of blocking, defeats V3 off-loop goal).
            No current consumer needs dedup/cache-key/ETag from this field;
            if needed later, compute off-loop or lazily. Kept as None for
            non-breaking schema evolution.
        filterable_fields: Distinct property keys present across features,
            used as tile attribute whitelist for MVT setFilter contract (#668).
            Bounded to 100 distinct keys (sorted, first 100) to keep descriptor
            small and SSE payload bounded; whitelist is advisory for honest
            ack (server does not enforce strict filtering on tile encode).
    """
    ref_id: str
    feature_count: int
    point_count: int
    geometry_types: List[str]
    bbox: Optional[List[float]]
    mvt_capable: bool
    raster_capable: bool
    estimated_bytes: int
    content_hash: Optional[str] = None
    filterable_fields: Optional[List[str]] = None

    def to_dict(self) -> dict:
        """Serialize to dict for SSE/JSON responses."""
        return {
            "ref_id": self.ref_id,
            "feature_count": self.feature_count,
            "point_count": self.point_count,
            "geometry_types": self.geometry_types,
            "bbox": self.bbox,
            "mvt_capable": self.mvt_capable,
            "raster_capable": self.raster_capable,
            "estimated_bytes": self.estimated_bytes,
            "content_hash": self.content_hash,
            "filterable_fields": self.filterable_fields,
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
            raster_capable=d.get("raster_capable", False),
            estimated_bytes=d.get("estimated_bytes", 0),
            content_hash=d.get("content_hash"),
            filterable_fields=d.get("filterable_fields"),
        )


def iter_leaf_coords(coordinates):
    """Yield (lon, lat) leaf positions from any nested GeoJSON coordinates array.
    递归提取所有叶子坐标，覆盖 Point/MultiPoint/LineString/MultiLineString/Polygon/MultiPolygon
    的任意嵌套深度（含 Polygon holes）。"""
    if not isinstance(coordinates, list):
        return
    if coordinates and isinstance(coordinates[0], (int, float)):
        if len(coordinates) >= 2 and coordinates[0] is not None and coordinates[1] is not None:
            try:
                lon = float(coordinates[0])
                lat = float(coordinates[1])
                # 过滤 NaN/Inf
                import math
                if math.isfinite(lon) and math.isfinite(lat):
                    yield (coordinates[0], coordinates[1])
            except (TypeError, ValueError):
                pass
        return
    for child in coordinates:
        yield from iter_leaf_coords(child)


def collect_filterable_fields(features) -> Optional[List[str]]:
    """Collect distinct property keys across features for tile attribute whitelist (#668).

    Bounded to 100 distinct keys (sorted, first 100) to keep descriptor small
    and SSE payload bounded; whitelist is advisory for honest ack (server does
    not enforce strict filtering — see B5). Returns None when no keys found
    (keeps descriptor lean). Shared by store-time descriptor (compute_descriptor)
    and fallback path (app/api/routes/layer._compute_descriptor_fallback) so
    both stay byte-identical.
    """
    if not isinstance(features, list) or not features:
        return None
    keys: set = set()
    for f in features:
        props = f.get("properties") if isinstance(f, dict) else None
        if isinstance(props, dict):
            for k in props.keys():
                if isinstance(k, str) and k:
                    keys.add(k)
                    if len(keys) >= 100:
                        break
        if len(keys) >= 100:
            break
    return sorted(keys)[:100] if keys else None


# Back-compat alias — layer.py previously imported the private name.
_iter_leaf_coords = iter_leaf_coords


def estimate_bytes(feature_count: int) -> int:
    """Cheap heuristic reused by store-time and fallback paths — never materializes a giant string."""
    if feature_count > 0:
        return feature_count * 100 + 1024
    return 1024


def is_mvt_capable(geometry_types, feature_count: int) -> bool:
    """Shared capability rule — exactly one place.

    MVT encoder (app/services/mvt.py:89-95) supports Point/MultiPoint/
    LineString/MultiLineString/Polygon/MultiPolygon. Any FC with at least
    one non-GeometryCollection type is tile-capable.
    """
    try:
        gt_set = set(geometry_types) if geometry_types is not None else set()
    except TypeError:
        gt_set = set()
    return bool(feature_count > 0 and gt_set - {"GeometryCollection"})


def is_raster_capable(data) -> bool:
    """Shared raster detection — exactly one place.

    Mirrors the fallback path in app/api/routes/layer.py
    (_compute_descriptor_fallback) which checks for file_path/path keys.
    """
    return isinstance(data, dict) and ("file_path" in data or "path" in data)


def compute_descriptor(ref_id: str, data) -> RefDescriptor:
    """Compute descriptor from raw data at store time.
    
    Handles three shapes:
    - FeatureCollection dict
    - Wrapped: {"geojson": FeatureCollection}
    - Tool result: {"type": "...", "geojson": FeatureCollection}
    
    Returns descriptor with all fields populated. For non-FC data,
    feature_count/point_count/geometry_types will be zero/empty.
    """
    # Extract FeatureCollection
    fc = data
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict):
            fc = nested
    
    feature_count = 0
    point_count = 0
    geometry_types = set()
    bbox_coords: List = []
    features: List = []

    if isinstance(fc, dict) and fc.get("type") == "FeatureCollection":
        features = fc.get("features", []) if isinstance(fc.get("features"), list) else []
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
                # bbox 覆盖 MVT 编码器支持的所有类型（app/services/mvt.py:89-95）
                # 使用递归叶子提取，保证 holes 与多层嵌套均被计入。
                if geom_type == "GeometryCollection":
                    for sub in (geometry.get("geometries") or []):
                        if isinstance(sub, dict):
                            for lon, lat in iter_leaf_coords(sub.get("coordinates")):
                                bbox_coords.append((lon, lat))
                else:
                    for lon, lat in iter_leaf_coords(geometry.get("coordinates")):
                        bbox_coords.append((lon, lat))
    
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
    # Non-FC / empty FC → fixed overhead; never len(json.dumps/str) on large payloads.
    if feature_count > 0:
        estimated_bytes = estimate_bytes(feature_count)
    else:
        estimated_bytes = estimate_bytes(0)
    
    # content_hash intentionally omitted from hot-path compute: two full
    # json.dumps + SHA256 of a 30MB payload block the event loop in store().
    # Checkpoint already hashes independently for its own dedup.
    content_hash = None

    # #668: attribute whitelist via shared helper (identical to fallback path)
    filterable_fields = collect_filterable_fields(features)

    return RefDescriptor(
        ref_id=ref_id,
        feature_count=feature_count,
        point_count=point_count,
        geometry_types=sorted(list(geometry_types)),
        bbox=bbox,
        mvt_capable=is_mvt_capable(geometry_types, feature_count),
        raster_capable=is_raster_capable(data),
        estimated_bytes=estimated_bytes,
        content_hash=content_hash,
        filterable_fields=filterable_fields,
    )
