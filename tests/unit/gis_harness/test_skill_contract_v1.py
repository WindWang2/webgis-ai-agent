"""SkillContract V1 契约测试（ADR-0182 S1）。"""
import pytest
from pydantic import ValidationError

from app.services.gis_harness.skills.contract import (
    SKILL_SCHEMA_VERSION,
    CapabilityRequirement,
    SkillContract,
)
from app.services.gis_harness.skills.procedure_ir import (
    EvidenceNode,
    FallbackNode,
    ProcedureStep,
    SkillProcedure,
)
from app.services.gis_harness.skills.semantics import (
    StatisticalSemantics,
    TemporalSemantics,
)


def _minimal_skill(**overrides) -> SkillContract:
    payload = dict(
        id="test_skill",
        name="测试技能",
        description="用于测试的最小技能",
        domain="vector_analysis",
        intent_patterns=["测试"],
        when_to_use="测试时",
        procedure=SkillProcedure(steps=[
            ProcedureStep(step_id="s1", title="第一步", kind="inspect"),
        ]),
    )
    payload.update(overrides)
    return SkillContract(**payload)


class TestContractBasics:
    def test_schema_version(self):
        assert SKILL_SCHEMA_VERSION == 1

    def test_minimal_contract_roundtrip(self):
        skill = _minimal_skill()
        assert skill.version == "1.0.0"
        assert skill.deprecated is False
        assert skill.procedure.steps[0].step_id == "s1"

    def test_extra_fields_forbidden(self):
        # 资产纪律：未知字段拒绝（extra=forbid）
        with pytest.raises(ValidationError):
            _minimal_skill(totally_unknown_field=1)

    def test_when_to_use_required_by_validator(self):
        skill = _minimal_skill(when_to_use="")
        violations = skill.validate_contract()
        assert any("when_to_use" in v for v in violations)

    def test_unknown_domain(self):
        skill = _minimal_skill(domain="not_a_domain")
        assert any("unknown domain" in v for v in skill.validate_contract())

    def test_unknown_pack(self):
        skill = _minimal_skill(pack="everything")
        assert any("unknown pack" in v for v in skill.validate_contract())

    def test_deprecated_requires_successor(self):
        skill = _minimal_skill(deprecated=True)
        assert any("deprecated_by" in v for v in skill.validate_contract())
        skill2 = _minimal_skill(deprecated=True, deprecated_by="other")
        violations = skill2.validate_contract(
            skill_id_exists=lambda sid: sid == "other")
        assert not any("deprecated_by" in v for v in violations)


class TestReferenceValidation:
    def test_unknown_capability(self):
        skill = _minimal_skill(capability_requirements=[
            CapabilityRequirement(capability_id="no_such_cap"),
        ])
        violations = skill.validate_contract(
            capability_exists=lambda c: False)
        assert any("unknown capability" in v for v in violations)

    def test_known_capability_passes(self):
        skill = _minimal_skill(capability_requirements=[
            CapabilityRequirement(capability_id="thematic_cartography"),
        ])
        assert skill.validate_contract(capability_exists=lambda c: True) == []

    def test_duplicate_capability_requirement(self):
        skill = _minimal_skill(capability_requirements=[
            CapabilityRequirement(capability_id="a_cap"),
            CapabilityRequirement(capability_id="a_cap"),
        ])
        violations = skill.validate_contract(capability_exists=lambda c: True)
        assert any("duplicate capability" in v for v in violations)

    def test_unknown_data_role(self):
        skill = _minimal_skill(input_roles=["not_a_role"])
        assert any("unknown data role" in v
                   for v in skill.validate_contract())

    def test_unknown_artifact_type(self):
        skill = _minimal_skill(output_artifacts=["mystery_artifact"])
        assert any("unknown artifact type" in v
                   for v in skill.validate_contract(
                       artifact_type_exists=lambda a: False))


class TestSemanticVocab:
    def test_statistical_denominator_consistency(self):
        sem = StatisticalSemantics(
            measure_semantics=["count"], denominator_required=True)
        violations = sem.validate_vocabulary()
        assert any("denominator_required" in v for v in violations)

    def test_temporal_mode_min_periods(self):
        assert any("min_periods" in v for v in TemporalSemantics(
            temporal_mode="comparison", min_periods=1).validate_vocabulary())
        assert any("min_periods" in v for v in TemporalSemantics(
            temporal_mode="trend", min_periods=2).validate_vocabulary())
        assert TemporalSemantics(
            temporal_mode="trend", min_periods=3).validate_vocabulary() == []

    def test_unknown_temporal_mode(self):
        assert any("unknown" in v for v in TemporalSemantics(
            temporal_mode="parallel").validate_vocabulary())


