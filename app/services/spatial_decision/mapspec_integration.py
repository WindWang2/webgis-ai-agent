"""
MapSpec & Cartography Integration for Spatial Decision Intelligence V2.
Binds SpatialDecisionResult and ScenarioComparisonResult to the same
MapSpecStore / lifecycle / desired-state review path used by GIS dispatch.
"""
import logging
from typing import Any, Dict, Optional

from app.services.spatial_decision.models import (
    SpatialDecisionResult,
    ScenarioComparisonResult,
)

logger = logging.getLogger(__name__)


def _authoring_unavailable(error: str) -> Dict[str, Any]:
    return {
        "success": False,
        "mapspec": {},
        "cartographic_review": {
            "stage": "desired_state",
            "status": "not_evaluated",
            "review": {
                "status": "not_evaluated",
                "passed": False,
                "complete": False,
                "checks": [{
                    "rule": "MAPSPEC_AUTHORING",
                    "status": "not_evaluated",
                    "severity": "error",
                    "evidence_class": "deterministic",
                    "evidence": {"error": error[:240]},
                    "repairability": "not_repairable",
                }],
            },
            "termination_reason": "mapspec_authoring_unavailable",
        },
    }


async def _upsert_decision_layer(
    *,
    session_id: str,
    layer: Dict[str, Any],
    source_data: Any,
) -> Dict[str, Any]:
    from app.services.mapspec_store import mapspec_store

    lifecycle = await mapspec_store.layer_upsert(session_id, layer, source_data)
    mapspec = lifecycle.get("mapspec") if isinstance(lifecycle.get("mapspec"), dict) else {}
    if not lifecycle.get("success"):
        unavailable = _authoring_unavailable(
            str(lifecycle.get("message") or lifecycle.get("error_msg") or "MapSpec rejected")
        )
        if mapspec:
            unavailable["mapspec"] = mapspec
        if lifecycle.get("cartographic_review") is not None:
            unavailable["cartographic_review"] = lifecycle["cartographic_review"]
        return unavailable
    return {
        "success": True,
        "mapspec": mapspec,
        "layer": lifecycle.get("layer"),
        "cartographic_review": lifecycle.get("cartographic_review"),
        "mapspec_fingerprint": lifecycle.get("mapspec_fingerprint"),
        "is_compiled": lifecycle.get("is_compiled"),
        "mutation_revision": lifecycle.get("mutation_revision"),
        "cartography_findings": lifecycle.get("cartography_findings"),
    }


