"""V8 候选规划 —— 统一能力图上的资格/成本/可靠性排序（ADR-0137 §24）。

链路（确定性 fallback 保留；LLM 只参与语义解释/偏好，不裁决安全性）：

    Intent
      → capability graph retrieval（V7 select_capabilities 词法种子）
      → qualification 过滤（V8.3：硬剔 ineligible + 原因披露）
      → estimate 排序（V8.4：latency/cost 档位，basis 披露）
      → reliability 罚分（V7 ledger 聚合 → V8.5 entity 维度）
      → CandidatePlan（确定性 tie-break by id）

产出是**计划证据**（可序列化入 plan evidence），不是第二事实源 ——
执行仍走 ToolRegistry/ToolDispatchService 单一管线。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_harness.capability_graph import (
    KIND_CAPABILITY,
    KIND_TOOL,
    CapabilityGraph,
    GraphNode,
    get_capability_graph,
)
from app.services.gis_harness.qualification_v8 import (
    QualificationContext,
    QualificationResult,
    QualificationStatus,
    estimate_for_node,
    qualify_node,
)

__all__ = [
    "Candidate", "CandidatePlan", "plan_candidates_v8",
    "reliability_penalty_v8",
]


@dataclass
class Candidate:
    """单候选：节点 + 资格 + 成本 + 可靠性（全部可解释）。"""

    kind: str
    id: str
    qualification: QualificationResult
    latency_class: str
    reliability_penalty: float
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "id": self.id,
            "qualification": self.qualification.to_dict(),
            "latency_class": self.latency_class,
            "reliability_penalty": self.reliability_penalty,
            "score": round(self.score, 3),
        }


@dataclass
class CandidatePlan:
    """capability → 排序候选（models + tools 两面）。"""

    capability_id: str
    candidates: List[Candidate] = field(default_factory=list)
    excluded: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability_id,
            "candidates": [c.to_dict() for c in self.candidates],
            "excluded": self.excluded,
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
) -> CandidatePlan:
    """capability →（model 候选 + tool 候选）资格过滤 + 成本排序。

    确定性：同输入同序（score 相同按 kind, id tie-break）。LLM 不参与
    排序裁决 —— 这条链是确定性 fallback（§24）。
    """
    g = graph or get_capability_graph()
    cap_key = f"{KIND_CAPABILITY}:{capability_id}"
    if g.node(cap_key) is None:
        return CandidatePlan(capability_id, [], [{
            "reason": "capability_not_in_graph", "detail": capability_id}])

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

    plan = CandidatePlan(capability_id)
    for node in nodes:
        qual = qualify_node(node, ctx, g)
        entity_key = f"{node.kind}:{node.id}"
        penalty = reliability_penalty_v8(entity_key, session_id)
        if qual.status == QualificationStatus.INELIGIBLE:
            plan.excluded.append({
                "kind": node.kind, "id": node.id,
                "qualification": qual.to_dict()})
            continue
        est = estimate_for_node(node)
        cand = Candidate(
            kind=node.kind, id=node.id, qualification=qual,
            latency_class=est.latency_class,
            reliability_penalty=penalty,
        )
        # score：越低越好（latency 档位 + degraded 罚 0.5 + 可靠性罚）
        cand.score = (
            _LATENCY_RANK.get(cand.latency_class, 1)
            + (0.5 if qual.status == QualificationStatus.DEGRADED else 0.0)
            + penalty
        )
        plan.candidates.append(cand)

    plan.candidates.sort(key=lambda c: (c.score, c.kind, c.id))
    del plan.candidates[max_candidates:]
    return plan
