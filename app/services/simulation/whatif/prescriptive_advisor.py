"""处方性建议器（ADR-0193 §D4）——确定性核 + 可选 LLM 叙述层。

双层结构（对齐 ``gis_harness/intent_semantic.py`` 的降级范式，零静默降级）：

- **确定性核**（:meth:`PrescriptiveAdvisor.advise`，永远执行）：多方案指标
  矩阵 → 目标方向加权 → Pareto 非支配集 → ROI 敏感度（proxy:v1 投入代理）
  → 实施优先级。缺基线指标（delta_pct None）剔除出评分与支配判定；
- **LLM 叙述层**（:meth:`PrescriptiveAdvisor.advise_with_narrative`，可选，
  绝不阻断）：``_llm_available()`` 守卫 → lazy ``call_llm`` +
  ``resolve_llm_config(ModelRole.SPATIAL)`` → prompt 内嵌 JSON schema +
  fence 剥离 + pydantic 校验；不可用/非法/异常一律回落确定性模板并携带
  ``degraded_reason``（llm_unavailable / llm_output_invalid / llm_error）。

两个模块级 hook（``_llm_available`` / ``_call_llm``）供测试注入；None =
使用真实实现。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.services.spatial_decision.models import MetricDeltaV2

logger = logging.getLogger(__name__)

MODEL_TAG = "proxy:v1"

#: 指标方向缺省表（与 spatial_decision/comparison_engine 词汇一致）；
#: direction 为空串 = 信息性指标，不参与支配判定与评分。
DEFAULT_OPTIMIZATION_GOALS: Dict[str, str] = {
    "service_coverage_population": "maximize",
    "facility_count": "maximize",
    "green_area_m2": "maximize",
    "road_capacity_index": "maximize",
    "road_length_m": "",
}

_LLM_PLACEHOLDER_KEYS = {"", "your-api-key-here", "sk-...", "none", "null"}

# 测试注入 hook（None = 真实实现）
_llm_available = None  # type: ignore[var-annotated]
_call_llm = None  # type: ignore[var-annotated]


def _default_llm_available() -> bool:
    """LLM 可用性守卫（占位符 key = 不可用）。异常按不可用。"""
    try:
        return settings.LLM_API_KEY not in _LLM_PLACEHOLDER_KEYS
    except Exception:  # noqa: BLE001
        return False


# ────────────────────────────── 数据契约 ──────────────────────────────


class BranchDiffResult(BaseModel):
    """单分支相对基线的完整差分包（几何摘要 + 指标 delta + 对比图层）。"""

    branch_id: str = Field(..., description="分支 id（A/B/…）")
    title: str = Field(default="", description="方案标题")
    hypothesis: str = Field(default="", description="反事实问句")
    metric_deltas: List[MetricDeltaV2] = Field(default_factory=list)
    geometry_summary: Dict[str, Any] = Field(default_factory=dict)
    overlay_features: List[Dict[str, Any]] = Field(default_factory=list)
    cost_proxy: float = Field(default=0.0, description="投入代理（m + √m²，proxy:v1）")
    model: str = Field(default=MODEL_TAG, description="差分模型标识")


class PrescriptionResult(BaseModel):
    """处方性建议（确定性核产出；LLM 仅替换 narrative/因果链）。"""

    mode: str = Field(default="deterministic", description="llm | deterministic")
    degraded_reason: Optional[str] = Field(
        default=None, description="llm_unavailable | llm_output_invalid | llm_error"
    )
    recommended_branch_id: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    pareto_optimal: List[str] = Field(default_factory=list)
    rationale_causal_chain: List[str] = Field(default_factory=list)
    roi_sensitivity: List[Dict[str, Any]] = Field(default_factory=list)
    implementation_priority: List[Dict[str, Any]] = Field(default_factory=list)
    narrative: str = ""


class ScenarioComparison(BaseModel):
    """多方案对比包（矩阵 + 分支差分 + 处方建议）——专报的唯一数据源。"""

    comparison_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    parent_session_id: str = ""
    baseline_revision: Optional[int] = None
    baseline_fingerprint: str = ""
    baseline_metrics: Dict[str, Optional[float]] = Field(default_factory=dict)
    branches: List[BranchDiffResult] = Field(default_factory=list)
    metric_matrix: Dict[str, Dict[str, Optional[float]]] = Field(default_factory=dict)
    advice: PrescriptionResult = Field(default_factory=PrescriptionResult)


# ────────────────────────────── 确定性核 ──────────────────────────────


class PrescriptiveAdvisor:
    """多方案处方建议器（确定性核 + 可选 LLM 叙述）。"""

    def build_comparison(
        self,
        baseline_metrics: Dict[str, Optional[float]],
        branches: List[BranchDiffResult],
        optimization_goals: Optional[Dict[str, str]] = None,
        *,
        parent_session_id: str = "",
        baseline_revision: Optional[int] = None,
    ) -> ScenarioComparison:
        """指标矩阵 + 处方建议 → :class:`ScenarioComparison`（确定性）。"""
        baseline_metrics = dict(baseline_metrics or {})
        matrix: Dict[str, Dict[str, Optional[float]]] = {}
        keys = {str(k) for k in baseline_metrics}
        for branch in branches:
            for metric in branch.metric_deltas:
                keys.add(metric.metric_key)
        for key in sorted(keys):
            row: Dict[str, Optional[float]] = {
                "baseline": baseline_metrics.get(key),
            }
            for branch in branches:
                value = None
                for metric in branch.metric_deltas:
                    if metric.metric_key == key:
                        value = metric.simulated
                        break
                row[branch.branch_id] = value
            matrix[key] = row
        advice = self.advise(
            baseline_metrics=baseline_metrics,
            branches=branches,
            optimization_goals=optimization_goals,
        )
        return ScenarioComparison(
            parent_session_id=parent_session_id,
            baseline_revision=baseline_revision,
            baseline_fingerprint=self.fingerprint_metrics(baseline_metrics),
            baseline_metrics=baseline_metrics,
            branches=list(branches),
            metric_matrix=matrix,
            advice=advice,
        )

    @staticmethod
    def fingerprint_metrics(baseline_metrics: Dict[str, Optional[float]]) -> str:
        """基线指标指纹（canonical JSON SHA256；供专报锚定与对账）。"""
        import hashlib

        payload = json.dumps(baseline_metrics, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _directed_deltas(
        self,
        branch: BranchDiffResult,
        goals: Dict[str, str],
    ) -> Dict[str, float]:
        """可评价指标 → 有向 delta%（缺基线/无方向/信息性指标剔除）。"""
        out: Dict[str, float] = {}
        for metric in branch.metric_deltas:
            direction = goals.get(metric.metric_key, "")
            if not direction or metric.delta_pct is None:
                continue
            sign = 1.0 if direction == "maximize" else -1.0
            out[metric.metric_key] = sign * float(metric.delta_pct)
        return out

    def advise(
        self,
        baseline_metrics: Dict[str, Optional[float]],
        branches: List[BranchDiffResult],
        optimization_goals: Optional[Dict[str, str]] = None,
    ) -> PrescriptionResult:
        """确定性处方核：Pareto + ROI + 优先级 + 模板因果链（同输入同输出）。"""
        goals = {**DEFAULT_OPTIMIZATION_GOALS, **(optimization_goals or {})}
        if not branches:
            return PrescriptionResult(mode="deterministic")

        directed = {b.branch_id: self._directed_deltas(b, goals) for b in branches}
        id_by_branch = {b.branch_id: b for b in branches}

        # Pareto（共同可评价指标口径）：i 无劣于 j 且至少一项严格优于 j。
        pareto: List[str] = []
        for bid in directed:
            dominated = False
            for other in directed:
                if other == bid:
                    continue
                common = set(directed[bid]) & set(directed[other])
                if not common:
                    continue
                if all(directed[other][k] >= directed[bid][k] for k in common) and any(
                    directed[other][k] > directed[bid][k] for k in common
                ):
                    dominated = True
                    break
            if not dominated:
                pareto.append(bid)

        # 综合分（可评价指标均值，缺失不计）→ 并集 Pareto 前沿 → 推荐。
        scores = {
            bid: (sum(d.values()) / len(d) if d else 0.0) for bid, d in directed.items()
        }

        def _dominates_union(a: str, b: str) -> bool:
            union_keys = set(directed[a]) | set(directed[b])
            av = {k: directed[a].get(k, 0.0) for k in union_keys}
            bv = {k: directed[b].get(k, 0.0) for k in union_keys}
            return all(av[k] >= bv[k] for k in union_keys) and any(
                av[k] > bv[k] for k in union_keys
            )

        union_front = [
            bid
            for bid in directed
            if not any(
                _dominates_union(other, bid) for other in directed if other != bid
            )
        ]
        front = union_front or sorted(directed)
        recommended = sorted(front, key=lambda bid: (-scores[bid], bid))[0]

        # ROI 敏感度（按 方案×指标 展开；roi_index = 有向 delta%/100 / 成本代理）。
        roi_rows: List[Dict[str, Any]] = []
        for branch in branches:
            cost = max(float(branch.cost_proxy), 1e-9)
            for metric in branch.metric_deltas:
                direction = goals.get(metric.metric_key, "")
                if not direction or metric.delta_pct is None:
                    continue
                sign = 1.0 if direction == "maximize" else -1.0
                roi_rows.append(
                    {
                        "branch_id": branch.branch_id,
                        "metric_key": metric.metric_key,
                        "direction": direction,
                        "delta_pct": round(float(metric.delta_pct), 4),
                        "cost_proxy": branch.cost_proxy,
                        "roi_index": round(sign * float(metric.delta_pct) / 100.0 / cost, 8),
                    }
                )

        best_roi = {
            bid: max(
                (r["roi_index"] for r in roi_rows if r["branch_id"] == bid),
                default=0.0,
            )
            for bid in directed
        }
        priority = [
            {
                "branch_id": bid,
                "action": id_by_branch[bid].title or f"采纳方案 {bid}",
                "priority": rank + 1,
                "roi_index": best_roi[bid],
                "composite_score": round(scores[bid], 4),
                "rationale": self._priority_rationale(
                    id_by_branch[bid], best_roi[bid], bid in union_front
                ),
            }
            for rank, bid in enumerate(
                sorted(directed, key=lambda b: (-best_roi[b], b))
            )
        ]

        gaps = sum(
            1
            for branch in branches
            for metric in branch.metric_deltas
            if metric.missing_baseline
        )
        confidence = 0.7 if recommended else 0.0
        if recommended:
            confidence = max(0.1, min(0.95, 0.75 - 0.05 * gaps))
        chain = self._deterministic_chain(
            id_by_branch.get(recommended), goals
        )
        return PrescriptionResult(
            mode="deterministic",
            recommended_branch_id=recommended,
            confidence=round(confidence, 2),
            pareto_optimal=sorted(pareto),
            rationale_causal_chain=chain,
            roi_sensitivity=roi_rows,
            implementation_priority=priority,
            narrative=self._deterministic_narrative(
                recommended, branches, union_front, gaps
            ),
        )

    @staticmethod
    def _priority_rationale(branch: BranchDiffResult, roi: float, in_front: bool) -> str:
        if roi < 0:
            return f"方案 {branch.branch_id} 指标整体劣化，不建议实施"
        if in_front:
            return f"方案 {branch.branch_id} 位于非支配前沿，单位投入效益 {roi:.4f}"
        return f"方案 {branch.branch_id} 被其他方案支配，作为备选"

    def _deterministic_chain(
        self, branch: Optional[BranchDiffResult], goals: Dict[str, str]
    ) -> List[str]:
        """模板因果链：干预 → 指标变化 → 目标方向（确定性措辞）。"""
        if branch is None:
            return ["无可评价分支：全部方案缺少可计算指标"]
        chain: List[str] = []
        direction_word = {"maximize": "提升", "minimize": "下降"}
        for metric in branch.metric_deltas:
            direction = goals.get(metric.metric_key, "")
            if not direction or metric.delta_pct is None:
                if metric.missing_baseline:
                    chain.append(
                        f"{metric.metric_name} 缺基线证据，未参与评分（{metric.evidence_gap_note}）"
                    )
                continue
            word = direction_word.get(direction, "变化")
            chain.append(
                f"{branch.title or branch.branch_id} 干预 → {metric.metric_name}"
                f"{word} {abs(metric.delta_pct):.1f}%（{MODEL_TAG}）"
                f"→ 支持目标「{direction}」"
            )
        if not chain:
            chain.append(f"{branch.title or branch.branch_id} 无可评价指标变化")
        return chain

    @staticmethod
    def _deterministic_narrative(
        recommended: Optional[str],
        branches: List[BranchDiffResult],
        front: List[str],
        gaps: int,
    ) -> str:
        if recommended is None:
            return "当前无可评价方案：请先施加可度量的干预。"
        lines = [f"建议采纳方案 {recommended}（综合效益最高且位于非支配前沿）。"]
        front_txt = "、".join(sorted(front))
        lines.append(f"非支配前沿方案：{front_txt}。")
        for branch in branches:
            pos = sum(
                1
                for m in branch.metric_deltas
                if m.delta_pct is not None and m.delta_pct > 0
            )
            neg = sum(
                1
                for m in branch.metric_deltas
                if m.delta_pct is not None and m.delta_pct < 0
            )
            lines.append(
                f"方案 {branch.branch_id}：正向指标 {pos} 项、负向 {neg} 项"
                f"（投入代理 {branch.cost_proxy:.1f}）。"
            )
        if gaps:
            lines.append(f"注意：{gaps} 项指标缺基线证据，未参与评分（诚实披露）。")
        return "\n".join(lines)


# ────────────────────────────── LLM 叙述层 ──────────────────────────────

_LLM_JSON_SCHEMA = {
    "type": "object",
    "required": ["recommended_branch_id", "confidence", "causal_chain", "narrative"],
    "properties": {
        "recommended_branch_id": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "causal_chain": {"type": "array", "items": {"type": "string"}},
        "narrative": {"type": "string"},
    },
}


def _strip_fences(text: str) -> str:
    """剥离 markdown 代码围栏并截取首个平衡 JSON 对象（对齐 house 范式）。"""
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    start = cleaned.find("{")
    if start < 0:
        return cleaned
    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start : i + 1]
    return cleaned[start:]


class _LLMAdvice(BaseModel):
    recommended_branch_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    causal_chain: List[str]
    narrative: str


def _build_llm_prompt(
    baseline_metrics: Dict[str, Optional[float]],
    branches: List[BranchDiffResult],
    deterministic: PrescriptionResult,
    goals: Dict[str, str],
) -> List[Dict[str, str]]:
    payload = {
        "optimization_goals": {k: v for k, v in goals.items() if v},
        "baseline_metrics": baseline_metrics,
        "branches": [
            {
                "branch_id": b.branch_id,
                "title": b.title,
                "hypothesis": b.hypothesis,
                "cost_proxy": b.cost_proxy,
                "metric_deltas": [m.model_dump() for m in b.metric_deltas],
            }
            for b in branches
        ],
        "deterministic_core_result": {
            "recommended_branch_id": deterministic.recommended_branch_id,
            "pareto_optimal": deterministic.pareto_optimal,
            "roi_sensitivity": deterministic.roi_sensitivity,
        },
        "output_json_schema": _LLM_JSON_SCHEMA,
        "instructions": (
            "你是城市规划推演顾问。基于给定指标差分与确定性核结论，综合因果链"
            "给出处方性建议。只输出符合 output_json_schema 的 JSON，不要输出"
            "其他文本。缺基线（null）的指标不得编造数值。"
        ),
    }
    return [
        {"role": "system", "content": "你是严谨的空间规划处方性建议引擎。"},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


async def advise_with_narrative(
    advisor: PrescriptiveAdvisor,
    baseline_metrics: Dict[str, Optional[float]],
    branches: List[BranchDiffResult],
    optimization_goals: Optional[Dict[str, str]] = None,
) -> PrescriptionResult:
    """模块级便捷入口：advisor.advise_with_narrative 的转发。"""
    return await advisor.advise_with_narrative(
        baseline_metrics, branches, optimization_goals
    )


async def _advise_with_narrative_impl(
    self: PrescriptiveAdvisor,
    baseline_metrics: Dict[str, Optional[float]],
    branches: List[BranchDiffResult],
    optimization_goals: Optional[Dict[str, str]] = None,
) -> PrescriptionResult:
    """LLM 叙述层：任何失败显式降级（零静默），绝不 raise。"""
    goals = {**DEFAULT_OPTIMIZATION_GOALS, **(optimization_goals or {})}
    deterministic = self.advise(baseline_metrics, branches, optimization_goals)

    available_fn = _llm_available or _default_llm_available
    if not available_fn():
        deterministic.degraded_reason = "llm_unavailable"
        return deterministic

    try:
        from app.services.chat.model_config import ModelRole, resolve_llm_config

        cfg = resolve_llm_config(ModelRole.SPATIAL)
        call_fn = _call_llm
        if call_fn is None:
            from app.services.chat.llm_client import call_llm as call_fn
        messages = _build_llm_prompt(baseline_metrics, branches, deterministic, goals)
        response = await call_fn(cfg, messages)
        content = response["choices"][0]["message"]["content"]
        advice = _LLMAdvice.model_validate_json(_strip_fences(content))
    except (ValidationError, KeyError, IndexError, ValueError):
        logger.info("[whatif] llm advice output invalid; degraded", exc_info=True)
        deterministic.degraded_reason = "llm_output_invalid"
        return deterministic
    except Exception as exc:  # noqa: BLE001 — 网络等异常显式降级
        logger.info("[whatif] llm advice call failed: %s", exc)
        deterministic.degraded_reason = "llm_error"
        return deterministic

    deterministic.mode = "llm"
    deterministic.narrative = advice.narrative
    deterministic.rationale_causal_chain = advice.causal_chain
    deterministic.confidence = advice.confidence
    # LLM 只换叙述与因果链 —— 推荐结论仍以确定性核为准（防幻觉改判）。
    return deterministic


# 挂载方法（避免类体内 async 方法的循环依赖表述，保持 hook 可注入语义集中）
PrescriptiveAdvisor.advise_with_narrative = _advise_with_narrative_impl  # type: ignore[method-assign]
