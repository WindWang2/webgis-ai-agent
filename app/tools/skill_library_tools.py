"""GIS Skill Library 只读工具面（ADR-0182 §2.6；goal S17/S19/S20/S25）。

3 个只读工具，全部以 ``SkillLibrary`` 为界（零副作用、确定性、有界投影）：
LLM 可问「有哪些技能、某个技能的完整作业过程、它的义务是否已被
plan/evidence 覆盖」，但**看不到也不能改**技能资产，且第一步只看到
SkillCard（渐进披露，不注入全文）。

与 #1274 Typed Tool Surface 的边界：本模块只新增 tier-2 知识咨询工具，
不修改 Pi schema、不建 SkillAgent；与 knowledge_tools 同款注册模式
（``_TOOL_MODULES`` 一行）。

选择证据（skill_selected 等）经 :mod:`evidence` 记录器留痕（有界、
无 CoT），供 Goal Evaluator / replay 消费。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

# 会话级证据记录器（有界环形；进程级共享，测试可 reset）
_recorder = None


def get_skill_evidence_recorder():
    """进程级 SkillEvidenceRecorder（惰性单例；测试用 reset 函数清理）。"""
    global _recorder
    if _recorder is None:
        from app.services.gis_harness.skills.evidence import (
            SkillEvidenceRecorder,
        )
        _recorder = SkillEvidenceRecorder()
    return _recorder


def reset_skill_evidence_recorder() -> None:
    global _recorder
    _recorder = None


# ── args models（typed schema；public contract）─────────────────────────

class SkillSearchArgs(BaseModel):
    query: str = Field(..., description="用户自然语言 GIS 目标（≤200 字）")
    task_type: str = Field("", description="已知 intent task family（可选）")
    geometry_kinds: str = Field(
        "", description="已知数据几何类别，逗号分隔（point,line,polygon,raster；可选）")
    ontology_tasks: str = Field(
        "", description="已匹配的本体任务 id，逗号分隔（可选）")


class SkillDetailArgs(BaseModel):
    skill_id: str = Field(..., description="技能 id（如 point_distribution_analysis）")



class SkillPolicyArgs(BaseModel):
    query: str = Field(..., description="用户自然语言 GIS 目标（≤200 字）")
    task_type: str = Field("", description="已知 intent task family（可选）")
    geometry_kinds: str = Field(
        "", description="已知数据几何类别，逗号分隔（可选）")
    ontology_tasks: str = Field(
        "", description="已匹配的本体任务 id，逗号分隔（可选）")
    prefer_execute: bool = Field(
        True, description="高置信 core 时是否进入 execute_guided")


class SkillReplayArgs(BaseModel):
    skill_id: str = Field(..., description="技能 id")
    plan_facts: Optional[Dict[str, Any]] = Field(
        None, description="计划投影（capabilities/step_ids/evidence_kinds/...）")
    evidence_facts: Optional[Dict[str, Any]] = Field(
        None, description="执行证据投影（evidence_kinds/skipped_steps/...）")


def register_skill_library_tools(registry: ToolRegistry):
    """注册技能库只读工具（tier 2：查询廉价、无副作用）。"""

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="gis_skill_search",
        description=(
            "GIS 技能检索（确定性）。输入自然语言目标，返回候选技能的 SkillCard"
            "（id/用途/要求；渐进披露第一层）与被拒原因、澄清建议。"
            "\n何时用：规划一个 GIS 作业前，先查『有哪些标准作业方法（skill）可用』；"
            "选中的技能再经 gis_skill_detail 读取完整过程。"
        ),
        args_model=SkillSearchArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "技能", "skill", "方法", "knowledge"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def gis_skill_search(query: str, task_type: str = "",
                         geometry_kinds: str = "",
                         ontology_tasks: str = "") -> dict:
        from app.services.gis_harness.skills.loader import get_skill_library
        from app.services.gis_harness.skills.situation import SelectionFacts

        facts = SelectionFacts(
            goal_text=query[:200],
            task_type=task_type[:40],
            geometry_kinds=[g.strip() for g in geometry_kinds.split(",") if g.strip()],
            ontology_matches=[t.strip() for t in ontology_tasks.split(",") if t.strip()],
        )
        lib = get_skill_library()
        result = lib.resolver.resolve(facts)
        recorder = get_skill_evidence_recorder()
        if result.selected:
            top = result.top
            skill = lib.get(result.selected)
            recorder.record_selection(
                skill_id=result.selected,
                skill_version=skill.version if skill else "",
                selection_reason=top.matched_signals if top else [],
                confidence=top.confidence if top else 0.0,
            )
        return {
            "selection": result.to_bounded_dict(),
            "clarification": (
                result.clarification.model_dump() if result.clarification else None),
        }

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="gis_skill_detail",
        description=(
            "技能完整契约读取（渐进披露第二层；先 search 选中后再调用）。"
            "返回作业步骤 IR、决策点、统计/地理/时间语义、质量义务与回退策略。"
            "\n何时用：已选定技能、准备按其 procedure 执行作业时。"
        ),
        args_model=SkillDetailArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "技能", "过程", "knowledge"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def gis_skill_detail(skill_id: str) -> dict:
        from app.services.gis_harness.skills.catalog import SkillCatalog
        from app.services.gis_harness.skills.loader import get_skill_library

        lib = get_skill_library()
        return SkillCatalog(lib.skills).detail(skill_id[:64])

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="gis_skill_replay_check",
        description=(
            "技能过程重放校验（确定性）。对照 plan/evidence 投影，逐项核查"
            "技能的必需步骤与义务是否被覆盖（covered/missing/skipped_declared）。"
            "\n何时用：作业完成或验收前，验证『该做的步骤都做了、该留的证据都在』。"
        ),
        args_model=SkillReplayArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "技能", "重放", "验收", "knowledge"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def gis_skill_replay_check(skill_id: str,
                               plan_facts: Optional[Dict[str, Any]] = None,
                               evidence_facts: Optional[Dict[str, Any]] = None) -> dict:
        from app.services.gis_harness.skills.loader import get_skill_library
        from app.services.gis_harness.skills.replay import replay_procedure

        lib = get_skill_library()
        skill = lib.get(skill_id[:64])
        if skill is None:
            return {"error": f"unknown skill {skill_id[:64]}"}
        # 只对账调用方显式传入的投影 —— 不混入进程级 recorder 状态
        #（recorder 跨会话共享，混入会互染验收结论；它只作咨询留痕）。
        report = replay_procedure(skill, plan_facts, evidence_facts)
        return report.to_bounded_dict()


    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="gis_skill_policy",
        description=(
            "GIS 生产技能策略裁决（确定性）。规划前询问『当前目标是否应信任某技能』："
            "返回 mode（guide/execute_guided/fallback/none/blocked）、信任档、"
            "有界规划投影与回落原因。无合适技能时干净回落既有 harness planning。"
            "\n不授予能力/安全绕过权；induced 不进入本工具受信路径。"
        ),
        args_model=SkillPolicyArgs,
        side_effect="pure", deterministic=True,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "技能", "policy", "规划", "knowledge"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    def gis_skill_policy(query: str, task_type: str = "",
                         geometry_kinds: str = "",
                         ontology_tasks: str = "",
                         prefer_execute: bool = True) -> dict:
        from app.services.gis_harness.skills.hotpath import resolve_skill_guidance
        from app.services.gis_harness.skills.situation import SelectionFacts

        facts = SelectionFacts(
            goal_text=query[:200],
            task_type=task_type[:40],
            geometry_kinds=[g.strip() for g in geometry_kinds.split(",") if g.strip()],
            ontology_matches=[t.strip() for t in ontology_tasks.split(",") if t.strip()],
        )
        bundle = resolve_skill_guidance(
            facts, prefer_execute=prefer_execute, allow_shadow=False,
        )
        return bundle.to_bounded_dict()



__all__ = [
    "register_skill_library_tools",
    "get_skill_evidence_recorder",
    "reset_skill_evidence_recorder",
]
