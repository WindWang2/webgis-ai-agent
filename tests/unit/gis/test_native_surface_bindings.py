"""Native data/presentation operations must have explicit executable bindings."""
import pytest

BINDINGS = [
    ("data.prepare", "data_preparation", "prepare_dataset"),
    ("layer.features.page", "layer_browse", "webgis_layer_features_page"),
    ("layer.aggregate", "layer_browse", "webgis_layer_aggregate"),
    ("layer.feature.detail", "layer_browse", "webgis_layer_feature_detail"),
    ("spatial.semantics.inspect", "spatial_semantics", "inspect_spatial_semantics"),
    ("cartography.scale.inspect", "cartography_scale_inspect", "webgis_inspect_map_scale_profile"),
    ("cartography.scale.preview", "cartography_scale_preview", "webgis_preview_scale_representation"),
    ("cartography.scale.apply", "cartography_scale_apply", "webgis_apply_scale_representation"),
]

@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry()
    init_tools(registry)
    return registry

@pytest.mark.parametrize("algorithm_id,capability,tool_name", BINDINGS)
def test_native_surface_binding_contract(registry, algorithm_id, capability, tool_name):
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.capability_registry import get_capability_registry
    algorithm = get_algorithm_registry().get(algorithm_id)
    assert algorithm.runtime_status == "native"
    assert algorithm.capabilities == [capability]
    assert algorithm.tool_candidates == [tool_name]
    assert get_capability_registry().get(capability).status == "native"
    assert capability in registry.descriptor(tool_name).capabilities
    assert algorithm.assumptions and algorithm.limitations


def test_layer_browse_defaults_to_bounded_page():
    from app.lib.gis.algorithm_resolver import get_algorithm_resolver
    result = get_algorithm_resolver().resolve("layer_browse")
    assert result.status == "resolved"
    assert result.algorithm == "layer.features.page"
