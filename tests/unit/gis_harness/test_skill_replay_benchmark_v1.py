"""Skill Replay + Benchmark V1 测试（ADR-0182 S25/S24）。"""
import pytest

from app.services.gis_harness.skills.benchmark import (
    evaluate_case,
    evaluate_corpus,
    load_corpus,
)
from app.services.gis_harness.skills.loader import get_skill_library
from app.services.gis_harness.skills.replay import replay_procedure


@pytest.fixture(scope="module")
def library():
    return get_skill_library()


class TestReplay:
    def _skill(self, library):
        return library.get("administrative_aggregation")

    def test_full_coverage_complete(self, library):
        skill = self._skill(library)
        report = replay_procedure(skill, {
            "capabilities": ["spatial_join", "admin_aggregation",
                             "admin_boundary_query", "thematic_cartography"],
            "evidence_kinds": [
                "subject_qc_report", "boundary_level_declaration",
                "join_unmatched_report", "map_expression",
                "product_completeness", "denominator_evidence",
            ],
        })
        assert report.missing_steps == []
        assert report.complete
        assert all(o.state != "missing" for o in report.obligations)

    def test_empty_plan_all_missing(self, library):
        report = replay_procedure(self._skill(library), {})
        assert report.missing_steps
        assert not report.complete

    def test_declared_skip_not_missing(self, library):
        skill = self._skill(library)
        optional_steps = [s.step_id for s in skill.procedure.steps
                          if not s.required]
        if not optional_steps:
            pytest.skip("技能无可选步骤")
        report = replay_procedure(skill, {
            "capabilities": [],
            "skipped_steps": [{"step_id": optional_steps[0],
                               "disclosure": "与目标无关"}],
        })
        states = {s.step_id: s.state for s in report.steps}
        assert states[optional_steps[0]] == "skipped_declared"

    def test_denominator_obligation(self, library):
        # choropleth_map_design：分母证据缺席 → missing；在场 → satisfied
        skill = library.get("choropleth_map_design")
        evidence = ["choropleth_eligibility", "classification_record",
                    "product_completeness"]
        without = replay_procedure(skill, {
            "evidence_kinds": list(evidence)})
        denom = [o for o in without.obligations
                 if o.obligation_id == "statistical.denominator_required"]
        assert denom and denom[0].state == "missing"
        with_evidence = replay_procedure(skill, {
            "evidence_kinds": list(evidence),
            "has_denominator_evidence": True})
        denom2 = [o for o in with_evidence.obligations
                  if o.obligation_id == "statistical.denominator_required"]
        assert denom2 and denom2[0].state == "satisfied"

    def test_never_step_skip_is_violation(self, library):
        # P0 修复锁定：required + skip_policy=never 的步骤被声明跳过 = missing
        # （否则 plan_facts 可绕过验收门）
        skill = self._skill(library)
        never_step = next(s for s in skill.procedure.steps
                          if s.required and s.skip_policy == "never")
        report = replay_procedure(skill, {
            "skipped_steps": [{"step_id": never_step.step_id,
                               "disclosure": "随便填的披露"}],
        })
        states = {s.step_id: s.state for s in report.steps}
        assert states[never_step.step_id] == "missing"
        assert not report.complete

    def test_optional_step_skip_declared(self, library):
        skill = self._skill(library)
        optional = next((s for s in skill.procedure.steps
                         if not s.required), None)
        if optional is None:
            pytest.skip("技能无可选步骤")
        report = replay_procedure(skill, {
            "skipped_steps": [{"step_id": optional.step_id,
                               "disclosure": "与目标无关"}],
        })
        states = {s.step_id: s.state for s in report.steps}
        assert states[optional.step_id] == "skipped_declared"

    def test_unknown_skill_raises_keyerror(self, library):
        with pytest.raises(AttributeError):
            replay_procedure(None, {})

    def test_bounded_projection(self, library):
        report = replay_procedure(self._skill(library), {})
        d = report.to_bounded_dict()
        assert len(d["steps"]) <= 32
        assert len(d["obligations"]) <= 16


class TestBenchmark:
    def test_corpus_loads_and_size(self):
        cases = load_corpus()
        assert 150 <= len(cases) <= 300  # goal S24 语料规模
        ids = [c["id"] for c in cases]
        assert len(ids) == len(set(ids))

    def test_corpus_category_coverage(self):
        cases = load_corpus()
        cats = {c.get("cat") for c in cases}
        assert {"clear", "ambiguous", "insufficient", "unsupported",
                "multi"} <= cats
        langs = {c.get("lang") for c in cases}
        assert {"zh", "en"} <= langs

    def test_full_pass(self, library):
        metrics = evaluate_corpus(library)
        assert metrics["passed"] == metrics["total"]
        assert metrics["wrong_skill"] == 0
        assert metrics["top1_rate"] >= 0.85
        assert metrics["top3_rate"] >= 0.95

    def test_single_case_api(self, library):
        case = {"id": "t", "q": "成都小学分布情况",
                "expect": {"type": "select",
                           "top": "point_distribution_analysis"}}
        result = evaluate_case(library, case)
        assert result["passed"] and result["kind"] == "top1"

    def test_no_llm_dependency(self):
        # 语料评测是确定性纯函数：不触网、不调模型（结构性保证：纯 CPU 快）
        import time
        library = get_skill_library()
        t0 = time.perf_counter()
        evaluate_corpus(library)
        assert time.perf_counter() - t0 < 30.0
