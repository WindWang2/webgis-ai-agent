"""Issue #2123: real presentation ABI and scientific routing regressions."""
import pytest


# Explicit reviewed handlers; this is not derived from the descriptor under test.
LOCAL_PRESENTATION_TOOLS = (
    "add_marker",
    "alias_layer",
    "apply_layer_filter",
    "apply_layer_style",
    "apply_template",
    "brief_create_draft",
    "brief_export",
    "brief_freeze_release",
    "brief_inspect_evidence",
    "brief_preflight",
    "brief_update_block",
    "clear_annotations",
    "combine_map_theme",
    "create_3d_extrusion_map",
    "create_thematic_map",
    "display_layer",
    "export_batch_maps",
    "export_thematic_map",
    "finalize_display",
    "fly_to_location",
    "inventory_layers",
    "list_templates",
    "measure_area",
    "measure_distance",
    "remove_layer",
    "reorder_layer",
    "reset_map_view",
    "set_layer_status",
    "set_map_view",
    "switch_base_layer",
    "update_layer_appearance",
    "webgis_apply_composition",
    "webgis_apply_scale_portrayal",
    "webgis_apply_scale_representation",
    "webgis_cartography_status",
    "webgis_checkpoint",
    "webgis_compile_maplibre",
    "webgis_discover_components",
    "webgis_inspect_map_scale_profile",
    "webgis_inspect_scale_portrayal",
    "webgis_layer_remove",
    "webgis_layer_upsert",
    "webgis_layout_set",
    "webgis_map_combine",
    "webgis_plan_component_replace",
    "webgis_preview_scale_representation",
    "webgis_project_init",
    "webgis_rollback",
    "webgis_source_profile",
    "webgis_state_get",
    "webgis_validate",
    "webgis_view_set",
    "zoom_to_bbox",
    "zoom_to_layer",
)


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    init_tools(registry)
    return registry


@pytest.mark.parametrize("name", LOCAL_PRESENTATION_TOOLS)
def test_local_presentation_handlers_disclose_no_external_network(registry, name):
    metadata = registry.metadata(name)
    assert metadata["network"] is False
    assert metadata["side_effect"] != "unclassified"
    assert metadata["deterministic"] is not None
    assert metadata["result_size_policy"] != "unknown"


def test_runtime_renderer_discloses_network_separately_from_compile(registry):
    # This handler launches the runtime validator: rendering may fetch basemap
    # tiles/fonts, unlike CLI compilation, which only writes local style files.
    assert registry.metadata("webgis_runtime_validate")["network"] is True
    assert registry.metadata("webgis_compile_maplibre")["network"] is False


def test_service_area_prefers_geometry_producer_over_coverage_comparator():
    from app.lib.gis.algorithm_resolver import AlgorithmResolver
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    decision = AlgorithmResolver().resolve(
        "service_area",
        available_tools={"network_service_area", "network_service_coverage_consistency"},
    )
    assert decision.status == "resolved"
    assert decision.tool == "network_service_area"
    algorithm = get_algorithm_registry().get(decision.algorithm)
    assert algorithm.output_artifact_type == "service_area"


def test_coverage_comparator_does_not_offer_non_equivalent_fallback(registry):
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    algorithm = get_algorithm_registry().get("network.service_coverage.consistency")
    assert algorithm.output_artifact_type == "stats_table"
    assert not algorithm.fallback_algorithms
    assert not registry.metadata("network_service_coverage_consistency").get("fallback_tool")


def test_exploration_export_discloses_both_persisted_references(registry):
    metadata = registry.metadata("webgis_exploration_export")
    assert metadata["result_size_policy"] == "ref_offload"
    assert set(metadata["produced_refs"]) == {"artifact", "manifest"}
