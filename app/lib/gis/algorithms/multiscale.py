"""Native multiscale presentation operations, separate from analysis artifacts.

Each operation has its own binding: inspection and preview never mutate, while
apply writes a new representation ref under the lifecycle CAS/idempotency contract.
"""
from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS = [
    AlgorithmDescriptor(
        id="cartography.scale.inspect", name="Multiscale inspect",
        category="presentation", priority=10, capabilities=["cartography_scale_inspect"],
        tool_candidates=["webgis_inspect_map_scale_profile"],
        deterministic=True, preferred_execution_policy="ASYNC",
        crs_class="CRS_AGNOSTIC", algorithm_family="cartography",
        random_seed_policy="deterministic",
        assumptions=["Scale representation uses source evidence and preserves original-source statistical semantics"],
        limitations=["Presentation operation, not an analysis artifact producer; apply requires session state and lifecycle CAS"],
        conformance_tests=["tests/unit/gis/test_native_surface_bindings.py::test_native_surface_binding_contract",
            "tests/unit/gis_harness/scale_profile/test_tool.py::TestDispatch::test_inspect_dispatch_happy"],
    ),
    AlgorithmDescriptor(
        id="cartography.scale.preview", name="Multiscale preview",
        category="presentation", priority=10, capabilities=["cartography_scale_preview"],
        tool_candidates=["webgis_preview_scale_representation"],
        deterministic=True, preferred_execution_policy="ASYNC",
        crs_class="CRS_AGNOSTIC", algorithm_family="cartography",
        random_seed_policy="deterministic",
        assumptions=["Scale representation uses source evidence and preserves original-source statistical semantics"],
        limitations=["Presentation operation, not an analysis artifact producer; apply requires session state and lifecycle CAS"],
        conformance_tests=["tests/unit/gis/test_native_surface_bindings.py::test_native_surface_binding_contract",
            "tests/unit/gis_harness/scale_profile/test_tool.py::TestDispatch::test_apply_preview_dispatch"],
    ),
    AlgorithmDescriptor(
        id="cartography.scale.apply", name="Multiscale apply",
        category="presentation", priority=10, capabilities=["cartography_scale_apply"],
        tool_candidates=["webgis_apply_scale_representation"],
        deterministic=False, preferred_execution_policy="ASYNC",
        crs_class="CRS_AGNOSTIC", algorithm_family="cartography",
        random_seed_policy="none",
        assumptions=["Scale representation uses source evidence and preserves original-source statistical semantics"],
        limitations=["Presentation operation, not an analysis artifact producer; apply requires session state and lifecycle CAS"],
        conformance_tests=["tests/unit/gis/test_native_surface_bindings.py::test_native_surface_binding_contract",
            "tests/unit/gis_harness/scale_profile/test_tool.py::TestDispatch::test_apply_commit_dispatch"],
    ),
]
