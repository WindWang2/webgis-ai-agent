"""ProcedureCoverage V1 —— 运行期证据绑定/覆盖/drift 测试（C4/C5/C6）。"""
from __future__ import annotations

import pytest

from app.services.gis_harness.skills.loader import get_skill_library
from app.services.gis_harness.skills.procedure_coverage import (
    PROCEDURE_DRIFT_CODES,
    STEP_COVERAGE_STATES,
    compute_procedure_coverage,
    procedure_pi_card,
)
from tests.unit.gis_harness.procedure_binding_corpus import (
    bind,
    complete_all,
    kernel_envelope_for,
    plan_for_skill,
)


@pytest.fixture(scope="module")
def library():
    return get_skill_library()


def _chapter(library, skill_id="spatial_autocorrelation_analysis", **plan_kw):
    skill = library.get(skill_id)
    plan = plan_for_skill(skill, **plan_kw)
    binding = bind(skill, plan)
    chapter = dict(plan)
    chapter["procedure_binding"] = binding.to_bounded_dict()
    return skill, plan, chapter


# ── C4: runtime evidence binding ────────────────────────────────────────────


class TestEvidenceBinding:
    def test_covered_step_carries_kernel_evidence(self, library):
        _, _, chapter = _chapter(library)
        complete_all(chapter)
        env = kernel_envelope_for(chapter)
        cov = compute_procedure_coverage(chapter, envelope=env)
        assert cov is not None
        step = next(s for s in cov.steps if s.step_id == "global_test")
        assert step.state == "covered"
        assert step.matched_evidence
        assert step.matched_evidence[0]["tool_call_id"] == "call-global_morans_i"

    def test_exactly_once_dedup_on_duplicate_callbacks(self, library):
        _, _, chapter = _chapter(library)
        complete_all(chapter)
        env = kernel_envelope_for(chapter)
        # 重复 attach（锁重试/重复回调）：同 tool_call_id 计一次
        dup = {"steps": env["steps"] + env["steps"]}
        cov = compute_procedure_coverage(chapter, envelope=dup)
        step = next(s for s in cov.steps if s.step_id == "global_test")
        ids = [e["tool_call_id"] for e in step.matched_evidence]
        assert ids.count("call-global_morans_i") == 1

    def test_same_capability_repeated_distinguishable(self, library):
        # 同一 capability 多次执行（重跑）：不同 tool_call_id 都保留（≤4）
        _, _, chapter = _chapter(library)
        complete_all(chapter)
        env = kernel_envelope_for(chapter)
        first = env["steps"][0]
        env["steps"].append({
            "id": first["id"], "capability": first["capability"],
            "status": "succeeded",
            "evidence": [{
                "tool": first["evidence"][0]["tool"],
                "tool_call_id": "call-retry-2",
                "ref": first["evidence"][0]["ref"],
            }],
        })
        cov = compute_procedure_coverage(chapter, envelope=env)
        by_cap = {
            c.capability_id: c for c in cov.capabilities
        }.get(first["capability"])
        assert by_cap is not None
        # capability 级聚合 covered；step 级 evidence 含两次不同 call
        steps_with = [
            s for s in cov.steps
            if first["capability"] in s.bound_capabilities
            and s.matched_evidence
        ]
        all_ids = [
            e["tool_call_id"] for s in steps_with
            for e in s.matched_evidence]
        assert "call-retry-2" in all_ids

    def test_row_complete_without_kernel_evidence_still_covered(self, library):
        # 行 complete 是绑定事实（goal_satisfaction 同语义）；kernel 证据
        # 缺席不撤销 covered（但 detail 如实披露）
        _, _, chapter = _chapter(library)
        complete_all(chapter)
        cov = compute_procedure_coverage(chapter, envelope=None)
        step = next(s for s in cov.steps if s.step_id == "weights_diagnostic")
        assert step.state == "covered"
        assert not step.matched_evidence
        assert "kernel evidence 缺席" in step.detail

    def test_failed_row_blocks_not_missing(self, library):
        _, _, chapter = _chapter(library)
        rows = chapter["data_requirements"]
        rows[0]["status"] = "failed"
        for r in rows[1:]:
            r["status"] = "complete"
        cov = compute_procedure_coverage(chapter)
        assert any(s.state == "blocked" for s in cov.steps)
        assert not any(s.state == "missing" for s in cov.steps
                       if rows[0]["capability"] in s.bound_capabilities)

    def test_unavailable_row_blocks_with_fallback_disclosure(self, library):
        _, _, chapter = _chapter(library)
        rows = chapter["data_requirements"]
        rows[0]["status"] = "unavailable"
        for r in rows[1:]:
            r["status"] = "complete"
        cov = compute_procedure_coverage(chapter)
        blocked = [s for s in cov.steps if s.state == "blocked"]
        assert blocked
        assert any("fallback" in s.detail for s in blocked)