async def apply_decision_to_mapspec(
    session_id: str,
    result: SpatialDecisionResult,
    lifecycle_engine: Optional[Any] = None,
) -> Dict[str, Any]:
    """Ingest SpatialDecisionResult through canonical MapSpec authoring.

    ``lifecycle_engine`` is accepted for call-site compatibility and ignored:
    production review must go through MapSpecStore so desired-state checks and
    mutation revisions stay on the same seam as other GIS results.
    """
    del lifecycle_engine
    if not session_id:
        return _authoring_unavailable("missing session_id")

    layer_id = f"sim_layer_{result.decision_id}"
    result_ref = result.simulation_ref_id or ""
    layer_dict = {
        "id": layer_id,
        "name": f"{result.scenario.name} - 空间影响图层",
        "type": "polygon",
        "source": layer_id,
        "style": {
            "color": {
                "method": "match",
                "field": "zone_type",
                "stops": [
                    ["direct", "#EF4444"],
                    ["indirect", "#F59E0B"],
                ],
                "default": "#3B82F6",
            },
            "opacity": 0.45,
            "stroke_color": "#1E293B",
            "stroke_width": 1.5,
        },
        "provenance": {
            "tool": "spatial_decision_v2",
            "decision_id": result.decision_id,
            "result_ref": result_ref,
        },
    }
    source_data: Any = result.simulation_geojson
    try:
        return await _upsert_decision_layer(
            session_id=session_id,
            layer=layer_dict,
            source_data=source_data,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("spatial decision MapSpec authoring failed: %s", type(exc).__name__)
        return _authoring_unavailable(type(exc).__name__)


async def apply_comparison_to_mapspec(
    session_id: str,
    comparison: ScenarioComparisonResult,
    lifecycle_engine: Optional[Any] = None,
) -> Dict[str, Any]:
    """Ingest ScenarioComparisonResult through canonical MapSpec authoring."""
    del lifecycle_engine
    if not session_id:
        return _authoring_unavailable("missing session_id")

    layer_id = f"cmp_layer_{comparison.comparison_id}"
    result_ref = getattr(comparison, "comparison_ref_id", "") or ""
    palette = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6"]
    color_stops = [
        [res.scenario.scenario_id, palette[i % len(palette)]]
        for i, res in enumerate(comparison.scenarios)
    ]
    layer_dict = {
        "id": layer_id,
        "name": "多方案情景模拟对比图层",
        "type": "polygon",
        "source": layer_id,
        "style": {
            "color": {
                "method": "match",
                "field": "scenario_id",
                "stops": color_stops,
                "default": "#8B5CF6",
            },
            "opacity": 0.5,
        },
        "provenance": {
            "tool": "scenario_compare",
            "comparison_id": comparison.comparison_id,
            "result_ref": result_ref,
        },
    }
    source_data: Any = comparison.comparison_geojson
    try:
        return await _upsert_decision_layer(
            session_id=session_id,
            layer=layer_dict,
            source_data=source_data,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("scenario comparison MapSpec authoring failed: %s", type(exc).__name__)
        return _authoring_unavailable(type(exc).__name__)


async def apply_v3_decision_to_mapspec(
    session_id: str,
    result: Any,
    lifecycle_engine: Optional[Any] = None,
) -> Dict[str, Any]:
    """Ingest SpatialDecisionResultV3 through canonical MapSpec authoring."""
    del lifecycle_engine
    if not session_id:
        return _authoring_unavailable("missing session_id")

    prob = result.problem
    rec = result.recommendation
    layer_id = f"dec_v3_layer_{prob.problem_id}"
    result_ref = getattr(result, "comparison_ref_id", "") or ""

    source_data: Any = result.comparison_geojson
    features = source_data.get("features", []) if isinstance(source_data, dict) else []

    alt_features = [
        f for f in features
        if f.get("properties", {}).get("layer_type") != "constraint_exclusion_zone"
    ]
    constraint_features = [
        f for f in features
        if f.get("properties", {}).get("layer_type") == "constraint_exclusion_zone"
    ]

    target_features = alt_features if alt_features else features
    geom_types = {
        f.get("geometry", {}).get("type")
        for f in target_features
        if f.get("geometry")
    }

    is_point = any(t in ("Point", "MultiPoint") for t in geom_types)
    is_line = any(t in ("LineString", "MultiLineString") for t in geom_types)

    if is_point:
        layer_type = "circle"
        style = {
            "color": {
                "method": "match",
                "field": "status",
                "stops": [
                    ["Recommended", "#10B981"],
                    ["Feasible Candidate", "#3B82F6"],
                    ["Infeasible (Violates Constraints)", "#EF4444"],
                ],
                "default": "#8B5CF6",
            },
            "opacity": 0.85,
            "stroke_color": "#0F172A",
            "stroke_width": 2.0,
            "radius": 7.0,
        }
    elif is_line:
        layer_type = "line"
        style = {
            "color": {
                "method": "match",
                "field": "status",
                "stops": [
                    ["Recommended", "#10B981"],
                    ["Feasible Candidate", "#3B82F6"],
                    ["Infeasible (Violates Constraints)", "#EF4444"],
                ],
                "default": "#8B5CF6",
            },
            "opacity": 0.8,
            "stroke_width": 3.0,
        }
    else:
        layer_type = "polygon"
        style = {
            "color": {
                "method": "match",
                "field": "status",
                "stops": [
                    ["Recommended", "#10B981"],
                    ["Feasible Candidate", "#3B82F6"],
                    ["Infeasible (Violates Constraints)", "#EF4444"],
                ],
                "default": "#8B5CF6",
            },
            "opacity": 0.55,
            "stroke_color": "#0F172A",
            "stroke_width": 2.0,
        }

    layer_dict = {
        "id": layer_id,
        "name": f"{prob.goal} - 决策推演与选址分析图层",
        "type": layer_type,
        "source": layer_id,
        "style": style,
        "provenance": {
            "tool": "spatial_decision_v3",
            "problem_id": prob.problem_id,
            "decision_fingerprint": rec.decision_fingerprint,
            "result_ref": result_ref,
        },
    }

    try:
        if constraint_features and alt_features:
            # Upsert dedicated polygon layer for spatial constraint exclusion zones
            constraint_layer_id = f"dec_v3_constraints_{prob.problem_id}"
            constraint_layer_dict = {
                "id": constraint_layer_id,
                "name": f"{prob.goal} - 空间约束与禁建缓冲区",
                "type": "polygon",
                "source": constraint_layer_id,
                "style": {
                    "color": "#EF4444",
                    "opacity": 0.25,
                    "stroke_color": "#B91C1C",
                    "stroke_width": 1.5,
                },
                "provenance": {
                    "tool": "spatial_decision_v3",
                    "problem_id": prob.problem_id,
                    "decision_fingerprint": rec.decision_fingerprint,
                    "result_ref": result_ref,
                },
            }
            await _upsert_decision_layer(
                session_id=session_id,
                layer=constraint_layer_dict,
                source_data={"type": "FeatureCollection", "features": constraint_features},
            )
            source_data = {"type": "FeatureCollection", "features": alt_features}

        return await _upsert_decision_layer(
            session_id=session_id,
            layer=layer_dict,
            source_data=source_data,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("spatial decision V3 MapSpec authoring failed: %s", type(exc).__name__)
        return _authoring_unavailable(type(exc).__name__)
