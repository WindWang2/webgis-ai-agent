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
    def kde_surface(
        cls,
        features: Any,
        bandwidth: float = 0,
        cell_size: float = 500,
        value_field: str = "",
        bounds: Optional[list] = None,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        if callback: callback(20, "Executing KDE surface analysis...")
        fc = _to_feature_collection(features)
        return _kde_surface(fc, bandwidth=bandwidth, cell_size=cell_size,
                             value_field=value_field, bounds=bounds)

    @classmethod
    def kde_contours(
        cls,
        features: Any,
        levels: int = 8,
        bandwidth: float = 0,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        if callback: callback(20, "Executing KDE contour analysis...")
        fc = _to_feature_collection(features)
        return _kde_contours(fc, levels=levels, bandwidth=bandwidth)

    @classmethod
    def voronoi_polygons(
        cls,
        features: Any,
        clip_bounds: Optional[list] = None,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        if callback: callback(20, "Executing Voronoi tessellation...")
        fc = _to_feature_collection(features)
        return _voronoi_polygons(fc, clip_bounds=clip_bounds)

    @classmethod
    def convex_hull(
        cls,
        features: Any,
        group_by: str = "",
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        if callback: callback(20, "Executing convex hull...")
        fc = _to_feature_collection(features)
        return _convex_hull(fc, group_by=group_by)

    @classmethod
    def multi_ring_buffer(
        cls,
        features: Any,
        distances: Optional[list] = None,
        merge_rings: bool = True,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        if callback: callback(20, "Executing multi-ring buffer...")
        fc = _to_feature_collection(features)
        return _multi_ring_buffer(fc, distances=distances, merge_rings=merge_rings)

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

    @classmethod
    def spatial_join(
        cls,
        left_features: Any,
        right_features: Any,
        join_type: str = "inner",
        predicate: str = "intersects",
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        """Perform spatial join between left and right feature collections."""
        if callback: callback(20, f"Executing {join_type} spatial join with {predicate}...")
        fc_left = _to_feature_collection(left_features)
        fc_right = _to_feature_collection(right_features)
        feats_left = fc_left.get("features", [])
        feats_right = fc_right.get("features", [])

        if not feats_left or not feats_right:
            return GeoAnalysisResult(False, None, "Empty features in left or right layer for spatial join")

        try:
            import geopandas as gpd
            gdf_left = gpd.GeoDataFrame.from_features(feats_left, crs="EPSG:4326")
            gdf_right = gpd.GeoDataFrame.from_features(feats_right, crs="EPSG:4326")

            joined = gpd.sjoin(gdf_left, gdf_right, how=join_type, predicate=predicate)
            if "index_right" in joined.columns:
                joined = joined.drop(columns=["index_right"])

            summary = f"Joined {len(feats_left)} left features with {len(feats_right)} right features using predicate '{predicate}' ({join_type} join)."
            return GeoAnalysisResult(True, json.loads(joined.to_json()), summary)
        except Exception as e:
            return GeoAnalysisResult(False, None, f"Spatial join failed: {str(e)}")

    @classmethod
    def _prepare_raster_paths(cls, paths: List[str]) -> tuple[Optional[List[str]], Optional[GeoAnalysisResult]]:
        """Validate raster data paths to prevent path traversal and VFS abuse."""
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
    def zonal_stats(
        cls,
        features: Any,
        raster_path: str,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        """Compute zonal statistics for vector polygons against a raster surface."""
        if callback: callback(20, "Executing zonal statistics...")
        validated_paths, err_res = cls._prepare_raster_paths([raster_path])
        if err_res:
            return err_res
        valid_path = validated_paths[0]

        fc = _to_feature_collection(features)
        feat_list = fc.get("features", [])
        if not feat_list:
            return GeoAnalysisResult(False, None, "No features provided for zonal statistics")

        from app.lib.geo_analysis.raster_ops import zonal_statistics
        try:
            import rasterio
            with rasterio.Env(
                GDAL_DISABLE_READDIR_ON_OPEN="TRUE",
                GDAL_HTTP_TIMEOUT=5,
                GDAL_HTTP_MAX_RETRY=0,
            ):
                stats = zonal_statistics({"type": "FeatureCollection", "features": feat_list}, valid_path)
        except Exception as e:
            return GeoAnalysisResult(
                False, None,
                f"raster_path '{raster_path}' 无法打开: {e}",
                error_type="RasterError"
            )

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
    def raster_reclassify(
        cls,
        raster_path: str,
        scheme: List[dict],
        nodata: Optional[float] = None,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        """Reclassify continuous raster values according to a scheme."""
        if callback: callback(20, "Executing raster reclassification...")
        validated_paths, err_res = cls._prepare_raster_paths([raster_path])
        if err_res:
            return err_res
        valid_path = validated_paths[0]

        from app.lib.geo_analysis.raster_math import reclassify
        try:
            import rasterio
            with rasterio.Env(
                GDAL_DISABLE_READDIR_ON_OPEN="TRUE",
                GDAL_HTTP_TIMEOUT=5,
                GDAL_HTTP_MAX_RETRY=0,
            ):
                result = reclassify(valid_path, scheme, nodata)
            summary = f"Reclassified raster {raster_path} into {len(scheme)} classes."
            return GeoAnalysisResult(True, result, summary)
        except Exception as e:
            return GeoAnalysisResult(False, None, f"重分类失败: {e}", error_type="RasterError")

    @classmethod
    def raster_calculator(
        cls,
        raster_a: str,
        raster_b: Optional[str] = None,
        expression: str = "A + B",
        constant: Optional[float] = None,
        nodata: Optional[float] = None,
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        """Execute pixel-level math operations across one or two rasters."""
        if callback: callback(20, f"Executing raster calculation: {expression}...")
        raw_paths = [raster_a] + ([raster_b] if raster_b else [])
        validated_paths, err_res = cls._prepare_raster_paths(raw_paths)
        if err_res:
            return err_res

        from app.lib.geo_analysis.raster_math import raster_calculator
        try:
            import rasterio
            with rasterio.Env(
                GDAL_DISABLE_READDIR_ON_OPEN="TRUE",
                GDAL_HTTP_TIMEOUT=5,
                GDAL_HTTP_MAX_RETRY=0,
            ):
                result = raster_calculator(
                    validated_paths[0],
                    validated_paths[1] if len(validated_paths) > 1 else None,
                    expression,
                    constant,
                    nodata,
                )
            summary = f"Raster calculator operation '{expression}' completed."
            return GeoAnalysisResult(True, result, summary)
        except Exception as e:
            return GeoAnalysisResult(False, None, f"栅格计算失败: {e}", error_type="RasterError")

    @classmethod
    def raster_resample(
        cls,
        raster_path: str,
        target_resolution: float,
        target_crs: Optional[str] = None,
        resampling: str = "bilinear",
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        """Resample raster pixel resolution and/or reproject CRS."""
        if callback: callback(20, f"Executing raster resampling to {target_resolution}...")
        validated_paths, err_res = cls._prepare_raster_paths([raster_path])
        if err_res:
            return err_res

        from app.lib.geo_analysis.raster_math import resample_raster
        try:
            import rasterio
            with rasterio.Env(
                GDAL_DISABLE_READDIR_ON_OPEN="TRUE",
                GDAL_HTTP_TIMEOUT=5,
                GDAL_HTTP_MAX_RETRY=0,
            ):
                result = resample_raster(validated_paths[0], target_resolution, target_crs, resampling)
            summary = f"Resampled raster {raster_path} to resolution {target_resolution} ({resampling})."
            return GeoAnalysisResult(True, result, summary)
        except Exception as e:
            return GeoAnalysisResult(False, None, f"重采样失败: {e}", error_type="RasterError")

    @classmethod
    def isochrone_network(
        cls,
        network_features: Any,
        facilities: Any,
        travel_time: float = 15,
        mode: str = "walking",
        callback: Optional[Callable] = None,
    ) -> GeoAnalysisResult:
        """Compute network-based travel time isochrone polygon reachable areas."""
        if callback: callback(20, f"Executing {travel_time}-min {mode} isochrone analysis...")
        fc_net = _to_feature_collection(network_features)
        fc_facs = _to_feature_collection(facilities)
        return calculate_isochrones(fc_net, fc_facs, travel_time, mode)


__all__ = ["SpatialAnalyzer", "AnalysisResult"]
