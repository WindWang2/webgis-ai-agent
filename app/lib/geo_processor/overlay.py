import json
import logging
from typing import Union
import geopandas as gpd
from app.lib.geo_processor.core import safe_parse, GeoAnalysisResult

logger = logging.getLogger(__name__)

def overlay_smart(layer_a: Union[dict, str], layer_b: Union[dict, str], how: str = 'intersection') -> GeoAnalysisResult:
    """
    Performs a spatial overlay between layer_a and layer_b.
    Supported 'how' values: intersection, union, difference, symmetric_difference, identity.
    """
    try:
        t_parsed = safe_parse(layer_a)
        m_parsed = safe_parse(layer_b)
        
        if not t_parsed or not m_parsed:
            return GeoAnalysisResult(False, None, "Invalid input layers")
            
        gdf_a = gpd.GeoDataFrame.from_features(t_parsed.get("features", [t_parsed]) if t_parsed.get("type") in ["FeatureCollection", "Feature"] else [t_parsed], crs="EPSG:4326")
        gdf_b = gpd.GeoDataFrame.from_features(m_parsed.get("features", [m_parsed]) if m_parsed.get("type") in ["FeatureCollection", "Feature"] else [m_parsed], crs="EPSG:4326")
        
        if gdf_a.empty or gdf_b.empty:
            return GeoAnalysisResult(True, {"type": "FeatureCollection", "features": []}, "Input layer(s) empty, nothing to overlay.")

        # Perform spatial overlay
        res_gdf = gpd.overlay(gdf_a, gdf_b, how=how)
        
        summary = f"Overlay ({how}) completed. {len(res_gdf)} features generated."
        
        return GeoAnalysisResult(
            success=True,
            data=json.loads(res_gdf.to_json()),
            summary=summary
        )
    except Exception as e:
        logger.error(f"Overlay operation failed: {e}")
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Overlay operation failed: {str(e)}",
            error_type=type(e).__name__
        )
