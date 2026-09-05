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
    bd09_to_wgs84,
    normalize_chinese_crs,
)
from shapely.ops import transform as _shapely_transform


def _geometry_to_wgs84(geom, chinese_crs: str):
    """Convert a shapely geometry from a declared gcj02/bd09 offset frame to
    true WGS84 (audit #813). These names are not pyproj CRSes: building a
    GeoDataFrame with ``crs="gcj02"`` raises CRSError for the whole to_utm_gdf
    family, yet ``transform_geojson`` itself writes the member (GIS-22)."""
    conv = gcj02_to_wgs84 if chinese_crs == "gcj02" else bd09_to_wgs84

    def _fn(x, y, z=None):
        lng, lat = conv(float(x), float(y))
        return (lng, lat, z) if z is not None else (lng, lat)

    return _shapely_transform(_fn, geom)

@dataclass
class GeoAnalysisResult:
    """
    Standard interface for geoprocessing tool results.
    Explicitly supports LLM narration and self-healing hints.

    ``evidence``（V2 P9，additive）：轻量质量证据
    （input/output/dropped/working_crs…，app/lib/geo_analysis/evidence.py
    构造）。缺省 None —— 旧站点行为逐位不变；提供时经 to_llm_response
    有界透传（证据是 metadata，不是报告）。
    """
    success: bool
    data: Any
    summary: str
    error_type: Optional[str] = None
    correction_hint: Optional[str] = None
    evidence: Optional[dict] = None

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
        if self.evidence:
            result["quality_evidence"] = self.evidence
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
# GeoJSON `crs` member contract (audit #599)
#
# A legacy RFC 7946 `crs` member declares the coordinate system of the raw
# coordinates. to_utm_gdf reads it; the other operators previously hardcoded
# EPSG:4326 and silently misinterpreted projected input (empty results /
# misplaced geometry). These helpers are the single source of truth for that
# contract: read the member, build GeoDataFrames that honor it (unifying the
# working frame to WGS84), declare it on output, and warn when an absent
# member leaves coordinates that clearly are not WGS84.
# ---------------------------------------------------------------------------
def extract_declared_crs(geojson: Any) -> Optional[str]:
    """Read the GeoJSON top-level ``crs`` member (legacy RFC 7946) as a CRS
    string, or None when absent. Accepts the string form (``"EPSG:3857"``)
    and the object form (``{"type": "name", "properties": {"name": ...}}``
    or ``{"properties": {"code": 3857}}``) written by reproject_coordinates
    (GIS-22). Single source of truth shared by to_utm_gdf and the operators.
    """
    if not isinstance(geojson, dict):
        return None
    crs_obj = geojson.get("crs")
    if isinstance(crs_obj, str):
        return crs_obj.strip() or None
    if isinstance(crs_obj, dict):
        props = crs_obj.get("properties", {})
        if isinstance(props, dict):
            name = props.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
            code = props.get("code")
            if code is not None and str(code).strip():
                return f"EPSG:{str(code).strip()}"
    return None


def warn_if_non_wgs84_coordinates(gdf: Optional[gpd.GeoDataFrame], context: str) -> None:
    """Log a warning when a GeoDataFrame built from an UNDECLARED input has
    coordinates outside the WGS84 envelopes — a strong sign the data is in a
    projected CRS whose ``crs`` member was dropped, so interpreting it as
    EPSG:4326 silently misplaces or drops data (audit #599)."""
    try:
        if gdf is None or len(gdf) == 0 or gdf.crs is None:
            return
        minx, miny, maxx, maxy = gdf.total_bounds
    except Exception:
        return
    if minx < -180.0 or maxx > 180.0 or miny < -90.0 or maxy > 90.0:
        logger.warning(
            "%s: bounds [%.2f, %.2f, %.2f, %.2f] exceed the WGS84 envelope but "
            "the input declares no `crs` member — treating the coordinates as "
            "EPSG:4326 may silently misplace or drop data. Declare the CRS via "
            "the FeatureCollection `crs` member or reproject to EPSG:4326.",
            context, minx, miny, maxx, maxy,
        )


