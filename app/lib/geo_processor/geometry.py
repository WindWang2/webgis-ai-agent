import json
import logging
from typing import Any, Union
import geopandas as gpd
from app.lib.geo_processor.core import to_utm_gdf, safe_parse, GeoAnalysisResult

logger = logging.getLogger(__name__)

def buffer_smart(geojson: Union[dict, str], distance: float, unit: str = 'm') -> GeoAnalysisResult:
    """
    Buffers a GeoJSON object by a specified distance.
    If the input is in WGS84 and the unit is 'm' or 'km', it automatically
    projects to the appropriate UTM zone before buffering.
    """
    try:
        parsed = safe_parse(geojson)
        if not parsed:
            return GeoAnalysisResult(False, None, "Invalid GeoJSON input")
            
        # Handle unit conversion
        dist = distance
        if unit == 'km':
            dist = distance * 1000
        
        # Use to_utm_gdf for high precision
        res = to_utm_gdf(parsed)
        if not res or res[0] is None:
            return GeoAnalysisResult(False, None, "Failed to project data for buffering")
            
        gdf, utm_crs = res
        original_crs = "EPSG:4326"
        
        buffered_gdf = gdf.copy()
        buffered_gdf['geometry'] = gdf.buffer(dist)
        
        # Convert back to WGS84
        res_gdf = buffered_gdf.to_crs(original_crs)
        
        summary = f"Buffered {len(gdf)} features by {distance}{unit} using UTM projection ({utm_crs})."
        
        return GeoAnalysisResult(
            success=True,
            data=json.loads(res_gdf.to_json()),
            summary=summary
        )
    except Exception as e:
        logger.error(f"Buffer operation failed: {e}")
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Buffer operation failed: {str(e)}",
            error_type=type(e).__name__
        )

def clip_smart(target_layer: Union[dict, str], mask_layer: Union[dict, str]) -> GeoAnalysisResult:
    """
    Clips the target_layer to the boundary of the mask_layer.
    Automatically aligns CRS if they differ.
    """
    try:
        t_parsed = safe_parse(target_layer)
        m_parsed = safe_parse(mask_layer)
        
        if not t_parsed or not m_parsed:
            return GeoAnalysisResult(False, None, "Invalid input layers")
            
        tgdf = gpd.GeoDataFrame.from_features(t_parsed.get("features", [t_parsed]) if t_parsed.get("type") in ["FeatureCollection", "Feature"] else [t_parsed], crs="EPSG:4326")
        mgdf = gpd.GeoDataFrame.from_features(m_parsed.get("features", [m_parsed]) if m_parsed.get("type") in ["FeatureCollection", "Feature"] else [m_parsed], crs="EPSG:4326")
        
        if tgdf.empty or mgdf.empty:
            return GeoAnalysisResult(True, {"type": "FeatureCollection", "features": []}, "Input layer(s) empty, nothing to clip.")

        # Perform spatial clip
        clipped_gdf = gpd.clip(tgdf, mgdf)
        
        summary = f"Clipped {len(tgdf)} features to mask, {len(clipped_gdf)} features remaining."
        
        return GeoAnalysisResult(
            success=True,
            data=json.loads(clipped_gdf.to_json()),
            summary=summary
        )
    except Exception as e:
        logger.error(f"Clip operation failed: {e}")
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Clip operation failed: {str(e)}",
            error_type=type(e).__name__
        )

def dissolve_smart(geojson: Union[dict, str], field: str = None) -> GeoAnalysisResult:
    """Dissolve geometries in GeoJSON."""
    try:
        parsed = safe_parse(geojson)
        if not parsed:
            return GeoAnalysisResult(False, None, "Invalid GeoJSON input")
            
        gdf = gpd.GeoDataFrame.from_features(parsed.get("features", [parsed]) if parsed.get("type") in ["FeatureCollection", "Feature"] else [parsed], crs="EPSG:4326")
        
        if gdf.empty:
            return GeoAnalysisResult(True, {"type": "FeatureCollection", "features": []}, "Layer empty, nothing to dissolve.")
            
        dissolved = gdf.dissolve(by=field).reset_index()
        
        summary = f"Dissolved {len(gdf)} features into {len(dissolved)} features."
        if field:
            summary += f" Grouped by field: {field}"
            
        return GeoAnalysisResult(
            success=True,
            data=json.loads(dissolved.to_json()),
            summary=summary
        )
    except Exception as e:
        logger.error(f"Dissolve operation failed: {e}")
        return GeoAnalysisResult(False, None, f"Dissolve failed: {str(e)}")
