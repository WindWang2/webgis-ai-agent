"""
SpatialAnalyzer: Unified spatial analysis engine and operator execution seam.
Supports standardized GeoAnalysisResult payloads across all spatial operations.
"""
import json
import logging
import re
from typing import Dict, List, Any, Optional, Callable

from app.lib.geo_processor.core import GeoAnalysisResult, to_utm_gdf
from app.lib.geo_processor.geometry import buffer_smart, clip_smart
from app.lib.geo_processor.overlay import overlay_smart
from app.lib.geo_analysis.statistics import (
    calculate_sde, 
    moran_i_narrated, 
    cluster_narrated,
    calculate_central_feature,
    calculate_nearest
)
from app.lib.geo_analysis.aggregation import spatial_aggregate
from app.lib.geo_analysis.network import calculate_isochrones

logger = logging.getLogger(__name__)


# ADR-0009: analysis-result identity is GeoAnalysisResult itself — there is no
# second consumer justifying a separate interface. Candidate #3's "AnalysisResult
# class alias" therefore names a *type alias*, not a wrapper subclass: an earlier
# `class AnalysisResult(GeoAnalysisResult)` only delegated by identity (Middle Man)
# and was never instantiated, so it has been flattened to the alias the spec named.
AnalysisResult = GeoAnalysisResult


def _to_feature_collection(data: Any) -> Dict[str, Any]:
    """Normalize input data (GeoJSON dict, features list, string, or single feature) into a valid FeatureCollection dict."""
    if not data:
        return {"type": "FeatureCollection", "features": []}

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
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


