"""
MapSpec Layer Ingestion Pipeline.
Extracts domain transformations (Raster PNG rendering, Analysis converter routing,
GeoJSON profiling, and auto-view calculation) out of MapSpecStore.
"""
from pathlib import Path
import logging
from typing import Any, Dict, Optional, Tuple

from app.services.spatial_meta_profiler import profile_geojson_source
from app.services.mapspec_source import store_data, profile_data, is_raster_entry
from app.services.mapspec_store import view_has_center

logger = logging.getLogger(__name__)


def process_layer_ingestion(
    mapspec: Dict[str, Any],
    layer: Dict[str, Any],
    source_data: Optional[Any] = None,
    session_dir: Optional[Path] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Processes layer ingestion transformation rules.

    Domain transformation engine:
    1. Raster payload detection -> PNG rendering -> save_png -> imageRef assignment.
    2. Spatial Analysis result dict detection -> convert_analysis_to_mapspec_layer.
    3. Source data shape classification & storage (store_data).
    4. Auto-profiling & suggested view calculation.

    Pure with respect to `mapspec`: it reads `mapspec["sources"]` only to seed the
    source entry's existing keys (via a shallow copy), and never writes back to
    `mapspec`. All outputs — the processed layer, the source entry, and the
    suggested view — are returned for the caller (MapSpecStore) to apply. This
    keeps MapSpecStore the sole write authority over the mapspec document (its
    own docstrings claim that role), and keeps the pipeline unit-testable through
    its return values alone. An earlier review flagged an aliasing mutation here;
    the `dict(existing_entry)` copy already prevents it, and the purity invariant
    is locked by test_process_layer_ingestion_does_not_mutate_mapspec.

    Returns:
    (processed_layer, processed_source_entry, optional_suggested_view_dict)
    """
    processed_layer = dict(layer)
    processed_source_data = source_data

    # 1. Raster vs Analysis Result vs GeoJSON detection
    is_raster = False
    if isinstance(processed_source_data, dict):
        from app.services.raster_cartography_converter import is_raster_source
        is_raster = is_raster_source(processed_source_data)

    if is_raster:
        from app.services.raster_cartography_converter import convert_raster_to_mapspec_layer
        from app.services.raster_store import save_png

        raster_layer, legend, png, raster_source_data = convert_raster_to_mapspec_layer(
            processed_source_data, processed_layer
        )
        processed_layer = raster_layer
        if legend is not None:
            processed_layer.setdefault("legend_spec", legend)

        if raster_source_data is not None:
            if png is not None and session_dir is not None:
                src_id_for_raster = processed_layer.get("source", "default_source")
                raster_source_data["imageRef"] = save_png(session_dir, src_id_for_raster, png)
            processed_source_data = raster_source_data
        else:
            processed_source_data = None

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
        "inlineData" in source_entry or "url" in source_entry or is_raster_entry(source_entry)
    )
    if processed_source_data is not None and not already_has_data:
        store_data(source_entry, processed_source_data)

    # 2. Auto-profiling & auto-view injection
    suggested_view: Optional[Dict[str, Any]] = None
    if is_raster_entry(source_entry):
        data_to_profile = None
    else:
        data_to_profile = processed_source_data or profile_data(source_entry)

    if data_to_profile and "profile" not in source_entry:
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