# ── C5: coverage states ─────────────────────────────────────────────────────


class TestCoverageStates:
    def test_state_vocab_closed(self):
        assert set(STEP_COVERAGE_STATES) == {
            "covered", "missing", "unknown", "skipped_declared",
            "blocked", "stale"}

    def test_plan_time_all_unknown(self, library):
        _, _, chapter = _chapter(library)
        cov = compute_procedure_coverage(chapter)
        assert cov.counts.get("unknown", 0) > 0
        assert not cov.complete  # 强语义：未执行不冒充完成

    def test_complete_requires_required_covered(self, library):
        _, _, chapter = _chapter(library)
        complete_all(chapter)
        cov = compute_procedure_coverage(
            chapter, envelope=kernel_envelope_for(chapter))
        # variable_check（required、obligation-only）无运行期事实 →
        # unknown → complete=False（诚实：检视证据从未被记录）
        vc = next(s for s in cov.steps if s.step_id == "variable_check")
        assert vc.state == "unknown"
        assert not cov.complete

    def test_missing_when_required_capability_dropped(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(
            skill,
            drop_capabilities=[
                c.capability_id for c in skill.capability_requirements
                if c.criticality == "required"][:1])
        binding = bind(skill, plan)
        chapter = dict(plan)
        chapter["procedure_binding"] = binding.to_bounded_dict()
        complete_all(chapter)
        cov = compute_procedure_coverage(chapter)
        assert any(c.state == "missing" for c in cov.capabilities)
        assert any(s.state == "missing" for s in cov.steps)
        assert cov.missing_required

    def test_skipped_declared_via_decision_default(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill, include_optional=False)
        plan["analysis_steps"] = []
        binding = bind(skill, plan)
        chapter = dict(plan)
        chapter["procedure_binding"] = binding.to_bounded_dict()
        complete_all(chapter)
        cov = compute_procedure_coverage(chapter)
        lc = next(s for s in cov.steps if s.step_id == "local_clusters")
        # local_morans_i 未进计划 → decision default 分支 → skipped_declared
        assert lc.state == "skipped_declared"

    def test_actionable_missing_lists_entries(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(
            skill,
            drop_capabilities=[
                c.capability_id for c in skill.capability_requirements
                if c.criticality == "required"][:1])
        binding = bind(skill, plan)
        chapter = dict(plan)
        chapter["procedure_binding"] = binding.to_bounded_dict()
        complete_all(chapter)
        cov = compute_procedure_coverage(chapter)
        assert cov.actionable_missing
        assert all(len(m) <= 120 for m in cov.actionable_missing)

    def test_summary_line_bounded(self, library):
        _, _, chapter = _chapter(library)
        complete_all(chapter)
        cov = compute_procedure_coverage(
            chapter, envelope=kernel_envelope_for(chapter))
        line = cov.summary_line()
        assert line.startswith("Procedure: skill=")
        assert "covered=" in line
        assert len(line) < 400

    def test_next_expected_first_unfinished(self, library):
        _, _, chapter = _chapter(library)
        cov = compute_procedure_coverage(chapter)
        assert cov.next_expected() == "variable_check"


# ── C6: replay / drift / stale ──────────────────────────────────────────────


class TestDriftAndReplay:
    def test_no_drift_when_current(self, library):
        _, _, chapter = _chapter(library)
        cov = compute_procedure_coverage(chapter, library=library)
        assert cov.drift == []

    def test_version_drift(self, library):
        _, _, chapter = _chapter(library)
        chapter["procedure_binding"]["skill_version"] = "9.9.9"
        cov = compute_procedure_coverage(chapter, library=library)
        # 手改 version、指纹未动 → 只有 version drift（指纹仍对上当前库）
        assert cov.drift == ["skill_version_drift"]

    def test_fingerprint_drift_when_content_changed(self, library):
        # 模拟真实升级：库内容变了 → 指纹不再匹配旧绑定
        _, _, chapter = _chapter(library)
        chapter["procedure_binding"]["skill_fingerprint"] = "deadbeef00000000"
        cov = compute_procedure_coverage(chapter, library=library)
        assert "skill_fingerprint_drift" in cov.drift

    def test_skill_missing_drift(self, library):
        _, _, chapter = _chapter(library)
        chapter["procedure_binding"]["skill_id"] = "nope.not_there"
        cov = compute_procedure_coverage(chapter, library=library)
        assert cov.drift == ["skill_missing"]

    def test_drift_vocab_closed(self):
        assert set(PROCEDURE_DRIFT_CODES) == {
            "skill_version_drift", "skill_fingerprint_drift",
            "skill_deprecated", "skill_missing"}

    def test_stale_generation_not_counted(self, library):
        # supersede/replace：行 void → stale，不计 covered
        _, _, chapter = _chapter(library)
        caps = [r["capability"] for r in chapter["data_requirements"]]
        complete_all(chapter, skip=caps[:1])
        cov = compute_procedure_coverage(
            chapter, envelope=kernel_envelope_for(chapter))
        stale = [c for c in cov.capabilities if c.state == "stale"]
        assert stale
        assert all(s.state == "stale" for s in cov.steps
                   if s.bound_capabilities == [caps[0]])

    def test_replay_stability_identical_recompute(self, library):
        # 同输入重算 → 逐字节一致（replay trace 的 procedure coverage 稳定）
        _, _, chapter = _chapter(library)
        complete_all(chapter)
        env = kernel_envelope_for(chapter)
        a = compute_procedure_coverage(chapter, envelope=env,
                                       library=library)
        b = compute_procedure_coverage(chapter, envelope=env,
                                       library=library)
        assert a.to_bounded_dict() == b.to_bounded_dict()
        assert a.binding_fingerprint == b.binding_fingerprint

    def test_style_only_followup_no_science_rerun(self, library):
        # follow-up 只改样式：完成事实 carried，coverage 幂等（不因重算
        # 产生新证据/新状态）；样式步骤之外的 covered 保持
        _, _, chapter = _chapter(library)
        complete_all(chapter)
        env = kernel_envelope_for(chapter)
        before = compute_procedure_coverage(
            chapter, envelope=env, library=library)
        # 模拟 style-only turn：什么都不变，只重投影
        after = compute_procedure_coverage(
            chapter, envelope=env, library=library)
        assert before.to_bounded_dict() == after.to_bounded_dict()

    def test_cancellation_resume_failed_then_complete(self, library):
        # 取消（failed）→ 恢复（complete 覆写）：covered，可重放
        _, _, chapter = _chapter(library)
        rows = chapter["data_requirements"]
        rows[0]["status"] = "failed"
        for r in rows[1:]:
            r["status"] = "complete"
        mid = compute_procedure_coverage(chapter)
        assert any(s.state == "blocked" for s in mid.steps)
        rows[0]["status"] = "complete"
        rows[0]["bound_ref"] = "ref:x"
        done = compute_procedure_coverage(chapter)
        assert all(s.state != "blocked" for s in done.steps)

    def test_denominator_obligation_missing(self, library):
        # density/rate 家族：denominator_required=True 且无分母证据 →
        # obligation missing（S10 红线）
        skill = library.get("density_hotspot_analysis")
        plan = plan_for_skill(skill)
        binding = bind(skill, plan)
        chapter = dict(plan)
        chapter["procedure_binding"] = binding.to_bounded_dict()
        complete_all(chapter)
        cov = compute_procedure_coverage(
            chapter, envelope=kernel_envelope_for(chapter))
        denom = [o for o in cov.obligations
                 if o.obligation_id == "statistical.denominator_required"]
        assert denom and denom[0].state == "missing"
        assert not cov.complete


# ── C7: pi card ─────────────────────────────────────────────────────────────


class TestPiCard:
    def test_card_bounded_fields(self, library):
        _, _, chapter = _chapter(library)
        complete_all(chapter)
        cov = compute_procedure_coverage(
            chapter, envelope=kernel_envelope_for(chapter))
        card = procedure_pi_card(cov)
        assert card["schema"] == "procedure_pi_card.v1"
        assert card["phase"]["total"] == len(cov.steps)
        assert len(card["missing_obligations"]) <= 3
        assert "guidance" not in card  # 禁整份 YAML/procedure 正文

    def test_card_none_coverage(self):
        assert procedure_pi_card(None) == {}

    def test_card_no_cot_leak(self, library):
        _, _, chapter = _chapter(library)
        cov = compute_procedure_coverage(chapter)
        card = procedure_pi_card(cov)
        text = repr(card)
        for banned in ("chain_of_thought", "cot", "messages", "thinking"):
            assert banned not in text.lower() or banned == "cot" and "cot" not in text.lower()


# ── fail-closed ─────────────────────────────────────────────────────────────


class TestFailClosed:
    def test_no_binding_none(self):
        assert compute_procedure_coverage({}) is None
        assert compute_procedure_coverage(None) is None
        assert compute_procedure_coverage(
            {"procedure_binding": "not-a-dict"}) is None

    def test_broken_binding_none(self, library):
        _, _, chapter = _chapter(library)
        chapter["procedure_binding"]["steps"] = "corrupted"
        assert compute_procedure_coverage(chapter) is None
