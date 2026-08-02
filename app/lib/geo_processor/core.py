import json
from typing import Any
import geopandas as gpd
from shapely.geometry import shape

from dataclasses import dataclass
from typing import Any, Optional

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
    for char in s:
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
    return s + "".join(reversed(stack))

def safe_parse(geojson: Any) -> dict | None:
    """Robust parsing of GeoJSON string or dict."""
    if isinstance(geojson, dict):
        return geojson
    if isinstance(geojson, str):
        geojson = geojson.strip()
        if not geojson:
            return None
        try:
            return json.loads(geojson)
        except (json.JSONDecodeError, TypeError):
            # Try simple repair for truncated strings
            try:
                repaired = _repair_json(geojson)
                return json.loads(repaired)
            except Exception as e:
                return None
    return None


def to_feature_collection(data: Any) -> dict:
    """Normalize input data into a valid FeatureCollection dict.

    Accepts a GeoJSON dict, a features list, a string (parsed via
    :func:`safe_parse`, which also repairs truncated JSON), a single Feature,
    or a bare geometry — and always returns a ``{"type": "FeatureCollection",
    "features": [...]}`` dict (never None). Never raises.

    Relocated from ``app/services/spatial_analyzer.py:_to_feature_collection``
    (ADR-0037 Win 5) so the GeoJSON parse/normalize domain knowledge lives in
    one module alongside :func:`safe_parse`. The string branch now delegates
    to ``safe_parse``, gaining its truncated-JSON repair behavior for free
    (previously this function silently returned an empty FC on a truncated
    string, a latent inconsistency with safe_parse).
    """
    if not data:
        return {"type": "FeatureCollection", "features": []}

    if isinstance(data, str):
        # Delegate to safe_parse: gets truncated-JSON repair for free.
        data = safe_parse(data)
        if not isinstance(data, dict):
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

def to_utm_gdf(geojson: dict | str, source_crs: Optional[str] = None) -> tuple[gpd.GeoDataFrame, str] | None:
    """Convert GeoJSON to UTM GeoDataFrame with automatic zone detection.
    
    Returns:
        tuple[gpd.GeoDataFrame, str]: (projected_gdf, utm_crs_string) or (None, None)
    """
    parsed = safe_parse(geojson)
    if not parsed:
        return None, None
        
    # Handle both FeatureCollection and single Feature/Geometry
    if parsed.get("type") == "FeatureCollection":
        features = parsed.get("features", [])
    elif parsed.get("type") == "Feature":
        features = [parsed]
    else:
        # Assume it's a bare geometry
        features = [{"type": "Feature", "geometry": parsed, "properties": {}}]

    if not features:
        return None, None

    rows = []
    for f in features:
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
        
    gdf = gpd.GeoDataFrame(rows, crs=source_crs or "EPSG:4326")
    
    if gdf.crs and gdf.crs.is_projected:
        return gdf, str(gdf.crs)
        
    # Calculate UTM zone from centroid
    centroid = gdf.geometry.union_all().centroid
    zone_number = int((centroid.x + 180) / 6) + 1
    hemisphere = 32600 if centroid.y >= 0 else 32700
    utm_crs = f"EPSG:{hemisphere + zone_number}"
    
    return gdf.to_crs(utm_crs), utm_crs
