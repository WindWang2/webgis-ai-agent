"""
SpatialAnalyzer Refactored: Thin wrapper delegating to new geoprocessing and geo_analysis libraries.
Supports the standardized GeoAnalysisResult.
"""
import logging
from typing import Dict, List, Any, Optional, Callable
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_processor.geometry import buffer_smart, clip_smart
from app.lib.geo_processor.overlay import overlay_smart
from app.lib.geo_analysis.statistics import (
    calculate_sde, 
    moran_i_narrated, 
    hotspot_narrated, 
    cluster_narrated,
    calculate_central_feature,
    calculate_nearest
)
from app.lib.geo_analysis.aggregation import spatial_aggregate
from app.lib.geo_analysis.network import calculate_isochrones
from app.lib.geo_processor.core import to_utm_gdf

logger = logging.getLogger(__name__)

class AnalysisResult(GeoAnalysisResult):
    """Backward compatibility wrapper for GeoAnalysisResult."""
    @classmethod
    def from_geo(cls, r: GeoAnalysisResult) -> "AnalysisResult":
        return cls(
            success=r.success,
            data=r.data,
            summary=r.summary,
            error_type=r.error_type,
            correction_hint=r.correction_hint
        )

class SpatialAnalyzer:
    """
    Spatial analysis operator class - refactored to delegate to specialized libraries.
    """

    @classmethod
    def recognize_vector_data(
        cls,
        features: List[Dict],
        auto_repair: bool = True,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        if callback: callback(10, "Recognizing vector data...")
        data = {"type": "FeatureCollection", "features": features}
        res = to_utm_gdf(data)
        if not res:
             return GeoAnalysisResult(False, None, "Invalid vector data")
        
        gdf, utm_crs = res
        summary = f"Recognized {len(gdf)} features with CRS {utm_crs}."
        return GeoAnalysisResult(True, data, summary)

    @classmethod
    def buffer(
        cls, 
        features: List[Dict],
        distance: float,
        unit: str = "m",
        dissolve: bool = False,
        callback: Optional[Callable] = None,
        source_crs: Optional[str] = None
    ) -> "AnalysisResult":
        if callback: callback(20, "Executing buffer analysis...")
        data = {"type": "FeatureCollection", "features": features}
        res = buffer_smart(
            geojson=data,
            distance=distance,
            unit=unit,
            dissolve=dissolve,
            source_crs=source_crs
        )
        return AnalysisResult.from_geo(res)

    @classmethod
    def clip(
        cls,
        features: List[Dict],
        boundary: Dict,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        if callback: callback(20, "Executing clip analysis...")
        target = {"type": "FeatureCollection", "features": features}
        return clip_smart(target, boundary)

    @classmethod
    def overlay(
        cls,
        features_a: List[Dict],
        features_b: List[Dict],
        how: str = "intersection",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        if callback: callback(20, f"Executing {how} overlay...")
        layer_a = {"type": "FeatureCollection", "features": features_a}
        layer_b = {"type": "FeatureCollection", "features": features_b}
        return overlay_smart(layer_a, layer_b, how)

    @classmethod
    def attribute_filter(
        cls,
        features: List[Dict],
        query: str,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        try:
            import geopandas as gpd
            gdf = gpd.GeoDataFrame.from_features(features)
            filtered_gdf = gdf.query(query)
            summary = f"Filtered {len(features)} features to {len(filtered_gdf)} using query: {query}"
            return GeoAnalysisResult(True, filtered_gdf.__geo_interface__, summary)
        except Exception as e:
            return GeoAnalysisResult(False, None, f"Filter failed: {str(e)}")

    @classmethod
    def statistics(
        cls,
        features: List[Dict],
        field: Optional[str] = None,
        spatial_stats: bool = False,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        if spatial_stats:
             if field:
                 return moran_i_narrated({"type": "FeatureCollection", "features": features}, field)
             else:
                 return calculate_sde({"type": "FeatureCollection", "features": features})
        
        try:
            import pandas as pd
            df = pd.DataFrame([f["properties"] for f in features if "properties" in f])
            if field and field in df.columns:
                stats = df[field].describe().to_dict()
                return GeoAnalysisResult(True, {"stats": stats}, f"Statistics for {field}: {stats}")
            return GeoAnalysisResult(True, {"count": len(features)}, f"Total features: {len(features)}")
        except Exception as e:
            return GeoAnalysisResult(False, None, str(e))

    @classmethod
    def cluster(
        cls,
        features: List[Dict],
        method: str = "dbscan",
        n_clusters: int = 5,
        eps: float = 1000,
        min_samples: int = 5,
        value_field: str = "",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        return cluster_narrated(
            {"type": "FeatureCollection", "features": features},
            method=method,
            n_clusters=n_clusters,
            eps=eps,
            min_samples=min_samples,
            value_field=value_field
        )

    @classmethod
    def central_feature(
        cls,
        features: List[Dict],
        method: str = "mean_center",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        return calculate_central_feature({"type": "FeatureCollection", "features": features}, method)

    @classmethod
    def aggregate(
        cls,
        points: List[Dict],
        polygons: List[Dict],
        stats: List[str] = ['count'],
        value_field: Optional[str] = None,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        return spatial_aggregate(
            {"type": "FeatureCollection", "features": points},
            {"type": "FeatureCollection", "features": polygons},
            stats=stats,
            value_field=value_field
        )

    @classmethod
    def nearest(
        cls,
        source_features: List[Dict],
        target_features: List[Dict] = None,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        if not target_features:
            return calculate_nearest({"type": "FeatureCollection", "features": source_features})
        return GeoAnalysisResult(False, None, "Cross-layer nearest neighbor not yet implemented")

    @classmethod
    def path_analysis(
        cls,
        network_features: List[Dict],
        start_point: List[float],
        end_point: List[float],
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        from app.lib.geo_analysis.network import shortest_path
        return shortest_path(
            {"type": "FeatureCollection", "features": network_features},
            start_point,
            end_point
        )

ANALYSIS_OPERATORS = {
    "buffer": SpatialAnalyzer.buffer,
    "clip": SpatialAnalyzer.clip,
    "overlay": SpatialAnalyzer.overlay,
    "statistics": SpatialAnalyzer.statistics,
    "cluster": SpatialAnalyzer.cluster,
    "aggregate": SpatialAnalyzer.aggregate,
    "central_feature": SpatialAnalyzer.central_feature,
    "attribute_filter": SpatialAnalyzer.attribute_filter,
}

def execute_analysis(
    task_type: str,
    parameters: Dict,
    input_data: Dict,
    callback: Optional[Callable] = None
) -> GeoAnalysisResult:
    op_func = ANALYSIS_OPERATORS.get(task_type.lower())
    if not op_func:
        return GeoAnalysisResult(False, None, f"Unknown analysis type: {task_type}")
    
    features = input_data.get("features", [])
    if task_type == "buffer":
        return op_func(features, parameters.get("distance", 100), parameters.get("unit", "m"), callback=callback)
    elif task_type == "clip":
        return op_func(features, parameters.get("boundary", {}), callback=callback)
    elif task_type == "cluster":
        return op_func(features, **parameters, callback=callback)
    elif task_type == "aggregate":
        return op_func(features, parameters.get("polygons", []), **parameters, callback=callback)
    
    return op_func(features, **parameters, callback=callback)

__all__ = ["SpatialAnalyzer", "execute_analysis", "ANALYSIS_OPERATORS", "AnalysisResult"]
