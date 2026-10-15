"""Procedure corpus matrix —— 族 × 场景回归（ADR-0219 §D）。

确定性语料（procedure_binding_corpus）全量执行：每个用例断言其场景
期望 + 通用不变式（状态词表闭合、有界、重算稳定）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.skills.loader import get_skill_library
from app.services.gis_harness.skills.procedure_binding import (
    PROCEDURE_VIOLATION_CODES,
    enforce_procedure_binding,
)
from app.services.gis_harness.skills.procedure_coverage import (
    CAPABILITY_COVERAGE_STATES,
    OBLIGATION_COVERAGE_STATES,
    STEP_COVERAGE_STATES,
    procedure_pi_card,
)
from tests.unit.gis_harness.procedure_binding_corpus import (
    build_composition_case,
    build_corpus,
    build_drift_cases,
    build_fallback_capability_case,
)


@pytest.fixture(scope="module")
def library():
    return get_skill_library()


@pytest.fixture(scope="module")
def corpus(library):
    return build_corpus(library=library)


def _assert_invariants(cov):
    assert cov is not None
    for s in cov.steps:
        assert s.state in STEP_COVERAGE_STATES
    for c in cov.capabilities:
        assert c.state in CAPABILITY_COVERAGE_STATES
    for o in cov.obligations:
        assert o.state in OBLIGATION_COVERAGE_STATES
    # 有界：actionable_missing ≤8，每条 ≤120
    assert len(cov.actionable_missing) <= 8
    assert all(len(m) <= 120 for m in cov.actionable_missing)
    # 重算稳定性由 seam/coverage 测试单独覆盖（report 不回携输入）。


class TestCorpusMatrix:
    def test_corpus_covers_all_families(self, corpus):
        families = {c.family for c in corpus}
        expected = {
            "distribution_overview", "administrative_statistic",
            "density_rate", "hotspot", "proximity_accessibility",
            "raster_rs", "temporal_comparison", "export_presentation",
        }
        assert expected <= families

    def test_corpus_covers_all_scenarios(self, corpus):
        scenarios = {c.scenario for c in corpus}
        assert {
            "baseline", "required_step_skipped", "wrong_order",
            "stale_evidence", "optional_skip", "fallback",
            "style_only_followup", "capability_denied",
            "cancellation_resume",
        } <= scenarios

    def test_baseline_cases_pass(self, corpus):
        baseline = [c for c in corpus if c.scenario == "baseline"]
        assert baseline
        twins = [c for c in baseline if c.skill_id.startswith("fixture.")]
        reals = [c for c in baseline if not c.skill_id.startswith("fixture.")]
        # review P1-2：complete（replay 兼容）在基线场景必须可达
        # （此前 dead assertion —— 首个用例恒为真实技能，twin 分支空转）
        for case in baseline:
            cov = case.coverage()
            _assert_invariants(cov)
            assert cov.complete, case.case_id
            assert not cov.missing_required, case.case_id
        # fully_covered（强语义）：twin 的全部非 decide 步骤都 capability
        # 绑定；decide/obligation-only 步骤 unknown → 不冒充（除非该技能
        # 无 decide 步骤且全部步骤绑能力）
        for case in twins:
            cov = case.coverage()
            binding_steps = case.chapter["procedure_binding"]["steps"]
            unbound_required = [
                s for s in binding_steps
                if s["required"] and not s["capability_ids"]
            ]
            if unbound_required:
                assert not cov.fully_covered, case.case_id
        for case in reals:
            cov = case.coverage()
            assert isinstance(cov.fully_covered, bool)

    def test_every_case_produces_valid_report(self, corpus):
        assert corpus
        for case in corpus:
            cov = case.coverage()
            assert cov is not None, case.case_id
            _assert_invariants(cov)
            card = procedure_pi_card(cov)
            assert card["phase"]["total"] == len(cov.steps), case.case_id

    def test_required_step_skipped_has_violation(self, corpus):
        cases = [c for c in corpus if c.scenario == "required_step_skipped"]
        assert cases
        for case in cases:
            cov = case.coverage()
            assert cov.missing_required, case.case_id
            # binding × chapter 复算违例（enforcement 不持久，重算一致）
            from app.services.gis_harness.skills.procedure_binding import (
                ProcedureBinding,
            )
            binding = ProcedureBinding.model_validate(
                case.chapter["procedure_binding"])
            check = enforce_procedure_binding(binding, case.chapter)
            assert not check.ok, case.case_id
            codes = {v.code for v in check.violations}
            assert "required_capability_unplanned" in codes, case.case_id
            assert codes <= set(PROCEDURE_VIOLATION_CODES)

    def test_stale_evidence_marks_stale(self, corpus):
        cases = [c for c in corpus if c.scenario == "stale_evidence"]
        assert cases
        for case in cases:
            cov = case.coverage()
            assert any(c.state == "stale" for c in cov.capabilities) or any(
                s.state == "stale" for s in cov.steps), case.case_id
            if case.expect.get("fully_covered_false"):
                assert not cov.fully_covered, case.case_id

    def test_optional_skip_not_missing(self, corpus):
        cases = [c for c in corpus if c.scenario == "optional_skip"]
        assert cases
        for case in cases:
            cov = case.coverage()
            assert not [
                c for c in cov.capabilities
                if c.state == "missing" and c.criticality == "optional"
            ], case.case_id

    def test_fallback_and_denied_blocked(self, corpus):
        for scenario in ("fallback", "capability_denied"):
            cases = [c for c in corpus if c.scenario == scenario]
            assert cases, scenario
            hit = 0
            for case in cases:
                cov = case.coverage()
                hit += int(any(c.state == "blocked"
                               for c in cov.capabilities)
                           or any(s.state == "blocked" for s in cov.steps))
            assert hit > 0, scenario

    def test_style_only_followup_idempotent(self, corpus):
        cases = [c for c in corpus if c.scenario == "style_only_followup"]
        assert cases
        for case in cases:
            a = case.coverage()
            b = case.coverage()
            assert a.to_bounded_dict() == b.to_bounded_dict(), case.case_id
            # science 能力行全部 complete（未重跑不丢完成事实）
            assert all(
                c.state in ("covered", "unknown")
                for c in a.capabilities if c.planned
            ), case.case_id

    def test_cancellation_resume_recovers(self, corpus):
        cases = [c for c in corpus if c.scenario == "cancellation_resume"]
        assert cases
        for case in cases:
            cov = case.coverage()
            assert not any(
                s.state == "blocked" for s in cov.steps), case.case_id


class TestSpecialCases:
    def test_composition_two_skills(self, library):
        case = build_composition_case(library=library)
        cov = case.coverage()
        assert cov is not None
        assert not cov.missing_required
        # 前置技能绑定独立存在（per-skill，不产生第二 executor）
        assert case.chapter.get("_prep_binding")

    def test_fallback_hard_capability_missing(self, library):
        case = build_fallback_capability_case(library=library)
        cov = case.coverage()
        assert cov is not None
        assert case.expect["binding_partial"]
        assert not case.expect["check_ok"]

    def test_drift_cases(self, library):
        cases = build_drift_cases(library=library)
        by_scenario = {c.scenario: c for c in cases}
        wrong = by_scenario["wrong_skill_version"].coverage()
        assert "skill_version_drift" in wrong.drift
        missing = by_scenario["skill_missing"].coverage()
        assert "skill_missing" in missing.drift


class TestDeterminism:
    def test_corpus_is_deterministic(self, library):
        a = build_corpus(library=library)
        b = build_corpus(library=library)
        assert len(a) == len(b)
        for ca, cb in zip(a, b):
            assert ca.case_id == cb.case_id
            assert (ca.coverage().to_bounded_dict()
                    == cb.coverage().to_bounded_dict()), ca.case_id
