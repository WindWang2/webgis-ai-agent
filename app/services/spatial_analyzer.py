"""
SpatialAnalyzer: Unified spatial analysis engine and operator execution seam.
Supports standardized GeoAnalysisResult payloads across all spatial operations.
"""
import hashlib
import json
import logging
import re
from collections import OrderedDict
from typing import Dict, List, Any, Optional, Callable

from app.lib.geo_processor.core import GeoAnalysisResult, to_utm_gdf, to_feature_collection
from app.lib.geo_processor.geometry import buffer_smart, clip_smart
from app.lib.geo_processor.overlay import overlay_smart
from app.lib.geo_analysis.statistics import (
    calculate_sde,
    moran_i_narrated,
    hotspot_narrated,
    cluster_narrated,
    calculate_central_feature,
    calculate_nearest,
    h3_lisa,
)
from app.lib.geo_analysis.aggregation import spatial_aggregate
from app.lib.geo_analysis.network import calculate_isochrones
from app.lib.geo_analysis.density import kde_surface as _kde_surface, kde_contours as _kde_contours
from app.lib.geo_analysis.geometry_ops import (
    voronoi_polygons as _voronoi_polygons,
    convex_hull as _convex_hull,
    multi_ring_buffer as _multi_ring_buffer,
)
from app.services.spatial_operator import spatial_operator

logger = logging.getLogger(__name__)

AnalysisResult = GeoAnalysisResult

_to_feature_collection = to_feature_collection


