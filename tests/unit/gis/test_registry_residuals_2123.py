"""Real-tool and option-contract regressions for the bounded registry repair."""
from __future__ import annotations

import hashlib
import uuid

import pytest


@pytest.fixture(scope="module")
def live_tools():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    init_tools(registry)
    return registry


def test_linked_exploration_has_real_preview_algorithm(live_tools):
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.tools.linked_exploration_tools import PreviewArgs

    algorithms = get_algorithm_registry().algorithms_for_capability("linked_exploration")
    assert len(algorithms) == 1
    algorithm = algorithms[0]
    assert algorithm.tool_candidates == ["webgis_exploration_preview"]
    assert algorithm.runtime_status == "native"
    assert algorithm.crs_class == "CRS_AGNOSTIC"
    assert "冻结" in " ".join(algorithm.assumptions)
    assert "JSON" in " ".join(algorithm.limitations)
    assert live_tools._models["webgis_exploration_preview"] is PreviewArgs
    schema = PreviewArgs.model_json_schema()
    assert schema["properties"]["charts"]["maxItems"] == 8
    assert schema["properties"]["conditions"]["maxItems"] == 16


@pytest.mark.asyncio
async def test_linked_exploration_preview_tool_uses_real_contract(live_tools):
    from app.services.session_data import session_data_manager

    session_id = f"registry-exploration-{uuid.uuid4().hex[:8]}"
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [i, 0]},
                 "properties": {"kind": kind, "value": value}}
                for i, (kind, value) in enumerate([("a", 10), ("b", 20), ("a", 30)])]
    ref = await session_data_manager.store(
        session_id, {"type": "FeatureCollection", "features": features}, prefix="g46")
    try:
        result = await live_tools.dispatch("webgis_exploration_preview", {
            "ref": ref,
            "charts": [{"chart_id": "category", "kind": "category", "field": "kind"}],
            "conditions": [{"kind": "category", "condition_id": "choose-a",
                            "chart_id": "category", "field": "kind", "values": ["a"]}],
        }, session_id=session_id)
        assert result["success"] is True, result
        assert result["match_count"] == 2
        assert result["match_count_exact"] is True
        assert result["charts"][0]["facet_matched"] == 3
        assert result["charts"][0]["full_matched"] == 2
        assert result["anchor"]["ref_id"] == ref
        invalid = await live_tools.dispatch("webgis_exploration_preview", {
            "ref": ref,
            "charts": [{"chart_id": "bad", "kind": "range", "field": "value",
                        "aggregation": "sum", "value_field": "value"}],
            "conditions": [],
        }, session_id=session_id)
        assert invalid["success"] is False
        assert invalid["code"] == "INVALID_CHART"
    finally:
        await session_data_manager.clear_session(session_id)


def test_cartogram_export_uses_existing_publication_capability(live_tools):
    from app.lib.gis.capability_registry import get_capability_registry

    capabilities = live_tools.metadata("webgis_dorling_cartogram_export")["capabilities"]
    assert set(capabilities) == {"thematic_cartography", "map_export_publishing"}
    assert all(get_capability_registry().has(capability) for capability in capabilities)


@pytest.mark.asyncio
async def test_cartogram_export_binding_preserves_service_result(monkeypatch):
    from app.services import cartogram_authoring
    from app.tools.cartogram_tools import register_cartogram_tools
    from app.tools.registry import ToolRegistry

    calls = []
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'

    async def export(session_id, layout_ref, format, *, dpi):
        calls.append((session_id, layout_ref, format, dpi))
        return payload, "image/svg+xml", "cartogram.svg"

    monkeypatch.setattr(cartogram_authoring, "export_layout", export)
    registry = ToolRegistry()
    register_cartogram_tools(registry)
    result = await registry.dispatch("webgis_dorling_cartogram_export", {
        "layout_ref": "ref:layout-one", "format": "svg", "dpi": 96,
    }, session_id="export-owner")
    assert result["success"] is True, result
    assert calls == [("export-owner", "ref:layout-one", "svg", 96)]
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["bytes"] == len(payload)
    assert result["download_path"].startswith("/api/v1/cartogram/sessions/export-owner/")


def test_exploration_component_abi_accepts_real_category_and_range_options():
    from app.lib.cartography.component_abi import COMPONENT_ABI_META, validate_props
    from app.services.gis_harness.components import _FACTORY_BY_TYPE

    assert {"chartId", "kind", "field", "aggregation"} <= (
        COMPONENT_ABI_META["exploration_chart_panel"].props_schema.keys())
    for kind, bins in (("category", None), ("range", 10)):
        options = {"viewId": "view-one", "chartId": "chart-one", "kind": kind,
                   "field": "value", "aggregation": "count", "valueField": "",
                   "bins": bins, "layerId": "layer-one", "sourceRef": "ref:one",
                   "sourceRevision": 1, "title": "Real source"}
        assert validate_props("exploration_chart_panel", options) == []
    factory = _FACTORY_BY_TYPE["exploration_chart_panel"]("component-one")
    assert validate_props("exploration_chart_panel", factory.options) == []


@pytest.mark.parametrize("options,issue", [
    ({"kind": "pie"}, "props_invalid:kind:not_in_enum"),
    ({"aggregation": "median"}, "props_invalid:aggregation:not_in_enum"),
    ({"chartId": 4}, "props_invalid:chartId:expected_str"),
    ({"field": "x" * 65}, "props_invalid:field:too_long"),
    ({"sourceRevision": True}, "props_invalid:sourceRevision:bool_not_int"),
])
def test_exploration_component_abi_rejects_incorrect_option_types(options, issue):
    from app.lib.cartography.component_abi import validate_props

    assert issue in validate_props("exploration_chart_panel", options)


def test_exploration_abi_grounding_matches_real_writer_and_contract():
    import ast
    import inspect

    from app.lib.cartography.component_abi import COMPONENT_ABI_META, _SERVICE_WRITTEN_KEYS
    from app.services.feature_pages import FILTER_MAX_FIELD_LEN
    from app.services.linked_exploration import service
    from app.services.linked_exploration.contract import ChartSpec

    tree = ast.parse(inspect.getsource(service.apply_view_to_mapspec))
    option_dicts = [node.value for node in ast.walk(tree)
                    if isinstance(node, ast.keyword) and node.arg == "options"
                    and isinstance(node.value, ast.Dict)]
    assert len(option_dicts) == 1
    actual_keys = {key.value for key in option_dicts[0].keys if isinstance(key, ast.Constant)}
    schema = COMPONENT_ABI_META["exploration_chart_panel"].props_schema
    assert schema.keys() <= _SERVICE_WRITTEN_KEYS["exploration_chart_panel"] <= actual_keys
    assert schema["field"].max_len == FILTER_MAX_FIELD_LEN
    assert schema["valueField"].max_len == FILTER_MAX_FIELD_LEN
    # ChartSpec owns cross-field semantics, including nullable category bins.
    category = ChartSpec(chart_id="cat", kind="category", field="kind")
    assert category.kind == "category"
    with pytest.raises(ValueError):
        ChartSpec(chart_id="range", kind="range", field="value", bins=33)