class SpatialAnalyzer:
    """
    Spatial analysis operator class - delegates to specialized geoprocessing & geo_analysis libraries.

    The class exposes its concrete operators (buffer, clip, overlay, statistics, cluster,
    ...) directly as the interface. A dynamic name-dispatch seam (execute() /
    execute_analysis() / OPERATOR_MAP) previously fronted these, but had zero production
    callers and swapped parameter order between its two signatures — see ADR-0013.
    """

    @classmethod
    def recognize_vector_data(
        cls,
        features: Any,
        auto_repair: bool = True,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        if callback: callback(10, "Recognizing vector data...")
        fc = _to_feature_collection(features)
        res = to_utm_gdf(fc)
        if not res:
             return GeoAnalysisResult(False, None, "Invalid vector data")
        
        gdf, utm_crs = res
        summary = f"Recognized {len(gdf)} features with CRS {utm_crs}."
        return GeoAnalysisResult(True, fc, summary)

    @classmethod
    def buffer(
        cls, 
        features: Any,
        distance: float = 100,
        unit: str = "m",
        dissolve: bool = False,
        callback: Optional[Callable] = None,
        source_crs: Optional[str] = None
    ) -> GeoAnalysisResult:
        if callback: callback(20, "Executing buffer analysis...")
        fc = _to_feature_collection(features)
        return buffer_smart(
            geojson=fc,
            distance=distance,
            unit=unit,
            dissolve=dissolve,
            source_crs=source_crs
        )

    @classmethod
    def clip(
        cls,
        features: Any,
        boundary: Dict,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        if callback: callback(20, "Executing clip analysis...")
        fc = _to_feature_collection(features)
        return clip_smart(fc, boundary)

    @classmethod
    def overlay(
        cls,
        features_a: Any,
        features_b: Any,
        how: str = "intersection",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        if callback: callback(20, f"Executing {how} overlay...")
        layer_a = _to_feature_collection(features_a)
        layer_b = _to_feature_collection(features_b)
        return overlay_smart(layer_a, layer_b, how)

    # Whitelist: identifiers, numbers, strings, comparison/logical operators, parens, brackets
    _SAFE_QUERY_RE = re.compile(
        r"^[\w\s.\'\":<>=!(),\[\]-]+$"
    )
    _BLOCKED_KEYWORDS = frozenset({
        "import", "exec", "eval", "compile", "open", "breakpoint",
        "globals", "locals", "getattr", "setattr", "delattr",
        "__import__", "__builtins__", "__name__",
    })

    @classmethod
    def _validate_query(cls, query: str) -> Optional[str]:
        """Return error message if query is unsafe, None if safe."""
        if not cls._SAFE_QUERY_RE.match(query):
            return f"Unsafe query: disallowed characters in '{query}'"
        if "__" in query:
            return f"Unsafe query: double-underscore attribute access forbidden in '{query}'"
        lowered = query.lower()
        for kw in cls._BLOCKED_KEYWORDS:
            if kw in lowered:
                return f"Unsafe query: disallowed keyword '{kw}'"
        return None

    @classmethod
    def attribute_filter(
        cls,
        features: Any,
        query: str,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        validation_error = cls._validate_query(query)
        if validation_error:
            return GeoAnalysisResult(False, None, validation_error)
        fc = _to_feature_collection(features)
        feat_list = fc.get("features", [])
        try:
            import geopandas as gpd
            gdf = gpd.GeoDataFrame.from_features(feat_list)
            filtered_gdf = gdf.query(query)
            summary = f"Filtered {len(feat_list)} features to {len(filtered_gdf)} using query: {query}"
            return GeoAnalysisResult(True, filtered_gdf.__geo_interface__, summary)
        except Exception as e:
            return GeoAnalysisResult(False, None, f"Filter failed: {str(e)}")

    @classmethod
    def statistics(
        cls,
        features: Any,
        field: Optional[str] = None,
        spatial_stats: bool = False,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        fc = _to_feature_collection(features)
        if spatial_stats:
             if field:
                 return moran_i_narrated(fc, field)
             else:
                 return calculate_sde(fc)
        
        feat_list = fc.get("features", [])
        try:
            import pandas as pd
            df = pd.DataFrame([f["properties"] for f in feat_list if isinstance(f, dict) and "properties" in f])
            if field and field in df.columns:
                stats = df[field].describe().to_dict()
                return GeoAnalysisResult(True, {"stats": stats}, f"Statistics for {field}: {stats}")
            return GeoAnalysisResult(True, {"count": len(feat_list)}, f"Total features: {len(feat_list)}")
        except Exception as e:
            return GeoAnalysisResult(False, None, str(e))

    @classmethod
    def cluster(
        cls,
        features: Any,
        method: str = "dbscan",
        n_clusters: int = 5,
        eps: float = 1000,
        min_samples: int = 5,
        value_field: str = "",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        fc = _to_feature_collection(features)
        return cluster_narrated(
            fc,
            method=method,
            n_clusters=n_clusters,
            eps=eps,
            min_samples=min_samples,
            value_field=value_field
        )

    @classmethod
    def central_feature(
        cls,
        features: Any,
        method: str = "mean_center",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        fc = _to_feature_collection(features)
        return calculate_central_feature(fc, method)

    @classmethod
    def aggregate(
        cls,
        points: Any,
        polygons: Any,
        stats: List[str] = ['count'],
        value_field: Optional[str] = None,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        fc_points = _to_feature_collection(points)
        fc_polygons = _to_feature_collection(polygons)
        return spatial_aggregate(
            fc_points,
            fc_polygons,
            stats=stats,
            value_field=value_field
        )

    @classmethod
    def nearest(
        cls,
        source_features: Any,
        target_features: Any = None,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        fc_source = _to_feature_collection(source_features)
        if not target_features:
            return calculate_nearest(fc_source)
        return GeoAnalysisResult(False, None, "Cross-layer nearest neighbor not yet implemented")

    @classmethod
    def path_analysis(
        cls,
        network_features: Any,
        start_point: List[float],
        end_point: List[float],
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        from app.lib.geo_analysis.network import shortest_path
        fc_network = _to_feature_collection(network_features)
        return shortest_path(
            fc_network,
            start_point,
            end_point
        )


__all__ = ["SpatialAnalyzer", "AnalysisResult"]