class SpatialAnalyzer:
    """
    Spatial analysis operator class - delegates to specialized geoprocessing & geo_analysis libraries.

    The class exposes its concrete operators (buffer, clip, overlay, statistics, cluster,
    ...) directly as the interface. A dynamic name-dispatch seam (execute() /
    execute_analysis() / OPERATOR_MAP) previously fronted these, but had zero production
    callers and swapped parameter order between its two signatures — see ADR-0013.
    """

    @classmethod
    @spatial_operator(name="Recognize vector data", progress_pct=10)
    def recognize_vector_data(
        cls,
        features: Any,
        auto_repair: bool = True,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        res = to_utm_gdf(features)
        if not res:
             return GeoAnalysisResult(False, None, "Invalid vector data")
        
        gdf, utm_crs = res
        summary = f"Recognized {len(gdf)} features with CRS {utm_crs}."
        return GeoAnalysisResult(True, features, summary)

    @classmethod
    @spatial_operator(name="buffer")
    def buffer(
        cls, 
        features: Any,
        distance: float = 100,
        unit: str = "m",
        dissolve: bool = False,
        callback: Optional[Callable] = None,
        source_crs: Optional[str] = None
    ) -> GeoAnalysisResult:
        return buffer_smart(
            geojson=features,
            distance=distance,
            unit=unit,
            dissolve=dissolve,
            source_crs=source_crs
        )

    @classmethod
    @spatial_operator(name="clip")
    def clip(
        cls,
        features: Any,
        boundary: Dict,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        return clip_smart(features, boundary)

    @classmethod
    @spatial_operator(name="overlay", feature_keys=["features_a", "features_b"])
    def overlay(
        cls,
        features_a: Any,
        features_b: Any,
        how: str = "intersection",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        return overlay_smart(features_a, features_b, how)

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
    @spatial_operator(name="attribute_filter")
    def attribute_filter(
        cls,
        features: Any,
        query: str,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        validation_error = cls._validate_query(query)
        if validation_error:
            return GeoAnalysisResult(False, None, validation_error)
        feat_list = features.get("features", [])
        import geopandas as gpd
        gdf = gpd.GeoDataFrame.from_features(feat_list)
        filtered_gdf = gdf.query(query)
        summary = f"Filtered {len(feat_list)} features to {len(filtered_gdf)} using query: {query}"
        return GeoAnalysisResult(True, filtered_gdf.__geo_interface__, summary)

    @classmethod
    @spatial_operator(name="statistics")
    def statistics(
        cls,
        features: Any,
        field: Optional[str] = None,
        spatial_stats: bool = False,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        if spatial_stats:
             if field:
                 return moran_i_narrated(features, field)
             else:
                 return calculate_sde(features)
        
        feat_list = features.get("features", [])
        import pandas as pd
        df = pd.DataFrame([f["properties"] for f in feat_list if isinstance(f, dict) and "properties" in f])
        if field and field in df.columns:
            stats = df[field].describe().to_dict()
            return GeoAnalysisResult(True, {"stats": stats}, f"Statistics for {field}: {stats}")
        return GeoAnalysisResult(True, {"count": len(feat_list)}, f"Total features: {len(feat_list)}")

    @classmethod
    @spatial_operator(name="cluster")
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
        return cluster_narrated(
            features,
            method=method,
            n_clusters=n_clusters,
            eps=eps,
            min_samples=min_samples,
            value_field=value_field
        )

    @classmethod
    @spatial_operator(name="central_feature")
    def central_feature(
        cls,
        features: Any,
        method: str = "mean_center",
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        return calculate_central_feature(features, method)

    @classmethod
    @spatial_operator(name="aggregate", feature_keys=["points", "polygons"])
    def aggregate(
        cls,
        points: Any,
        polygons: Any,
        stats: List[str] = ['count'],
        value_field: Optional[str] = None,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        return spatial_aggregate(
            points,
            polygons,
            stats=stats,
            value_field=value_field
        )

    @classmethod
    @spatial_operator(name="nearest", feature_keys=["source_features", "target_features"])
    def nearest(
        cls,
        source_features: Any,
        target_features: Any = None,
        callback: Optional[Callable] = None
    ) -> GeoAnalysisResult:
        if not target_features:
            return calculate_nearest(source_features)
        return GeoAnalysisResult(False, None, "Cross-layer nearest neighbor not yet implemented")

    @classmethod
    @spatial_operator(name="KDE surface")
    def kde_surface(
        cls,
        features: Any,
        bandwidth: float = 0,
        cell_size: float = 500,
        value_field: str = "",
        bounds: Optional[list] = None,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        return _kde_surface(features, bandwidth=bandwidth, cell_size=cell_size,
                             value_field=value_field, bounds=bounds)

    @classmethod
    @spatial_operator(name="KDE contour")
    def kde_contours(
        cls,
        features: Any,
        levels: int = 8,
        bandwidth: float = 0,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        return _kde_contours(features, levels=levels, bandwidth=bandwidth)

    @classmethod
    @spatial_operator(name="Voronoi tessellation")
    def voronoi_polygons(
        cls,
        features: Any,
        clip_bounds: Optional[list] = None,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        return _voronoi_polygons(features, clip_bounds=clip_bounds)

    @classmethod
    @spatial_operator(name="convex hull")
    def convex_hull(
        cls,
        features: Any,
        group_by: str = "",
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        return _convex_hull(features, group_by=group_by)

    @classmethod
    @spatial_operator(name="multi-ring buffer")
    def multi_ring_buffer(
        cls,
        features: Any,
        distances: Optional[list] = None,
        merge_rings: bool = True,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        return _multi_ring_buffer(features, distances=distances, merge_rings=merge_rings)

    @classmethod
    @spatial_operator(name="hotspot")
    def hotspot(
        cls,
        features: Any,
        value_field: str,
        distance_band: float = 0,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        return hotspot_narrated(features, value_field, distance_band)

    @classmethod
    @spatial_operator(name="LISA")
    def lisa(
        cls,
        features: Any,
        value_field: str,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        return h3_lisa(features, value_field)

    @classmethod
    @spatial_operator(name="spatial join", feature_keys=["left_features", "right_features"])
    def spatial_join(
        cls,
        left_features: Any,
        right_features: Any,
        join_type: str = "inner",
        predicate: str = "intersects",
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        feats_left = left_features.get("features", [])
        feats_right = right_features.get("features", [])

        if not feats_left or not feats_right:
            return GeoAnalysisResult(False, None, "Empty features in left or right layer for spatial join")

        import geopandas as gpd
        gdf_left = gpd.GeoDataFrame.from_features(feats_left, crs="EPSG:4326")
        gdf_right = gpd.GeoDataFrame.from_features(feats_right, crs="EPSG:4326")

        joined = gpd.sjoin(gdf_left, gdf_right, how=join_type, predicate=predicate)
        if "index_right" in joined.columns:
            joined = joined.drop(columns=["index_right"])

        summary = f"Joined {len(feats_left)} left features with {len(feats_right)} right features using predicate '{predicate}' ({join_type} join)."
        return GeoAnalysisResult(True, json.loads(joined.to_json()), summary)

    @classmethod
    def _prepare_raster_paths(cls, paths: List[str]) -> tuple[Optional[List[str]], Optional[GeoAnalysisResult]]:
        from app.utils.path import validate_data_path
        validated = []
        for p in paths:
            try:
                validated.append(validate_data_path(p))
            except ValueError as e:
                return None, GeoAnalysisResult(
                    False, None,
                    f"raster_path '{p}' 不在允许的 data_dir 范围内: {e}",
                    error_type="ValidationError"
                )
        return validated, None

    @classmethod
    @spatial_operator(name="zonal statistics")
    def zonal_stats(
        cls,
        features: Any,
        raster_path: str,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        validated_paths, err_res = cls._prepare_raster_paths([raster_path])
        if err_res:
            return err_res
        valid_path = validated_paths[0]

        feat_list = features.get("features", [])
        if not feat_list:
            return GeoAnalysisResult(False, None, "No features provided for zonal statistics")

        from app.lib.geo_analysis.raster_ops import zonal_statistics
        from app.lib.geo_analysis.raster_math import rasterio_env
        with rasterio_env():
            stats = zonal_statistics({"type": "FeatureCollection", "features": feat_list}, valid_path)

        for i, s in enumerate(stats):
            if i < len(feat_list):
                feat_list[i]["properties"].update(s)

        summary = f"Computed zonal statistics for {len(feat_list)} zones against raster {raster_path}."
        return GeoAnalysisResult(
            success=True,
            data={"type": "FeatureCollection", "features": feat_list},
            summary=summary,
        )

    @classmethod
    @spatial_operator(name="raster reclassification")
    def raster_reclassify(
        cls,
        raster_path: str,
        scheme: List[dict],
        nodata: Optional[float] = None,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        validated_paths, err_res = cls._prepare_raster_paths([raster_path])
        if err_res:
            return err_res
        valid_path = validated_paths[0]

        from app.lib.geo_analysis.raster_math import reclassify, rasterio_env
        with rasterio_env():
            result = reclassify(valid_path, scheme, nodata)
        summary = f"Reclassified raster {raster_path} into {len(scheme)} classes."
        return GeoAnalysisResult(True, result, summary)

    @classmethod
    @spatial_operator(name="raster calculation")
    def raster_calculator(
        cls,
        raster_a: str,
        raster_b: Optional[str] = None,
        expression: str = "A + B",
        constant: Optional[float] = None,
        nodata: Optional[float] = None,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        raw_paths = [raster_a] + ([raster_b] if raster_b else [])
        validated_paths, err_res = cls._prepare_raster_paths(raw_paths)
        if err_res:
            return err_res

        from app.lib.geo_analysis.raster_math import raster_calculator, rasterio_env
        with rasterio_env():
            result = raster_calculator(
                validated_paths[0],
                validated_paths[1] if len(validated_paths) > 1 else None,
                expression,
                constant,
                nodata,
            )
        summary = f"Raster calculator operation '{expression}' completed."
        return GeoAnalysisResult(True, result, summary)

    @classmethod
    @spatial_operator(name="raster resampling")
    def raster_resample(
        cls,
        raster_path: str,
        target_resolution: float,
        target_crs: Optional[str] = None,
        resampling: str = "bilinear",
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        validated_paths, err_res = cls._prepare_raster_paths([raster_path])
        if err_res:
            return err_res

        from app.lib.geo_analysis.raster_math import resample_raster, rasterio_env
        with rasterio_env():
            result = resample_raster(validated_paths[0], target_resolution, target_crs, resampling)
        summary = f"Resampled raster {raster_path} to resolution {target_resolution} ({resampling})."
        return GeoAnalysisResult(True, result, summary)

    @classmethod
    @spatial_operator(name="isochrone", feature_keys=["network_features", "facilities"])
    def isochrone_network(
        cls,
        network_features: Any,
        facilities: Any,
        travel_time: float = 15,
        mode: str = "walking",
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        return calculate_isochrones(network_features, facilities, travel_time, mode)

    # ── ST-DBSCAN Pairwise Distance Matrix LRU Cache Accessors ──
    @classmethod
    def clear_st_dbscan_cache(cls) -> None:
        """Clear the ST-DBSCAN pairwise distance matrix LRU cache."""
        from app.lib.geo_analysis.statistics import clear_distance_matrix_cache
        clear_distance_matrix_cache()

    @classmethod
    def get_st_dbscan_cache_info(cls) -> Dict[str, Any]:
        """Return distance matrix cache hits, misses, current size, and maxsize."""
        from app.lib.geo_analysis.statistics import get_distance_matrix_cache_info
        return get_distance_matrix_cache_info()

    @classmethod
    @spatial_operator(name="st_dbscan")
    def st_dbscan(
        cls,
        features: Any,
        eps1_spatial_meters: float = 1000.0,
        eps2_temporal_seconds: float = 3600.0,
        min_samples: int = 5,
        timestamp_field: str = "timestamp",
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        from app.lib.geo_analysis.statistics import st_dbscan_narrated
        return st_dbscan_narrated(
            features,
            eps1_spatial_meters=eps1_spatial_meters,
            eps2_temporal_seconds=eps2_temporal_seconds,
            min_samples=min_samples,
            timestamp_field=timestamp_field,
        )


class SpatialAnalysisEngine:
    """Deep Spatial Analysis Engine.
    
    Consolidates spatial operator dispatch, GeoJSON normalization, CRS transformation,
    result payload trimming/caching via SessionStore cursor payloads, and standard LLM response formatting.
    """

    def __init__(self):
        self.analyzer = SpatialAnalyzer

    def analyze(
        self,
        operator: str,
        features: Any,
        session_id: Optional[str] = None,
        auto_trim: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """Dispatch a spatial operation by name, normalizing input features and session state."""
        func = getattr(self.analyzer, operator, None)
        if not callable(func):
            return {
                "success": False,
                "summary": f"未知空间分析算子: {operator}",
                "data": {},
            }
        try:
            res: GeoAnalysisResult = func(features, **kwargs)
            out = res.to_llm_response()
            if auto_trim and isinstance(out, dict):
                from app.tools._utils import trim_features
                if out.get("type") == "FeatureCollection":
                    out = trim_features(out)
                elif isinstance(out.get("data"), dict) and out["data"].get("type") == "FeatureCollection":
                    out["data"] = trim_features(out["data"])
            if session_id and res.success and isinstance(out, dict):
                self._persist_session_cursor(session_id, operator, out)
            return out
        except Exception as e:
            logger.error(f"SpatialAnalysisEngine execution error ({operator}): {e}", exc_info=True)
            return {
                "success": False,
                "summary": f"空间分析算子 {operator} 执行失败: {e}",
                "data": {},
            }

    def _persist_session_cursor(self, session_id: str, operator: str, result_payload: Dict[str, Any]) -> None:
        """Persist result payload in session cursor cache with trimmed feature collection."""
        try:
            from app.tools._utils import trim_features
            from app.services.session_data import session_store
            safe_payload = result_payload
            if isinstance(result_payload, dict):
                if result_payload.get("type") == "FeatureCollection":
                    safe_payload = trim_features(result_payload)
                elif isinstance(result_payload.get("data"), dict) and result_payload["data"].get("type") == "FeatureCollection":
                    safe_payload = dict(result_payload)
                    safe_payload["data"] = trim_features(result_payload["data"])
            session_store.upsert_ref_data(
                session_id=session_id,
                ref_key=f"analysis_result_{operator}",
                data=safe_payload,
            )
        except Exception as err:
            logger.warning(f"Failed to persist session cursor for {operator}: {err}")

    def buffer(self, features: Any, distance: float, unit: str = "m", session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.analyze("buffer", features, session_id=session_id, distance=distance, unit=unit)

    def clip(self, features: Any, mask_features: Any, session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.analyze("clip", features, session_id=session_id, boundary=mask_features)

    def overlay(self, features_a: Any, features_b: Any, how: str = "intersection", session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.analyze("overlay", features_a, session_id=session_id, features_b=features_b, how=how)

    def statistics(self, features: Any, session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.analyze("statistics", features, session_id=session_id)

    def nearest(self, features: Any, session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.analyze("nearest", features, session_id=session_id)

    def spatial_join(self, target_features: Any, join_features: Any, how: str = "inner", predicate: str = "intersects", session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.analyze("spatial_join", target_features, session_id=session_id, join_features=join_features, how=how, predicate=predicate)

    def zonal_stats(self, raster_data: Any, polygon_features: Any, stats: Optional[List[str]] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.analyze("zonal_stats", raster_data, session_id=session_id, polygon_features=polygon_features, stats=stats)

    def isochrone_network(self, network_features: Any, facilities: Any, travel_time: float = 15, mode: str = "walking", session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.analyze("isochrone_network", network_features, session_id=session_id, facilities=facilities, travel_time=travel_time, mode=mode)


spatial_analysis_engine = SpatialAnalysisEngine()


__all__ = ["SpatialAnalyzer", "AnalysisResult", "SpatialAnalysisEngine", "spatial_analysis_engine"]
