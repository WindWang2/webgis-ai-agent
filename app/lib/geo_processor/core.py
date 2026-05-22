import json
import math
from typing import Any
import geopandas as gpd
from shapely.geometry import shape

from dataclasses import dataclass
from typing import Any, Optional

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
        """
        return {
            "success": self.success,
            "summary": self.summary,
            "data": self.data,
            "error_type": self.error_type,
            "correction_hint": self.correction_hint
        }

_A = 6378245.0
_EE = 0.00669342162296594323

def _out_of_china(lng: float, lat: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)

def _transform_lat(lng: float, lat: float) -> float:
    ret = (-100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat +
           0.1 * lng * lat + 0.2 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) +
            20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) +
            40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) +
            320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret

def _transform_lng(lng: float, lat: float) -> float:
    ret = (300.0 + lng + 2.0 * lat + 0.1 * lng * lng +
           0.1 * lng * lat + 0.1 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) +
            20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi) +
            40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi) +
            300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret

def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat

def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng - dlng, lat - dlat

def gcj02_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * math.pi * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006

def bd09_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    lng -= 0.0065
    lat -= 0.006
    z = math.sqrt(lng * lng + lat * lat) - 0.00002 * math.sin(lat * math.pi * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) - 0.000003 * math.cos(lng * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta), z * math.sin(theta)

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
            except Exception:
                return None
    return None

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
    centroid = gdf.geometry.unary_union.centroid
    zone_number = int((centroid.x + 180) / 6) + 1
    hemisphere = 32600 if centroid.y >= 0 else 32700
    utm_crs = f"EPSG:{hemisphere + zone_number}"
    
    return gdf.to_crs(utm_crs), utm_crs
