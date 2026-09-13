"""Skill 库静态校验（ADR-0182 §2.5；goal S16）。

覆盖：重复 skill id、悬空 capability/recipe/ontology/artifact/precondition
引用、悬空 fallback/composition/deprecated 引用、步骤依赖环、非法决策节点、
未知证据种类、deprecated 依赖、版本/词汇违规。

全部纯函数；loader 装载期与 ``registry_validation.validate_gis_library``
共用 —— **启动时 fail loud**，不允许静默悬空。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from app.services.gis_harness.skills.composition import SkillComposition
from app.services.gis_harness.skills.contract import SkillContract
from app.services.gis_harness.skills.procedure_ir import MAX_FALLBACKS, MAX_STEPS


def validate_skill_library(
    skills: List[SkillContract],
    *,
    compositions: Optional[List[SkillComposition]] = None,
    capability_exists: Optional[Callable[[str], bool]] = None,
    recipe_exists: Optional[Callable[[str], bool]] = None,
    ontology_task_exists: Optional[Callable[[str], bool]] = None,
    artifact_type_exists: Optional[Callable[[str], bool]] = None,
    precondition_exists: Optional[Callable[[str], bool]] = None,
) -> List[str]:
    """全库校验。返回违规列表（空 = 通过）。纯函数，谓词可注入。"""
    violations: List[str] = []
    by_id: Dict[str, SkillContract] = {}
    for skill in skills:
        if skill.id in by_id:
            violations.append(f"skill_library: duplicate skill id {skill.id}")
        by_id[skill.id] = skill

    def _sid(skill_id: str) -> bool:
        return skill_id in by_id

    for skill in by_id.values():
        violations.extend(skill.validate_contract(
            capability_exists=capability_exists,
            recipe_exists=recipe_exists,
            ontology_task_exists=ontology_task_exists,
            artifact_type_exists=artifact_type_exists,
            precondition_exists=precondition_exists,
            skill_id_exists=_sid,
        ))
        # 悬空 alternative_skill 引用（fallback 节点指向的技能必须存在或
        # 是本体任务词表内的路径；此处按技能 id 对账）
        for fb in skill.procedure.fallbacks:
            if fb.action == "alternative_skill" and fb.fallback_skill_id \
                    and fb.fallback_skill_id not in by_id:
                violations.append(
                    f"skill[{skill.id}]: fallback[{fb.node_id}] 悬空替代技能 "
                    f"{fb.fallback_skill_id}")

    # deprecated 依赖对账（fatal：版本化资产链上的弃用必须显式跟进）：
    # 存活技能的 alternative_skill 目标 / 组合成员若指向已弃用技能 ——
    # 而 resolver 默认排除 deprecated —— 会建议一个自己必然拒绝的目标。
    for skill in by_id.values():
        if skill.deprecated:
            continue
        for fb in skill.procedure.fallbacks:
            if fb.action != "alternative_skill" or not fb.fallback_skill_id:
                continue
            target = by_id.get(fb.fallback_skill_id)
            if target is not None and target.deprecated:
                violations.append(
                    f"skill[{skill.id}]: fallback[{fb.node_id}] 指向已弃用技能 "
                    f"{fb.fallback_skill_id}（resolver 默认排除 deprecated）")
    for comp in compositions or []:
        for m in comp.members:
            target = by_id.get(m.skill_id)
            if target is not None and target.deprecated:
                violations.append(
                    f"composition[{comp.composition_id}]: 成员 {m.skill_id} "
                    "已弃用")

    # S14 资产完备性：每个技能必须对三个核心失败触发器（missing_input /
    # unsupported_geometry / insufficient_data）至少各有一条 fallback 声明
    # —— 缺失即 fatal，防止"机制在、内容缺"的静默裸奔。
    for skill in by_id.values():
        covered = {fb.trigger for fb in skill.procedure.fallbacks}
        for trigger in ("missing_input", "unsupported_geometry",
                        "insufficient_data"):
            if trigger not in covered:
                violations.append(
                    f"skill[{skill.id}]: 缺核心 fallback 触发器 {trigger}"
                    "（S14 资产完备性）")

    # 组合层
    for comp in compositions or []:
        violations.extend(comp.validate_composition(_sid))

    # 库规模护栏（防止把整个学科塞成一个 YAML 巨石）
    if len(by_id) > 512:
        violations.append(f"skill_library: 技能数 {len(by_id)} 超护栏 512（拆 pack）")
    oversize = [s.id for s in by_id.values()
                if len(s.procedure.steps) > MAX_STEPS
                or len(s.procedure.fallbacks) > MAX_FALLBACKS]
    for sid in oversize:
        violations.append(f"skill[{sid}]: procedure 超 IR 规模上限")
    return violations


__all__ = [
    "validate_skill_library",
]
