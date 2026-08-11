import json
import logging
import threading
from typing import Any, Optional
import geopandas as gpd
from shapely.geometry import shape

from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Re-export coordinate transform functions from the canonical module
# (app/utils/coord_transform.py). Duplicate implementations removed below.
from app.utils.coord_transform import (  # noqa: F401
    wgs84_to_gcj02,
    gcj02_to_wgs84,
    gcj02_to_bd09,
    bd09_to_gcj02,
)

@dataclass
class GeoAnalysisResult:
    """
    Standard interface for geoprocessing tool results.
    Explicitly supports LLM narration and self-healing hints.
    """
    success: bool
    data: Any
    summary: str
    error_type: Optional[str] = None
    correction_hint: Optional[str] = None

    @property
    def error_message(self) -> Optional[str]:
        return self.summary if not self.success else None

    @property
    def stats(self) -> Optional[dict]:
        if isinstance(self.data, dict) and "stats" in self.data:
            return self.data["stats"]
        return None

    def to_llm_response(self) -> dict:
        """
        Converts the result into a format the ChatEngine can easily digest.
        Includes stats when available so the LLM can reference numeric
        summaries without re-parsing the full data payload.
        """
        result = {
            "success": self.success,
            "summary": self.summary,
            "data": self.data,
            "error_type": self.error_type,
            "correction_hint": self.correction_hint
        }
        if self.stats is not None:
            result["stats"] = self.stats
        return result


def _repair_json(s: str) -> str:
    """Very simple JSON repair: adds missing closing brackets/braces."""
    stack = []
    in_string = False
    escaped = False
    for char in s:
        if escaped:
            escaped = False
            continue
        if char == '\\' and in_string:
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif not in_string:
            if char == '{':
                stack.append('}')
            elif char == '[':
                stack.append(']')
            elif char == '}':
                if stack and stack[-1] == '}':
                    stack.pop()
            elif char == ']':
                if stack and stack[-1] == ']':
                    stack.pop()
    suffix = '"' if in_string else ''
    return s + suffix + "".join(reversed(stack))

def safe_parse(geojson: Any) -> dict | list | None:
    """Robust parsing of GeoJSON string, dict, or feature list."""
    if geojson is None:
        return None
    if isinstance(geojson, dict):
        return geojson
    if isinstance(geojson, list):
        if not geojson or all(isinstance(x, dict) for x in geojson):
            return geojson
        return None
    if isinstance(geojson, str):
        geojson = geojson.strip()
        if not geojson or geojson.startswith("ref:"):
            return None
        try:
            parsed = json.loads(geojson)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                if not parsed or all(isinstance(x, dict) for x in parsed):
                    return parsed
            return None
        except (json.JSONDecodeError, TypeError):
            # Try simple repair for truncated strings
            try:
                repaired = _repair_json(geojson)
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list):
                    if not parsed or all(isinstance(x, dict) for x in parsed):
                        return parsed
                return None
            except Exception:
                return None
    return None


def to_feature_collection(data: Any) -> dict:
    """Normalize input data into a valid FeatureCollection dict.

    Accepts a GeoJSON dict, a features list, a string (parsed via
    :func:`safe_parse`, which also repairs truncated JSON), a single Feature,
    or a bare geometry — and always returns a ``{"type": "FeatureCollection",
    "features": [...]}`` dict (never None). Never raises.
    """
    if not data:
        return {"type": "FeatureCollection", "features": []}

    if isinstance(data, str):
        if data.strip().startswith("ref:"):
            return {"type": "FeatureCollection", "features": []}
        data = safe_parse(data)
        if not data:
            return {"type": "FeatureCollection", "features": []}

    if isinstance(data, dict):
        d_type = data.get("type")
        if d_type == "FeatureCollection":
            return data
        if d_type == "Feature":
            return {"type": "FeatureCollection", "features": [data]}
        if "features" in data and isinstance(data["features"], list):
            return {"type": "FeatureCollection", "features": data["features"]}
        if "coordinates" in data and "type" in data:
            return {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": data, "properties": {}}]
            }
        return {"type": "FeatureCollection", "features": []}

    if isinstance(data, list):
        return {"type": "FeatureCollection", "features": data}

    return {"type": "FeatureCollection", "features": []}

