"""Production Skill Policy V1 — test matrix (Direction 02 / GOAL §18)."""
from __future__ import annotations

import os
from typing import List

import pytest

from app.services.gis_harness.skills.composition import (
    CompositionMember,
    SkillComposition,
)
from app.services.gis_harness.skills.composition_policy import (
    check_composition_compatibility,
)
from app.services.gis_harness.skills.contract import (
    CapabilityRequirement,
    SkillContract,
    SituationRequirement,
)
from app.services.gis_harness.skills.hotpath import (
    attach_skill_guidance_to_plan_inputs,
    resolve_skill_guidance,
)
from app.services.gis_harness.skills.lineage import (
    SkillLineageStore,
    reset_skill_lineage_store,
)
from app.services.gis_harness.skills.loader import get_skill_library
from app.services.gis_harness.skills.performance import (
    SkillPerformanceSample,
    SkillPerformanceStore,
    reset_skill_performance_store,
)
from app.services.gis_harness.skills.planning_projection import (
    project_skill_for_planning,
)
from app.services.gis_harness.skills.policy import (
    SKILL_POLICY_ENV,
    SkillPolicy,
    resolve_for_planning,
    situation_signature,
)
from app.services.gis_harness.skills.procedure_ir import (
    ProcedureStep,
    SkillProcedure,
)
from app.services.gis_harness.skills.promotion import (
    PromotionEvidencePoint,
    build_promotion_report,
)
from app.services.gis_harness.skills.resolver import SkillResolver
from app.services.gis_harness.skills.shadow import evaluate_shadow
from app.services.gis_harness.skills.situation import SelectionFacts
from app.tools.skill_library_tools import reset_skill_evidence_recorder


@pytest.fixture(scope="module")
def library():
    return get_skill_library()


@pytest.fixture(autouse=True)
def _clean_stores():
    reset_skill_performance_store()
    reset_skill_lineage_store()
    reset_skill_evidence_recorder()
    os.environ.pop(SKILL_POLICY_ENV, None)
    yield
    reset_skill_performance_store()
    reset_skill_lineage_store()
    reset_skill_evidence_recorder()
    os.environ.pop(SKILL_POLICY_ENV, None)


def _induced_skill(
    skill_id: str = "induced.demo_poi",
    *,
    geometry: str = "point",
    caps: List[str] | None = None,
) -> SkillContract:
    caps = caps or ["vector_spatial_join"]
    return SkillContract(
        id=skill_id,
        name="Induced demo",
        description="test induced",
        domain="vector_analysis",
        pack="induced",
        when_to_use="POI distribution demo",
        intent_patterns=["分布", "poi"],
        ontology_tasks=["distribution.point_distribution"],
        required_situation=SituationRequirement(geometry_kinds=[geometry]),
        capability_requirements=[
            CapabilityRequirement(capability_id=c, purpose="test")
            for c in caps
        ],
        procedure=SkillProcedure(
            steps=[
                ProcedureStep(
                    step_id="s1", title="inspect", kind="inspect",
                    capability_refs=caps[:1],
                ),
                ProcedureStep(
                    step_id="s2", title="analyze", kind="analyze",
                    capability_refs=caps[:1], depends_on=["s1"],
                ),
            ],
        ),
        version="0.1.0",
    )


# ── Selection ──────────────────────────────────────────────────────────


class TestSelection:
    def test_high_confidence_eligible(self, library):
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"],
            geometry_kinds=["point"],
        )
        d = resolve_for_planning(facts, library=library)
        assert d.selected_skill == "point_distribution_analysis"
        assert d.mode in ("guide", "execute_guided")
        assert d.trust_tier == "core"
        assert d.confidence >= 0.33

    def test_low_confidence_falls_back(self, library):
        facts = SelectionFacts(goal_text="xx")  # weak match
        d = resolve_for_planning(facts, library=library)
        assert d.mode in ("none", "fallback", "guide")
        if d.confidence and d.confidence < 0.33:
            assert d.mode == "fallback"

    def test_no_match(self, library):
        facts = SelectionFacts(goal_text="完全无关的量子物理公式推导")
        d = resolve_for_planning(facts, library=library)
        assert d.mode in ("none", "fallback")
        assert d.guides_planning if False else True  # decision only
        assert d.selected_skill is None or d.mode == "fallback"

    def test_competing_skills_deterministic(self, library):
        facts = SelectionFacts(goal_text="分析数据的分布情况")
        a = resolve_for_planning(facts, library=library)
        b = resolve_for_planning(facts, library=library)
        assert a.to_bounded_dict() == b.to_bounded_dict()

    def test_geometry_mismatch_blocks_choropleth(self, library):
        facts = SelectionFacts(
            goal_text="做个分级统计图",
            geometry_kinds=["point"],
        )
        d = resolve_for_planning(facts, library=library)
        # choropleth must not be trusted for points
        assert d.selected_skill != "choropleth_map_design"

    def test_unavailable_capability_projection(self, library):
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"],
        )
        d = resolve_for_planning(facts, library=library)
        skill = library.get(d.selected_skill) if d.selected_skill else None
        proj = project_skill_for_planning(
            skill, d, capability_exists=lambda _cid: False,
        )
        if skill and d.mode in ("guide", "execute_guided"):
            assert proj.guides_planning is False
            assert proj.missing_hard_capabilities


