"""Unit tests verifying Knowledge Registries & Map Models Parity fixes (#956-#960)."""
from app.lib.gis.algorithm_resolver import AlgorithmResolver
from app.lib.cartography.model_library import get_map_model
from app.lib.gis.algorithm_registry import AlgorithmRegistry, AlgorithmDescriptor, get_algorithm_registry
from app.lib.gis.capability_registry import CapabilityRegistry, CapabilityDescriptor


def test_algorithm_resolver_fallback_cycle_detection():
    """AlgorithmResolver detects and terminates circular fallback loops safely."""
    cap_reg = CapabilityRegistry()
    algo_reg = AlgorithmRegistry()

    # Create circular capabilities A -> B -> A with algorithms that are ineligible
    cap_reg.register(CapabilityDescriptor(
        id="cap_a", name="Cap A", category="general", status="native",
        fallback_capabilities=["cap_b"],
    ))
    cap_reg.register(CapabilityDescriptor(
        id="cap_b", name="Cap B", category="general", status="native",
        fallback_capabilities=["cap_a"],
    ))

    algo_reg.register(AlgorithmDescriptor(
        id="algo_a", name="Algo A", category="general",
        capabilities=["cap_a"], tool_candidates=["tool_a"],
        min_features=100,
    ))
    algo_reg.register(AlgorithmDescriptor(
        id="algo_b", name="Algo B", category="general",
        capabilities=["cap_b"], tool_candidates=["tool_b"],
        min_features=100,
    ))

    resolver = AlgorithmResolver(capabilities=cap_reg, algorithms=algo_reg)
    res = resolver.resolve("cap_a", profile={"featureCount": 5})
    assert res.status == "unavailable"
    assert "insufficient_features" in res.reason or "no_eligible_algorithm" in res.reason


def test_flow_od_arc_model_geometry_and_artifact_parity():
    """flow_od_arc map model accepts line/point geometry and line_feature_set artifact."""
    model = get_map_model("flow_od_arc")
    assert model is not None
    assert "line" in model.geometry_kinds
    assert "line_feature_set" in (model.accepted_artifact_types or [])


def test_terrain_aspect_algorithm_tool_resolution():
    """terrain.aspect algorithm is registered and points to valid native tool candidates."""
    algo_reg = get_algorithm_registry()
    aspect_algo = algo_reg.get("terrain.aspect")
    assert aspect_algo is not None
    assert "compute_terrain" in aspect_algo.tool_candidates
