"""Obligation Inheritance V4 —— 科学义务的组合/嵌套继承与 provenance。

组合工作流（CompositeRecipe：base + supporting 层）与嵌套工作流
（subworkflow 节点引用 package）在 V3 只做「层并入」，义务不从层传播。
本模块给出确定性继承语义：

    同 obligation_id 去重 → provenance 记录全部来源链 →
    on_violation 取最强（block_method > degrade_with_disclosure > warn）→
    kind 混合时科学类（precondition/denominator/temporal/uncertainty）优先。

红线：

- 义务声明仍以 WorkflowProfile.obligations（workflow_schema）为唯一事实
  源；本模块只做继承组合，不新造义务语义；
- 继承幂等：inherit(inherit(x)) == inherit(x)；
- 全部确定性、有界、零 LLM / 零 I/O。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.workflow_schema import (
    ScientificObligation,
)

#: 义务强度（继承冲突时取最强；与 OBLIGATION_ACTIONS 同词表）。
_VIOLATION_STRENGTH = {
    "warn": 0,
    "degrade_with_disclosure": 1,
    "block_method": 2,
}

#: 科学类义务（继承时语义优先于披露类）。
_SCIENTIFIC_KINDS = frozenset({"precondition", "denominator", "temporal",
                               "uncertainty"})

_MAX_CHAIN_SOURCES = 16


class ObligationProvenance(BaseModel):
    """义务的一个来源（recipe/family → 组合 → 嵌套深度）。"""
    source_recipe_id: str = ""
    source_family: str = ""            # 方法论族（workflow_v4）
    via_composite: str = ""            # 经由哪个组合层并入
    via_subworkflow: str = ""          # 经由哪个嵌套 package
    depth: int = 0                     # 嵌套深度（0 = 直接来源）

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "source_recipe_id": self.source_recipe_id[:64],
            "source_family": self.source_family[:40],
            "via_composite": self.via_composite[:64],
            "via_subworkflow": self.via_subworkflow[:64],
            "depth": self.depth,
        }


class InheritedObligation(BaseModel):
    """继承后的义务：合并裁决语义 + 完整来源链。"""
    obligation_id: str
    kind: str                          # ⊆ OBLIGATION_KINDS
    precondition_id: str = ""
    effective_on_violation: str        # 最强 on_violation
    warning_code: str = ""
    description: str = ""
    provenance: List[ObligationProvenance] = Field(default_factory=list)
    conflict: bool = False             # 来源间 on_violation/kind 不一致

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "obligation_id": self.obligation_id[:64],
            "kind": self.kind,
            "precondition_id": self.precondition_id[:64],
            "effective_on_violation": self.effective_on_violation,
            "warning_code": self.warning_code[:64],
            "description": self.description[:200],
            "provenance": [
                p.to_bounded_dict() for p in self.provenance[:6]],
            "conflict": self.conflict,
        }


class ObligationChain(BaseModel):
    """一次继承的产物（有序：科学类优先，obligation_id 稳定排序）。"""
    obligations: List[InheritedObligation] = Field(default_factory=list)
    sources_count: int = 0
    fingerprint: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "obligations": [
                o.to_bounded_dict() for o in self.obligations[:_MAX_CHAIN_SOURCES]],
            "sources_count": self.sources_count,
            "fingerprint": self.fingerprint[:64] or "",
        }


class ObligationSource(BaseModel):
    """一个继承来源：其 workflow profile 的义务 + 来源标识。"""
    recipe_id: str
    family: str = ""
    via_composite: str = ""
    via_subworkflow: str = ""
    depth: int = 0
    obligations: Tuple[ScientificObligation, ...] = ()


def _strongest(a: str, b: str) -> str:
    return a if _VIOLATION_STRENGTH.get(a, 0) >= _VIOLATION_STRENGTH.get(b, 0) else b


def inherit_obligations(sources: Sequence[ObligationSource]) -> ObligationChain:
    """确定性义务继承：去重 + provenance + 最强裁决语义。幂等。"""
    merged: Dict[str, InheritedObligation] = {}
    for src in sources:
        prov = ObligationProvenance(
            source_recipe_id=src.recipe_id, source_family=src.family,
            via_composite=src.via_composite,
            via_subworkflow=src.via_subworkflow, depth=src.depth,
        )
        for obl in src.obligations:
            cur = merged.get(obl.obligation_id)
            if cur is None:
                merged[obl.obligation_id] = InheritedObligation(
                    obligation_id=obl.obligation_id, kind=obl.kind,
                    precondition_id=obl.precondition_id,
                    effective_on_violation=obl.on_violation,
                    warning_code=obl.warning_code,
                    description=obl.description,
                    provenance=[prov],
                )
                continue
            conflict = (
                cur.effective_on_violation != obl.on_violation
                or cur.kind != obl.kind
            )
            cur.effective_on_violation = _strongest(
                cur.effective_on_violation, obl.on_violation)
            # kind 冲突：科学类优先（科学红线强于披露）
            if cur.kind != obl.kind:
                cur.kind = (
                    obl.kind
                    if obl.kind in _SCIENTIFIC_KINDS
                    and cur.kind not in _SCIENTIFIC_KINDS
                    else cur.kind
                )
            cur.precondition_id = cur.precondition_id or obl.precondition_id
            cur.warning_code = cur.warning_code or obl.warning_code
            cur.conflict = cur.conflict or conflict
            cur.provenance.append(prov)

    ordered = sorted(
        merged.values(),
        key=lambda o: (o.kind not in _SCIENTIFIC_KINDS, o.obligation_id),
    )
    payload = _chain_fingerprint(ordered)
    return ObligationChain(
        obligations=ordered, sources_count=len(sources), fingerprint=payload,
    )


def _chain_fingerprint(ordered: List[InheritedObligation]) -> str:
    import hashlib
    import json
    payload = json.dumps(
        [o.to_bounded_dict() for o in ordered[:_MAX_CHAIN_SOURCES]],
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def profile_to_source(
    recipe_id: str,
    workflow_profile: Optional[Any],
    *,
    family: str = "",
    via_composite: str = "",
    via_subworkflow: str = "",
    depth: int = 0,
) -> ObligationSource:
    """WorkflowProfile → 继承来源（无 profile = 空义务来源，诚实保留）。"""
    obligations: Tuple[ScientificObligation, ...] = ()
    if workflow_profile is not None:
        obligations = tuple(getattr(workflow_profile, "obligations", ()) or ())
    return ObligationSource(
        recipe_id=recipe_id, family=family, via_composite=via_composite,
        via_subworkflow=via_subworkflow, depth=depth,
        obligations=obligations,
    )
