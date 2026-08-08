"""
MapSpec & Cartography Integration for Spatial Decision Intelligence V2.
Binds SpatialDecisionResult and ScenarioComparisonResult directly to MapSpecLifecycleEngine.
Ensures simulation layers (baseline, impact, comparison, difference, uncertainty) use canonical MapSpec path.
"""
import asyncio
import logging
from typing import Dict, Any, Optional

from app.services.spatial_decision.models import (
    SpatialDecisionResult,
    ScenarioComparisonResult,
)
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine, UpsertLayerIntent, SetViewIntent

logger = logging.getLogger(__name__)


async def _dispatch_mutation(engine: MapSpecLifecycleEngine, session_id: str, intent: Any) -> Dict[str, Any]:
    """Async dispatch MapSpec mutation intent."""
    try:
        res = await engine.apply_mutation(session_id, intent)
        return res.mapspec if hasattr(res, "mapspec") else {}
    except Exception as e:
        logger.warning(f"MapSpec mutation dispatch warning: {e}")
        return {}


async def apply_decision_to_mapspec(
    session_id: str,
    result: SpatialDecisionResult,
    lifecycle_engine: Optional[MapSpecLifecycleEngine] = None,
) -> Dict[str, Any]:
    """
    Ingest SpatialDecisionResult impact layer and view into MapSpec for session.
    """
    if not lifecycle_engine:
        lifecycle_engine = MapSpecLifecycleEngine()

    # 1. Update MapSpec View centered at target area
    if result.target_area.center:
        lng, lat = result.target_area.center
        view_intent = SetViewIntent(center=[lng, lat], zoom=13.0)
        await _dispatch_mutation(lifecycle_engine, session_id, view_intent)

    # 2. Ingest Simulation GeoJSON Layer
    layer_id = f"sim_layer_{result.decision_id}"
    layer_title = f"{result.scenario.name} - 空间影响图层"
    
    # Thematic style method: color by zone (direct vs indirect)
    style_spec = {
        "color": {
            "method": "match",
            "field": "zone",
            "stops": [
                ["direct", "#EF4444"],    # Red for direct zone
                ["indirect", "#F59E0B"],  # Amber for indirect zone
            ],
            "default": "#3B82F6",
        },
        "opacity": 0.45,
        "stroke_color": "#1E293B",
        "stroke_width": 1.5,
    }

    layer_dict = {
        "id": layer_id,
        "name": layer_title,
        "type": "polygon",
        "style": style_spec,
    }

    layer_intent = UpsertLayerIntent(
        layer=layer_dict,
        source_data=result.simulation_geojson,
    )

    return await _dispatch_mutation(lifecycle_engine, session_id, layer_intent)


async def apply_comparison_to_mapspec(
    session_id: str,
    comparison: ScenarioComparisonResult,
    lifecycle_engine: Optional[MapSpecLifecycleEngine] = None,
) -> Dict[str, Any]:
    """
    Ingest ScenarioComparisonResult comparison layer into MapSpec for session.
    """
    if not lifecycle_engine:
        lifecycle_engine = MapSpecLifecycleEngine()

    layer_id = f"cmp_layer_{comparison.comparison_id}"
    layer_title = "多方案情景模拟对比图层"

    style_spec = {
        "color": {
            "method": "match",
            "field": "scenario",
            "stops": [
                ["scenario_a", "#3B82F6"],
                ["scenario_b", "#10B981"],
                ["scenario_c", "#F59E0B"],
            ],
            "default": "#8B5CF6",
        },
        "opacity": 0.5,
    }

    layer_dict = {
        "id": layer_id,
        "name": layer_title,
        "type": "polygon",
        "style": style_spec,
    }

    layer_intent = UpsertLayerIntent(
        layer=layer_dict,
        source_data=comparison.comparison_geojson,
    )

    return await _dispatch_mutation(lifecycle_engine, session_id, layer_intent)

