"""G41 测试助手：生产式建层 / 冻结源 / 会话 ref。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def make_fc(rows: Iterable[Dict[str, Any]], *, geom: str = "Polygon") -> Dict[str, Any]:
    features = []
    for i, props in enumerate(rows):
        base = 116.0 + i * 0.01
        if geom == "Point":
            g: Dict[str, Any] = {"type": "Point", "coordinates": [base, 39.0]}
        elif geom == "LineString":
            g = {"type": "LineString",
                 "coordinates": [[base, 39.0], [base + 0.01, 39.01]]}
        else:
            g = {"type": "Polygon", "coordinates": [[
                [base, 39.0], [base + 0.01, 39.0], [base + 0.01, 39.01],
                [base, 39.01], [base, 39.0],
            ]]}
        features.append({"type": "Feature", "geometry": g,
                         "properties": dict(props)})
    return {"type": "FeatureCollection", "features": features}


async def register_geojson_ref(session_id: str, fc: Dict[str, Any]) -> str:
    from app.services.session_data import session_data_manager
    return str(await session_data_manager.store(
        session_id, {"kind": "geojson", "geojson": fc}, prefix="result"))


async def seed_layer(
    session_id: str, fc: Dict[str, Any], *,
    layer_id: str = "L1", layer_type: str = "fill",
    legend: Optional[Dict[str, Any]] = None,
    paint: Optional[Dict[str, Any]] = None,
) -> str:
    """生产式建层：ref → 单笔 UpsertLayerIntent(source_data=ref)。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    ref_id = await register_geojson_ref(session_id, fc)
    layer: Dict[str, Any] = {
        "id": layer_id, "type": layer_type, "source": "s1",
        "paint": paint or {"color": "#cccccc"},
        "legend_spec": legend or {
            "type": "graduated", "field": "a",
            "breaks": [0, 5, 10], "palette_colors": ["#ffffff", "#888888"],
        },
        "data_binding": {"field": "a"},
    }
    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer=layer, source_data={"ref_id": ref_id}),
        origin="agent", actor="test:seed")
    assert not result.is_error, f"seed failed: {result.error_msg}"
    return ref_id


async def read_layer(session_id: str, layer_id: str = "L1") -> Dict[str, Any]:
    from app.services.mapspec_store import mapspec_store
    spec = await mapspec_store.get_mapspec(session_id) or {}
    for layer in spec.get("layers") or []:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            return layer
    raise AssertionError(f"layer {layer_id} missing")


async def read_source(session_id: str, source_id: str = "s1") -> Dict[str, Any]:
    from app.services.mapspec_store import mapspec_store
    spec = await mapspec_store.get_mapspec(session_id) or {}
    return (spec.get("sources") or {}).get(source_id) or {}


async def current_revision(session_id: str) -> int:
    from app.services.session_data import session_data_manager
    raw = await session_data_manager.get_state_field(
        session_id, "_cartographic_mutation_revision")
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def bivariate_plan(**kwargs) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "n": 3,
        "axis_a": {"field": "a", "unit": "人"},
        "axis_b": {"field": "b", "unit": "%"},
        "matrix_id": "BiPurpleOrange",
    }
    body.update(kwargs)
    return {"layer_id": "L1", "mode": "bivariate_matrix", "bivariate": body}


def reliability_plan(**kwargs) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "estimate_field": "est", "estimate_unit": "m", "estimate_k": 4,
        "measure": {
            "kind": "standard_error", "value_field": "se", "unit": "m",
            "source": "author_declared",
            "method_note": "仪器给出的逐要素标准误（测试声明）",
        },
        "visual": {
            "channel": "opacity", "min_value": 0, "max_value": 2,
            "min_output": 0.25, "max_output": 0.9, "unknown_output": 0.5,
            "buckets": 4, "clip": "clamp",
        },
    }
    body.update(kwargs)
    return {"layer_id": "L1", "mode": "reliability_dual_channel",
            "reliability": body}