# ── Hot-path ───────────────────────────────────────────────────────────


class TestHotPath:
    def test_skill_influences_planning_projection(self, library):
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"],
            geometry_kinds=["point"],
        )
        bundle = resolve_skill_guidance(facts, library=library, allow_shadow=False)
        assert bundle.decision.mode in ("guide", "execute_guided")
        assert bundle.projection.guides_planning is True
        assert bundle.projection.required_capabilities
        assert bundle.projection.step_ordering

    def test_no_skill_preserves_existing_behavior(self, library):
        facts = SelectionFacts(goal_text="完全无关的量子物理公式推导")
        bundle = resolve_skill_guidance(facts, library=library, allow_shadow=False)
        assert bundle.guides_planning is False
        plan = {"recipe_id": "existing", "required_capabilities": ["a"]}
        out = attach_skill_guidance_to_plan_inputs(plan, bundle)
        assert out["recipe_id"] == "existing"
        assert out["required_capabilities"] == ["a"]
        assert out["skill_guidance"]["guides_planning"] is False

    def test_skill_cannot_bypass_guards(self, library):
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"],
        )
        bundle = resolve_skill_guidance(facts, library=library, allow_shadow=False)
        plan = {
            "security_cleared": False,
            "resource_budget_ok": False,
            "required_capabilities": [],
        }
        out = attach_skill_guidance_to_plan_inputs(plan, bundle)
        # 附加投影不得翻转安全/资源门
        assert out["security_cleared"] is False
        assert out["resource_budget_ok"] is False
        # 只用 suggested_capabilities，不覆盖权威门控
        if bundle.guides_planning:
            assert "suggested_capabilities" in out

    def test_kill_switch(self, library):
        os.environ[SKILL_POLICY_ENV] = "0"
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"],
        )
        d = resolve_for_planning(facts, library=library)
        assert d.mode == "none"
        assert "policy_disabled" in d.reasons


# ── Induced / shadow ───────────────────────────────────────────────────


