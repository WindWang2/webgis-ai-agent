"""Production composition compatibility checks (ADR-0182 composition + policy).

不新造工作流语言：只在既有 SkillComposition 上做前置/输出兼容与回落建议。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.composition import SkillComposition
from app.services.gis_harness.skills.contract import SkillContract


class CompositionCompatibilityReport(BaseModel):
    composition_id: str = ""
    compatible: bool = True
    missing_prerequisites: List[str] = Field(default_factory=list)
    incompatible_edges: List[str] = Field(default_factory=list)
    crs_notes: List[str] = Field(default_factory=list)
    fallback_route: str = ""
    execution_order: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "composition_id": self.composition_id[:64],
            "compatible": self.compatible,
            "missing_prerequisites": [m[:64] for m in self.missing_prerequisites[:8]],
            "incompatible_edges": [e[:80] for e in self.incompatible_edges[:8]],
            "crs_notes": [c[:80] for c in self.crs_notes[:4]],
            "fallback_route": self.fallback_route[:80],
            "execution_order": [s[:64] for s in self.execution_order[:12]],
        }


def check_composition_compatibility(
    composition: SkillComposition,
    skills_by_id: Dict[str, SkillContract],
    *,
    available_roles: Optional[List[str]] = None,
) -> CompositionCompatibilityReport:
    """检查成员依赖、输出→输入角色兼容、缺失前置。"""
    order = composition.execution_order()
    missing: List[str] = []
    incompatible: List[str] = []
    crs_notes: List[str] = []
    available = set(available_roles or [])

    for member in composition.members:
        skill = skills_by_id.get(member.skill_id)
        if skill is None:
            missing.append(f"unknown_skill:{member.skill_id}")
            continue
        for dep in member.depends_on:
            if dep not in skills_by_id:
                missing.append(f"missing_prereq:{member.skill_id}->{dep}")
                continue
            upstream = skills_by_id[dep]
            # 输出→输入语义：仅当双方都声明了非空角色且无交集时记不兼容
            # （空角色 = unknown，不虚构冲突；与 eligibility 红线一致）
            if skill.input_roles and upstream.output_roles:
                needed = set(skill.input_roles)
                provided = set(upstream.output_roles)
                if needed.isdisjoint(provided) and not needed <= provided:
                    incompatible.append(
                        f"role_mismatch:{dep}->{member.skill_id}"
                    )
            # 地理单元提示：双方均为已知且不同 → 披露，不单独判失败
            ug = upstream.geographic_semantics
            dg = skill.geographic_semantics
            if ug and dg:
                au = ug.analysis_unit or "unknown"
                bu = dg.analysis_unit or "unknown"
                if au != "unknown" and bu != "unknown" and au != bu:
                    crs_notes.append(f"analysis_unit:{au}->{bu}")

        if available and skill.required_situation.data_roles:
            if not set(skill.required_situation.data_roles) <= available:
                missing.append(f"data_role:{member.skill_id}")

    compatible = not missing and not incompatible
    fallback = "" if compatible else "existing_harness_planning"
    return CompositionCompatibilityReport(
        composition_id=composition.composition_id,
        compatible=compatible,
        missing_prerequisites=missing,
        incompatible_edges=incompatible,
        crs_notes=crs_notes,
        fallback_route=fallback,
        execution_order=order,
    )


__all__ = [
    "CompositionCompatibilityReport",
    "check_composition_compatibility",
]