# ---------------------------------------------------------------------------
# to_utm_gdf memoization (Phase 4 perf)
#
# Multi-step analyses (cluster -> hotspot on the same layer) each call
# to_utm_gdf and re-pay parse + UTM reprojection. We cache by object identity
# (id(geojson)). The cache is bounded (LRU) and returns COPIES on hit so caller
# mutations never poison the cached entry. Identity-keyed (not content-hashed)
# so hashing a 100k-point FeatureCollection stays O(1).
#
# CRITICAL correctness note (found via flaky CI test): the cache entry MUST
# hold a strong reference to the geojson object. CPython reuses memory
# addresses: once an object is GC'd, a new object can land on the same id().
# Without the reference pin, a fresh FeatureCollection from another test can
# key-collide with an evicted-but-still-live cache entry and silently get the
# OLD object's cached result. Holding the reference makes id() reuse
# impossible while the entry is live; LRU eviction releases the pin and only
# then can the address be reused.
# ---------------------------------------------------------------------------
_UTM_CACHE_MAX = 64
_utm_cache: dict[tuple, tuple] = {}  # key -> (geojson_ref, gdf, utm_crs_str)
_utm_cache_order: list[tuple] = []   # LRU order (MRU at end)
_utm_cache_lock = threading.Lock()


def clear_utm_cache() -> None:
    """Drop all cached to_utm_gdf results (tests / memory-pressure escape hatch)."""
    with _utm_cache_lock:
        _utm_cache.clear()
        _utm_cache_order.clear()


def get_utm_cache_info() -> dict:
    """Introspection accessor (size only; hit/miss counters omitted for simplicity)."""
    with _utm_cache_lock:
        return {"size": len(_utm_cache), "max": _UTM_CACHE_MAX}


def _cache_key_for(geojson: Any, source_crs: Optional[str]) -> Optional[tuple]:
    """Build an identity-based cache key, or None if input is unhashable-by-identity.

    Only dict / list inputs are cacheable (the common GeoJSON shapes). Strings
    are parsed-and-discarded so identity caching is unsafe for them.
    """
    if isinstance(geojson, (dict, list)):
        return (id(geojson), source_crs)
    return None


def _cache_get(key: tuple) -> Optional[tuple]:
    """Return (gdf, utm_crs_str) on hit; None on miss."""
    with _utm_cache_lock:
        if key not in _utm_cache:
            return None
        # move to MRU
        _utm_cache_order.remove(key)
        _utm_cache_order.append(key)
        _geojson_ref, gdf, utm = _utm_cache[key]
        return gdf, utm


def _cache_put(key: tuple, geojson_ref: Any, value: tuple) -> None:
    """Store (gdf, utm_crs_str) keyed by id(geojson), pinning geojson_ref alive."""
    with _utm_cache_lock:
        if key in _utm_cache:
            _utm_cache_order.remove(key)
        gdf, utm = value
        _utm_cache[key] = (geojson_ref, gdf, utm)  # 引用钉子：防 id 复用
        _utm_cache_order.append(key)
        # evict LRU entries beyond capacity
        while len(_utm_cache_order) > _UTM_CACHE_MAX:
            old = _utm_cache_order.pop(0)
            _utm_cache.pop(old, None)


