"""F06 — capability relation vocabulary v2 契约测试（ADR-0215 D6）.

覆盖：新关系声明面（descriptor 字段 + validate）、图投影（边 + kind 对
约束 + 自环）、解析面消费（alternative_to 替代源 + depends_on 降级）、
确定性。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.lib.gis.capability_registry import CapabilityDescriptor
from app.services.gis_harness.capability_graph import (
    KIND_CAPABILITY,
    KIND_TOOL,
    REL_ALTERNATIVE_TO,
    REL_CONSUMES,
    REL_DEPENDS_ON,
    CapabilityGraph,
    GraphEdge,
    GraphNode,
    GRAPH_RELATIONS,
    validate_graph,
)


def _descriptor(cid, **kw):
    return CapabilityDescriptor(id=cid, name=cid, **kw)


def _graph(nodes, edges):
    return CapabilityGraph(
        nodes={n.key: n for n in nodes},
        edges=edges,
        source_fingerprint="test",
        issues=[],
    )


class TestDescriptorDeclarations:
    def test_additive_defaults(self):
        d = _descriptor("cap_x")
        assert d.depends_on == []
        assert d.alternative_to == []

    def test_validate_flags_unknown_refs(self):
        from app.lib.gis.capability_registry import CapabilityRegistry

        reg = CapabilityRegistry()
        reg.register(_descriptor("cap_a", depends_on=["cap_ghost"]))
        reg.register(_descriptor("cap_b", alternative_to=["cap_ghost"]))
        reg.register(_descriptor("cap_c", depends_on=["cap_c"]))
        reg.register(_descriptor("cap_d", alternative_to=["cap_d"]))
        issues = reg.validate()
        assert any("cap_a" in i and "cap_ghost" in i for i in issues)
        assert any("cap_b" in i and "cap_ghost" in i for i in issues)
        assert any("cap_c: depends_on itself" in i for i in issues)
        assert any("cap_d: alternative_to itself" in i for i in issues)

    def test_valid_refs_pass(self):
        from app.lib.gis.capability_registry import CapabilityRegistry

        reg = CapabilityRegistry()
        reg.register(_descriptor("cap_a", depends_on=["cap_base"]))
        reg.register(_descriptor("cap_base"))
        reg.register(_descriptor("cap_b", alternative_to=["cap_base"]))
        assert reg.validate() == []


class TestGraphProjection:
    def test_vocabulary_extended(self):
        assert REL_CONSUMES in GRAPH_RELATIONS
        assert REL_DEPENDS_ON in GRAPH_RELATIONS
        assert REL_ALTERNATIVE_TO in GRAPH_RELATIONS

    def test_new_edges_projected_from_declarations(self):
        reg = _mini_registry()
        graph = _build_mini_graph(reg)
        cap_a_edges = [
            (e.relation, e.dst) for e in graph._edges  # noqa: SLF001
            if e.src == "capability:cap_a"
        ]
        assert ("depends_on", "capability:cap_dep") in cap_a_edges
        assert ("alternative_to", "capability:cap_alt") in cap_a_edges

    def test_kind_pair_and_self_loop_validation(self):
        g = _graph(
            [GraphNode("cap_x", KIND_CAPABILITY, "t"),
             GraphNode("cap_y", KIND_CAPABILITY, "t"),
             GraphNode("tool_x", KIND_TOOL, "t"),
             GraphNode("data", "artifact_type", "t")],
            [GraphEdge("capability:cap_x", REL_DEPENDS_ON, "tool:tool_x"),
             GraphEdge("capability:cap_x", REL_ALTERNATIVE_TO,
                       "capability:cap_x"),
             GraphEdge("tool:tool_x", REL_CONSUMES, "artifact_type:data"),
             GraphEdge("capability:cap_x", REL_ALTERNATIVE_TO,
                       "capability:cap_y")],
        )
        issues = validate_graph(g)
        codes = [i.code for i in issues]
        assert "invalid_relation_endpoint" in codes  # depends_on → tool
        assert "self_relation" in codes
        # 合法边不产 noise
        assert not any("consumes" in i.detail and i.code !=
                       "artifact_types_absent" for i in issues)

    def test_depends_on_cycle_audited(self):
        g = _graph(
            [GraphNode("cap_x", KIND_CAPABILITY, "t"),
             GraphNode("cap_y", KIND_CAPABILITY, "t")],
            [GraphEdge("capability:cap_x", REL_DEPENDS_ON,
                       "capability:cap_y"),
             GraphEdge("capability:cap_y", REL_DEPENDS_ON,
                       "capability:cap_x")],
        )
        issues = validate_graph(g)
        assert any(i.code == "cycle_detected" for i in issues)

    def test_alternative_to_symmetric_pair_not_cycle(self):
        """对称替代双边是规范形 —— 不得误报环。"""
        g = _graph(
            [GraphNode("cap_x", KIND_CAPABILITY, "t"),
             GraphNode("cap_y", KIND_CAPABILITY, "t")],
            [GraphEdge("capability:cap_x", REL_ALTERNATIVE_TO,
                       "capability:cap_y"),
             GraphEdge("capability:cap_y", REL_ALTERNATIVE_TO,
                       "capability:cap_x")],
        )
        issues = validate_graph(g)
        assert not any(i.code == "cycle_detected" for i in issues)


class TestResolutionConsumption:
    def test_alternative_to_extends_alternatives(self, monkeypatch):
        from app.services.gis_harness.capability_resolution import (
            _fallback_alternatives,
        )
        from app.services.gis_harness.qualification_v8 import (
            QualificationContext,
        )

        g = _graph(
            [GraphNode("cap_main", KIND_CAPABILITY, "t"),
             GraphNode("cap_fb", KIND_CAPABILITY, "t"),
             GraphNode("cap_alt", KIND_CAPABILITY, "t")],
            [GraphEdge("capability:cap_main", "fallback_to",
                       "capability:cap_fb"),
             GraphEdge("capability:cap_main", REL_ALTERNATIVE_TO,
                       "capability:cap_alt")],
        )
        monkeypatch.setattr(
            "app.services.gis_harness.capability_resolution.capability_status",
            lambda cid, ctx, **kw: ("eligible", [], []),
        )
        alts = _fallback_alternatives(
            "cap_main", QualificationContext(), g, session_id="s")
        ids = [a["capability"] for a in alts]
        assert ids == ["cap_fb", "cap_alt"]  # fallback 链在前，替代去重追加

    def test_depends_on_unavailable_degrades(self, monkeypatch):
        """依赖能力确认 INELIGIBLE → 本能力 degraded + 披露。"""
        from app.services.gis_harness import capability_resolution as cr
        from app.services.gis_harness.qualification_v8 import (
            QualificationContext,
            QualificationStatus,
        )

        g = _graph(
            [GraphNode("cap_main", KIND_CAPABILITY, "t"),
             GraphNode("cap_dep", KIND_CAPABILITY, "t")],
            [GraphEdge("capability:cap_main", REL_DEPENDS_ON,
                       "capability:cap_dep")],
        )

        def fake_status(cid, ctx, **kw):
            if cid == "cap_dep":
                return QualificationStatus.INELIGIBLE, [], []
            return QualificationStatus.ELIGIBLE, [], []

        monkeypatch.setattr(cr, "capability_status", fake_status)
        decision = cr._resolve_one(
            "cap_main", required=True, situation=QualificationContext(),
            graph=g, session_id="s", max_candidates=4)
        assert decision.status == QualificationStatus.DEGRADED
        assert "dependency_unavailable:cap_dep" in decision.why
        assert any("cap_dep" in h for h in decision.make_available)

    def test_unknown_dependency_not_adjudicated(self, monkeypatch):
        """依赖 unknown（图缺席）不降级 —— 缺席面不裁决。"""
        from app.services.gis_harness import capability_resolution as cr
        from app.services.gis_harness.qualification_v8 import (
            QualificationContext,
            QualificationStatus,
        )

        g = _graph(
            [GraphNode("cap_main", KIND_CAPABILITY, "t")],
            [GraphEdge("capability:cap_main", REL_DEPENDS_ON,
                       "capability:cap_ghost")],
        )
        monkeypatch.setattr(
            cr, "capability_status",
            lambda cid, ctx, **kw: (QualificationStatus.ELIGIBLE, [], []))
        decision = cr._resolve_one(
            "cap_main", required=True, situation=QualificationContext(),
            graph=g, session_id="s", max_candidates=4)
        assert decision.status == QualificationStatus.ELIGIBLE

    def test_eligible_dependency_no_degradation(self, monkeypatch):
        from app.services.gis_harness import capability_resolution as cr
        from app.services.gis_harness.qualification_v8 import (
            QualificationContext,
            QualificationStatus,
        )

        g = _graph(
            [GraphNode("cap_main", KIND_CAPABILITY, "t"),
             GraphNode("cap_ok", KIND_CAPABILITY, "t")],
            [GraphEdge("capability:cap_main", REL_DEPENDS_ON,
                       "capability:cap_ok")],
        )
        monkeypatch.setattr(
            cr, "capability_status",
            lambda cid, ctx, **kw: (QualificationStatus.ELIGIBLE, [], []))
        decision = cr._resolve_one(
            "cap_main", required=True, situation=QualificationContext(),
            graph=g, session_id="s", max_candidates=4)
        assert decision.status == QualificationStatus.ELIGIBLE


# ── helpers ──────────────────────────────────────────────────────────


def _mini_registry():
    """CapabilityRegistry：cap_a 声明 depends_on/alternative_to。"""
    from app.lib.gis.capability_registry import CapabilityRegistry

    reg = CapabilityRegistry()
    reg.register(_descriptor("cap_a", depends_on=["cap_dep"],
                             alternative_to=["cap_alt"]))
    reg.register(_descriptor("cap_dep"))
    reg.register(_descriptor("cap_alt"))
    return reg


def _build_mini_graph(cap_registry) -> CapabilityGraph:
    from app.services.gis_harness import capability_graph as cg

    class FakeAlgos:
        _by_id = {}

    with patch.object(cg, "_lazy_tool_registry", return_value=None), \
         patch("app.lib.gis.capability_registry.get_capability_registry",
               return_value=cap_registry), \
         patch("app.lib.gis.algorithm_registry.get_algorithm_registry",
               return_value=FakeAlgos):
        return cg.build_capability_graph()
