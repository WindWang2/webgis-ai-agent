import json
from typing import Any, Optional
import geopandas as gpd
from shapely.geometry import shape

from dataclasses import dataclass

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

def to_utm_gdf(geojson: Any, source_crs: Optional[str] = None) -> tuple[gpd.GeoDataFrame, str] | tuple[None, None]:
    """Convert GeoJSON to UTM GeoDataFrame with automatic zone detection.
    
    Returns:
        tuple[gpd.GeoDataFrame, str]: (projected_gdf, utm_crs_string) or (None, None)
    """
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
        return gdf, str(gdf.crs)
        
    utm_crs = None
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
            return projected, utm_crs
        except Exception:
            utm_crs = None

    centroid = gdf.geometry.union_all().centroid
    lon = (centroid.x + 180) % 360 - 180
    zone_number = int((lon + 180) / 6) + 1
    zone_number = max(1, min(60, zone_number))
    hemisphere = 32600 if centroid.y >= 0 else 32700
    utm_crs = f"EPSG:{hemisphere + zone_number}"
    
    projected = gdf.to_crs(utm_crs)
    projected["geometry"] = projected.geometry.make_valid()
    projected._original_crs = gdf._original_crs
    return projected, utm_crs

