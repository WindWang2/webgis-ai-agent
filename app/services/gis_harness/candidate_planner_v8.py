"""V8 候选规划 —— 统一能力图上的资格/成本/可靠性排序（ADR-0137 §24）。

链路（确定性 fallback 保留；LLM 只参与语义解释/偏好，不裁决安全性）：

    Intent
      → capability graph retrieval（V7 select_capabilities 词法种子）
      → qualification 过滤（V8.3：硬剔 ineligible + 原因披露）
      → estimate 排序（V8.4 → ADR-0204 D3：rg.v1 数值估算 resource-aware
        排序 —— latency + cost + 资源压力加权，basis 披露）
      → reliability 罚分（V7 ledger 聚合 → V8.5 entity 维度）
      → CandidatePlan（确定性 tie-break by id）

资源排序纪律（ADR-0204 D3）：
- 排序只在 **eligible 候选间**生效 —— 资格门槛（qualify_node）绝不因资源
  压力放宽；资源压力只改变顺序，不改变资格；
- 降级语义候选（qualification=degraded）在 payload 显式披露 ``semantics``
  （绝不偷偷改变任务语义）；
- kill-switch ``GIS_RESOURCE_AWARE_RANK=0`` 回退 latency-only 排序。

产出是**计划证据**（可序列化入 plan evidence），不是第二事实源 ——
执行仍走 ToolRegistry/ToolDispatchService 单一管线，Governor 保持最终
执行准入权。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_harness.capability_graph import (
    KIND_CAPABILITY,
    KIND_TOOL,
    CapabilityGraph,
    GraphNode,
    get_capability_graph,
)
from app.services.gis_harness.estimate_bridge import (
    current_resource_pressure,
    latency_class_of,
    memory_class_of,
    resource_estimate_for_node,
)
from app.services.gis_harness.qualification_v8 import (
    QualificationContext,
    QualificationResult,
    QualificationStatus,
    qualify_node,
)
from app.services.governor.contract import Dimension

#: rg.v1 维度别名（可读性）
_WALL_DIM = Dimension.WALL_TIME_S
_MEM_DIM = Dimension.MEMORY_BYTES
_NET_DIM = Dimension.NETWORK_BYTES
_COST_DIM = Dimension.ESTIMATED_LLM_COST

__all__ = [
    "Candidate", "CandidatePlan", "plan_candidates_v8",
    "reliability_penalty_v8", "resource_rank_enabled", "RESOURCE_RANK_ENV",
]


#: resource-aware 排序 kill-switch（=0 回退 latency-only；回滚面）
RESOURCE_RANK_ENV = "GIS_RESOURCE_AWARE_RANK"

#: 内存档 → rank（0..1；rg.v1 投影反推档位）
_MEMORY_RANK = {"light": 0.0, "medium": 0.5, "heavy": 1.0}

#: 外部成本维"显著"阈值（与保守地板同量级；provisional）
_NOTABLE_NETWORK_BYTES = 16 * 1024**2
_NOTABLE_LLM_COST_USD = 0.001

#: 恒定 cost 项权重（无压力时排序仍轻度偏向便宜路径）
_COST_WEIGHT = 0.25
#: 压力加权项上限（pressure=1 时 heavy 内存候选最多罚 2.0）
_PRESSURE_WEIGHT = 2.0


def resource_rank_enabled() -> bool:
    return (os.getenv(RESOURCE_RANK_ENV, "1") not in ("0", "false", "off"))


@dataclass
class Candidate:
    """单候选：节点 + 资格 + 成本 + 可靠性（全部可解释）。"""

    kind: str
    id: str
    qualification: QualificationResult
    latency_class: str
    reliability_penalty: float
    score: float = 0.0
    # ── ADR-0204 D3：resource-aware 排序面（默认值保持向后兼容）───────
    cost_rank: float = 0.0
    memory_rank: float = 0.0
    #: 降级语义披露（None = comparable 路径）
    semantics: Optional[str] = None
    #: rg.v1 估算摘要（bounded；进 plan evidence）
    estimate: Optional[Dict[str, Any]] = None
    #: score 因子分解（selection disclosure）
    factors: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "kind": self.kind, "id": self.id,
            "qualification": self.qualification.to_dict(),
            "latency_class": self.latency_class,
            "reliability_penalty": self.reliability_penalty,
            "score": round(self.score, 3),
            "cost_rank": round(self.cost_rank, 3),
            "memory_rank": round(self.memory_rank, 3),
            "factors": {k: round(v, 3) for k, v in self.factors.items()},
        }
        if self.semantics:
            out["semantics"] = self.semantics
        if self.estimate:
            out["estimate"] = self.estimate
        return out


@dataclass
class CandidatePlan:
    """capability → 排序候选（models + tools 两面）。"""

    capability_id: str
    candidates: List[Candidate] = field(default_factory=list)
    excluded: List[Dict[str, Any]] = field(default_factory=list)
    #: 全计划最差科学语义（选中候选含降级路径时非 None；R3 披露面）
    worst_semantics: Optional[str] = None
    #: 排序时的资源压力标量（[0,1]；None = resource-aware 未启用）
    resource_pressure: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability_id,
            "candidates": [c.to_dict() for c in self.candidates],
            "excluded": self.excluded,
            "worst_semantics": self.worst_semantics,
            "resource_pressure": (
                round(self.resource_pressure, 3)
                if self.resource_pressure is not None else None),
        }


# ── V8.5 可靠性罚分（entity 维度扩展，bounded/decayed 语义承自 ledger）──


def reliability_penalty_v8(entity_key: str, session_id: str = "") -> float:
    """durable ledger 聚合 → [0, 1] 罚分（无记录 = 0 中性）。

    V7 的 tool 维度聚合扩展到任意 entity key（model:xxx@1 / tool:yyy /
    provider:zzz）—— ledger 键格式 ``entity:failure_class``，attempts
    即未清零失败数。衰减/上限由 ledger 既有语义承载（≤32 entries，
    成功清零），本函数只做有界映射：penalty = min(1, fails / 4)。
    """
    if not session_id:
        return 0.0
    try:
        from app.services.gis_harness.recovery_ledger import get_recovery_ledger

        snapshot = get_recovery_ledger().snapshot(session_id, max_entries=32)
    except Exception:  # noqa: BLE001 — 反馈缺席中性
        return 0.0
    fails = 0
    # ledger 键 = "{entity}||{failure_class}"（_key 的双竖线分隔；entity
    # 自身可含冒号 kind:id）—— 前缀匹配。
    prefix = entity_key + "|"
    for key, info in list((snapshot or {}).items())[:32]:
        if isinstance(info, dict) and str(key).startswith(prefix):
            fails += int(info.get("attempts") or 0)
    return min(1.0, fails / 4.0)


# ── V8.6 候选规划 ────────────────────────────────────────────────────────

_LATENCY_RANK = {"fast": 0, "medium": 1, "slow": 2}


def plan_candidates_v8(
    capability_id: str,
    ctx: QualificationContext,
    *,
    graph: Optional[CapabilityGraph] = None,
    session_id: str = "",
    max_candidates: int = 8,
    resource_pressure: Optional[float] = None,
) -> CandidatePlan:
    """capability →（model 候选 + tool 候选）资格过滤 + resource-aware 排序。

    确定性：同输入同序（score 相同按 kind, id tie-break）。LLM 不参与
    排序裁决 —— 这条链是确定性 fallback（§24）。

    ``resource_pressure``：[0,1] 资源压力标量；None = 从 governor live
    内存自动推导（fail-open 0）。显式传入用于测试与上层覆写。
    """
    g = graph or get_capability_graph()
    cap_key = f"{KIND_CAPABILITY}:{capability_id}"
    if g.node(cap_key) is None:
        return CandidatePlan(capability_id, [], [{
            "reason": "capability_not_in_graph", "detail": capability_id}])

    aware = resource_rank_enabled()
    pressure = 0.0
    if aware:
        pressure = (max(0.0, min(1.0, float(resource_pressure)))
                    if resource_pressure is not None
                    else current_resource_pressure())

    nodes: List[GraphNode] = []
    # model 面（V8.2 implements 边）
    nodes.extend(g.models_for_capability(capability_id))
    # tool 面（algorithm 链 + 直接 implements）
    for tool_id in g.tools_for_capability(capability_id):
        node = g.node(f"{KIND_TOOL}:{tool_id}")
        if node is not None:
            nodes.append(node)

    # Review A RA-2：跨面去重（model 面 + tool 面可能经由 implements/
    # exposed_by 双路命中同一节点）—— 以 node.key（kind:id）为唯一键。
    seen_keys = set()
    deduped: List[GraphNode] = []
    for node in nodes:
        if node.key not in seen_keys:
            seen_keys.add(node.key)
            deduped.append(node)
    nodes = deduped

    plan = CandidatePlan(
        capability_id,
        resource_pressure=(pressure if aware else None),
    )
    for node in nodes:
        qual = qualify_node(node, ctx, g)
        entity_key = f"{node.kind}:{node.id}"
        penalty = reliability_penalty_v8(entity_key, session_id)
        if qual.status == QualificationStatus.INELIGIBLE:
            plan.excluded.append({
                "kind": node.kind, "id": node.id,
                "qualification": qual.to_dict()})
            continue
        # 单次桥投影：档位（latency_class）与排序因子同源（ADR-0204 D1）
        resource = resource_estimate_for_node(node)
        latency_class = latency_class_of(resource)
        factors: Dict[str, float] = {
            "latency_rank": float(_LATENCY_RANK.get(latency_class, 1)),
        }
        cand = Candidate(
            kind=node.kind, id=node.id, qualification=qual,
            latency_class=latency_class,
            reliability_penalty=penalty,
        )
        if qual.status == QualificationStatus.DEGRADED:
            factors["degraded_penalty"] = 0.5
            cand.semantics = "approximate"
        if penalty:
            factors["reliability_penalty"] = penalty
        if aware:
            mem_class = memory_class_of(resource)
            cand.memory_rank = _MEMORY_RANK.get(mem_class, 0.5)
            cand.cost_rank = min(
                1.0, cand.memory_rank + _external_cost_rank(resource))
            # 恒定轻成本项 + 压力加权内存项（bounded、确定性）
            factors["cost_rank"] = _COST_WEIGHT * cand.cost_rank
            pressure_term = _PRESSURE_WEIGHT * pressure * cand.memory_rank
            if pressure_term:
                factors["pressure_term"] = pressure_term
            cand.estimate = _estimate_summary(resource)
        # score：越低越好（latency 档位 + degraded 罚 0.5 + 可靠性罚
        # + cost 轻权项 + 压力×内存项）
        cand.factors = factors
        cand.score = sum(factors.values())
        plan.candidates.append(cand)

    plan.candidates.sort(key=lambda c: (c.score, c.kind, c.id))
    del plan.candidates[max_candidates:]
    if aware:
        plan.worst_semantics = next(
            (c.semantics for c in plan.candidates if c.semantics), None)
    return plan


def _external_cost_rank(resource) -> float:
    """外部成本显著度（network / LLM cost；bounded 0..1）。"""
    rank = 0.0
    net = resource.dim(_NET_DIM)
    if net.is_meaningful() and (net.expected or 0.0) >= _NOTABLE_NETWORK_BYTES:
        rank += 0.5
    cost = resource.dim(_COST_DIM)
    if cost.is_meaningful() and (cost.expected or 0.0) >= _NOTABLE_LLM_COST_USD:
        rank += 0.5
    return rank


def _estimate_summary(resource) -> Dict[str, Any]:
    """rg.v1 → bounded 摘要（wall/memory range + confidence + source）。"""
    wall = resource.dim(_WALL_DIM)
    mem = resource.dim(_MEM_DIM)

    def _triple(dv) -> Optional[List[float]]:
        if not dv.is_meaningful():
            return None
        return [
            round(dv.min, 3) if dv.min is not None else None,
            round(dv.expected, 3) if dv.expected is not None else None,
            round(dv.max, 3) if dv.max is not None else None,
        ]

    return {
        "wall_s": _triple(wall),
        "memory_bytes": _triple(mem),
        "memory_class": memory_class_of(resource),
        "confidence": round(resource.overall_confidence(), 2),
        "source": resource.source[:160],
    }
