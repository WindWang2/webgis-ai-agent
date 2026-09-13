"""SkillPack —— 技能包（ADR-0182 §2.5；goal S15）。

V1 只建 core 包；pack registry 结构就绪（领域包=纯加法演进），**不一次
建几十个 pack**。pack 是治理边界：加载、校验、目录都按 pack 过滤。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SkillPack(BaseModel):
    """一个技能包的元数据（版本化资产）。"""
    pack_id: str
    label: str
    description: str = ""
    domains: List[str] = Field(default_factory=list)   # ⊆ SKILL_DOMAINS
    version: str = "1.0.0"


#: V1 内建包（core 唯一；新增包走 ADR + 纯加法）。
BUILTIN_PACKS: List[SkillPack] = [
    SkillPack(
        pack_id="core",
        label="GIS Core Skills",
        description="矢量/栅格/遥感/制图/数据准备/时序的领域作业方法基础库",
        domains=[
            "vector_analysis", "raster_terrain", "remote_sensing",
            "cartography", "data_preparation", "temporal_analysis",
            "network_accessibility", "general",
        ],
    ),
]


class SkillPackRegistry:
    """包登记表（只读；重复 pack_id 视为致命装载错误）。"""

    def __init__(self, packs: Optional[List[SkillPack]] = None) -> None:
        self._by_id: Dict[str, SkillPack] = {}
        for p in (packs if packs is not None else BUILTIN_PACKS):
            if p.pack_id in self._by_id:
                raise ValueError(f"SkillPackRegistry: duplicate pack {p.pack_id}")
            self._by_id[p.pack_id] = p

    def get(self, pack_id: str) -> Optional[SkillPack]:
        return self._by_id.get(pack_id)

    def has(self, pack_id: str) -> bool:
        return pack_id in self._by_id

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id)

    def __len__(self) -> int:
        return len(self._by_id)


__all__ = [
    "SkillPack",
    "BUILTIN_PACKS",
    "SkillPackRegistry",
]