def gdf_from_features(fc: dict | list, context: str = "") -> gpd.GeoDataFrame:
    """Build a GeoDataFrame from a FeatureCollection, honoring a declared
    top-level ``crs`` member (falling back to EPSG:4326 when absent or
    invalid). A declared non-geographic CRS is reprojected to EPSG:4326 so the
    operator's WGS84 working frame sees correct coordinates (audit #599:
    hardcoding EPSG:4326 silently dropped declared projected input); an absent
    member with suspicious coordinates only logs a warning.
    """
    declared = extract_declared_crs(fc)
    chinese_crs = normalize_chinese_crs(declared) if declared else None
    if chinese_crs in ("gcj02", "bd09"):
        # audit #813: offset frames are not pyproj CRSes — normalize the
        # geometries to true WGS84 instead of the previous except-fallback,
        # which silently kept the ~100-600m offsets uncorrected.
        gdf = gpd.GeoDataFrame.from_features(fc, crs="EPSG:4326")
        gdf["geometry"] = gdf.geometry.apply(lambda g: _geometry_to_wgs84(g, chinese_crs))
        return gdf
    try:
        gdf = gpd.GeoDataFrame.from_features(fc, crs=declared or "EPSG:4326")
    except Exception:
        # #1113 P3-7: declared-but-invalid CRS previously fell back to WGS84
        # silently (skipping the undeclared-path warning). Log the declared
        # value and warn on suspicious coordinates.
        logger.warning(
            "%s: invalid declared CRS %r; falling back to EPSG:4326",
            context or "geo_processor",
            declared,
        )
        gdf = gpd.GeoDataFrame.from_features(fc, crs="EPSG:4326")
        warn_if_non_wgs84_coordinates(gdf, context or "geo_processor")
        return gdf
    if declared is None:
        warn_if_non_wgs84_coordinates(gdf, context or "geo_processor")
    else:
        try:
            if gdf.crs is not None and not gdf.crs.is_geographic:
                gdf = gdf.to_crs("EPSG:4326")
        except Exception:
            pass
    return gdf


# Geographic CRS spellings whose coordinates are longitude/latitude and need no
# ``crs`` member on output (RFC 7946 forbids the member for EPSG:4326).
_WGS84_CRS_NAMES = frozenset({
    "EPSG:4326", "CRS:84", "OGC:CRS84", "WGS84",
})


def declare_crs(fc: dict, crs: Any) -> dict:
    """Attach a legacy GeoJSON ``crs`` member to an output FeatureCollection
    when its coordinates are NOT WGS84, so consumers that read the member
    (to_utm_gdf, zonal_statistics, …) do not misread projected output as
    EPSG:4326 (audit #599). Returns the input unchanged when the CRS is
    WGS84 or absent — the member must not be emitted for EPSG:4326.
    """
    if not isinstance(fc, dict) or not crs:
        return fc
    if str(crs).strip().upper().replace(" ", "") in _WGS84_CRS_NAMES:
        return fc
    out = dict(fc)
    out["crs"] = {"type": "name", "properties": {"name": str(crs)}}
    return out

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
_utm_cache: dict[tuple, tuple] = {}  # key -> (geojson_ref, gdf, utm_crs_str, crs_note)
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
    """Return (gdf, utm_crs_str, crs_note) on hit; None on miss."""
    with _utm_cache_lock:
        if key not in _utm_cache:
            return None
        # move to MRU
        _utm_cache_order.remove(key)
        _utm_cache_order.append(key)
        _geojson_ref, gdf, utm, note = _utm_cache[key]
        return gdf, utm, note


def _cache_put(key: tuple, geojson_ref: Any, value: tuple) -> None:
    """Store (gdf, utm_crs_str, crs_note) keyed by id(geojson), pinning geojson_ref alive."""
    with _utm_cache_lock:
        if key in _utm_cache:
            _utm_cache_order.remove(key)
        gdf, utm, note = value
        _utm_cache[key] = (geojson_ref, gdf, utm, note)  # 引用钉子：防 id 复用
        _utm_cache_order.append(key)
        # evict LRU entries beyond capacity
        while len(_utm_cache_order) > _UTM_CACHE_MAX:
            old = _utm_cache_order.pop(0)
            _utm_cache.pop(old, None)


