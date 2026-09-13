"""SkillCatalog —— 渐进披露目录（ADR-0182 §2.6；goal S17/S19）。

模型**第一步只看到 SkillCard**（有界：id/描述/when_to_use/要求），
选中后才读取完整 SkillProcedure（detail）。禁止把全部技能全文一次性
注入 context（200 skills × 2KB 红线）。

字节预算：``MAX_CARD_LIST_BYTES`` 约束一次 SkillCard 列表投影；
超预算时截断并如实标注 ``truncated=True``。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.contract import SkillContract

#: SkillCard 单卡投影上限（字节；utf-8）。
MAX_CARD_BYTES = 1024
#: SkillCard 列表投影上限（字节）。~30 技能可见；生产披露路径应优先走
#: gis_skill_search（相关性排序），cards() 是目录/调试视图。
MAX_CARD_LIST_BYTES = 16384
#: detail 投影上限（字节；防御异常巨型契约）。
MAX_DETAIL_BYTES = 16384


def _utf8_len(payload: Dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


class SkillCard(BaseModel):
    """渐进披露第一层：只含选择所需的最小面。"""
    id: str
    name: str
    description: str = ""
    domain: str = "general"
    pack: str = "core"
    version: str = "1.0.0"
    when_to_use: str = ""
    required_geometry: List[str] = Field(default_factory=list)
    required_data_roles: List[str] = Field(default_factory=list)
    capability_ids: List[str] = Field(default_factory=list)
    step_count: int = 0
    deprecated: bool = False

    @classmethod
    def from_contract(cls, skill: SkillContract) -> "SkillCard":
        return cls(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            domain=skill.domain,
            pack=skill.pack,
            version=skill.version,
            when_to_use=skill.when_to_use,
            required_geometry=list(skill.required_situation.geometry_kinds),
            required_data_roles=list(skill.required_situation.data_roles),
            capability_ids=skill.capability_ids(),
            step_count=len(skill.procedure.steps),
            deprecated=skill.deprecated,
        )

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name[:64],
            "description": self.description[:160],
            "domain": self.domain[:32],
            "version": self.version[:16],
            "when_to_use": self.when_to_use[:160],
            "required_geometry": list(self.required_geometry)[:4],
            "required_data_roles": list(self.required_data_roles)[:6],
            "capability_ids": list(self.capability_ids)[:8],
            "step_count": self.step_count,
            "deprecated": self.deprecated,
        }


class SkillCatalog:
    """技能目录：cards（第一层）与 detail（第二层）的唯一出口。"""

    def __init__(self, skills: List[SkillContract]) -> None:
        self._skills = {s.id: s for s in skills}

    def __len__(self) -> int:
        return len(self._skills)

    def cards(
        self,
        *,
        domain: str = "",
        pack: str = "",
        include_deprecated: bool = False,
    ) -> Dict[str, Any]:
        """SkillCard 列表（字节预算内截断；truncated 如实标注）。"""
        ids = sorted(self._skills)
        items: List[Dict[str, Any]] = []
        truncated = False
        total = 0
        for sid in ids:
            skill = self._skills[sid]
            if not include_deprecated and skill.deprecated:
                continue
            if domain and skill.domain != domain:
                continue
            if pack and skill.pack != pack:
                continue
            card = SkillCard.from_contract(skill).to_bounded_dict()
            size = _utf8_len(card)
            if total + size > MAX_CARD_LIST_BYTES:
                truncated = True
                break
            items.append(card)
            total += size
        return {
            "cards": items,
            "count": len(items),
            "total_skills": len(self._skills),
            "truncated": truncated,
            "budget_bytes": MAX_CARD_LIST_BYTES,
        }

    def card(self, skill_id: str) -> Dict[str, Any]:
        skill = self._skills.get(skill_id)
        if skill is None:
            return {"error": f"unknown skill {skill_id}"}
        return SkillCard.from_contract(skill).to_bounded_dict()

    def detail(self, skill_id: str) -> Dict[str, Any]:
        """渐进披露第二层：完整契约投影（按需读取；有界）。"""
        skill = self._skills.get(skill_id)
        if skill is None:
            return {"error": f"unknown skill {skill_id}"}
        payload = skill.to_detail_dict()
        # 有界裁剪（防御性；正常契约远小于预算）
        payload["examples"] = payload.get("examples", [])[:8]
        return {
            "skill": payload,
            "bytes": _utf8_len(payload),
            "budget_bytes": MAX_DETAIL_BYTES,
            "truncated": _utf8_len(payload) > MAX_DETAIL_BYTES,
        }


__all__ = [
    "MAX_CARD_BYTES",
    "MAX_CARD_LIST_BYTES",
    "MAX_DETAIL_BYTES",
    "SkillCard",
    "SkillCatalog",
]
