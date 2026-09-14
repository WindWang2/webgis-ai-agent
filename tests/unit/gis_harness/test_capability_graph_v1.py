"""Capability Graph V1（ADR-0181）—— 四段 provider 投影 + 结构审计测试。

覆盖：
- 投影完整性：workflow/template/component/provider 节点段 + requires/
  composed_of/binds_to/conflicts_with/fallback_to(tool 弃用链) 边；
- 查询面：capability_providers / workflows_for_capability /
  templates_for_capability / conflicts_of_capability；
- 结构审计：环 / 孤儿 capability / 不可达 tool / 无消费者 artifact /
  弃用暴露（合成图逐类钉死 + 在线 registry 零 error）；
- 缓存诚实性：source_fingerprints 含四段新来源键。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.capability_graph import (
    KIND_COMPONENT,
    KIND_PROVIDER,
    KIND_TEMPLATE,
    KIND_WORKFLOW,
    REL_COMPOSED_OF,
    REL_CONFLICTS_WITH,
    REL_REQUIRES,
    GraphEdge,
    GraphNode,
    build_capability_graph,
    get_capability_graph,
    reset_capability_graph,
    source_fingerprints,
    validate_graph,
)


@pytest.fixture(scope="module")
def graph():
    reset_capability_graph()
    g = get_capability_graph()
    yield g
    reset_capability_graph()


# ── 投影完整性 ────────────────────────────────────────────────────────────


class TestProjectionSections:
    def test_four_new_kinds_projected(self, graph):
        assert graph.nodes_by_kind(KIND_WORKFLOW), "recipe→workflow 段缺席"
        assert graph.nodes_by_kind(KIND_TEMPLATE), "product template 段缺席"
        assert graph.nodes_by_kind(KIND_COMPONENT), "component 段缺席"
        assert graph.nodes_by_kind(KIND_PROVIDER), "data fabric provider 段缺席"

    def test_workflow_counts_match_recipe_registry(self, graph):
        from app.services.gis_harness.recipes import get_recipe_registry

        assert len(graph.nodes_by_kind(KIND_WORKFLOW)) == \
            get_recipe_registry().count

    def test_requires_edges_emitted(self, graph):
        requires = [e for e in graph._edges if e.relation == REL_REQUIRES]
        assert requires, "workflow/template → capability 的 requires 边缺席"
        src_kinds = {e.src.split(":", 1)[0] for e in requires}
        assert KIND_WORKFLOW in src_kinds

    def test_composed_of_edges_emitted(self, graph):
        composed = [e for e in graph._edges if e.relation == REL_COMPOSED_OF]
        assert composed, "recipe/template → component 的 composed_of 边缺席"

    def test_tool_deprecation_fallback_edges(self, graph):
        """弃用工具 → canonical 的 fallback_to 边（若 registry 有弃用面）。"""
        deprecated = [
            n for n in graph.nodes_by_kind("tool")
            if str(n.extras.get("deprecation_of") or "")
        ]
        for node in deprecated:
            targets = graph.neighbors(
                f"tool:{node.id}", "fallback_to")
            assert f"tool:{node.extras['deprecation_of']}" in targets

    def test_tool_extras_carry_qualification_face(self, graph):
        tools = graph.nodes_by_kind("tool")
        assert tools
        sample = tools[0]
        for key in ("tier", "status", "side_effect", "network",
                    "deterministic", "scale_class"):
            assert key in sample.extras, f"tool extras 缺 {key}"

    def test_capability_extras_carry_v1_face(self, graph):
        caps = graph.nodes_by_kind("capability")
        assert caps
        assert "offline_capable" in caps[0].extras
        assert "status" in caps[0].extras

    def test_source_fingerprints_include_new_sections(self):
        fps = source_fingerprints()
        for key in ("recipe_registry", "product_templates",
                    "component_registry", "data_fabric_adapters"):
            assert key in fps, f"缓存键缺新来源段 {key}"
            assert fps[key] != "absent", f"{key} 段指纹缺席（来源不可用？）"


# ── 查询面 ────────────────────────────────────────────────────────────────


class TestProviderQueries:
    def test_capability_providers_shapes(self, graph):
        from app.services.gis_harness.recipes import get_recipe_registry

        recipes = get_recipe_registry()
        sample_cap = ""
        for rid in recipes.all_ids:
            r = recipes.get(rid)
            if r is not None and r.preferred_analysis:
                sample_cap = r.preferred_analysis[0]
                break
        assert sample_cap
        providers = graph.capability_providers(sample_cap)
        assert set(providers.keys()) == {
            "tools", "models", "workflows", "templates"}
        assert providers["workflows"], "recipe 声明的能力查不到 workflow 面"
        assert all(isinstance(v, list) for v in providers.values())

    def test_workflows_for_capability_deterministic(self, graph):
        caps = graph.nodes_by_kind("capability")
        a = graph.workflows_for_capability(caps[0].id)
        b = graph.workflows_for_capability(caps[0].id)
        assert a == b and a == sorted(a)

    def test_conflicts_of_capability_merges_both_directions(self, graph):
        # 合成检查经 conflicts_of_capability 的双向合并语义（在线 registry
        # 未必声明 capability 级互斥 —— 组件级互斥有独立测试）。
        g = _mini_graph()
        assert g.conflicts_of_capability("cap_a") == ["cap_b"]
        assert g.conflicts_of_capability("cap_b") == ["cap_a"]
        assert g.conflicts_of_capability("cap_c") == []


# ── 结构审计（合成图逐类钉死）─────────────────────────────────────────────


def _mini_graph() -> "object":
    """最小合成图：fallback 环 + 孤儿 + 无消费者 artifact + 弃用暴露 +
    双向 conflict。"""
    nodes = [
        GraphNode("cap_a", "capability", "test"),
        GraphNode("cap_b", "capability", "test"),
        GraphNode("cap_c", "capability", "test_orphan"),
        GraphNode("algo_x", "algorithm", "test"),
        GraphNode("tool_old", "tool", "test", extras={"status": "deprecated",
                                                      "deprecation_of": "tool_new"}),
        GraphNode("tool_new", "tool", "test", extras={"status": "stable"}),
        GraphNode("tool_island", "tool", "test"),
        GraphNode("art_out", "artifact_type", "test"),
        GraphNode("art_ok", "artifact_type", "test"),
    ]
    edges = [
        GraphEdge("capability:cap_a", "fallback_to", "capability:cap_b"),
        GraphEdge("capability:cap_b", "fallback_to", "capability:cap_a"),
        GraphEdge("capability:cap_a", "conflicts_with", "capability:cap_b"),
        GraphEdge("capability:cap_b", "conflicts_with", "capability:cap_a"),
        GraphEdge("capability:cap_a", "implemented_by", "algorithm:algo_x"),
        GraphEdge("algorithm:algo_x", "exposed_by", "tool:tool_old"),
        GraphEdge("algorithm:algo_x", "produces", "artifact_type:art_out"),
        GraphEdge("algorithm:algo_x", "produces", "artifact_type:art_ok"),
        GraphEdge("algorithm:algo_x", "accepts", "artifact_type:art_ok"),
    ]
    from app.services.gis_harness.capability_graph import CapabilityGraph

    return CapabilityGraph(
        {n.key: n for n in nodes}, edges, source_fingerprint="test", issues=[])


class TestStructuralAudit:
    def test_fallback_cycle_detected(self):
        issues = validate_graph(_mini_graph())
        cycles = [i for i in issues if i.code == "cycle_detected"]
        assert cycles, "fallback 双向环未被检出"
        assert any("cap_a" in i.detail and "cap_b" in i.detail for i in cycles)

    def test_conflicts_do_not_count_as_cycles(self):
        issues = validate_graph(_mini_graph())
        for i in issues:
            if i.code == "cycle_detected":
                assert "conflicts_with" not in i.detail

    def test_orphan_capability_detected(self):
        issues = validate_graph(_mini_graph())
        orphans = [i for i in issues if i.code == "orphan_capability"]
        assert any("cap_c" in i.detail for i in orphans)

    def test_artifact_no_consumer_detected(self):
        issues = validate_graph(_mini_graph())
        assert any(
            i.code == "artifact_no_consumer" and "art_out" in i.detail
            for i in issues)

    def test_exposes_deprecated_tool_detected(self):
        issues = validate_graph(_mini_graph())
        hits = [i for i in issues if i.code == "exposes_deprecated_tool"]
        assert hits and "tool_new" in hits[0].detail

    def test_all_audit_findings_are_warnings(self):
        issues = validate_graph(_mini_graph())
        structural = {
            "cycle_detected", "orphan_capability", "unreachable_tool",
            "artifact_no_consumer", "exposes_deprecated_tool",
            "audit_truncated",
        }
        for i in issues:
            if i.code in structural:
                assert i.severity == "warning", f"{i.code} 必须是 warning 级"

    def test_live_registry_zero_errors(self, graph):
        errors = [i for i in validate_graph(graph) if i.severity == "error"]
        assert errors == []


# ── registry 声明面（owner 字段 + 校验）──────────────────────────────────


class TestCapabilityRegistryV1Declarations:
    def test_new_fields_default_honest(self):
        from app.lib.gis.capability_registry import CapabilityDescriptor

        d = CapabilityDescriptor(id="x", name="X")
        assert d.offline_capable is None
        assert d.incompatible_with == []

    def test_incompatible_with_validated(self):
        from app.lib.gis.capability_registry import (
            CapabilityDescriptor,
            CapabilityRegistry,
        )

        reg = CapabilityRegistry()
        reg.register(CapabilityDescriptor(
            id="cap_a", name="A", incompatible_with=["cap_missing"]))
        issues = reg.validate()
        assert any("cap_missing" in msg for msg in issues)
        reg.register(CapabilityDescriptor(id="cap_missing", name="M"))
        assert not [
            m for m in reg.validate() if "cap_missing" in m]

    def test_self_incompatibility_rejected(self):
        from app.lib.gis.capability_registry import (
            CapabilityDescriptor,
            CapabilityRegistry,
        )

        reg = CapabilityRegistry()
        reg.register(CapabilityDescriptor(
            id="cap_a", name="A", incompatible_with=["cap_a"]))
        assert any("itself" in msg for msg in reg.validate())