def to_utm_gdf(geojson: Any, source_crs: Optional[str] = None) -> tuple[gpd.GeoDataFrame, str] | tuple[None, None]:
    """Convert GeoJSON to UTM GeoDataFrame with automatic zone detection.

    Results are memoized by object identity to amortize parse+reproject across
    multi-step analyses. Cached hits return a fresh ``.copy()`` so caller
    mutations (column adds, geometry edits) never corrupt the cache.

    Returns:
        tuple[gpd.GeoDataFrame, str]: (projected_gdf, utm_crs_string) or (None, None)
    """
    # Identity-keyed cache lookup. None-keyed inputs (str/bytes) always recompute.
    cache_key = _cache_key_for(geojson, source_crs)
    if cache_key is not None:
        cached = _cache_get(cache_key)
        if cached is not None:
            gdf_cached, utm = cached
            # Defensive copy: callers may add columns / edit geometry.
            gdf_copy = gdf_cached.copy()
            gdf_copy._original_crs = gdf_cached._original_crs
            return gdf_copy, utm

    parsed = safe_parse(geojson)
    if parsed is None:
        return None, None

    fc = to_feature_collection(parsed)
    features = fc.get("features", [])

    if not features:
        return None, None

    if source_crs is None and isinstance(fc, dict) and "crs" in fc:
        crs_obj = fc["crs"]
        if isinstance(crs_obj, str):
            source_crs = crs_obj
        elif isinstance(crs_obj, dict):
            props = crs_obj.get("properties", {})
            if "name" in props:
                source_crs = props["name"]
            elif "code" in props:
                source_crs = f"EPSG:{props['code']}"

    rows = []
    for f in features:
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry")
        if not geom:
            continue
        try:
            s = shape(geom)
            if s.is_empty:
                continue
            props = f.get("properties", {}) or {}
            rows.append({"geometry": s, **props})
        except (ValueError, TypeError):
            continue

    if not rows:
        return None, None

    original_crs_explicit = source_crs
    gdf = gpd.GeoDataFrame(rows, crs=source_crs or "EPSG:4326")
    gdf["geometry"] = gdf.geometry.make_valid()
    gdf._original_crs = original_crs_explicit or (str(gdf.crs) if gdf.crs else "EPSG:4326")

    if gdf.crs and gdf.crs.is_projected:
        result = (gdf, str(gdf.crs))
    else:
        # Bounds-driven CRS selection. UTM is the right tool for local metric
        # work, but two pathological cases previously produced silent garbage
        # (audit V-F04/F05/F06): (a) polar data — estimate_utm_crs() raises
        # beyond 84N/80S and the manual fallback assigned a UTM zone that is
        # not defined there; (b) continental spans where a single zone
        # distorts distances/areas near the edges. We keep UTM for the common
        # case, switch to polar stereographic at the poles, and warn loudly
        # when a single zone cannot represent the extent honestly.
        minx, miny, maxx, maxy = gdf.total_bounds
        lon_span = float(maxx - minx)
        abs_max_lat = max(abs(float(miny)), abs(float(maxy)))
        polar = abs_max_lat > 84.0

        utm_crs = None
        if not polar:
            try:
                utm_crs_obj = gdf.estimate_utm_crs()
                if utm_crs_obj is not None:
                    utm_crs = str(utm_crs_obj)
            except Exception:
                utm_crs = None

        if utm_crs:
            try:
                projected = gdf.to_crs(utm_crs)
                projected["geometry"] = projected.geometry.make_valid()
                projected._original_crs = gdf._original_crs
                result = (projected, utm_crs)
            except Exception:
                utm_crs = None

        if utm_crs is None:
            centroid = gdf.geometry.union_all().centroid
            # Normalize longitude to [-180, 180] so a centroid at, e.g., 200°
            # does not pick a zone on the far side of the globe.
            lon = (centroid.x + 180.0) % 360.0 - 180.0
            if polar:
                # UTM is undefined poleward of 84N/80S; polar stereographic
                # (NSIDC) is the correct metric frame there.
                utm_crs = "EPSG:3413" if centroid.y >= 0 else "EPSG:3031"
                logger.warning(
                    "to_utm_gdf: data reaches |lat| %.1f (polar); UTM is "
                    "undefined there, using polar stereographic %s. Metric "
                    "results are approximate at the extremes.",
                    abs_max_lat, utm_crs,
                )
            else:
                zone_number = int((lon + 180) / 6) + 1
                zone_number = max(1, min(60, zone_number))
                hemisphere = 32600 if centroid.y >= 0 else 32700
                utm_crs = f"EPSG:{hemisphere + zone_number}"

            projected = gdf.to_crs(utm_crs)
            projected["geometry"] = projected.geometry.make_valid()
            projected._original_crs = gdf._original_crs
            result = (projected, utm_crs)

        # Honesty signal for multi-zone/continental extents: a single UTM zone
        # (~6° wide) introduces growing scale error toward the edges. Does not
        # change the projection; lets callers/results disclose the limitation.
        if lon_span > 6.0 and not polar:
            logger.warning(
                "to_utm_gdf: longitudinal span %.1f° exceeds one UTM zone "
                "(~6°); single-zone %s distorts distances/areas near the "
                "edges. For survey-grade work use an equal-area CRS or "
                "geodesic distances.",
                lon_span, result[1],
            )

    # Cache the canonical result. Callers get copies; the cached gdf itself is
    # never handed out, so its geometry/columns stay pristine. `parsed` is
    # pinned as the strong reference that prevents id() reuse collisions.
    if cache_key is not None:
        _cache_put(cache_key, parsed, result)

    gdf_out, utm_out = result
    gdf_copy = gdf_out.copy()
    gdf_copy._original_crs = gdf_out._original_crs
    return gdf_copy, utm_out

