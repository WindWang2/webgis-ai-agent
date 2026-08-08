"""MapSpec Layer Ingestion Pipeline (app/services/mapspec/pipeline.py).

负责图层 Ingestion 规则转换 (Raster PNG 渲染, Spatial Analysis 转换,
GeoJSON 自动 Profiling, DataFabric 源代理与 View 计算)。
"""
from pathlib import Path
import logging
from typing import Any, Dict, Optional, Tuple

from app.services.spatial_meta_profiler import profile_geojson_source
from app.services.mapspec_source import store_data, profile_data, is_raster_entry, is_data_fabric_entry
from app.services.mapspec.store import view_has_center

logger = logging.getLogger(__name__)


def process_layer_ingestion(
    mapspec: Dict[str, Any],
    layer: Dict[str, Any],
    source_data: Optional[Any] = None,
    session_dir: Optional[Path] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    """处理图层转换与封装逻辑 (支持 GeoJSON, Raster, DataFabric 延迟/实例化源)"""
    processed_layer = dict(layer)
    processed_source_data = source_data

    is_raster = False
    if isinstance(processed_source_data, dict):
        from app.services.raster_cartography_converter import is_raster_source
        is_raster = is_raster_source(processed_source_data)

    if is_raster:
        from app.services.raster_cartography_converter import convert_raster_to_mapspec_layer

        raster_layer, legend, png, raster_source_data = convert_raster_to_mapspec_layer(
            processed_source_data, processed_layer, session_dir=session_dir
        )
        processed_layer = raster_layer
        processed_source_data = raster_source_data

    elif isinstance(processed_source_data, dict):
        from app.services.analysis_cartography_converter import (
            is_analysis_result,
            convert_analysis_to_mapspec_layer,
        )
        if is_analysis_result(processed_source_data):
            converted_layer, inline_geojson, _ = convert_analysis_to_mapspec_layer(
                processed_source_data, processed_layer
            )
            processed_layer = converted_layer
            processed_source_data = inline_geojson

    source_id = processed_layer.get("source", "default_source")
    sources = mapspec.get("sources", {})
    existing_entry = sources.get(source_id, {"type": "geojson"})
    source_entry = dict(existing_entry)

    already_has_data = (
        "inlineData" in source_entry
        or "url" in source_entry
        or is_raster_entry(source_entry)
        or is_data_fabric_entry(source_entry)
    )

    if processed_source_data is not None and not already_has_data:
        store_data(source_entry, processed_source_data)

    suggested_view: Optional[Dict[str, Any]] = None
    if is_raster_entry(source_entry) or is_data_fabric_entry(source_entry):
        data_to_profile = source_entry.get("inlineData")
    else:
        data_to_profile = processed_source_data or profile_data(source_entry)

    if data_to_profile and isinstance(data_to_profile, dict) and "profile" not in source_entry:
        try:
            profile = profile_geojson_source(data_to_profile)
            source_entry["profile"] = profile

            if not view_has_center(mapspec) and "suggestedView" in profile:
                suggested_view = {
                    "center": profile["suggestedView"]["center"],
                    "zoom": profile["suggestedView"]["zoom"],
                }
        except Exception as e:
            logger.warning(f"Auto-profiling failed for layer {processed_layer.get('id')}: {e}")

    return processed_layer, source_entry, suggested_view
