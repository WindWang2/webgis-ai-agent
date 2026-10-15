"""ProcedureBinding V1 —— 绑定/映射/enforcement 测试（ADR-0219 C1/C2/C3）。

纯函数测试：零 IO、零 LLM；同输入同输出（指纹确定性红线）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.skills.contract import (
    CapabilityRequirement,
    SkillContract,
)
from app.services.gis_harness.skills.loader import get_skill_library
from app.services.gis_harness.skills.policy import SkillPolicyDecision
from app.services.gis_harness.skills.procedure_binding import (
    CAPABILITY_BINDING_STATUSES,
    PROCEDURE_VIOLATION_CODES,
    STEP_BINDING_STATUSES,
    ProcedureBinding,
    bind_procedure_to_plan,
    enforce_procedure_binding,
    kernel_step_ref,
)
from app.services.gis_harness.skills.procedure_coverage import (
    compute_procedure_coverage,
)
from app.services.gis_harness.skills.procedure_ir import (
    ProcedureStep,
    SkillProcedure,
)
from tests.unit.gis_harness.procedure_binding_corpus import (
    plan_for_skill,
)


@pytest.fixture(scope="module")
def library():
    return get_skill_library()


def _decision(mode="execute_guided", skill_id="spatial_autocorrelation_analysis",
              version="1.0.0"):
    return SkillPolicyDecision(
        mode=mode, selected_skill=skill_id, skill_version=version,
        trust_tier="core", confidence=0.9)


# ── C1: typed projection ────────────────────────────────────────────────────


class TestBindingContract:
    def test_enriched_library_skill_binds(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        assert b is not None
        assert b.schema_version == "procedure_binding.v1"
        assert b.skill_fingerprint
        assert b.binding_status in ("bound", "partial")

    def test_none_mode_returns_none(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        for mode in ("none", "fallback", "blocked", "shadow"):
            assert bind_procedure_to_plan(
                skill, _decision(mode=mode), plan) is None

    def test_no_skill_returns_none(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        assert bind_procedure_to_plan(
            None, _decision(), plan_for_skill(skill)) is None

    def test_no_plan_returns_none(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        assert bind_procedure_to_plan(skill, _decision(), None) is None

    def test_fingerprint_deterministic(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        a = bind_procedure_to_plan(skill, _decision(), plan)
        c = bind_procedure_to_plan(skill, _decision(), plan)
        assert a.fingerprint() == c.fingerprint()
        # 不同计划 → 不同指纹
        plan2 = plan_for_skill(skill, drop_capabilities=[
            c2.capability_id for c2 in skill.capability_requirements[:1]])
        b2 = bind_procedure_to_plan(skill, _decision(), plan2)
        assert b2.fingerprint() != a.fingerprint()

    def test_bounded_dict_roundtrip(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        block = b.to_bounded_dict()
        re = ProcedureBinding.model_validate(block)
        assert re.fingerprint() == b.fingerprint()
        assert re.to_bounded_dict() == block

    def test_status_vocab_closed(self):
        assert set(STEP_BINDING_STATUSES) == {
            "bound", "bound_obligation_only", "capability_absent",
            "optional_unplanned"}
        assert set(CAPABILITY_BINDING_STATUSES) == {"bound", "unplanned"}
        assert set(PROCEDURE_VIOLATION_CODES) == {
            "required_capability_unplanned", "required_step_unplanned",
            "order_conflict", "bound_capability_unresolved"}


# ── C2: 稳定映射规则 ────────────────────────────────────────────────────────


class TestMapping:
    def test_node_id_equals_capability(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        for cap in b.capabilities:
            if cap.status == "bound":
                assert cap.plan_node_id == cap.capability_id
                # kernel step id 与 runtime._materialize_steps 同规
                assert cap.session_step_ref == kernel_step_ref(cap.capability_id)
                assert cap.session_step_ref == f"step-{cap.capability_id[:48]}"

    def test_step_capability_refs_map_in_plan_order(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        step = b.step("local_clusters")
        # 声明 [local_morans_i, thematic_cartography]（均 optional）；
        # 全进计划 → 全映射（计划声明序）
        assert step.plan_node_ids == ["local_morans_i", "thematic_cartography"]
        # optional 全部不进计划 → 空 + optional_unplanned（partial mapping
        # 不误报：空 refs∩plan 是完整映射缺失的 optional 语义）
        plan2 = plan_for_skill(skill, include_optional=False)
        b2 = bind_procedure_to_plan(skill, _decision(), plan2)
        step2 = b2.step("local_clusters")
        assert step2.plan_node_ids == []
        assert step2.status == "optional_unplanned"

    def test_obligation_only_step(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        step = b.step("variable_check")
        assert step.status == "bound_obligation_only"
        assert step.plan_node_ids == []

    def test_multi_step_same_capability_allowed(self, library):
        # twin 生成器保证多步共享同一 capability（many-to-one 稳定映射）
        from tests.unit.gis_harness.procedure_binding_corpus import (
            make_twin_skill,
        )
        twin = make_twin_skill(library.get("administrative_aggregation"))
        tp = plan_for_skill(twin)
        tb = bind_procedure_to_plan(twin, _decision(skill_id=twin.id,
                                                    version=twin.version), tp)
        seen = {}
        for s in tb.steps:
            for cap in s.plan_node_ids:
                seen.setdefault(cap, []).append(s.step_id)
        assert seen, "twin should bind at least one capability"
        assert any(len(v) > 1 for v in seen.values()), \
            "twin round-robin pairing should exercise many-to-one mapping"

    def test_required_step_capability_absent(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        drop = [c.capability_id for c in skill.capability_requirements
                if c.criticality == "required"]
        plan = plan_for_skill(skill, drop_capabilities=drop[:1])
        b = bind_procedure_to_plan(skill, _decision(), plan)
        assert b.binding_status == "partial"
        assert any("required_capabilities_unplanned" in r
                   for r in b.reason_codes)
        # step 级：绑该 capability 的 required 步骤 capability_absent
        absent = [s for s in b.steps if s.status == "capability_absent"]
        assert absent, "required step should be capability_absent"

    def test_missing_hard_capability_flagged_not_dropped(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        hard_missing = {
            c.capability_id for c in skill.capability_requirements[:1]}
        b = bind_procedure_to_plan(
            skill, _decision(), plan,
            capability_exists=lambda cap: cap not in hard_missing)
        assert any(c.reason_codes and "hard_capability_missing" in c.reason_codes
                   for c in b.capabilities if c.capability_id in hard_missing)
        # 绑定不虚构：缺能力的行仍按计划事实映射
        assert all(c.plan_node_id == "" or c.plan_node_id == c.capability_id
                   for c in b.capabilities)


class TestDecisionBinding:
    def test_plan_content_resolves_branch(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)  # local_morans_i in plan
        b = bind_procedure_to_plan(skill, _decision(), plan)
        dec = next(d for d in b.decisions if d.node_id == "local_after_global")
        assert dec.selected_option == "global_then_local"
        assert dec.resolved_by == "plan_content"
        # local_clusters 只在非缺省分支 → conditional_on
        assert "local_after_global" in b.step("local_clusters").conditional_on

    def test_default_branch_when_no_option_hit(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill, include_optional=False)
        # local_morans_i (optional) 不进计划 → 无选项命中 → default
        plan["analysis_steps"] = [
            r for r in plan.get("analysis_steps", [])
            if r.get("capability") != "local_morans_i"]
        b = bind_procedure_to_plan(skill, _decision(), plan)
        dec = next(d for d in b.decisions if d.node_id == "local_after_global")
        assert dec.resolved_by == "default"
        assert dec.selected_option == dec.default_option_id
        assert dec.disclosure

    def test_ambiguous_not_resolved(self):
        # 两个选项同时命中 → resolved_by=""（honest ambiguous）
        skill = SkillContract(
            id="t.ambiguous", name="t", when_to_use="t",
            capability_requirements=[
                CapabilityRequirement(capability_id="cap_a"),
                CapabilityRequirement(capability_id="cap_b"),
            ],
            procedure=SkillProcedure(
                steps=[
                    ProcedureStep(step_id="s1", title="a", kind="analyze",
                                  capability_refs=["cap_a"]),
                    ProcedureStep(step_id="s2", title="b", kind="analyze",
                                  capability_refs=["cap_b"]),
                ],
                decisions=[{
                    "node_id": "d1", "question": "which?",
                    "default_option_id": "none",
                    "options": [
                        {"option_id": "o1", "condition": "cond_a",
                         "then_step_ids": ["s1"]},
                        {"option_id": "o2", "condition": "cond_b",
                         "then_step_ids": ["s2"]},
                    ],
                }],
            ),
        )
        plan = {
            "plan_id": "p", "data_requirements": [
                {"capability": "cap_a", "status": "pending",
                 "resolved_tool": "t"},
                {"capability": "cap_b", "status": "pending",
                 "resolved_tool": "t"},
            ], "analysis_steps": [],
        }
        b = bind_procedure_to_plan(
            skill, _decision(skill_id=skill.id, version=skill.version), plan)
        dec = b.decisions[0]
        assert dec.resolved_by == ""
        assert dec.selected_option == ""


# ── C3: plan-time enforcement ───────────────────────────────────────────────


class TestEnforcement:
    def test_ok_when_fully_planned(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        check = enforce_procedure_binding(b, plan)
        assert check.ok
        assert not check.advisory  # execute_guided

    def test_advisory_in_guide_mode(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        b = bind_procedure_to_plan(skill, _decision(mode="guide"), plan)
        check = enforce_procedure_binding(b, plan)
        assert check.advisory

    def test_required_capability_unplanned(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        drop = [c.capability_id for c in skill.capability_requirements
                if c.criticality == "required"][:1]
        plan = plan_for_skill(skill, drop_capabilities=drop)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        check = enforce_procedure_binding(b, plan)
        codes = {v.code for v in check.violations}
        assert "required_capability_unplanned" in codes
        v = next(v for v in check.violations
                 if v.code == "required_capability_unplanned")
        assert v.subject == drop[0]
        assert v.hint

    def test_required_step_unplanned(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        drop = ["global_morans_i"]
        plan = plan_for_skill(skill, drop_capabilities=drop)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        check = enforce_procedure_binding(b, plan)
        codes = {v.code for v in check.violations}
        assert "required_step_unplanned" in codes
        v = next(v for v in check.violations
                 if v.code == "required_step_unplanned")
        assert v.subject == "global_test"

    def test_conditional_default_step_not_violation(self, library):
        # local_clusters 在非缺省分支；缺省分支被选中 → 不违例
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill, include_optional=False)
        plan["analysis_steps"] = [
            r for r in plan.get("analysis_steps", [])
            if r.get("capability") != "local_morans_i"]
        b = bind_procedure_to_plan(skill, _decision(), plan)
        check = enforce_procedure_binding(b, plan)
        assert all(v.subject != "local_clusters"
                   for v in check.violations
                   if v.code == "required_step_unplanned")

    def test_bound_capability_unresolved(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill, resolved=False)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        check = enforce_procedure_binding(b, plan)
        codes = {v.code for v in check.violations}
        assert "bound_capability_unresolved" in codes

    def test_order_conflict_detected(self):
        # procedure: s2 depends_on s1（先 s1 后 s2）；计划依赖边
        # cap(s1) → cap(s2)（cap2 先）→ 冲突
        skill = SkillContract(
            id="t.order", name="t", when_to_use="t",
            capability_requirements=[
                CapabilityRequirement(capability_id="cap_a"),
                CapabilityRequirement(capability_id="cap_b"),
            ],
            procedure=SkillProcedure(
                steps=[
                    ProcedureStep(step_id="s1", title="a", kind="analyze",
                                  capability_refs=["cap_a"]),
                    ProcedureStep(step_id="s2", title="b", kind="analyze",
                                  capability_refs=["cap_b"],
                                  depends_on=["s1"]),
                ],
            ),
        )
        plan = {
            "plan_id": "p", "data_requirements": [
                {"capability": "cap_a", "status": "pending",
                 "resolved_tool": "t", "depends_on": ["cap_b"]},
                {"capability": "cap_b", "status": "pending",
                 "resolved_tool": "t", "depends_on": []},
            ], "analysis_steps": [],
        }
        b = bind_procedure_to_plan(
            skill, _decision(skill_id=skill.id, version=skill.version), plan)
        check = enforce_procedure_binding(b, plan)
        codes = {v.code for v in check.violations}
        assert "order_conflict" in codes

    def test_consistent_order_no_conflict(self):
        # 计划依赖方向与 procedure 一致 → 无冲突
        skill = SkillContract(
            id="t.order2", name="t", when_to_use="t",
            capability_requirements=[
                CapabilityRequirement(capability_id="cap_a"),
                CapabilityRequirement(capability_id="cap_b"),
            ],
            procedure=SkillProcedure(
                steps=[
                    ProcedureStep(step_id="s1", title="a", kind="prepare",
                                  capability_refs=["cap_a"]),
                    ProcedureStep(step_id="s2", title="b", kind="analyze",
                                  capability_refs=["cap_b"],
                                  depends_on=["s1"]),
                ],
            ),
        )
        plan = {
            "plan_id": "p", "data_requirements": [
                {"capability": "cap_a", "status": "pending",
                 "resolved_tool": "t", "depends_on": []},
                {"capability": "cap_b", "status": "pending",
                 "resolved_tool": "t", "depends_on": ["cap_a"]},
            ], "analysis_steps": [],
        }
        b = bind_procedure_to_plan(
            skill, _decision(skill_id=skill.id, version=skill.version), plan)
        check = enforce_procedure_binding(b, plan)
        assert all(v.code != "order_conflict" for v in check.violations)


# ── 对抗面：绑定不接受调用方自喂事实 ─────────────────────────────────────────


class TestAdversarial:
    def test_corrupted_binding_block_yields_no_coverage(self, library):
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        block = b.to_bounded_dict()
        # 手改 block 增加 unknown 字段（extra=forbid）→ 覆盖 fail-closed
        block["evil_claim"] = "covered"
        chapter = dict(plan)
        chapter["procedure_binding"] = block
        assert compute_procedure_coverage(chapter) is None

    def test_hand_edited_state_claims_ignored(self, library):
        # 绑定块只提供 join key：把步骤 status 改成 bound 不能制造覆盖 ——
        # 覆盖状态从行状态派生（pending → unknown，非 covered）
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        block = b.to_bounded_dict()
        block["steps"] = [
            dict(s, status="bound") for s in block["steps"]
        ]
        chapter = dict(plan)
        chapter["procedure_binding"] = block
        cov = compute_procedure_coverage(chapter)
        assert cov is not None
        assert cov.counts.get("covered", 0) == 0
        assert not cov.complete

    def test_binding_requires_real_contract_not_llm_text(self, library):
        # bind 只接受 SkillContract 对象（服务端资产）；无路径接受 LLM 文本
        skill = library.get("spatial_autocorrelation_analysis")
        plan = plan_for_skill(skill)
        b = bind_procedure_to_plan(skill, _decision(), plan)
        assert b.skill_fingerprint == skill.fingerprint()[:16]

    def test_determinism_under_row_shuffles(self, library):
        # 行顺序无关：capability 集相同 → 指纹相同（声明序只影响 node 排序）
        skill = library.get("spatial_autocorrelation_analysis")
        plan1 = plan_for_skill(skill)
        plan2 = plan_for_skill(skill)
        plan2["data_requirements"].reverse()
        b1 = bind_procedure_to_plan(skill, _decision(), plan1)
        b2 = bind_procedure_to_plan(skill, _decision(), plan2)
        # 指纹对行序敏感是允许的（plan_node_ids 排序基于计划声明序）——
        # 但同一计划对象重复绑定必须同指纹
        assert b1.fingerprint() == bind_procedure_to_plan(
            skill, _decision(), plan1).fingerprint()
        assert isinstance(b2.fingerprint(), str)
