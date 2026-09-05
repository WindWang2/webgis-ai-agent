import json
import logging
from typing import Union
import geopandas as gpd
from app.lib.geo_processor.core import safe_parse, to_feature_collection, GeoAnalysisResult, gdf_from_features

logger = logging.getLogger(__name__)

VALID_HOW_METHODS = {"intersection", "union", "difference", "symmetric_difference", "identity"}

def overlay_smart(
    layer_a: Union[dict, str, list],
    layer_b: Union[dict, str, list],
    how: str = 'intersection'
) -> GeoAnalysisResult:
    """
    Performs a spatial overlay between layer_a and layer_b.
    Supported 'how' values: intersection, union, difference, symmetric_difference, identity.
    """
    if how not in VALID_HOW_METHODS:
        sorted_methods = sorted(list(VALID_HOW_METHODS))
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Invalid overlay method '{how}'. Must be one of: {sorted_methods}",
            error_type="ValueError",
            correction_hint=f"Use one of: {sorted_methods}"
        )

    try:
        t_parsed = safe_parse(layer_a)
        m_parsed = safe_parse(layer_b)
        
        if t_parsed is None or m_parsed is None:
            return GeoAnalysisResult(False, None, "Invalid input layers")
            
        fc_a = to_feature_collection(t_parsed)
        fc_b = to_feature_collection(m_parsed)

        # Degenerate-input honesty: an empty layer short-circuits to an empty
        # success BEFORE gdf_from_features — geopandas raises
        # ValueError("Assigning CRS to a GeoDataFrame without a geometry
        # column") on a zero-feature list, which previously surfaced as a
        # generic "Overlay operation failed" instead of the honest result.
        if not fc_a.get("features") or not fc_b.get("features"):
            return GeoAnalysisResult(
                True, {"type": "FeatureCollection", "features": []},
                "Input layer(s) empty, nothing to overlay.")

        # GIS-599: honor a declared `crs` member instead of hardcoding
        # EPSG:4326 — a declared projected input (e.g. EPSG:3857) was
        # previously misinterpreted as WGS84 and silently dropped.
        gdf_a = gdf_from_features(fc_a, "overlay_smart layer_a")
        gdf_b = gdf_from_features(fc_b, "overlay_smart layer_b")
        
        if gdf_a.empty or gdf_b.empty:
            return GeoAnalysisResult(True, {"type": "FeatureCollection", "features": []}, "Input layer(s) empty, nothing to overlay.")

        # Align CRS of layer_b to match layer_a
        if gdf_b.crs != gdf_a.crs:
            gdf_b = gdf_b.to_crs(gdf_a.crs)

        # Make valid before spatial overlay operation
        gdf_a['geometry'] = gdf_a.geometry.make_valid()
        gdf_b['geometry'] = gdf_b.geometry.make_valid()

        # Perform spatial overlay
        res_gdf = gpd.overlay(gdf_a, gdf_b, how=how)
        res_gdf['geometry'] = res_gdf.geometry.make_valid()
        
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

