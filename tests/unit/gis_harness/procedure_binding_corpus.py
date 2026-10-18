"""ProcedureBinding/ProcedureCoverage deterministic corpus（ADR-0219 §D）。

语料族（对齐任务 §D）× 场景矩阵，全部确定性生成（零网络/零 LLM/零随机）：

族（family → 真实库技能 + 同构合成 fixture）：

- distribution_overview      → point_distribution_analysis
- administrative_statistic   → administrative_aggregation
- density_rate               → density_hotspot_analysis（denominator 义务）
- hotspot                    → spatial_autocorrelation_analysis（已带
                                 capability_refs 的库内技能）
- proximity_accessibility    → buffer_proximity_analysis
- raster_rs                  → raster_zonal_statistics
- temporal_comparison        → temporal_comparison_workflow（denominator 义务）
- export_presentation        → publication_map_design
- skill_composition          → prepare_vector_for_mapping +
                                 point_distribution_analysis（既有 topo 序，
                                 绑定保持 per-skill）
- fallback_missing_capability→ fixture（硬能力缺席）

场景（scenario）：baseline / required_step_skipped / wrong_order /
stale_evidence / wrong_skill_version / optional_skip / fallback /
style_only_followup / capability_denied / cancellation_resume。

合成 fixture 说明：库内多数技能的 procedure steps 尚未声明
``capability_refs``（历史形态）。为在全部族上驱动 step 级状态机，本语料
对每个真实技能**生成一个合成孪生**（copy 契约 + 按 kind 规则确定性配对
capability_refs）—— 只用于状态机覆盖测试，不构成语义主张（真实语义配对
以库内声明为准；spatial_autocorrelation_analysis 是库内已声明的真样本）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_harness.skills.contract import SkillContract
from app.services.gis_harness.skills.loader import get_skill_library
from app.services.gis_harness.skills.policy import SkillPolicyDecision
from app.services.gis_harness.skills.procedure_binding import (
    ProcedureBinding,
    bind_procedure_to_plan,
    enforce_procedure_binding,
)
from app.services.gis_harness.skills.procedure_coverage import (
    ProcedureCoverageReport,
    compute_procedure_coverage,
)

#: 族 → 真实库技能 id。
FAMILY_SKILLS: Dict[str, str] = {
    "distribution_overview": "point_distribution_analysis",
    "administrative_statistic": "administrative_aggregation",
    "density_rate": "density_hotspot_analysis",
    "hotspot": "spatial_autocorrelation_analysis",
    "proximity_accessibility": "buffer_proximity_analysis",
    "raster_rs": "raster_zonal_statistics",
    "temporal_comparison": "temporal_comparison_workflow",
    "export_presentation": "publication_map_design",
}

#: 合成孪生使用的前缀（不与库内 id 冲突）。
FIXTURE_PREFIX = "fixture.procedure_corpus."

#: step kind → 期望的 capability criticality（合成配对规则的一部分）。
_KIND_CAP_ORDER = ("inspect", "prepare", "analyze", "decide",
                   "design", "validate", "deliver")


def _decision(mode: str = "execute_guided") -> SkillPolicyDecision:
    return SkillPolicyDecision(
        mode=mode, selected_skill="", skill_version="1.0.0",
        trust_tier="core", confidence=0.9,
    )


def make_twin_skill(skill: SkillContract) -> SkillContract:
    """真实技能 → 合成孪生（确定性 capability_refs 配对）。

    配对规则（确定性、可解释）：按契约 required 能力声明序轮转分配给
    非决策步骤；``decide`` 步骤不绑能力（方法选择无独立能力面）。
    """
    payload = skill.model_dump()
    required_caps = [
        c["capability_id"] for c in payload.get("capability_requirements", [])
        if c.get("criticality") == "required"
    ] or [
        c["capability_id"] for c in payload.get("capability_requirements", [])
    ]
    cap_iter = iter(required_caps * (len(payload["procedure"]["steps"]) + 1))
    for step in payload["procedure"]["steps"]:
        if step.get("kind") == "decide":
            step["capability_refs"] = []
            continue
        step["capability_refs"] = [next(cap_iter)]
    payload["id"] = FIXTURE_PREFIX + skill.id
    payload["pack"] = "core"
    return SkillContract.model_validate(payload)


def plan_for_skill(
    skill: SkillContract,
    *,
    include_optional: bool = True,
    drop_capabilities: Optional[List[str]] = None,
    resolved: bool = True,
    plan_id: str = "plan-corpus",
) -> Dict[str, Any]:
    """从技能契约确定性生成计划 chapter（声明序 = 契约能力序）。"""
    drop = set(drop_capabilities or [])
    reqs: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    for i, cap in enumerate(skill.capability_requirements):
        if cap.capability_id in drop:
            continue
        if cap.criticality == "required":
            row = reqs
        else:
            if not include_optional:
                continue
            row = steps
        row.append({
            "capability": cap.capability_id,
            "purpose": cap.purpose,
            "status": "pending",
            "resolved_tool": f"tool_{cap.capability_id}" if resolved else "",
            "resolved_algorithm": "algo_x" if resolved else "",
            "depends_on": [],
        })
        if i > 0 and row:
            pass  # 依赖边由场景按需注入
    return {
        "plan_id": plan_id,
        "manifest_fingerprint": f"fp-{plan_id}",
        "query": f"query for {skill.id}",
        "recipe_id": (skill.recipe_refs or ["none"])[0],
        "data_requirements": reqs,
        "analysis_steps": steps,
    }


def bind(
    skill: SkillContract,
    plan: Dict[str, Any],
    *,
    mode: str = "execute_guided",
    capability_exists=None,
) -> ProcedureBinding:
    dec = _decision(mode)
    dec.selected_skill = skill.id
    dec.skill_version = skill.version
    return bind_procedure_to_plan(
        skill, dec, plan, capability_exists=capability_exists)


def complete_all(chapter: Dict[str, Any], *, skip: Optional[List[str]] = None
                 ) -> Dict[str, Any]:
    """确定性推进：全部行 complete。

    ``skip``（replace 失效面）不再把 chapter 行写成 "voided"（生产中
    不存在该词表 —— review P2-10）：行回 pending，void 事实由
    ``kernel_envelope_for(chapter, voided=...)`` 的 progress 行承载。
    """
    skip = set(skip or [])
    for key in ("data_requirements", "analysis_steps"):
        for row in chapter.get(key) or []:
            cap = str(row.get("capability") or "")
            if cap in skip:
                row["status"] = "pending"
                row["bound_ref"] = ""
            else:
                row["status"] = "complete"
                row["bound_ref"] = f"ref:{cap}"
    return chapter


def voided_caps(chapter: Dict[str, Any]) -> List[str]:
    """stale 场景配套：提取 chapter 中 pending（= 曾被 void）的 capability。

    与 `_scenario_stale_evidence` 的 skip 集合同源 —— envelope 构建时
    以 voided= 传入。
    """
    return [
        str(row.get("capability") or "")
        for key in ("data_requirements", "analysis_steps")
        for row in chapter.get(key) or []
        if row.get("status") == "pending"
    ]


def kernel_envelope_for(
    chapter: Dict[str, Any],
    *,
    voided: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """为 chapter 生成 kernel envelope 投影。

    - steps：每 complete 行一步一证据（tool_call_id 确定性）；
    - progress：replace 语义的 voided 行（真实词表：chapter 行被新计划
      替换为 pending，**progress 行**带 bound_ref 置 voided —— review
      P2-10：chapter 行在生产中永不为 "voided"）。
    """
    steps = []
    progress = []
    voided_set = set(voided or [])
    for key in ("data_requirements", "analysis_steps"):
        for row in chapter.get(key) or []:
            cap = str(row.get("capability") or "")
            if not cap:
                continue
            if row.get("status") == "complete":
                steps.append({
                    "id": f"step-{cap[:48]}",
                    "capability": cap,
                    "status": "succeeded",
                    "evidence": [{
                        "tool": str(row.get("resolved_tool") or f"tool_{cap}"),
                        "tool_call_id": f"call-{cap}",
                        "ref": str(row.get("bound_ref") or ""),
                    }],
                })
                progress.append({
                    "capability": cap,
                    "status": "voided" if cap in voided_set else "complete",
                    "bound_ref": str(row.get("bound_ref") or ""),
                })
            elif cap in voided_set:
                progress.append({
                    "capability": cap, "status": "voided",
                    "bound_ref": f"ref:{cap}",
                })
    return {"steps": steps, "progress": progress}


@dataclass
class CorpusCase:
    """一个语料用例：族 × 场景 × chapter/envelope + 期望。"""

    family: str
    scenario: str
    skill_id: str
    chapter: Dict[str, Any]
    envelope: Dict[str, Any]
    expect: Dict[str, Any] = field(default_factory=dict)

    @property
    def case_id(self) -> str:
        return f"{self.family}/{self.scenario}"

    def coverage(self, **kwargs) -> Optional[ProcedureCoverageReport]:
        return compute_procedure_coverage(
            self.chapter, envelope=self.envelope, **kwargs)


def _scenario_baseline(skill, chapter, expect):
    complete_all(chapter)
    expect["all_caps_covered"] = True
    expect["no_missing"] = True
    expect["violations_empty"] = True


def _scenario_required_step_skipped(skill, chapter, expect):
    # 计划里丢掉第一个 required 能力 → required_capability_unplanned 违例
    # + capability/step missing。
    drop = next(
        (c.capability_id for c in skill.capability_requirements
         if c.criticality == "required"), None)
    if drop:
        for key in ("data_requirements", "analysis_steps"):
            chapter[key] = [
                r for r in chapter.get(key) or []
                if r.get("capability") != drop]
    complete_all(chapter)
    expect["violation_codes"] = ["required_capability_unplanned"]
    expect["has_missing"] = True


def _scenario_wrong_order(skill, chapter, expect):
    # 反转前两个 required 能力的依赖方向 → order_conflict（仅当步骤
    # capability_refs 显式 depends_on 存在时触发；合成孪生 s2 depends_on s1
    # 不在语料内构造 —— 用显式注入覆盖）。
    reqs = chapter.get("data_requirements") or []
    if len(reqs) >= 2:
        # 计划依赖方向与声明序相反（reqs[0] 反而依赖 reqs[1]）——
        # order_conflict 的触发面由 test_procedure_binding_v1 的显式
        # 用例覆盖；此处只构造反向边。
        reqs[0]["depends_on"] = [reqs[1]["capability"]]
    complete_all(chapter)
    expect["maybe_order_conflict"] = True


def _scenario_stale_evidence(skill, chapter, expect):
    # 一半行 void（supersede/replace 语义）→ 对应 capability/step stale。
    caps = [
        r["capability"] for key in ("data_requirements", "analysis_steps")
        for r in chapter.get(key) or []
    ]
    complete_all(chapter, skip=caps[: max(1, len(caps) // 2)])
    expect["has_stale"] = True
    expect["fully_covered_false"] = True


def _scenario_optional_skip(skill, chapter, expect):
    # 丢掉全部 optional 能力行 → optional 未计划（不违例、不 missing）。
    opt = [
        c.capability_id for c in skill.capability_requirements
        if c.criticality == "optional"
    ]
    for key in ("data_requirements", "analysis_steps"):
        chapter[key] = [
            r for r in chapter.get(key) or []
            if r.get("capability") not in set(opt)]
    complete_all(chapter)
    expect["optional_unplanned_ok"] = True
    expect["no_missing"] = True


def _scenario_fallback(skill, chapter, expect):
    # required 能力缺一个 + 行不可用 → blocked + binding partial。
    complete_all(chapter)
    reqs = chapter.get("data_requirements") or []
    if reqs:
        reqs[0]["status"] = "unavailable"
    expect["has_blocked"] = True


def _scenario_style_only_followup(skill, chapter, expect):
    # follow-up 只改样式：完成事实 carried（complete），无重跑 ——
    # coverage 不变（同 chapter 重算结果一致 = 证据不重复计数）。
    complete_all(chapter)
    expect["idempotent_recompute"] = True
    expect["no_missing"] = True


def _scenario_capability_denied(skill, chapter, expect):
    # 能力被拒（unavailable 行 + 依赖者 blocked 传播在 plan_graph 层；
    # coverage 只看行状态）。
    complete_all(chapter)
    rows = (chapter.get("data_requirements") or []) + (
        chapter.get("analysis_steps") or [])
    if rows:
        rows[0]["status"] = "unavailable"
    expect["has_blocked"] = True


def _scenario_cancellation_resume(skill, chapter, expect):
    # 取消后重试成功：failed → complete 覆写 → covered（可恢复）。
    rows = (chapter.get("data_requirements") or []) + (
        chapter.get("analysis_steps") or [])
    complete_all(chapter)
    if rows:
        # 先 failed 再恢复 complete（语料直接呈现恢复后状态 + 一条
        # failed 历史 evidence）
        chapter.setdefault("_history_failed", [rows[0]["capability"]])
    expect["no_missing"] = True


_SCENARIOS = {
    "baseline": _scenario_baseline,
    "required_step_skipped": _scenario_required_step_skipped,
    "wrong_order": _scenario_wrong_order,
    "stale_evidence": _scenario_stale_evidence,
    "optional_skip": _scenario_optional_skip,
    "fallback": _scenario_fallback,
    "style_only_followup": _scenario_style_only_followup,
    "capability_denied": _scenario_capability_denied,
    "cancellation_resume": _scenario_cancellation_resume,
}


def build_corpus(
    *,
    library=None,
    families: Optional[List[str]] = None,
    use_twins: bool = True,
) -> List[CorpusCase]:
    """生成语料矩阵（族 × 场景；确定性）。"""
    lib = library or get_skill_library()
    cases: List[CorpusCase] = []
    fams = families or list(FAMILY_SKILLS)
    for family in fams:
        skill_id = FAMILY_SKILLS[family]
        real = lib.get(skill_id)
        if real is None:
            continue
        skills = [real]
        if use_twins:
            skills.append(make_twin_skill(real))
        for skl in skills:
            for name, mutate in _SCENARIOS.items():
                plan = plan_for_skill(skl)
                binding = bind(skl, plan)
                if binding is None:
                    continue
                chapter: Dict[str, Any] = dict(plan)
                chapter["procedure_binding"] = binding.to_bounded_dict()
                expect: Dict[str, Any] = {
                    "binding_status": binding.binding_status,
                }
                mutate(skl, chapter, expect)
                envelope = kernel_envelope_for(
                    chapter, voided=voided_caps(chapter))
                cases.append(CorpusCase(
                    family=family,
                    scenario=name,
                    skill_id=skl.id,
                    chapter=chapter,
                    envelope=envelope,
                    expect=expect,
                ))
    return cases


def build_drift_cases(*, library=None) -> List[CorpusCase]:
    """C6 drift 场景：wrong_skill_version / deprecated / missing。"""
    lib = library or get_skill_library()
    real = lib.get("spatial_autocorrelation_analysis")
    plan = plan_for_skill(real)
    binding = bind(real, plan)
    chapter = dict(plan)
    chapter["procedure_binding"] = binding.to_bounded_dict()
    complete_all(chapter)

    # wrong version：手改绑定块的 version（模拟技能升级后旧绑定回放）
    bumped = dict(plan)
    blk = binding.to_bounded_dict()
    blk["skill_version"] = "9.9.9"
    bumped["procedure_binding"] = blk
    complete_all(bumped)

    # missing：绑定引用不存在的技能
    missing_blk = binding.to_bounded_dict()
    missing_blk["skill_id"] = "fixture.procedure_corpus.nope"
    gone = dict(plan)
    gone["procedure_binding"] = missing_blk
    complete_all(gone)

    return [
        CorpusCase(
            family="hotspot", scenario="wrong_skill_version",
            skill_id=real.id, chapter=bumped,
            envelope=kernel_envelope_for(bumped),
            expect={"drift": ["skill_version_drift", "skill_fingerprint_drift"]},
        ),
        CorpusCase(
            family="hotspot", scenario="skill_missing",
            skill_id=real.id, chapter=gone,
            envelope=kernel_envelope_for(gone),
            expect={"drift": ["skill_missing"]},
        ),
    ]


def build_composition_case(*, library=None) -> CorpusCase:
    """skill_composition 族：两个技能顺序（既有 topo 序），绑定 per-skill。

    prepare_vector_for_mapping（前置）+ point_distribution_analysis（主）：
    两份绑定各自独立覆盖，互不串扰（组合不产生第二 executor）。
    """
    lib = library or get_skill_library()
    prep = lib.get("prepare_vector_for_mapping")
    main = lib.get("point_distribution_analysis")
    plan = plan_for_skill(main)
    # 前置技能的能力并入同一计划（组合 = 能力并集；既有 composition 语义）
    for cap in prep.capability_requirements:
        plan["data_requirements"].append({
            "capability": cap.capability_id,
            "purpose": cap.purpose,
            "status": "pending",
            "resolved_tool": f"tool_{cap.capability_id}",
            "resolved_algorithm": "algo_x",
            "depends_on": [],
        })
    b_main = bind(main, plan)
    b_prep = bind(prep, plan)
    chapter = dict(plan)
    chapter["procedure_binding"] = b_main.to_bounded_dict()
    chapter["_prep_binding"] = b_prep.to_bounded_dict()
    complete_all(chapter)
    return CorpusCase(
        family="skill_composition", scenario="composition_two_skills",
        skill_id=main.id, chapter=chapter,
        envelope=kernel_envelope_for(chapter),
        expect={"no_missing": True, "prep_binding_present": True},
    )


def build_fallback_capability_case(*, library=None) -> CorpusCase:
    """fallback_missing_capability 族：硬能力缺席（registry 谓词返回 False）。"""
    lib = library or get_skill_library()
    real = lib.get("buffer_proximity_analysis")
    twin = make_twin_skill(real)
    first_req = next(
        (c.capability_id for c in twin.capability_requirements
         if c.criticality == "required"), "")
    # registry 缺席 → planner 无法为其发计划行（计划内无行）
    plan = plan_for_skill(twin, drop_capabilities=[first_req])
    binding = bind(
        twin, plan,
        capability_exists=lambda cap: cap != first_req)
    chapter = dict(plan)
    chapter["procedure_binding"] = binding.to_bounded_dict()
    complete_all(chapter)
    check = enforce_procedure_binding(binding, chapter)
    return CorpusCase(
        family="fallback_missing_capability", scenario="hard_capability_missing",
        skill_id=twin.id, chapter=chapter,
        envelope=kernel_envelope_for(chapter),
        expect={
            "binding_partial": True,
            "violation_codes": ["required_capability_unplanned"],
            "check_ok": check.ok,
        },
    )


def corpus_summary(cases: List[CorpusCase]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in cases:
        out[c.case_id] = out.get(c.case_id, 0) + 1
    return out
