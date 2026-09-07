"""SDK 投影测试（ADR-0104 Wave 3-7）：algorithm / provider / cartography /
recipe 经 ExtensionContext 投影到权威 registry 并可零残留回滚。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import (
    DiagnosticCode,
    ExtensionPlatformError,
    has_errors,
)
from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.extensions_platform.sdk.algorithm import (
    AlgorithmExtensionSpec,
    NumericalSmokeCase,
    run_authoring_checks,
)
from app.extensions_platform.sdk.declarations import CartographyItemSpec
from app.extensions_platform.sdk.provider import ProviderExtensionSpec
from app.tools.registry import ToolRegistry

TOOL_MAIN = '''
from app.extensions_platform.sdk import ToolExtensionSpec


def _run(values: list) -> dict:
    return {"n": len(values)}


def activate(ctx):
    ctx.register_tool(ToolExtensionSpec(
        name="count_values", description="Count values.",
        func=_run, side_effect="pure", deterministic=True,
    ))
'''

ALGO_MAIN = '''
from app.extensions_platform.sdk.algorithm import AlgorithmExtensionSpec


def activate(ctx):
    ctx.register_algorithm(AlgorithmExtensionSpec(
        id="value_summary",
        name="Value Summary",
        capabilities=[],
        tool_candidates=["extdemo_count_values"],
        category="statistics",
    ))
'''

BAD_ALGO_MAIN = '''
from app.extensions_platform.sdk.algorithm import AlgorithmExtensionSpec


def activate(ctx):
    ctx.register_algorithm(AlgorithmExtensionSpec(
        id="ghost_algo",
        name="Ghost",
        capabilities=[],
        tool_candidates=["extdemo_missing_tool"],
    ))
'''


def _write(root: Path, name: str, main: str, manifest: dict) -> Path:
    ext_dir = root / name
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "manifest.json").write_text(json.dumps(manifest))
    (ext_dir / "main.py").write_text(main)
    return ext_dir


def _activate(root: Path, tool_registry: ToolRegistry | None = None):
    tool_registry = tool_registry or ToolRegistry()
    host = ExtensionHost(
        tool_registry=tool_registry,
        policy=HostPolicy(roots=(root,), grants={"extdemo.pack": frozenset({"network"})}),
    )
    host.discover()
    diagnostics = host.activate("extdemo.pack")
    return host, tool_registry, diagnostics


class TestAlgorithmProjection:
    def test_algorithm_projected_with_namespaced_id(self, tmp_path):
        _write(tmp_path, "extdemo-pack", TOOL_MAIN, {
            "id": "extdemo.pack", "name": "pack", "namespace": "extdemo",
            "version": "1.0.0", "entry_point": "main", "description": "x",
            "tools": [{"name": "count_values", "description": "x"}],
        })
        _write(tmp_path, "extdemo-algo", ALGO_MAIN, {
            "id": "extdemo.algo", "name": "algo", "namespace": "extdemo",
            "version": "1.0.0", "entry_point": "main", "description": "x",
            "algorithms": [{"id": "value_summary", "description": "x"}],
        })
        registry = ToolRegistry()
        host = ExtensionHost(tool_registry=registry, policy=HostPolicy(roots=(tmp_path,)))
        host.discover()
        for ext in ("extdemo.pack", "extdemo.algo"):
            assert not has_errors(host.activate(ext)), host.get_record(ext).diagnostics
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        algo = get_algorithm_registry().get("extdemo.value_summary")
        assert algo is not None
        assert algo.tool_candidates == ["extdemo_count_values"]
        # 回滚零残留。
        host.deactivate("extdemo.algo")
        assert get_algorithm_registry().get("extdemo.value_summary") is None

    def test_algorithm_referencing_missing_tool_fails(self, tmp_path):
        _write(tmp_path, "extdemo-algo", BAD_ALGO_MAIN, {
            "id": "extdemo.algo", "name": "algo", "namespace": "extdemo",
            "version": "1.0.0", "entry_point": "main", "description": "x",
            "algorithms": [{"id": "ghost_algo", "description": "x"}],
        })
        host = ExtensionHost(tool_registry=ToolRegistry(), policy=HostPolicy(roots=(tmp_path,)))
        host.discover()
        diagnostics = host.activate("extdemo.algo")
        assert any(d.code is DiagnosticCode.REGISTRY_PROJECTION_COLLISION for d in diagnostics)

    def test_authoring_harness_smoke(self):
        def _impl(radius: float) -> dict:
            return {"area": 3.14159 * radius ** 2}

        spec = AlgorithmExtensionSpec(
            id="circle_area", name="Circle Area",
            smoke_cases=[
                NumericalSmokeCase(arguments={"radius": 1.0}, expect_key="area",
                                   expect_value=3.14159, tolerance=1e-5),
            ],
        )
        diagnostics = run_authoring_checks(spec, _impl)
        assert not has_errors(diagnostics)
        spec_bad = AlgorithmExtensionSpec(
            id="circle_area", name="x",
            smoke_cases=[NumericalSmokeCase(arguments={"radius": 1.0},
                                            expect_key="area", expect_value=42.0)],
        )
        diagnostics = run_authoring_checks(spec_bad, _impl)
        assert has_errors(diagnostics)


class TestProviderProjection:
    def test_provider_projection_and_rollback(self, tmp_path):
        main = '''
from app.extensions_platform.sdk import ProviderExtensionSpec
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter


class DemoAdapter(GeospatialDataSourceAdapter):
    pass


def activate(ctx):
    ctx.register_data_provider(ProviderExtensionSpec(
        source_type="demo_src", description="Demo.", adapter_cls=DemoAdapter,
        requires_network=False,
    ))
'''
        _write(tmp_path, "extdemo-pack", main, {
            "id": "extdemo.pack", "name": "pack", "namespace": "extdemo",
            "version": "1.0.0", "entry_point": "main", "description": "x",
            "data_providers": [{"source_type": "demo_src", "description": "x"}],
        })
        host, _registry, diagnostics = _activate(tmp_path)
        assert not has_errors(diagnostics), diagnostics
        from app.services.data_fabric.registry import get_registry, resolve_adapter_spec

        spec = resolve_adapter_spec("extdemo_demo_src")
        assert spec.canonical == "extdemo_demo_src"
        assert spec.adapter_cls is not None
        host.deactivate("extdemo.pack")
        assert not get_registry().is_supported("extdemo_demo_src")

    def test_provider_network_requires_declared_permission(self, tmp_path):
        main = '''
from app.extensions_platform.sdk import ProviderExtensionSpec
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter


class DemoAdapter(GeospatialDataSourceAdapter):
    pass


def activate(ctx):
    ctx.register_data_provider(ProviderExtensionSpec(
        source_type="net_src", description="Net.", adapter_cls=DemoAdapter,
        requires_network=True,
    ))
'''
        _write(tmp_path, "extdemo-pack", main, {
            "id": "extdemo.pack", "name": "pack", "namespace": "extdemo",
            "version": "1.0.0", "entry_point": "main", "description": "x",
            "data_providers": [{"source_type": "net_src", "description": "x"}],
        })
        host, _registry, diagnostics = _activate(tmp_path)
        assert has_errors(diagnostics)
        assert any(
            d.code is DiagnosticCode.PERMISSION_DECLARATION_INVALID for d in diagnostics
        )


class TestCartographyProjection:
    def test_planned_component_projection(self, tmp_path):
        main = '''
from app.extensions_platform.sdk import CartographyItemSpec


def activate(ctx):
    ctx.register_cartography_item(CartographyItemSpec(
        kind="component", id="note_panel", runtime_status="planned",
        payload={
            "type": "extdemo_note_panel", "category": "annotation",
            "cardinality": "zero_or_one", "priority": 10,
            "required_context": [], "states": ["visible", "hidden"],
            "collision_class": "panel", "accessibility": {"role": "group"},
        },
        export_behavior="degraded",
        degradation_policy="omit_with_disclosure",
    ))
'''
        _write(tmp_path, "extdemo-pack", main, {
            "id": "extdemo.pack", "name": "pack", "namespace": "extdemo",
            "version": "1.0.0", "entry_point": "main", "description": "x",
            "cartography_items": [{"kind": "component", "id": "note_panel"}],
        })
        host, _registry, diagnostics = _activate(tmp_path)
        if diagnostics and has_errors(diagnostics):
            pytest.fail(f"activation failed: {diagnostics}")
        from app.lib.cartography.component_registry import get_component_registry

        comp = get_component_registry().get("extdemo_note_panel")
        assert comp is not None
        assert comp.runtime_status == "planned"
        host.deactivate("extdemo.pack")
        assert get_component_registry().get("extdemo_note_panel") is None


class TestWorkflowPackProjection:
    def test_recipe_projection_and_rollback(self, tmp_path):
        main = '''
from app.extensions_platform.sdk import WorkflowPackSpec


def _build_recipe():
    from app.services.gis_harness.recipes import CartographyRecipe

    return CartographyRecipe(
        id="extdemo_demo_overview",
        name="ExtDemo Overview",
        description="Example extension recipe.",
        intent_tasks=["poi_distribution"],
        preferred_analysis=[],
    )


def activate(ctx):
    ctx.register_workflow_pack(WorkflowPackSpec(
        pack_id="overview", recipes=[_build_recipe()],
    ))
'''
        _write(tmp_path, "extdemo-pack", main, {
            "id": "extdemo.pack", "name": "pack", "namespace": "extdemo",
            "version": "1.0.0", "entry_point": "main", "description": "x",
            "workflow_packs": [{"pack_id": "overview", "recipe_count": 1}],
        })
        host, _registry, diagnostics = _activate(tmp_path)
        assert not has_errors(diagnostics), diagnostics
        from app.services.gis_harness.recipes import get_recipe_registry

        recipe = get_recipe_registry().get("extdemo_demo_overview")
        assert recipe is not None
        host.deactivate("extdemo.pack")
        assert get_recipe_registry().get("extdemo_demo_overview") is None

    def test_unnamespaced_recipe_id_rejected(self, tmp_path):
        main = '''
from app.extensions_platform.sdk import WorkflowPackSpec


def _build_recipe():
    from app.services.gis_harness.recipes import CartographyRecipe

    return CartographyRecipe(id="no_prefix", name="Bad")


def activate(ctx):
    ctx.register_workflow_pack(WorkflowPackSpec(pack_id="p", recipes=[_build_recipe()]))
'''
        _write(tmp_path, "extdemo-pack", main, {
            "id": "extdemo.pack", "name": "pack", "namespace": "extdemo",
            "version": "1.0.0", "entry_point": "main", "description": "x",
            "workflow_packs": [{"pack_id": "p", "recipe_count": 1}],
        })
        host, _registry, diagnostics = _activate(tmp_path)
        assert has_errors(diagnostics)
        assert any(d.code is DiagnosticCode.MANIFEST_INVALID for d in diagnostics)
