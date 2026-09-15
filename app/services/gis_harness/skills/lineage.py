"""Skill version lineage —— 演化轨迹（不覆盖历史证据）。

skill v1 → observed failures → candidate v2；保留 parent、原因、证据引用。
回滚 = 指向先前受信版本（不删历史）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

MAX_LINEAGE_NODES = 256


class SkillLineageNode(BaseModel):
    skill_id: str
    version: str
    parent_version: str = ""
    trust_tier: str = "core"
    evolution_reason: str = ""
    evidence_refs: List[str] = Field(default_factory=list)
    counterexamples: List[str] = Field(default_factory=list)
    performance_comparison: Dict[str, float] = Field(default_factory=dict)
    superseded_by: str = ""
    active: bool = True

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id[:64],
            "version": self.version[:16],
            "parent_version": self.parent_version[:16],
            "trust_tier": self.trust_tier[:24],
            "evolution_reason": self.evolution_reason[:160],
            "evidence_refs": [e[:80] for e in self.evidence_refs[:12]],
            "counterexamples": [c[:80] for c in self.counterexamples[:8]],
            "performance_comparison": {
                k[:32]: round(float(v), 4)
                for k, v in list(self.performance_comparison.items())[:8]
            },
            "superseded_by": self.superseded_by[:16],
            "active": self.active,
        }


class SkillLineageStore:
    """有界谱系库（按 skill_id 分组；历史只追加）。"""

    def __init__(self, *, capacity: int = MAX_LINEAGE_NODES) -> None:
        self._nodes: Dict[str, List[SkillLineageNode]] = {}
        self._capacity = capacity
        self._total = 0

    def register(
        self,
        skill_id: str,
        version: str,
        *,
        parent_version: str = "",
        trust_tier: str = "core",
        evolution_reason: str = "",
        evidence_refs: Optional[List[str]] = None,
        counterexamples: Optional[List[str]] = None,
        performance_comparison: Optional[Dict[str, float]] = None,
    ) -> SkillLineageNode:
        series = self._nodes.setdefault(skill_id, [])
        # 同版本幂等：返回已有节点，不覆盖证据
        for node in series:
            if node.version == version:
                return node
        if parent_version:
            for node in series:
                if node.version == parent_version and node.active:
                    node.active = False
                    node.superseded_by = version
        new = SkillLineageNode(
            skill_id=skill_id,
            version=version,
            parent_version=parent_version,
            trust_tier=trust_tier,
            evolution_reason=evolution_reason,
            evidence_refs=list(evidence_refs or [])[:12],
            counterexamples=list(counterexamples or [])[:8],
            performance_comparison=dict(performance_comparison or {}),
            active=True,
        )
        series.append(new)
        self._total += 1
        self._evict_if_needed()
        return new

    def history(self, skill_id: str) -> List[SkillLineageNode]:
        return list(self._nodes.get(skill_id, []))

    def active_version(self, skill_id: str) -> Optional[SkillLineageNode]:
        for node in reversed(self._nodes.get(skill_id, [])):
            if node.active:
                return node
        return None

    def rollback(self, skill_id: str, to_version: str) -> Optional[SkillLineageNode]:
        """回滚到历史受信版本：标记目标 active，其后版本 inactive（保留节点）。"""
        series = self._nodes.get(skill_id, [])
        target = None
        for node in series:
            if node.version == to_version:
                target = node
                break
        if target is None:
            return None
        seen_target = False
        for node in series:
            if node.version == to_version:
                node.active = True
                node.superseded_by = ""
                seen_target = True
            elif seen_target:
                node.active = False
            else:
                # 更早版本保持 inactive 历史
                node.active = False
        # 修正：仅 to_version active
        for node in series:
            node.active = node.version == to_version
            if node.version != to_version and not node.superseded_by:
                # 保持既有 superseded 链；若目标回滚则后继指向目标
                if _version_after(node.version, to_version, series):
                    node.superseded_by = node.superseded_by or to_version
        target.active = True
        target.superseded_by = ""
        return target

    def _evict_if_needed(self) -> None:
        if self._total <= self._capacity:
            return
        # 淘汰最旧 skill 的最旧非 active 节点
        for skill_id in sorted(self._nodes):
            series = self._nodes[skill_id]
            for i, node in enumerate(series):
                if not node.active:
                    del series[i]
                    self._total -= 1
                    if not series:
                        del self._nodes[skill_id]
                    if self._total <= self._capacity:
                        return
                    break

    def clear(self) -> None:
        self._nodes.clear()
        self._total = 0


def _version_after(version: str, pivot: str, series: List[SkillLineageNode]) -> bool:
    ids = [n.version for n in series]
    try:
        return ids.index(version) > ids.index(pivot)
    except ValueError:
        return False


_lineage: Optional[SkillLineageStore] = None


def get_skill_lineage_store() -> SkillLineageStore:
    global _lineage
    if _lineage is None:
        _lineage = SkillLineageStore()
    return _lineage


def reset_skill_lineage_store() -> None:
    global _lineage
    _lineage = None


__all__ = [
    "MAX_LINEAGE_NODES",
    "SkillLineageNode",
    "SkillLineageStore",
    "get_skill_lineage_store",
    "reset_skill_lineage_store",
]
