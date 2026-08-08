"""
MapSpec & Cartography Integration for Spatial Decision Intelligence V2.
Binds SpatialDecisionResult and ScenarioComparisonResult directly to MapSpecLifecycleEngine.
Ensures simulation layers (baseline, impact, comparison, difference, uncertainty) use canonical MapSpec path.
"""
import logging
from typing import Dict, Any, Optional

from app.services.spatial_decision.models import (
    SpatialDecisionResult,
    ScenarioComparisonResult,
)
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine, UpsertLayerIntent, SetViewIntent

logger = logging.getLogger(__name__)


def apply_decision_to_mapspec(
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
        try:
            lifecycle_engine.process_intent(session_id, view_intent)
        except Exception as e:
            logger.warning(f"Failed to update MapSpec view: {e}")

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

    layer_intent = UpsertLayerIntent(
        layer_id=layer_id,
        title=layer_title,
        layer_type="polygon",
        source_data=result.simulation_geojson,
        style=style_spec,
    )

    try:
        updated_spec = lifecycle_engine.process_intent(session_id, layer_intent)
        return updated_spec
    except Exception as e:
        logger.error(f"Failed to process MapSpec layer intent for decision {result.decision_id}: {e}")
        return {}


def apply_comparison_to_mapspec(
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
            "field": "scenario_id",
            "stops": [
                [scen.scenario.scenario_id, feat.get("properties", {}).get("scenario_color", "#3B82F6")]
                for scen in comparison.scenarios
                for feat in scen.simulation_geojson.get("features", [])
            ],
            "default": "#3B82F6",
        },
        "opacity": 0.40,
        "stroke_color": "#0F172A",
        "stroke_width": 2.0,
    }

    layer_intent = UpsertLayerIntent(
        layer_id=layer_id,
        title=layer_title,
        layer_type="polygon",
        source_data=comparison.comparison_geojson,
        style=style_spec,
    )

    try:
        updated_spec = lifecycle_engine.process_intent(session_id, layer_intent)
        return updated_spec
    except Exception as e:
        logger.error(f"Failed to process MapSpec layer intent for comparison {comparison.comparison_id}: {e}")
        return {}