def to_utm_gdf_with_note(
    geojson: Any, source_crs: Optional[str] = None,
) -> tuple[gpd.GeoDataFrame, str, dict] | tuple[None, None, None]:
    """Convert GeoJSON to UTM GeoDataFrame with automatic zone detection,
    returning the CRS transformation note for scientific disclosure.

    This is the canonical implementation; :func:`to_utm_gdf` is a thin
    delegate that drops the note (behaviour byte-identical to the previous
    inline body).

    Results are memoized by object identity to amortize parse+reproject across
    multi-step analyses. Cached hits return a fresh ``.copy()`` so caller
    mutations (column adds, geometry edits) never corrupt the cache.

    Returns:
        tuple: ``(projected_gdf, utm_crs_string, note)`` where ``note`` is the
        CRS-disclosure dict ``{"target_crs": "EPSG:32650", "source_crs": ...,
        "gcj02_normalized": bool}`` — or ``(None, None, None)`` when the input
        cannot be parsed / has no usable features. For an already-projected
        input ``target_crs == source_crs`` (identity: no transform happened),
        which callers must report honestly rather than claim an auto-UTM.
    """
    # Identity-keyed cache lookup. None-keyed inputs (str/bytes) always recompute.
    cache_key = _cache_key_for(geojson, source_crs)
    if cache_key is not None:
        cached = _cache_get(cache_key)
        if cached is not None:
            gdf_cached, utm, note = cached
            # Defensive copy: callers may add columns / edit geometry.
            gdf_copy = gdf_cached.copy()
            gdf_copy._original_crs = gdf_cached._original_crs
            return gdf_copy, utm, note

    parsed = safe_parse(geojson)
    if parsed is None:
        return None, None, None

    fc = to_feature_collection(parsed)
    features = fc.get("features", [])

    if not features:
        return None, None, None

    if source_crs is None:
        source_crs = extract_declared_crs(fc)

    # audit #813: "gcj02"/"bd09" are offset WGS84 frames, not pyproj CRSes —
    # normalize the geometries to true WGS84 so crs="gcj02" never reaches
    # GeoDataFrame construction (raw CRSError for the whole analysis family).
    chinese_crs = normalize_chinese_crs(source_crs) if source_crs else None
    if chinese_crs in ("gcj02", "bd09"):
        source_crs = "EPSG:4326"

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
        return None, None, None

    if chinese_crs in ("gcj02", "bd09"):
        for r in rows:
            r["geometry"] = _geometry_to_wgs84(r["geometry"], chinese_crs)
        logger.info(
            "to_utm_gdf: normalized %d declared-%s features to WGS84",
            len(rows), chinese_crs,
        )

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
        crosses_am = lon_span > 180.0
        if crosses_am:
            # #709: a dataset straddling ±180 has a raw lon span near 360°,
            # so bounds-midpoint zone selection picks a Greenwich-centred
            # zone (opposite side of the globe) and the >6° warning mislabels
            # a 2° true span as continental. Rewrap per-geometry bounds into
            # a continuous frame: a geometry that itself crosses AM reports
            # [maxx, minx+360]; fully-negative lons shift +360. The selected
            # zone is centered on the true extent; PROJ's TM trig handles the
            # ±180 wrap on projection.
            import numpy as np

            b = gdf.geometry.bounds
            lo, hi = b["minx"], b["maxx"]
            am_geom = (hi - lo) > 180.0
            lo_s = np.where(am_geom, hi, np.where(lo < 0, lo + 360.0, lo))
            hi_s = np.where(am_geom, lo + 360.0, np.where(hi < 0, hi + 360.0, hi))
            true_min, true_max = float(lo_s.min()), float(hi_s.max())
            lon_span = true_max - true_min  # the honest span for the warning
        abs_max_lat = max(abs(float(miny)), abs(float(maxy)))
        polar = abs_max_lat > 84.0

        utm_crs = None
        result = None  # 跨 AM 分支成功时已填；#1063 守卫读取
        if not polar:
            if crosses_am:
                center_shifted = (true_min + true_max) / 2.0
                center_lon = (center_shifted + 180.0) % 360.0 - 180.0
                zone_number = max(1, min(60, int((center_lon + 180) / 6) + 1))
                hemisphere = 32600 if (float(miny) + float(maxy)) >= 0 else 32700
                utm_crs = f"EPSG:{hemisphere + zone_number}"
                try:
                    projected = gdf.to_crs(utm_crs)
                    projected["geometry"] = projected.geometry.make_valid()
                    projected._original_crs = gdf._original_crs
                    result = (projected, utm_crs)
                except Exception:
                    utm_crs = None
            else:
                try:
                    utm_crs_obj = gdf.estimate_utm_crs()
                    if utm_crs_obj is not None:
                        utm_crs = str(utm_crs_obj)
                except Exception:
                    utm_crs = None

        if utm_crs and result is None:
            # #1063: 跨 AM 分支已在上方完成投影 + make_valid 并写入 result ——
            # 此前无条件再次 to_crs 同一 CRS，白付一次全量投影 + make_valid。
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

    # CRS transformation note (scientific disclosure): what frame the data
    # came in, what metric frame it was projected to, and whether a Chinese
    # offset frame (gcj02/bd09) was normalized to true WGS84 first. For an
    # already-projected input target == source (identity — no transform).
    note = {
        "target_crs": result[1],
        "source_crs": gdf._original_crs,
        "gcj02_normalized": chinese_crs in ("gcj02", "bd09"),
    }

    # Cache the canonical result. Callers get copies; the cached gdf itself is
    # never handed out, so its geometry/columns stay pristine. `parsed` is
    # pinned as the strong reference that prevents id() reuse collisions.
    if cache_key is not None:
        _cache_put(cache_key, parsed, (*result, note))

    gdf_out, utm_out = result
    gdf_copy = gdf_out.copy()
    gdf_copy._original_crs = gdf_out._original_crs
    return gdf_copy, utm_out, note


def to_utm_gdf(geojson: Any, source_crs: Optional[str] = None) -> tuple[gpd.GeoDataFrame, str] | tuple[None, None]:
    """Convert GeoJSON to UTM GeoDataFrame with automatic zone detection.

    Thin delegate over :func:`to_utm_gdf_with_note` that drops the CRS
    transformation note (behaviour unchanged for all existing callers).

    Results are memoized by object identity to amortize parse+reproject across
    multi-step analyses. Cached hits return a fresh ``.copy()`` so caller
    mutations (column adds, geometry edits) never corrupt the cache.

    Returns:
        tuple[gpd.GeoDataFrame, str]: (projected_gdf, utm_crs_string) or (None, None)
    """
    gdf, utm_crs, _note = to_utm_gdf_with_note(geojson, source_crs=source_crs)
    return gdf, utm_crs