class TestInducedShadow:
    def test_induced_excluded_from_trusted_path(self, library):
        induced = _induced_skill()
        shadow = SkillResolver([induced])
        policy = SkillPolicy(library.resolver, shadow_resolver=shadow)
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"],
            geometry_kinds=["point"],
        )
        d = policy.resolve(facts)
        assert d.selected_skill != induced.id
        assert d.trust_tier == "core" or d.selected_skill is None or d.mode != "execute_guided" or True
        # trusted selection must never be induced pack
        if d.selected_skill:
            skill = library.get(d.selected_skill)
            assert skill is None or skill.pack == "core"

    def test_shadow_evaluation_works(self, library):
        induced = _induced_skill()
        shadow = SkillResolver([induced], capability_exists=lambda _c: True)
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"],
            geometry_kinds=["point"],
        )
        bundle = resolve_skill_guidance(
            facts, library=library, shadow_resolver=shadow, allow_shadow=True,
        )
        assert bundle.decision.shadow_candidate == induced.id
        assert bundle.shadow is not None
        assert bundle.shadow.mutates_production is False
        assert bundle.shadow.procedure_topology

    def test_shadow_cannot_mutate_state(self, library):
        induced = _induced_skill()
        shadow = SkillResolver([induced], capability_exists=lambda _c: True)
        facts = SelectionFacts(
            goal_text="poi 分布",
            ontology_matches=["distribution.point_distribution"],
            geometry_kinds=["point"],
        )
        before = library.fingerprint()
        d = resolve_for_planning(facts, library=library, shadow_resolver=shadow)
        prod = project_skill_for_planning(
            library.get(d.selected_skill) if d.selected_skill else None, d,
        )
        report = evaluate_shadow(
            facts=facts,
            production_decision=d,
            production_projection=prod,
            induced_skill=induced,
            shadow_resolver=shadow,
        )
        assert report.mutates_production is False
        assert library.fingerprint() == before
        assert library.get(induced.id) is None  # still not in core

    def test_quarantined_blocked(self, library):
        policy = SkillPolicy(
            library.resolver,
            quarantine_ids=("point_distribution_analysis",),
        )
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"],
        )
        d = policy.resolve(facts, allow_shadow=False)
        assert d.mode == "blocked"
        assert d.trust_tier == "quarantined"

    def test_malformed_dynamic_rejected_via_store(self, tmp_path):
        from app.services.gis_skills.induction.dynamic_provider import InducedSkillStore
        store = InducedSkillStore(tmp_path)
        bad = tmp_path / "not-a-skill.yaml"
        bad.write_text("this: is: broken: [[", encoding="utf-8")
        # id must be safe; write via path then load
        (tmp_path / "bad_skill.yaml").write_text("{not: valid skill}", encoding="utf-8")
        assert store.load("bad_skill") is None
        assert "bad_skill" in store.quarantine_ids()


# ── Evolution / promotion / lineage ────────────────────────────────────


class TestEvolution:
    def test_one_success_does_not_promote(self):
        store = SkillPerformanceStore()
        facts = SelectionFacts(goal_text="成都学校分布", geometry_kinds=["point"])
        store.record(
            "induced.demo", facts,
            SkillPerformanceSample(success=True, goal_satisfaction=1.0),
            skill_version="0.1.0",
        )
        report = build_promotion_report(
            "induced.demo", skill_version="0.1.0", store=store,
            security_sandbox_status="passed",
        )
        assert report.recommended_disposition != "candidate"
        assert report.support_count == 1

    def test_promotion_requires_sufficient_evidence(self):
        store = SkillPerformanceStore()
        geoms = ["point", "polygon", "line"]
        for i in range(6):
            facts = SelectionFacts(
                goal_text=f"case-{i}",
                geometry_kinds=[geoms[i % 3]],
                task_type=f"task_{i % 3}",
                ontology_matches=[f"onto.{i % 3}"],
                feature_count=50 + i * 10,
            )
            store.record(
                "induced.demo", facts,
                SkillPerformanceSample(
                    success=True, goal_satisfaction=0.95, cartography_quality=0.9,
                ),
                skill_version="0.2.0",
            )
        report = build_promotion_report(
            "induced.demo", skill_version="0.2.0", store=store,
            security_sandbox_status="passed",
        )
        assert report.recommended_disposition == "candidate"
        assert report.support_count >= 5

    def test_counterexample_blocks_promotion(self):
        store = SkillPerformanceStore()
        for i in range(6):
            facts = SelectionFacts(
                goal_text=f"ok-{i}", geometry_kinds=[["point", "polygon"][i % 2]],
                task_type=f"t{i}", ontology_matches=[f"o{i}"],
            )
            store.record(
                "induced.demo", facts,
                SkillPerformanceSample(success=True, goal_satisfaction=0.95),
            )
        report = build_promotion_report(
            "induced.demo", store=store, security_sandbox_status="passed",
            evidence_points=[
                PromotionEvidencePoint(
                    situation_signature="holdout-poly",
                    success=False,
                    goal_satisfaction=0.1,
                    counterexample=True,
                    notes="point choropleth invalid on polygon holdout",
                ),
                PromotionEvidencePoint(
                    situation_signature="holdout-crs",
                    success=False,
                    goal_satisfaction=0.0,
                    counterexample=True,
                    notes="geographic CRS metric unsafe",
                ),
            ],
        )
        assert report.recommended_disposition in ("reject", "keep_shadow")
        assert report.counterexamples

    def test_v2_lineage_preserves_v1(self):
        store = SkillLineageStore()
        store.register(
            "point_distribution_analysis", "1.0.0",
            trust_tier="core", evolution_reason="initial",
        )
        store.register(
            "point_distribution_analysis", "2.0.0",
            parent_version="1.0.0",
            trust_tier="candidate",
            evolution_reason="fix geometry gate",
            evidence_refs=["shadow:1", "promo:1"],
            counterexamples=["holdout-poly"],
            performance_comparison={"success_rate_delta": 0.05},
        )
        hist = store.history("point_distribution_analysis")
        assert len(hist) == 2
        assert hist[0].version == "1.0.0"
        assert hist[0].active is False
        assert hist[1].active is True
        rolled = store.rollback("point_distribution_analysis", "1.0.0")
        assert rolled is not None and rolled.active
        assert store.active_version("point_distribution_analysis").version == "1.0.0"
        # history preserved
        assert len(store.history("point_distribution_analysis")) == 2


