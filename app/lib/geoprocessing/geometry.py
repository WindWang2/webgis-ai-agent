import geopandas as gpd
import json
from typing import Any, Union
from app.lib.geoprocessing.interface import GeoAnalysisResult

def buffer_smart(geojson: Union[dict, str], distance: float, unit: str = 'm') -> GeoAnalysisResult:
    """
    Buffers a GeoJSON object by a specified distance.
    If the input is in WGS84 and the unit is 'm' or 'km', it automatically
    projects to the appropriate UTM zone before buffering.
    """
    try:
        if isinstance(geojson, str):
            geojson = json.loads(geojson)
        
        gdf = gpd.GeoDataFrame.from_features(geojson)
        if gdf.crs is None:
            gdf.set_crs("EPSG:4326", inplace=True)
        
        original_crs = gdf.crs
        summary_suffix = ""
        
        # Handle unit conversion
        dist = distance
        if unit == 'km':
            dist = distance * 1000
            unit = 'm'
        
        if gdf.crs.is_geographic and unit in ['m', 'km']:
            # Automatically find the best UTM CRS for the data
            utm_crs = gdf.estimate_utm_crs()
            gdf = gdf.to_crs(utm_crs)
            gdf['geometry'] = gdf.buffer(dist)
            gdf = gdf.to_crs(original_crs)
            summary_suffix = f" using {utm_crs.to_string()} projection"
        else:
            gdf['geometry'] = gdf.buffer(dist)
            
        summary = f"Buffered {len(gdf)} features by {distance}{unit}{summary_suffix}."
        
        return GeoAnalysisResult(
            success=True,
            data=json.loads(gdf.to_json()),
            summary=summary
        )
    except Exception as e:
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
        if isinstance(target_layer, str):
            target_layer = json.loads(target_layer)
        if isinstance(mask_layer, str):
            mask_layer = json.loads(mask_layer)
            
        target_gdf = gpd.GeoDataFrame.from_features(target_layer)
        mask_gdf = gpd.GeoDataFrame.from_features(mask_layer)
        
        if target_gdf.crs is None:
            target_gdf.set_crs("EPSG:4326", inplace=True)
        if mask_gdf.crs is None:
            mask_gdf.set_crs("EPSG:4326", inplace=True)
            
        if target_gdf.crs != mask_gdf.crs:
            mask_gdf = mask_gdf.to_crs(target_gdf.crs)
            
        original_count = len(target_gdf)
        clipped_gdf = gpd.clip(target_gdf, mask_gdf)
        remaining_count = len(clipped_gdf)
        
        summary = f"Clipped {original_count} features to the mask boundary, {remaining_count} features remaining."
        
        return GeoAnalysisResult(
            success=True,
            data=json.loads(clipped_gdf.to_json()),
            summary=summary
        )
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Clip operation failed: {str(e)}",
            error_type=type(e).__name__
        )

def overlay_smart(layer_a: Union[dict, str], layer_b: Union[dict, str], how: str = 'intersection') -> GeoAnalysisResult:
    """
    Performs a spatial overlay between layer_a and layer_b.
    Supported 'how' values: intersection, union, difference, symmetric_difference, identity.
    """
    try:
        if isinstance(layer_a, str):
            layer_a = json.loads(layer_a)
        if isinstance(layer_b, str):
            layer_b = json.loads(layer_b)
            
        gdf_a = gpd.GeoDataFrame.from_features(layer_a)
        gdf_b = gpd.GeoDataFrame.from_features(layer_b)
        
        if gdf_a.crs is None:
            gdf_a.set_crs("EPSG:4326", inplace=True)
        if gdf_b.crs is None:
            gdf_b.set_crs("EPSG:4326", inplace=True)
            
        if gdf_a.crs != gdf_b.crs:
            gdf_b = gdf_b.to_crs(gdf_a.crs)
            
        overlay_gdf = gpd.overlay(gdf_a, gdf_b, how=how)
        
        summary = f"{how.capitalize()} operation completed. Result contains {len(overlay_gdf)} features."
        
        return GeoAnalysisResult(
            success=True,
            data=json.loads(overlay_gdf.to_json()),
            summary=summary
        )
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Overlay operation failed: {str(e)}",
            error_type=type(e).__name__
        )
