import json
import logging
from typing import Union, Optional
import geopandas as gpd
from app.lib.geo_processor.core import to_utm_gdf, safe_parse, to_feature_collection, GeoAnalysisResult, gdf_from_features, declare_crs

logger = logging.getLogger(__name__)

def buffer_smart(
    geojson: Union[dict, str, list],
    distance: float,
    unit: str = 'm',
    dissolve: bool = False,
    source_crs: Optional[str] = None
) -> GeoAnalysisResult:
    """
    Buffers a GeoJSON object by a specified distance.
    If the input is in WGS84 and the unit is 'm' or 'km', it automatically
    projects to the appropriate UTM zone before buffering.
    """
    try:
        parsed = safe_parse(geojson)
        if parsed is None:
            return GeoAnalysisResult(False, None, "Invalid GeoJSON input")
            
        fc = to_feature_collection(parsed)
        if not fc.get("features"):
            return GeoAnalysisResult(False, None, "Input features list is empty")

        allowed_units = {"m", "km"}
        if unit not in allowed_units:
            return GeoAnalysisResult(
                False, None,
                f"Unsupported buffer unit '{unit}'. Allowed: m, km.",
                error_type="ValueError",
            )
        # Handle unit conversion while preserving the sign of distance.
        dist = distance
        if unit == 'km':
            dist = dist * 1000
        
        # Use to_utm_gdf for high precision
        res = to_utm_gdf(parsed, source_crs=source_crs)
        if not res or res[0] is None:
            return GeoAnalysisResult(False, None, "Failed to project data for buffering")
            
        gdf, utm_crs = res
        original_crs = source_crs or getattr(gdf, "_original_crs", None) or (gdf.crs if gdf is not None and gdf.crs is not None else "EPSG:4326")

        # GIS-P3-8: to_utm_gdf returns an already-projected input UNCHANGED —
        # if that CRS is not metre-based (state-plane feet etc.), the
        # "meters" distance must be converted to the CRS's linear unit.
        # unit_conversion_factor is "metres per CRS unit" (pyproj axis
        # semantics): a 1000 m buffer in a US-survey-foot CRS needs
        # 1000 / 0.3048 ≈ 3280.8 CRS units. Multiplying (the pre-#524 bug)
        # shrank the radius by factor² (~0.093 → ~10.8× too small).
        if gdf.crs is not None and gdf.crs.is_projected:
            try:
                axis = gdf.crs.axis_info[0]
                factor = float(getattr(axis, "unit_conversion_factor", 1.0) or 1.0)
                if axis.unit_name and "metre" not in axis.unit_name and "meter" not in axis.unit_name and factor != 1.0:
                    dist = dist / factor
            except Exception:
                pass

        buffered_gdf = gdf.copy()
        buffered_gdf['geometry'] = buffered_gdf.geometry.make_valid()
        buffered_gdf['geometry'] = buffered_gdf.buffer(dist)
        buffered_gdf['geometry'] = buffered_gdf.geometry.make_valid()
        
        if dissolve:
            # Dissolve all geometries into one
            dissolved_geom = buffered_gdf.geometry.union_all()
            buffered_gdf = gpd.GeoDataFrame(geometry=[dissolved_geom], crs=utm_crs)
            buffered_gdf['geometry'] = buffered_gdf.geometry.make_valid()
        
        # Convert back to original CRS
        res_gdf = buffered_gdf.to_crs(original_crs)
        res_gdf['geometry'] = res_gdf.geometry.make_valid()
        
        summary = f"Buffered {len(gdf)} features by {distance}{unit} using UTM projection ({utm_crs})."
        
        # GIS-599: the output keeps the ORIGINAL CRS coordinates for projected
        # input, so it must declare that CRS — otherwise downstream consumers
        # (MVT/geojson_bbox/frontend) misread, e.g., EPSG:3857 metres as WGS84.
        out_fc = declare_crs(
            {
                "type": "FeatureCollection",
                "features": json.loads(res_gdf.to_json())["features"],
                "stats": {
                    "input_count": len(gdf),
                    "output_count": len(res_gdf),
                    "reprojected": original_crs != utm_crs,
                    "working_crs": utm_crs,
                    "dissolve": dissolve
                }
            },
            original_crs,
        )

        return GeoAnalysisResult(
            success=True,
            data=out_fc,
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

def clip_smart(target_layer: Union[dict, str, list], mask_layer: Union[dict, str, list]) -> GeoAnalysisResult:
    """
    Clips the target_layer to the boundary of the mask_layer.
    Automatically aligns CRS if they differ.
    """
    try:
        t_parsed = safe_parse(target_layer)
        m_parsed = safe_parse(mask_layer)
        
        if t_parsed is None or m_parsed is None:
            return GeoAnalysisResult(False, None, "Invalid input layers")
            
        t_fc = to_feature_collection(t_parsed)
        m_fc = to_feature_collection(m_parsed)
        
        # GIS-599: honor a declared `crs` member instead of hardcoding
        # EPSG:4326 — a declared projected input (e.g. EPSG:3857) was
        # previously misinterpreted as WGS84 and silently dropped.
        tgdf = gdf_from_features(t_fc, "clip_smart target layer")
        mgdf = gdf_from_features(m_fc, "clip_smart mask layer")
        
        if tgdf.empty or mgdf.empty:
            return GeoAnalysisResult(True, {"type": "FeatureCollection", "features": []}, "Input layer(s) empty, nothing to clip.")

        # Align CRS of mask layer to match target layer
        if mgdf.crs != tgdf.crs:
            mgdf = mgdf.to_crs(tgdf.crs)

        # Make valid before spatial operations
        tgdf['geometry'] = tgdf.geometry.make_valid()
        mgdf['geometry'] = mgdf.geometry.make_valid()

        # Perform spatial clip
        clipped_gdf = gpd.clip(tgdf, mgdf)
        clipped_gdf['geometry'] = clipped_gdf.geometry.make_valid()
        
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

def dissolve_smart(geojson: Union[dict, str, list], field: Union[str, list, None] = None) -> GeoAnalysisResult:
    """Dissolve geometries in GeoJSON."""
    try:
        parsed = safe_parse(geojson)
        if parsed is None:
            return GeoAnalysisResult(False, None, "Invalid GeoJSON input")
            
        fc = to_feature_collection(parsed)
        if not fc.get("features"):
            return GeoAnalysisResult(True, {"type": "FeatureCollection", "features": []}, "Layer empty, nothing to dissolve.")

        # GIS-599: honor a declared `crs` member instead of hardcoding
        # EPSG:4326 — a declared projected input was silently misread as WGS84.
        gdf = gdf_from_features(fc, "dissolve_smart layer")
        
        if gdf.empty:
            return GeoAnalysisResult(True, {"type": "FeatureCollection", "features": []}, "Layer empty, nothing to dissolve.")
            
        # Column validation
        if field:
            fields_to_check = [field] if isinstance(field, str) else list(field)
            missing = [f for f in fields_to_check if f not in gdf.columns]
            if missing:
                avail = [c for c in gdf.columns if c != "geometry"]
                return GeoAnalysisResult(
                    success=False,
                    data=None,
                    summary=f"Dissolve field(s) {missing} not found in layer properties. Available fields: {avail}",
                    error_type="KeyError",
                    correction_hint=f"Specify a valid property field from: {avail}"
                )

        gdf['geometry'] = gdf.geometry.make_valid()
        dissolved = gdf.dissolve(by=field).reset_index()
        dissolved['geometry'] = dissolved.geometry.make_valid()
        
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
        return GeoAnalysisResult(False, None, f"Dissolve failed: {str(e)}", error_type=type(e).__name__)

