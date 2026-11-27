"""The Pi boundary is raw dict lists; typed domain validation happens inside."""
from __future__ import annotations

from typing import Any, Dict, List, get_type_hints
import uuid

from pydantic import TypeAdapter
import pytest

from app.tools.linked_exploration_tools import PreviewArgs, register_linked_exploration_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def tools():
    registry = ToolRegistry()
    register_linked_exploration_tools(registry)
    return registry


def test_preview_type_hints_match_actual_raw_dictionary_boundary(tools):
    handler = tools._tools["webgis_exploration_preview"]
    hints = get_type_hints(handler)
    model_hints = get_type_hints(PreviewArgs)
    for field in ("charts", "conditions"):
        assert hints[field] == model_hints[field] == List[Dict[str, Any]]
        raw = [{"field": "kind", "nested": {"value": 1}}]
        assert TypeAdapter(hints[field]).validate_python(raw) == raw
    assert hints["ref"] is str
    assert hints["session_id"] is str
    assert hints["return"] is dict


def test_preview_public_schema_stays_flat_bounded_and_dict_based(tools):
    assert tools._models["webgis_exploration_preview"] is PreviewArgs
    schema = PreviewArgs.model_json_schema()
    assert "$defs" not in schema
    for field, maximum in (("charts", 8), ("conditions", 16)):
        entry = schema["properties"][field]
        assert entry["type"] == "array"
        assert entry["maxItems"] == maximum
        assert entry["items"] == {"additionalProperties": True, "type": "object"}
    assert PreviewArgs(ref="ref:source").model_dump() == {
        "ref": "ref:source", "charts": [], "conditions": [],
    }


@pytest.mark.asyncio
async def test_real_preview_keeps_dictionary_parsing_and_typed_rejection(tools):
    from app.services.session_data import session_data_manager

    session_id = "annotation-preview-" + uuid.uuid4().hex
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [i, 0]},
         "properties": {"kind": kind, "value": i}}
        for i, kind in enumerate(("a", "b", "a"))
    ]
    ref = await session_data_manager.store(
        session_id, {"type": "FeatureCollection", "features": features}, prefix="annotations")
    try:
        result = await tools.dispatch("webgis_exploration_preview", {
            "ref": ref,
            "charts": [{"chart_id": "kind", "kind": "category", "field": "kind"}],
            "conditions": [{"kind": "category", "condition_id": "only-a",
                            "chart_id": "kind", "field": "kind", "values": ["a"]}],
        }, session_id=session_id)
        assert result["success"] is True, result
        assert result["match_count"] == 2
        assert result["match_count_exact"] is True
        assert result["charts"][0]["facet_matched"] == 3
        assert result["charts"][0]["full_matched"] == 2
        assert result["anchor"]["ref_id"] == ref
        invalid = await tools.dispatch("webgis_exploration_preview", {
            "ref": ref,
            "charts": [{"chart_id": "bad", "kind": "range", "field": "value",
                        "aggregation": "sum", "value_field": "value"}],
            "conditions": [],
        }, session_id=session_id)
        assert invalid["success"] is False
        assert invalid["code"] == "INVALID_CHART"
    finally:
        await session_data_manager.clear_session(session_id)


@pytest.mark.asyncio
async def test_preview_non_typed_condition_error_returns_typed_receipt(tools):
    """parse_conditions 抛非 LinkedExplorationError 时也回 typed 域错误面。

    绕过 registry 参数校验直调 handler（内部调用方路径）：非 dict 条件会让
    parse_conditions 抛 AttributeError；工具必须把它规范成 INVALID_CONDITION，
    而不是让异常穿出工具（P2-app_tools_linked_exploration_tools.py-00:116）。
    """
    handler = tools._tools["webgis_exploration_preview"]
    result = await handler(
        ref="ref:source", charts=[], conditions=["not-a-dict"],
        session_id="annotation-preview-cond-error",
    )
    assert result["success"] is False
    assert result["code"] == "INVALID_CONDITION"
