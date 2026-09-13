"""Skill → Capability / Product / Execution 桥接层（ADR-0182 §2；goal S3/S22/S28/S30/S31）。

全部是**引用与投影**，不复制任何 registry：

- `default_capability_exists`：能力存在性谓词（CapabilityRegistry）；
  Capability Graph 分支（#1274/#1275 之外那支）的 `resolve_capabilities`
  合并后可在此增强，契约不变；
- `project_product_requirements`：Skill 的 product_requirements →
  MapProduct/组件期望投影（只出 requirements，不写 MapSpec paint）；
- `project_capability_plan_inputs`：Skill 契约 → 规划侧需求投影
  （capability id 列表 + 数据角色 + 完成证据），供 planner/evaluator
  消费；本层不做调度（ExecutionGraph 职责仍归 SessionPlan/PlanGraph）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from app.services.gis_harness.skills.contract import SkillContract


def default_capability_exists(capability_id: str) -> bool:
    """CapabilityRegistry 存在性谓词（registry 缺席时保守返回 False）。"""
    try:
        from app.lib.gis.capability_registry import get_capability_registry
        return get_capability_registry().has(capability_id)
    except Exception:  # noqa: BLE001 - 校验面宁可保守，不虚报存在
        return False


def default_recipe_exists(recipe_id: str) -> bool:
    try:
        from app.services.gis_harness.recipes import get_recipe_registry
        return recipe_id in get_recipe_registry()
    except Exception:  # noqa: BLE001
        return False


def default_ontology_task_exists(task_id: str) -> bool:
    try:
        from app.services.gis_harness.gis_ontology import get_task_ontology
        return get_task_ontology().has(task_id)
    except Exception:  # noqa: BLE001
        return False


def default_artifact_type_exists(artifact_type: str) -> bool:
    try:
        from app.lib.gis.artifacts import get_artifact_type_registry
        return get_artifact_type_registry().has(artifact_type)
    except Exception:  # noqa: BLE001
        return False


def default_precondition_exists(precondition_id: str) -> bool:
    try:
        from app.lib.gis.scientific_preconditions import precondition_exists
        return bool(precondition_exists(precondition_id))
    except Exception:  # noqa: BLE001
        return False


def default_validation_predicates() -> Dict[str, Callable[[str], bool]]:
    """校验谓词集合（loader / registry_validation 共用；可测试注入覆写）。"""
    return {
        "capability_exists": default_capability_exists,
        "recipe_exists": default_recipe_exists,
        "ontology_task_exists": default_ontology_task_exists,
        "artifact_type_exists": default_artifact_type_exists,
        "precondition_exists": default_precondition_exists,
    }


def missing_capabilities(
    skill: SkillContract,
    *,
    capability_exists: Optional[Callable[[str], bool]] = None,
) -> List[str]:
    """运行期能力缺口清单（required 且 hard_gate 的能力不可用 = 技能不可行，
    触发 fallback 而不是静默降级）。"""
    predicate = capability_exists or default_capability_exists
    return sorted(
        req.capability_id for req in skill.capability_requirements
        if req.criticality == "required" and req.hard_gate
        and not predicate(req.capability_id)
    )


def project_product_requirements(skill: SkillContract) -> Dict[str, Any]:
    """Skill → MapProduct 需求投影（S22）。

    只声明"产品应满足什么"（组件期望 + 产物类型 + 完成证据），具体
    paint/模板/导出决策仍由 recipe / template / MapSpec 体系负责。
    """
    return {
        "skill_id": skill.id,
        "skill_version": skill.version,
        "component_expectations": list(skill.product_requirements),
        "output_artifacts": list(skill.output_artifacts),
        "completion_evidence": list(skill.completion_evidence),
    }


def project_capability_plan_inputs(skill: SkillContract) -> Dict[str, Any]:
    """Skill 契约 → 规划侧需求投影（planner/evaluator 的消费面）。

    纯函数、有界、无调度语义 —— S31 红线：Skill Procedure 是语义过程，
    运行期执行由既有 SessionPlan / PlanGraph 承担。
    """
    return {
        "skill_id": skill.id,
        "skill_version": skill.version,
        "required_capabilities": skill.capability_ids(required_only=True),
        "optional_capabilities": [
            r.capability_id for r in skill.capability_requirements
            if r.criticality == "optional"
        ],
        "input_roles": list(skill.input_roles),
        "output_roles": list(skill.output_roles),
        "completion_evidence": list(skill.completion_evidence),
        "procedure_step_ids": [s.step_id for s in skill.procedure.steps],
    }


__all__ = [
    "default_capability_exists",
    "default_recipe_exists",
    "default_ontology_task_exists",
    "default_artifact_type_exists",
    "default_precondition_exists",
    "default_validation_predicates",
    "missing_capabilities",
    "project_product_requirements",
    "project_capability_plan_inputs",
]
