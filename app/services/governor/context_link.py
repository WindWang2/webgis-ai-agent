"""R8 资源感知计划提示（窄协议）+ R12 context/LLM 预算协同（D2/D3）。

两个解耦面，一个模块：

**R8 —— ResourceAwarePlanHint 窄协议**：capability/execution graph 的
owner 在别的线（本地分支 harness/gis-capability-graph-v1 未合并，
recon §d）。governor 绝不复制图结构 —— 这里只定义 governor 需要消费的
最小形状（候选计划 × 资源估算），并把「当前预算下选哪条」的建议作为
纯函数产出。Owner 合并后，其图适配本协议即可获得预算感知，零侵入。

**R12 —— Context/LLM 预算协同**：token 估算语义的单一真相在
``context_budget.py``（不重写 tokenizer/压缩器）。governor 只做
（a）把 chat 面的 ``BudgetReport`` 事实记入会话账本；（b）把
``GisBudgetAdvice`` 动作转为优先级提示（condense/offload 顺序的
harness 视角）。输入 duck-typed（只读属性），不 import 上层模块。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from app.services.governor.contract import (
    Dimension,
    ResourceBudget,
    ResourceEstimate,
)


# ---------------------------------------------------------------------------
# R8 — 窄协议
# ---------------------------------------------------------------------------


@dataclass
class PlanAlternativeView:
    """一个候选计划/变体的资源视图（owner 投影，governor 只读）。"""

    label: str
    estimate: ResourceEstimate
    notes: str = ""


@dataclass
class ResourceAwarePlanHint:
    """一个规划问题的资源提示（多候择优的输入）。"""

    plan_id: str
    session_id: str = ""
    goal_id: str = ""
    alternatives: List[PlanAlternativeView] = field(default_factory=list)


@runtime_checkable
class ResourceHintProvider(Protocol):
    """capability/execution graph owner 未来实现的最小接口（适配即用）。"""

    def resource_hints(self) -> List[ResourceAwarePlanHint]: ...


@dataclass
class PlanAdvice:
    """一条择优建议（建议权在 governor，选择权在 planner）。"""

    plan_id: str
    preferred: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    per_alternative: Dict[str, Dict] = field(default_factory=dict)

    def as_dict(self) -> Dict:
        return {
            "plan_id": self.plan_id,
            "preferred": self.preferred,
            "reasons": list(self.reasons),
            "per_alternative": dict(self.per_alternative),
        }


def advise_plan(hint: ResourceAwarePlanHint,
                budget: Optional[ResourceBudget]) -> PlanAdvice:
    """在候选变体中按「当前预算可行性 → 估工」给出确定性建议。

    规则（KDE 例：exact 4GB vs native 300MB，预算 1GB → 选 native）：
    1. 有预算时，adjudged memory 超预算的变体标记 infeasible；
    2. 可行变体中估工（memory+wall 的 adjudged 和）最小者 preferred；
    3. 全部不可行 → preferred=None + reasons（诚实不可行）。
    """
    advice = PlanAdvice(plan_id=hint.plan_id)
    best_key: Optional[float] = None
    mem_cap = budget.limit_for(Dimension.MEMORY_BYTES) if budget else None
    for alt in hint.alternatives:
        mem = alt.estimate.adjudged(Dimension.MEMORY_BYTES)
        wall = alt.estimate.adjudged(Dimension.WALL_TIME_S)
        work = mem + wall
        entry: Dict[str, Any] = {
            "adjudged_memory": mem,
            "adjudged_wall_s": wall,
            "confidence": alt.estimate.overall_confidence(),
            "notes": alt.notes,
        }
        if mem_cap is not None and mem > mem_cap:
            entry["infeasible"] = f"memory {mem:.4g} > budget {mem_cap:.4g}"
            advice.reasons.append(f"{alt.label}: {entry['infeasible']}")
        elif best_key is None or work < best_key:
            best_key = work
            advice.preferred = alt.label
        advice.per_alternative[alt.label] = entry
    if advice.preferred is None and hint.alternatives:
        advice.reasons.append("no feasible alternative under current budget")
    return advice


# ---------------------------------------------------------------------------
# R12 — context/LLM 预算协同（duck-typed，不 import chat 面）
# ---------------------------------------------------------------------------


def record_context_report(ledger, session_id: str, turn_id: str,
                          report: Any) -> Dict:
    """把 chat 面预算度量事实（context_budget.BudgetReport 形状）记入账本。

    只读消费：total_est_tokens / over_budget / window_unknown。
    返回 governor 视角的摘要（trace/evidence 用）。
    """
    tokens = int(getattr(report, "total_est_tokens", 0) or 0)
    ledger.record_context_tokens(session_id, turn_id, tokens)
    summary = {
        "tokens": tokens,
        "over_budget": bool(getattr(report, "over_budget", False)),
        "window_unknown": bool(getattr(report, "window_unknown", False)),
        "violations": list(getattr(report, "violations", []) or []),
    }
    return summary


def context_priority_hints(advice: Any) -> List[str]:
    """GisBudgetAdvice 动作 → harness 优先级提示（只排序建议，不执行）。

    offload_ref > drop_oldest > compress > condense 的顺序是 harness 视角
    的成本序（offload 保信息量、drop 最便宜）。同输入必同提示。
    """
    cost_order = {
        "offload_ref": 0,
        "drop_oldest": 1,
        "compress": 2,
        "condense": 3,
    }
    actions = getattr(advice, "actions", None) or []
    ranked = sorted(
        actions,
        key=lambda a: (cost_order.get(str(a.get("action", "condense")), 9),
                       -int(a.get("est_tokens", 0) or 0)),
    )
    return [
        f"{a.get('action')}:{a.get('item')}:{a.get('est_tokens', 0)}tok"
        for a in ranked
    ]


__all__ = [
    "PlanAlternativeView",
    "ResourceAwarePlanHint",
    "ResourceHintProvider",
    "PlanAdvice",
    "advise_plan",
    "record_context_report",
    "context_priority_hints",
]
