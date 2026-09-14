"""Skill Procedure IR V1 测试（ADR-0182 S2）。"""
import pytest
from pydantic import ValidationError

from app.services.gis_harness.skills.procedure_ir import (
    DecisionNode,
    DecisionOption,
    FallbackNode,
    ProcedureStep,
    SkillProcedure,
)


def _steps(*step_ids: str) -> list:
    return [ProcedureStep(step_id=s, title=s, kind="analyze")
            for s in step_ids]


class TestStructure:
    def test_at_least_one_step(self):
        with pytest.raises(ValidationError):
            SkillProcedure(steps=[])

    def test_duplicate_step_ids(self):
        proc = SkillProcedure(steps=_steps("a", "a"))
        assert any("duplicate" in v for v in proc.validate_structure(set()))

    def test_unknown_step_kind(self):
        proc = SkillProcedure(steps=[
            ProcedureStep(step_id="a", title="x", kind="party")])
        assert any("unknown kind" in v for v in proc.validate_structure(set()))

    def test_dangling_depends_on(self):
        proc = SkillProcedure(steps=_steps("a", "b"))
        proc.steps[1].depends_on = ["ghost"]
        assert any("depends_on" in v for v in proc.validate_structure(set()))

    def test_depends_on_cycle_detected(self):
        proc = SkillProcedure(steps=_steps("a", "b", "c"))
        proc.steps[0].depends_on = ["c"]
        proc.steps[2].depends_on = ["b"]
        proc.steps[1].depends_on = ["a"]
        violations = proc.validate_structure(set())
        assert any("环" in v for v in violations)

    def test_self_depend_cycle(self):
        proc = SkillProcedure(steps=_steps("a"))
        proc.steps[0].depends_on = ["a"]
        assert any("环" in v for v in proc.validate_structure(set()))

    def test_capability_refs_must_be_declared(self):
        proc = SkillProcedure(steps=[
            ProcedureStep(step_id="a", title="x", kind="analyze",
                          capability_refs=["undeclared_cap"])])
        assert any("capability ref" in v
                   for v in proc.validate_structure({"declared_cap"}))


class TestDecisions:
    def test_default_option_required(self):
        proc = SkillProcedure(
            steps=_steps("a"),
            decisions=[DecisionNode(
                node_id="d1", question="?",
                options=[DecisionOption(option_id="o1", condition="c",
                                        then_step_ids=["a"])])])
        assert any("default_option_id" in v
                   for v in proc.validate_structure(set()))

    def test_option_references_known_step(self):
        proc = SkillProcedure(
            steps=_steps("a"),
            decisions=[DecisionNode(
                node_id="d1", question="?", default_option_id="o1",
                options=[DecisionOption(option_id="o1", condition="c",
                                        then_step_ids=["ghost"])])])
        assert any("未知步骤" in v for v in proc.validate_structure(set()))

    def test_valid_decision_passes(self):
        proc = SkillProcedure(
            steps=_steps("a", "b"),
            decisions=[DecisionNode(
                node_id="d1", question="?", default_option_id="o1",
                options=[
                    DecisionOption(option_id="o1", condition="c1",
                                   then_step_ids=["a"]),
                    DecisionOption(option_id="o2", condition="c2",
                                   then_step_ids=["b"]),
                ])])
        assert proc.validate_structure(set()) == []


class TestFallbacks:
    def test_unknown_trigger(self):
        proc = SkillProcedure(steps=_steps("a"), fallbacks=[
            FallbackNode(node_id="f", trigger="full_moon",
                         action="blocked", disclosure="x")])
        assert any("unknown trigger" in v
                   for v in proc.validate_structure(set()))

    def test_silent_fallback_forbidden(self):
        # S14：reduced_output / blocked 必须携带 disclosure
        proc = SkillProcedure(steps=_steps("a"), fallbacks=[
            FallbackNode(node_id="f", trigger="insufficient_data",
                         action="reduced_output")])
        assert any("disclosure" in v for v in proc.validate_structure(set()))

    def test_alternative_skill_requires_target(self):
        proc = SkillProcedure(steps=_steps("a"), fallbacks=[
            FallbackNode(node_id="f", trigger="missing_input",
                         action="alternative_skill")])
        assert any("fallback_skill_id" in v
                   for v in proc.validate_structure(set()))


class TestStepPolicy:
    def test_required_never_conflict(self):
        proc = SkillProcedure(steps=[
            ProcedureStep(step_id="a", title="x", kind="inspect",
                          required=False, skip_policy="never")])
        assert any("矛盾" in v for v in proc.validate_structure(set()))

    def test_guidance_cap(self):
        proc = SkillProcedure(steps=[
            ProcedureStep(step_id="a", title="x", kind="inspect",
                          guidance="g" * 241)])
        assert any("240" in v for v in proc.validate_structure(set()))


class testHelpers:
    def test_all_evidence_kinds(self):
        proc = SkillProcedure(steps=[
            ProcedureStep(step_id="a", title="x", kind="inspect",
                          evidence_requirements=[
                              {"evidence_kind": "k1"}]),
        ], evidence_nodes=[
            {"node_id": "e", "evidence_kind": "k2"}])
        assert proc.all_evidence_kinds() == ["k1", "k2"]
