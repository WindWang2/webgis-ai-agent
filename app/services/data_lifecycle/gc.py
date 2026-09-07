"""Artifact GC Planner —— 安全清理规划（§十四；dry-run 诊断优先）。

既有 ``collect_orphan_refs``（artifact_registry）已经带着完整的活引用
复检纪律在执行删除；本模块补的是它缺的**规划面**：

- ``plan_session_gc``：**只读** dry-run —— 谁是候选、为什么、谁被保护、
  保护理由；绝不删任何东西；
- 保护规则（§十四永远不能删）：
  1. 仍在活引用集合（plan 行 / MapSpec sources / 组件 chart/table ref）
     —— 与 collect_orphan_refs 同一真相源；
  2. metadata 持久层为 workspace/persistent（用户/工作空间级资产）；
  3. 血缘根保留：仍有 ``valid`` 下游依赖的记录（删了会断 lineage 链，
     replay/resume 失去重建依据）；
- ``execute_session_gc``：执行仍**完全委托**给 ``collect_orphan_refs``
  （它自带锁内活引用重检），本模块不发明第二删除路径。

候选来源：账本记录状态 ∈ {superseded, stale, expired, failed} 且
不在活引用集合、且不触发任何保护规则。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PROTECTED_TIERS = ("workspace", "persistent")


class GCCandidate(BaseModel):
    artifact_id: str
    status: str
    reason: str
    size_hint: Optional[int] = None


class GCProtection(BaseModel):
    artifact_id: str
    status: str
    reason: str


class GCPlan(BaseModel):
    """GC 计划（§十四 dry-run diagnostics；只读产物）。"""

    session_id: str
    candidates: List[GCCandidate] = Field(default_factory=list)
    protected: List[GCProtection] = Field(default_factory=list)
    live_reference_ids: int = 0

    @property
    def candidate_ids(self) -> List[str]:
        return [c.artifact_id for c in self.candidates]

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "candidates": [c.model_dump() for c in self.candidates],
            "protected": [p.model_dump() for p in self.protected],
            "live_reference_ids": self.live_reference_ids,
            "candidate_count": len(self.candidates),
        }


async def plan_session_gc(
    session_id: str,
    *,
    chapter: Optional[Dict[str, Any]] = None,
    mapspec: Optional[Dict[str, Any]] = None,
) -> GCPlan:
    """只读 GC 规划（§十四）。绝不删除任何东西。"""
    from app.services.artifact_registry import (
        _load_chapter_fresh,
        _load_mapspec_fresh,
        _TERMINAL_GC_STATUSES,
        list_artifacts,
        load_records_for_plan,
    )

    plan = GCPlan(session_id=session_id)
    if not session_id:
        return plan
    records = {r.artifact_id: r for r in await list_artifacts(session_id)}
    if not records:
        return plan
    if chapter is None:
        chapter = await _load_chapter_fresh(session_id)
    if mapspec is None:
        mapspec = await _load_mapspec_fresh(session_id)
    live = await load_records_for_plan(session_id, chapter, mapspec)
    plan.live_reference_ids = len(live)

    # 血缘根保留集：valid 记录的全部上游（上游被删则下游血缘断链）
    retained_roots: set = set()
    for rec in records.values():
        if getattr(rec, "status", "") == "valid":
            for parent in (getattr(rec, "inputs", None) or []):
                if parent in records:
                    retained_roots.add(parent)

    for aid, rec in records.items():
        status = getattr(rec, "status", "")
        metadata = getattr(rec, "metadata", None) or {}
        metadata = metadata if isinstance(metadata, dict) else {}
        tier = str(metadata.get("persistence_tier") or "")
        if status not in _TERMINAL_GC_STATUSES:
            if tier in _PROTECTED_TIERS:
                plan.protected.append(GCProtection(
                    artifact_id=aid, status=status,
                    reason=f"persistence_tier={tier}",
                ))
            continue
        if aid in live:
            plan.protected.append(GCProtection(
                artifact_id=aid, status=status,
                reason="still referenced by plan/mapspec/component",
            ))
            continue
        if tier in _PROTECTED_TIERS:
            plan.protected.append(GCProtection(
                artifact_id=aid, status=status,
                reason=f"persistence_tier={tier}",
            ))
            continue
        if aid in retained_roots:
            plan.protected.append(GCProtection(
                artifact_id=aid, status=status,
                reason="retained lineage root (has live downstream)",
            ))
            continue
        plan.candidates.append(GCCandidate(
            artifact_id=aid,
            status=status,
            reason={
                "superseded": "replaced by newer artifact",
                "stale": "no live references",
                "expired": "payload no longer probeable",
                "failed": "failed production record",
            }.get(status, status),
            size_hint=metadata.get("size_bytes") if isinstance(metadata.get("size_bytes"), int) else None,
        ))
    return plan


async def execute_session_gc(session_id: str) -> List[str]:
    """执行 GC —— 完全委托 ``collect_orphan_refs``（锁内活引用重检纪律）。"""
    from app.services.artifact_registry import collect_orphan_refs

    return await collect_orphan_refs(session_id)