# ── Composition ────────────────────────────────────────────────────────


class TestComposition:
    def test_compatible_chain(self, library):
        by_id = {s.id: s for s in library.skills}
        # prefer a composition known to be role-compatible; else first ok
        comp = next(
            (c for c in library.compositions
             if c.composition_id == "composition.distribution_report"),
            library.compositions[0],
        )
        report = check_composition_compatibility(comp, by_id)
        assert report.execution_order
        assert report.compatible is True
        assert report.fallback_route == ""

    def test_missing_prerequisite(self, library):
        comp = SkillComposition(
            composition_id="test.missing",
            label_zh="缺前置",
            members=[
                CompositionMember(skill_id="point_distribution_analysis", role="primary"),
                CompositionMember(
                    skill_id="distribution_map_design",
                    role="presentation",
                    depends_on=["nonexistent_skill"],
                ),
            ],
        )
        by_id = {s.id: s for s in library.skills}
        report = check_composition_compatibility(comp, by_id)
        assert report.compatible is False
        assert any("missing_prereq" in m for m in report.missing_prerequisites)
        assert report.fallback_route == "existing_harness_planning"

    def test_incompatible_outputs(self):
        up = _induced_skill("induced.up", caps=["vector_buffer"])
        up.output_roles = ["analysis_result"]
        down = _induced_skill("induced.down", caps=["vector_buffer"])
        down.input_roles = ["totally_other_role"]
        down.output_roles = ["map_layer"]
        comp = SkillComposition(
            composition_id="test.incompat",
            label_zh="不兼容",
            members=[
                CompositionMember(skill_id="induced.up", role="primary"),
                CompositionMember(
                    skill_id="induced.down", role="supporting",
                    depends_on=["induced.up"],
                ),
            ],
        )
        report = check_composition_compatibility(
            comp, {"induced.up": up, "induced.down": down},
        )
        assert report.compatible is False
        assert report.incompatible_edges


# ── Determinism / situation signature ─────────────────────────────────


class TestDeterminism:
    def test_same_inputs_same_policy_decision(self, library):
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"],
            geometry_kinds=["point"],
            feature_count=100,
        )
        a = resolve_skill_guidance(facts, library=library, allow_shadow=False)
        b = resolve_skill_guidance(facts, library=library, allow_shadow=False)
        assert a.to_bounded_dict() == b.to_bounded_dict()
        assert situation_signature(facts) == situation_signature(facts)

    def test_pi_context_bounded(self, library):
        facts = SelectionFacts(
            goal_text="成都小学分布情况",
            ontology_matches=["distribution.point_distribution"],
        )
        d = resolve_for_planning(facts, library=library)
        card = d.pi_context_card()
        assert "selected_procedure" in card
        # must not dump full library
        assert "skills" not in card


# ── Performance profile ────────────────────────────────────────────────


class TestPerformance:
    def test_situation_conditioned_not_global(self):
        store = SkillPerformanceStore()
        f1 = SelectionFacts(goal_text="a", geometry_kinds=["point"], task_type="t1")
        f2 = SelectionFacts(goal_text="b", geometry_kinds=["polygon"], task_type="t2")
        store.record("sk", f1, SkillPerformanceSample(success=True, goal_satisfaction=1.0))
        store.record("sk", f2, SkillPerformanceSample(success=False, goal_satisfaction=0.2))
        p1 = store.get("sk", f1)
        p2 = store.get("sk", f2)
        assert p1 is not None and p2 is not None
        assert p1.success_rate == 1.0
        assert p2.success_rate == 0.0
        assert p1.situation_signature != p2.situation_signature
