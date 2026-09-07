"""Runtime Evaluation Metrics（ADR-0103 §十一）—— 系统级评测指标库。

五族指标（全部确定性、可从评测语料/录制输入离线计算）：

1. Tool retrieval   ：recall@k / precision@k / irrelevant tool rate
2. Model routing    ：fallback rate / failure rate / 平均时延
3. Agent execution  ：completion rate / repeated calls / timeout rate /
                      no-progress incidence / 平均工具数
4. GIS correctness  ：算法族正确率（工具 → capability 与期望 capability
                      交集判定）、数据资格、final map state 断言透传
5. Context          ：overflow / truncation / schema & result token ratio

检索标注的**真相来源**：AlgorithmRegistry.tool_to_capability 反查 +
golden case 的 expected_capabilities —— 不引入新的人工标注真相。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# 1. Tool retrieval
# ---------------------------------------------------------------------------

def relevant_tools_for_case(registry: Any, expected_capabilities: Sequence[str]) -> List[str]:
    """case 期望 capability → 相关工具集（algorithm registry 反查，单一真相）。"""
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        cap_tool_map = get_algorithm_registry().capability_tool_map()
    except Exception:  # noqa: BLE001
        return []
    relevant: set = set()
    for cap in expected_capabilities:
        relevant.update(cap_tool_map.get(cap, ()))
    # 只保留活注册表内、模型可见的工具
    out: List[str] = []
    for name in sorted(relevant):
        try:
            desc = registry.descriptor(name)
        except KeyError:
            continue
        if desc.model_visible and int(desc.tier) < 3:
            out.append(name)
    return out


@dataclass
class RetrievalMetrics:
    k: int
    recall_at_k: float
    precision_at_k: float
    irrelevant_rate: float
    cases: int
    skipped: int = 0       # 无相关工具集的 case（不计入 recall 分母）

    def as_dict(self) -> Dict[str, Any]:
        return {
            "k": self.k,
            "recall_at_k": round(self.recall_at_k, 4),
            "precision_at_k": round(self.precision_at_k, 4),
            "irrelevant_rate": round(self.irrelevant_rate, 4),
            "cases": self.cases,
            "skipped": self.skipped,
        }


def retrieval_metrics(
    registry: Any,
    cases: Sequence[Any],
    *,
    k: int = 30,
) -> RetrievalMetrics:
    """对 golden 语料跑动态面选择并计算 recall@k / precision@k。

    相关性 = case.expected_capabilities 与工具 capability 的交集非空。
    """
    from app.services.chat.tool_surface_v3 import DynamicToolSurface, ToolSelectionContext

    surface = DynamicToolSurface(registry)
    recalls: List[float] = []
    precisions: List[float] = []
    skipped = 0
    for case in cases:
        caps = tuple(getattr(case, "expected_capabilities", ()) or ())
        query = getattr(case, "query", "") or ""
        selected = surface.select(
            ToolSelectionContext(user_message=query, active_capabilities=caps, k_max=k)
        ).names
        relevant = set(relevant_tools_for_case(registry, caps))
        if not relevant:
            skipped += 1
            continue
        hit = relevant & set(selected)
        recalls.append(len(hit) / len(relevant))
        precisions.append(len(hit) / len(selected) if selected else 0.0)
    n = len(recalls)
    return RetrievalMetrics(
        k=k,
        recall_at_k=sum(recalls) / n if n else 0.0,
        precision_at_k=sum(precisions) / n if n else 0.0,
        irrelevant_rate=1.0 - (sum(precisions) / n if n else 0.0),
        cases=n,
        skipped=skipped,
    )


@dataclass
class SurfaceRetrievalReport:
    """V4 离线门报告（一次 select/case → 多 k recall + 安全/预算不变式）。"""

    cases: int
    skipped: int
    k_max: int
    recall_at_k: Dict[int, float]
    precision_at_k_max: float
    avg_active_tools: float
    tier3_leak: int                 # tier>=3 / effective_security_tier>=3 入面次数（恒期望 0）
    avg_schema_bytes: float         # 仅 byte_sample>0 时计算，否则 0.0
    max_schema_bytes: int
    budget_violations: int          # project 字节预算违约次数（恒期望 0）

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cases": self.cases,
            "skipped": self.skipped,
            "k_max": self.k_max,
            "recall_at_k": {str(k): round(v, 4) for k, v in sorted(self.recall_at_k.items())},
            "precision_at_k_max": round(self.precision_at_k_max, 4),
            "avg_active_tools": round(self.avg_active_tools, 2),
            "tier3_leak": self.tier3_leak,
            "avg_schema_bytes": round(self.avg_schema_bytes, 1),
            "max_schema_bytes": self.max_schema_bytes,
            "budget_violations": self.budget_violations,
        }


def surface_retrieval_report(
    registry: Any,
    cases: Sequence[Any],
    *,
    k_max: int = 30,
    top_ks: Sequence[int] = (5, 10, 30),
    byte_sample: int = 0,
    byte_budget: int = 24576,
    context_overrides: Optional[Dict[str, Any]] = None,
) -> SurfaceRetrievalReport:
    """对任意 ``{query, expected_capabilities}`` 案例序列跑一次 select/case。

    V4 additive helper（ADR-0104 决策 5 离线门）：
    - recall@k 对 ``top_ks`` 中每个 k ≤ k_max 计算（一次选择，多 k 复用）；
    - tier3_leak 恒期望 0（选择 + 投影两层都不允许 tier-3 入面）；
    - ``byte_sample``>0 时对前 N 案例跑 ``project()`` 统计 schema 字节
      （默认预算 24KB，与 tier-2 既有预算同门）；
    - ``context_overrides`` 透传进 ToolSelectionContext（会话信号消融/评测）。
    全程确定性，无 LLM、无时间戳。
    """
    from app.services.chat.tool_surface_v3 import DynamicToolSurface, ToolSelectionContext

    surface = DynamicToolSurface(registry)
    ks = sorted({k for k in top_ks if 0 < k <= k_max})
    recalls: Dict[int, List[float]] = {k: [] for k in ks}
    precisions: List[float] = []
    skipped = 0
    active_counts: List[int] = []
    tier3_leak = 0
    byte_sizes: List[int] = []
    budget_violations = 0

    for case in cases:
        caps = tuple(getattr(case, "expected_capabilities", ()) or ())
        query = getattr(case, "query", "") or ""
        ctx_kwargs: Dict[str, Any] = {
            "user_message": query,
            "active_capabilities": caps,
            "k_max": k_max,
        }
        ctx_kwargs.update(context_overrides or {})
        sel = surface.select(ToolSelectionContext(**ctx_kwargs))
        active_counts.append(len(sel.names))
        for name in sel.names:
            try:
                desc = registry.descriptor(name)
            except KeyError:
                continue
            if int(desc.tier) >= 3 or desc.effective_security_tier >= 3:
                tier3_leak += 1
        relevant = set(relevant_tools_for_case(registry, caps))
        if not relevant:
            skipped += 1
            continue
        for k in ks:
            hit = relevant & set(sel.names[:k])
            recalls[k].append(len(hit) / len(relevant))
        hit_max = relevant & set(sel.names)
        precisions.append(len(hit_max) / len(sel.names) if sel.names else 0.0)

        if byte_sample and len(byte_sizes) < int(byte_sample):
            out = surface.project(
                ToolSelectionContext(**{**ctx_kwargs, "byte_budget": byte_budget}),
            )
            byte_sizes.append(int(out.get("bytes_used") or 0))
            if int(out.get("bytes_used") or 0) > byte_budget:
                budget_violations += 1

    n = len(recalls[ks[0]]) if ks else 0
    return SurfaceRetrievalReport(
        cases=n,
        skipped=skipped,
        k_max=k_max,
        recall_at_k={
            k: (sum(v) / len(v) if v else 0.0) for k, v in recalls.items()
        },
        precision_at_k_max=sum(precisions) / len(precisions) if precisions else 0.0,
        avg_active_tools=sum(active_counts) / len(active_counts) if active_counts else 0.0,
        tier3_leak=tier3_leak,
        avg_schema_bytes=sum(byte_sizes) / len(byte_sizes) if byte_sizes else 0.0,
        max_schema_bytes=max(byte_sizes) if byte_sizes else 0,
        budget_violations=budget_violations,
    )


# ---------------------------------------------------------------------------
# 2. Model routing
# ---------------------------------------------------------------------------

@dataclass
class RoutingMetrics:
    calls: int
    fallback_rate: float
    failure_rate: float
    avg_latency_s: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "calls": self.calls,
            "fallback_rate": round(self.fallback_rate, 4),
            "failure_rate": round(self.failure_rate, 4),
            "avg_latency_s": round(self.avg_latency_s, 3),
        }


_FALLBACK_HINTS = ("fallback", "health_skip", "health_deprioritize",
                   "capability_skip", "no_capable_fallback", "primary_cooldown")


def routing_metrics(
    observations: Sequence[Dict[str, Any]],
) -> RoutingMetrics:
    """observations: ``{"reason_codes": [...], "failed": bool, "latency_s": float}``。

    典型来源：model_routing_bridge 的决策留痕 / 决策日志聚合。
    """
    n = len(observations)
    if not n:
        return RoutingMetrics(0, 0.0, 0.0, 0.0)
    fallbacks = 0
    failures = 0
    latencies: List[float] = []
    for obs in observations:
        codes = " ".join(obs.get("reason_codes", ()) or ())
        if any(h in codes for h in _FALLBACK_HINTS):
            fallbacks += 1
        if obs.get("failed"):
            failures += 1
        lat = obs.get("latency_s")
        if isinstance(lat, (int, float)):
            latencies.append(float(lat))
    return RoutingMetrics(
        calls=n,
        fallback_rate=fallbacks / n,
        failure_rate=failures / n,
        avg_latency_s=sum(latencies) / len(latencies) if latencies else 0.0,
    )


# ---------------------------------------------------------------------------
# 3. Agent execution
# ---------------------------------------------------------------------------

@dataclass
class ExecutionMetrics:
    turns: int
    completion_rate: float
    timeout_rate: float
    no_progress_incidence: float
    repeated_call_rate: float
    avg_tool_count: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "turns": self.turns,
            "completion_rate": round(self.completion_rate, 4),
            "timeout_rate": round(self.timeout_rate, 4),
            "no_progress_incidence": round(self.no_progress_incidence, 4),
            "repeated_call_rate": round(self.repeated_call_rate, 4),
            "avg_tool_count": round(self.avg_tool_count, 2),
        }


def execution_metrics(turn_reports: Sequence[Dict[str, Any]]) -> ExecutionMetrics:
    """turn_reports: ``{"completed": bool, "timed_out": bool, "no_progress": bool,
    "repeated_calls": int, "tool_calls": int}``（trace/evidence 聚合产物）。"""
    n = len(turn_reports)
    if not n:
        return ExecutionMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    completed = sum(1 for r in turn_reports if r.get("completed"))
    timed_out = sum(1 for r in turn_reports if r.get("timed_out"))
    no_progress = sum(1 for r in turn_reports if r.get("no_progress"))
    with_repeats = sum(1 for r in turn_reports if (r.get("repeated_calls") or 0) > 0)
    total_tools = sum(int(r.get("tool_calls") or 0) for r in turn_reports)
    return ExecutionMetrics(
        turns=n,
        completion_rate=completed / n,
        timeout_rate=timed_out / n,
        no_progress_incidence=no_progress / n,
        repeated_call_rate=with_repeats / n,
        avg_tool_count=total_tools / n,
    )


# ---------------------------------------------------------------------------
# 4. GIS correctness
# ---------------------------------------------------------------------------

@dataclass
class GisCorrectnessMetrics:
    cases: int
    correct_algorithm_family_rate: float
    correct_data_qualification_rate: float
    final_map_state_pass_rate: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cases": self.cases,
            "correct_algorithm_family_rate": round(self.correct_algorithm_family_rate, 4),
            "correct_data_qualification_rate": round(self.correct_data_qualification_rate, 4),
            "final_map_state_pass_rate": round(self.final_map_state_pass_rate, 4),
        }


def gis_correctness_metrics(case_outcomes: Sequence[Dict[str, Any]]) -> GisCorrectnessMetrics:
    """case_outcomes: 评测 runner 产物，字段（均可缺省 —— 缺省不计入分母）：

    - expected_capabilities / used_tools（算法族正确 = used_tools 的派生
      capability 与期望交集非空）
    - data_qualified（数据资格断言结果）
    - final_map_state_pass（final map state 断言结果）
    """
    n_family = n_family_ok = 0
    n_qual = n_qual_ok = 0
    n_map = n_map_ok = 0
    for out in case_outcomes:
        caps = set(out.get("expected_capabilities") or ())
        used = out.get("used_tools") or []
        if caps and used:
            n_family += 1
            used_caps: set = set()
            try:
                from app.lib.gis.algorithm_registry import get_algorithm_registry

                t2c = get_algorithm_registry().tool_to_capability()
                used_caps = {t2c[t] for t in used if t in t2c}
            except Exception:  # noqa: BLE001 — 反查缺席按空集（诚实不计正确）
                used_caps = set()
            if used_caps & caps:
                n_family_ok += 1
        if "data_qualified" in out:
            n_qual += 1
            if out["data_qualified"]:
                n_qual_ok += 1
        if "final_map_state_pass" in out:
            n_map += 1
            if out["final_map_state_pass"]:
                n_map_ok += 1
    return GisCorrectnessMetrics(
        cases=len(case_outcomes),
        correct_algorithm_family_rate=n_family_ok / n_family if n_family else 0.0,
        correct_data_qualification_rate=n_qual_ok / n_qual if n_qual else 0.0,
        final_map_state_pass_rate=n_map_ok / n_map if n_map else 0.0,
    )


# ---------------------------------------------------------------------------
# 5. Context
# ---------------------------------------------------------------------------

@dataclass
class ContextMetrics:
    reports: int
    overflow_rate: float
    truncation_incidence: float
    schema_token_ratio: float
    result_token_ratio: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "reports": self.reports,
            "overflow_rate": round(self.overflow_rate, 4),
            "truncation_incidence": round(self.truncation_incidence, 4),
            "schema_token_ratio": round(self.schema_token_ratio, 4),
            "result_token_ratio": round(self.result_token_ratio, 4),
        }


def context_metrics(budget_reports: Sequence[Dict[str, Any]]) -> ContextMetrics:
    """budget_reports: context_assembler 的 ``budget_report`` dict 序列。"""
    n = len(budget_reports)
    if not n:
        return ContextMetrics(0, 0.0, 0.0, 0.0, 0.0)
    overflows = 0
    truncations = 0
    schema_ratios: List[float] = []
    result_ratios: List[float] = []
    for rep in budget_reports:
        by = rep.get("by_category", {}) or {}
        total = rep.get("total_est_tokens") or 0
        if rep.get("over_budget") or any(
            str(v).startswith("over_budget") for v in (rep.get("violations") or [])
        ):
            overflows += 1
        if rep.get("violations"):
            truncations += 1
        if total:
            schema_ratios.append(by.get("TOOL_SCHEMAS", 0) / total)
            result_ratios.append(by.get("TOOL_RESULTS", 0) / total)
    return ContextMetrics(
        reports=n,
        overflow_rate=overflows / n,
        truncation_incidence=truncations / n,
        schema_token_ratio=sum(schema_ratios) / len(schema_ratios) if schema_ratios else 0.0,
        result_token_ratio=sum(result_ratios) / len(result_ratios) if result_ratios else 0.0,
    )
