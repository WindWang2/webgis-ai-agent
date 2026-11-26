"""科学评价与推荐证据（method-experiment WP4）。

核心裁决（任务红线）：

- **可比组**：只有「同一协议指纹 + 同一指标」的 completed 试验才互相
  排名；量纲不同的指标（value_unit vs ratio vs minutes）绝不归一化、
  绝不跨组联合排名——不同就并列披露不可比原因；
- **方向**：指标方向由 metrics 注册表裁决（lower/higher better），
  evaluate 不自己发明方向；
- **确定性排序**：值相同 → candidate_id 字典序（stable tie-break），
  排序完全可重放；
- **Pareto**：同协议下协议声明的多指标（同 comparable_group）构造
  非支配前沿——「更好」只按各自方向定义，不做加权求和（权重是拍脑袋）；
- **推荐** = 有证据的建议，不是裁决：top 候选附真实指标/样本量/折数/
  局限/敏感性（同方法跨参数的指标离散度）；证据不足（可比组 <2 或
  未评估）→ 明确弃权（abstain），绝不用 not_evaluated 冒充排名；
- **采纳** = 用户 decision receipt（记录选择+证据快照），本层不自动
  改地图/数据。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.method_experiment.metrics import (
    METRIC_REGISTRY,
    get_metric,
)
from app.services.gis_harness.method_experiment.plan import (
    MethodExperimentPlan,
)
from app.services.gis_harness.method_experiment.state import (
    ExperimentRunState,
    TrialRecord,
)

#: 弃权原因词表（推荐层诚实语义；不是错误）。
ABSTAIN_REASONS = (
    "insufficient_completed_trials",   # 可比组样本 <2
    "metrics_not_evaluated",           # 试验全部 not_evaluated
    "scenario_not_rankable",           # 情景族：候选是不同问题不是不同答案
)


class MetricRow(BaseModel):
    """一行可比值（试验投影；进对比表/导出）。"""

    trial_id: str
    candidate_id: str
    variant_index: int = 0
    params: Dict[str, Any] = Field(default_factory=dict)
    metric_id: str
    value: float
    basis: Dict[str, Any] = Field(default_factory=dict)
    state: str = "completed"


class GroupRanking(BaseModel):
    """一个可比组内的确定性排名（含并列披露）。"""

    metric_id: str
    direction: str = ""
    unit_kind: str = ""
    rows: Tuple[MetricRow, ...] = ()            # 已按方向+tie-break 排序
    ties: Tuple[str, ...] = ()                  # 值并列的 trial_id（披露）
    n_evaluated: int = 0
    n_excluded: int = 0                         # 未评估/失败（诚实计数）
    exclusion_reasons: Tuple[str, ...] = ()

    def best(self) -> Optional[MetricRow]:
        return self.rows[0] if self.rows else None


class Recommendation(BaseModel):
    """推荐（证据充分时的建议 + 弃权时的诚实原因）。"""

    status: str                                  # "recommended" | "abstained"
    abstain_reason: str = ""                     # ⊆ ABSTAIN_REASONS
    candidate_id: str = ""
    trial_id: str = ""
    metric_evidence: Dict[str, float] = Field(default_factory=dict)
    basis: Dict[str, Any] = Field(default_factory=dict)
    sensitivity: Dict[str, Any] = Field(default_factory=dict)
    alternatives: Tuple[Dict[str, Any], ...] = ()
    caveats: Tuple[str, ...] = ()
    pareto_front: Tuple[str, ...] = ()           # 非支配 trial_id（≤8）


class ExperimentEvaluation(BaseModel):
    """一次实验的完整评价投影（compare 工具/UI 的数据形状）。"""

    experiment_id: str
    status: str
    comparable_groups: Tuple[GroupRanking, ...] = ()
    incomparable: Tuple[str, ...] = ()           # 不可比/未评估的诚实披露
    recommendation: Optional[Recommendation] = None
    counts: Dict[str, int] = Field(default_factory=dict)
    total_wall_ms: int = 0

    def to_bounded_dict(self) -> Dict[str, Any]:
        """有界投影（LLM/SSE 消费；行 ≤24、键有界）。"""
        return {
            "experiment_id": self.experiment_id,
            "status": self.status,
            "counts": dict(self.counts),
            "total_wall_ms": self.total_wall_ms,
            "groups": [
                {
                    "metric_id": g.metric_id,
                    "direction": g.direction,
                    "unit_kind": g.unit_kind,
                    "n_evaluated": g.n_evaluated,
                    "n_excluded": g.n_excluded,
                    "rows": [
                        {"trial_id": r.trial_id, "candidate_id": r.candidate_id,
                         "params": dict(list(r.params.items())[:4]),
                         "value": r.value,
                         "basis": {k: v for k, v in list(r.basis.items())[:3]}}
                        for r in g.rows[:12]],
                    "ties": list(g.ties)[:6],
                    "exclusion_reasons": list(g.exclusion_reasons)[:4],
                }
                for g in self.comparable_groups],
            "incomparable": list(self.incomparable)[:8],
            "recommendation": (
                self._recommendation_dict() if self.recommendation else None),
        }

    def _recommendation_dict(self) -> Dict[str, Any]:
        r = self.recommendation
        assert r is not None
        return {
            "status": r.status,
            "abstain_reason": r.abstain_reason,
            "candidate_id": r.candidate_id,
            "trial_id": r.trial_id,
            "metric_evidence": dict(r.metric_evidence),
            "basis": {k: v for k, v in list(r.basis.items())[:4]},
            "sensitivity": dict(list(r.sensitivity.items())[:4]),
            "alternatives": [dict(list(a.items())[:4]) for a in r.alternatives[:3]],
            "caveats": list(r.caveats)[:6],
            "pareto_front": list(r.pareto_front)[:8],
        }


def evaluate_experiment(
    plan: MethodExperimentPlan, state: ExperimentRunState,
) -> ExperimentEvaluation:
    """诚实评价：可比组排名 + Pareto + 推荐或弃权（纯函数）。"""
    counts = state.counts()
    groups = _comparable_groups(plan, state)
    incomparable = _incomparable_disclosure(plan, state, groups)
    recommendation = _recommend(plan, state, groups)
    return ExperimentEvaluation(
        experiment_id=state.experiment_id,
        status=state.status,
        comparable_groups=tuple(groups),
        incomparable=tuple(incomparable),
        recommendation=recommendation,
        counts=counts,
        total_wall_ms=state.total_wall_ms(),
    )


def _comparable_groups(
    plan: MethodExperimentPlan, state: ExperimentRunState,
) -> List[GroupRanking]:
    """协议指标 → 各自可比组排名（同协议指纹 + completed/reused 才进组）。"""
    usable_states = {"completed", "reused"}
    groups: List[GroupRanking] = []
    for metric_id in plan.protocol.metrics:
        contract = get_metric(metric_id)
        if contract is None:
            groups.append(GroupRanking(
                metric_id=metric_id, direction="", unit_kind="",
                rows=(), n_evaluated=0, n_excluded=len(state.trials),
                exclusion_reasons=(f"unknown_metric:{metric_id}",)))
            continue
        rows: List[Tuple[float, TrialRecord]] = []
        excluded: List[str] = []
        for t in state.trials.values():
            if t.state not in usable_states:
                excluded.append(f"{t.trial_id}:{t.state}"
                                + (f":{t.reason_code}" if t.reason_code else ""))
                continue
            v = t.metrics.get(metric_id)
            if v is None:
                excluded.append(f"{t.trial_id}:metric_absent")
                continue
            rows.append((float(v), t))
        # 方向排序 + candidate_id/variant tie-break（全确定性）
        reverse = contract.direction == "higher_better"
        rows.sort(key=lambda pair: (
            -pair[0] if reverse else pair[0],
            pair[1].candidate_id, pair[1].variant_index, pair[1].trial_id))
        ties = _detect_ties(rows, contract.direction)
        metric_rows = tuple(
            MetricRow(
                trial_id=t.trial_id, candidate_id=t.candidate_id,
                variant_index=t.variant_index, params=dict(t.params),
                metric_id=metric_id, value=v,
                basis=dict(t.metric_basis.get(metric_id, {})),
                state=t.state,
            )
            for v, t in rows
        )
        groups.append(GroupRanking(
            metric_id=metric_id, direction=contract.direction,
            unit_kind=contract.unit_kind, rows=metric_rows, ties=ties,
            n_evaluated=len(rows), n_excluded=len(excluded),
            exclusion_reasons=tuple(excluded[:6]),
        ))
    return groups


def _detect_ties(
    rows: List[Tuple[float, TrialRecord]], direction: str,
) -> Tuple[str, ...]:
    """值并列的 trial id（排序稳定性已由 tie-break 保证，并列只披露）。"""
    if len(rows) < 2:
        return ()
    ties: List[str] = []
    for i in range(1, len(rows)):
        if rows[i][0] == rows[i - 1][0]:
            ties.append(rows[i][1].trial_id)
    return tuple(ties)


def _incomparable_disclosure(
    plan: MethodExperimentPlan, state: ExperimentRunState,
    groups: List[GroupRanking],
) -> List[str]:
    """不可比/未评估的诚实披露（绝不默默吞）。"""
    notes: List[str] = []
    for t in state.trials.values():
        if t.state in ("not_evaluated", "failed", "skipped", "cancelled"):
            note = f"{t.trial_id}({t.candidate_id}):{t.state}"
            if t.reason_code:
                note += f":{t.reason_code}"
            notes.append(note[:120])
    # 协议声明但无任何值的指标
    for g in groups:
        if g.n_evaluated == 0:
            notes.append(f"metric_without_values:{g.metric_id}")
    # 跨数据集不可比提醒（value_unit 指标随输入量纲）
    contracts = [get_metric(m) for m in plan.protocol.metrics]
    if any(c is not None and c.unit_kind == "value_unit" for c in contracts):
        notes.append("value_unit_metrics_scoped_to_this_input")
    return notes


def _pareto_front(
    plan: MethodExperimentPlan, state: ExperimentRunState,
    groups: List[GroupRanking],
) -> Tuple[str, ...]:
    """同 comparable_group 的多指标非支配前沿（≤8 个 trial id）。

    支配定义：A 支配 B ⇔ 对组内全部指标 A 不劣于 B 且至少一指标严格更优。
    只在 ≥2 个指标均有值时构造（单指标排序已足够，无 Pareto 语义）。
    """
    by_metric = {g.metric_id: g for g in groups}
    metric_ids = [m for m in plan.protocol.metrics
                  if by_metric.get(m) is not None and by_metric[m].n_evaluated > 0]
    if len(metric_ids) < 2:
        return ()
    # 可比组必须一致才联合（跨组联合 = 归一化拍脑袋，红线禁止）
    cgs = {METRIC_REGISTRY[m].comparable_group for m in metric_ids
           if m in METRIC_REGISTRY}
    if len(cgs) > 1:
        return ()
    points: Dict[str, Dict[str, float]] = {}
    for t in state.trials.values():
        if t.state not in ("completed", "reused"):
            continue
        vals = {m: t.metrics.get(m) for m in metric_ids}
        if any(v is None for v in vals.values()):
            continue
        points[t.trial_id] = {m: float(v) for m, v in vals.items()}
    if len(points) < 2:
        return ()

    def dominates(a: Dict[str, float], b: Dict[str, float]) -> bool:
        better_or_equal = True
        strictly_better = False
        for m in metric_ids:
            contract = METRIC_REGISTRY[m]
            av, bv = a[m], b[m]
            if contract.direction == "lower_better":
                if av > bv:
                    return False
                if av < bv:
                    strictly_better = True
            else:
                if av < bv:
                    return False
                if av > bv:
                    strictly_better = True
        return better_or_equal and strictly_better

    front: List[str] = []
    for tid, pt in points.items():
        dominated = any(
            dominates(other, pt) for oid, other in points.items() if oid != tid)
        if not dominated:
            front.append(tid)
    # 确定性输出序（trial_id 字典序）+ 有界
    return tuple(sorted(front)[:8])


def _sensitivity(
    plan: MethodExperimentPlan, state: ExperimentRunState,
    candidate_id: str, metric_id: str,
) -> Dict[str, Any]:
    """同方法跨参数的指标离散度（敏感性证据；无值 = 空披露）。"""
    values: List[Tuple[Dict[str, Any], float]] = []
    for t in state.trials.values():
        if t.candidate_id != candidate_id or t.state not in ("completed", "reused"):
            continue
        v = t.metrics.get(metric_id)
        if v is not None:
            values.append((dict(t.params), float(v)))
    if len(values) < 2:
        return {}
    vals = [v for _, v in values]
    spread = max(vals) - min(vals)
    return {
        "metric_id": metric_id,
        "n_variants": len(values),
        "min": round(min(vals), 6),
        "max": round(max(vals), 6),
        "spread": round(spread, 6),
        "note": "同方法不同参数的指标跨度（>0 即参数敏感）",
    }


def _recommend(
    plan: MethodExperimentPlan, state: ExperimentRunState,
    groups: List[GroupRanking],
) -> Recommendation:
    """推荐或弃权（推荐只基于证据充分的第一可比组）。

    scenario 族一律弃权（review C-P1）：情景候选回答的是**不同问题**
    （30 分钟 vs 10 分钟覆盖、kNN-k vs 距离带权重）——按方向排名会
    把「阈值最大的情景」伪装成「更好的方法」。可比组照常呈现（有序
    对比是情景信息），但推荐语义不成立。
    """
    if plan.protocol.kind == "scenario":
        return Recommendation(
            status="abstained", abstain_reason="scenario_not_rankable",
            caveats=(
                "情景对比（同底数据、参数即情景）不产生『更好方法』推荐——"
                "候选是不同问题不是不同答案；指标表只做并列披露",
                "如需方法选型结论，请比较同一情景参数下的不同方法/参数候选"),
        )
    usable = [g for g in groups if g.n_evaluated >= 2 and g.best() is not None]
    if not usable:
        reason = "metrics_not_evaluated" if all(
            g.n_evaluated == 0 for g in groups) else "insufficient_completed_trials"
        return Recommendation(
            status="abstained", abstain_reason=reason,
            caveats=("证据不足——未评估不产生推荐（诚实弃权），"
                     "提高样本/修复失败候选后重跑",))

    primary = usable[0]
    best = primary.best()
    assert best is not None
    contract = get_metric(primary.metric_id)
    caveats: List[str] = []
    if contract is not None and contract.unit_kind == "value_unit":
        caveats.append("指标与输入值字段同量纲——结论仅对本数据集成立")
    caveats.extend(list(plan.protocol.notes)[:3])

    # 候选局限 + 唯一试验失败披露
    for sel in plan.candidates:
        if sel.candidate_id == best.candidate_id:
            caveats.extend(lim[:120] for lim in sel.limitations[:3])

    alternatives: List[Dict[str, Any]] = []
    for row in primary.rows[1:4]:
        alternatives.append({
            "candidate_id": row.candidate_id,
            "trial_id": row.trial_id,
            primary.metric_id: row.value,
            "params": dict(list(row.params.items())[:4]),
        })

    pareto = _pareto_front(plan, state, groups)
    evidence_values = best_metric_values(state, best.trial_id)
    return Recommendation(
        status="recommended",
        candidate_id=best.candidate_id,
        trial_id=best.trial_id,
        metric_evidence={m: v for m, v in evidence_values.items()
                         if m in plan.protocol.metrics},
        basis=dict(best.basis),
        sensitivity=_sensitivity(
            plan, state, best.candidate_id, primary.metric_id),
        alternatives=tuple(alternatives),
        caveats=tuple(caveats),
        pareto_front=pareto,
    )


def best_metric_values(
    state: ExperimentRunState, trial_id: str,
) -> Dict[str, float]:
    """一个试验的全部已评估指标（推荐证据投影）。"""
    t = state.trials.get(trial_id)
    if t is None:
        return {}
    return {m: v for m, v in t.metrics.items()}


def build_adoption_receipt(
    plan: MethodExperimentPlan, state: ExperimentRunState,
    *, trial_id: str, reason: str = "",
) -> Tuple[Optional[Any], str]:
    """采纳回执（decision receipt；用户选择 + 证据快照）。

    返回 (AdoptionReceipt|None, decline_code)。decline_code 非空 = 拒绝
    （试验不存在/未完成——绝不采纳未评估的候选）。
    """
    trial = state.trials.get(trial_id)
    if trial is None:
        return None, "MEXP_TRIAL_NOT_FOUND"
    if trial.state not in ("completed", "reused"):
        return None, "MEXP_TRIAL_NOT_COMPLETED"
    from app.services.gis_harness.method_experiment.state import AdoptionReceipt

    receipt = AdoptionReceipt(
        candidate_id=trial.candidate_id,
        trial_id=trial.trial_id,
        metric_snapshot=dict(trial.metrics),
        reason=reason[:200],
        received_at=time.time(),
        plan_fingerprint=state.plan_fingerprint,
    )
    return receipt, ""