class TestCompletionEvidence:
    def test_completion_evidence_must_map_to_ir(self):
        skill = _minimal_skill(completion_evidence=["phantom_evidence"])
        assert any("phantom_evidence" in v
                   for v in skill.validate_contract())

    def test_completion_evidence_from_step(self):
        skill = _minimal_skill(procedure=SkillProcedure(steps=[
            ProcedureStep(
                step_id="s1", title="x", kind="inspect",
                evidence_requirements=[
                    {"evidence_kind": "qc_report"}]),
        ], evidence_nodes=[
            EvidenceNode(node_id="e1", evidence_kind="qc_report"),
        ], fallbacks=[
            FallbackNode(node_id="f1", trigger="missing_input",
                         action="blocked", disclosure="缺数据"),
        ]), completion_evidence=["qc_report"])
        assert skill.validate_contract() == []

    def test_guidance_bounded(self):
        skill = _minimal_skill(guidance="x" * 241)
        assert any("240" in v for v in skill.validate_contract())


class TestVocabularyGates:
    def test_situation_data_roles_vocabulary(self):
        skill = _minimal_skill()
        skill.required_situation.data_roles = ["typo_role"]
        violations = skill.validate_contract()
        assert any("unknown data role" in v for v in violations)

    def test_task_types_vocabulary(self):
        skill = _minimal_skill(task_types=["not_a_task"])
        violations = skill.validate_contract()
        assert any("unknown task_type" in v for v in violations)

    def test_reverse_denominator_consistency(self):
        from app.services.gis_harness.skills.semantics import StatisticalSemantics
        sem = StatisticalSemantics(
            measure_semantics=["rate", "density"], denominator_required=False)
        violations = sem.validate_vocabulary()
        assert any("denominator_required=false" in v for v in violations)
        # 混合度量集允许 false（分支级义务承担）
        mixed = StatisticalSemantics(
            measure_semantics=["count", "density"], denominator_required=False)
        assert not any("denominator_required=false" in v
                       for v in mixed.validate_vocabulary())


class TestLibraryGovernanceValidation:
    def test_deprecated_fallback_target_red(self):
        from app.services.gis_harness.skills.loader import load_skill_library
        import copy
        skills, comps, _ = load_skill_library()
        by_id = {s.id: s for s in skills}
        dep = copy.deepcopy(by_id["density_map_design"])
        dep.deprecated = True
        dep.deprecated_by = "density_hotspot_analysis"
        live = by_id["point_distribution_analysis"]
        live.procedure.fallbacks.append(
            FallbackNode(node_id="fb_dep", trigger="missing_input",
                         action="alternative_skill",
                         fallback_skill_id="density_map_design",
                         disclosure="x"))
        from app.services.gis_harness.skills.validation import (
            validate_skill_library)
        violations = validate_skill_library(
            skills + [dep],
            compositions=comps,
            capability_exists=lambda c: True,
            recipe_exists=lambda r: True,
            ontology_task_exists=lambda t: True,
            artifact_type_exists=lambda a: True,
            precondition_exists=lambda p: True)
        assert any("指向已弃用技能" in v for v in violations)

    def test_missing_core_trigger_fallback_red(self):
        from app.services.gis_harness.skills.loader import load_skill_library
        from app.services.gis_harness.skills.validation import (
            validate_skill_library)
        import copy
        skills, comps, _ = load_skill_library()
        by_id = {s.id: s for s in skills}
        stripped = copy.deepcopy(by_id["density_map_design"])
        stripped.procedure.fallbacks = [
            f for f in stripped.procedure.fallbacks
            if f.trigger == "quality_failure"]
        violations = validate_skill_library(
            [stripped], capability_exists=lambda c: True,
            recipe_exists=lambda r: True, ontology_task_exists=lambda t: True,
            artifact_type_exists=lambda a: True,
            precondition_exists=lambda p: True)
        assert sum("缺核心 fallback 触发器" in v for v in violations) == 3

    def test_registry_validation_red_on_broken_yaml(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from app.services.gis_harness import registry_validation as rv
        from app.services.gis_harness.skills import loader as skill_loader
        import app.services.gis_harness.skills.loader as loader_mod

        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "bad.yaml").write_text(
            _yaml.dump([{"id": "x", "name": "x"}], allow_unicode=True),
            encoding="utf-8")
        monkeypatch.setattr(loader_mod, "LIBRARY_DIR", tmp_path)
        skill_loader.reset_skill_library()
        try:
            issues = rv.validate_gis_library()
        finally:
            skill_loader.reset_skill_library()
        assert any("skill_library" in i for i in issues)


class TestFingerprint:
    def test_fingerprint_stable_and_content_sensitive(self):
        a = _minimal_skill()
        b = _minimal_skill()
        c = _minimal_skill(description="不同描述")
        assert a.fingerprint() == b.fingerprint()
        assert a.fingerprint() != c.fingerprint()
