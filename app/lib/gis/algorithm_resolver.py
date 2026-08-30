"""Algorithm Resolver —— capability → algorithm → tool 的确定性裁决点。

规则（§8 / ADR-0083）：capability 匹配 → native 状态 → 工具已注册 →
artifact/几何/字段/样本量兼容（含 max_features_hint 渲染上限）→
**成本模型**（ExecutionPolicy 加权；声明式 cpu/memory/io/approximate/
transport）→ fallback → 稳定排序（score, priority, id）。LLM 只能给
hint，最终由本 resolver 裁决；resolver 只读 descriptor/profile，不加载
FeatureCollection、不调 LLM、候选集有界。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.lib.gis.algorithm_registry import AlgorithmDescriptor, AlgorithmRegistry
from app.lib.gis.capability_registry import CapabilityRegistry
from app.lib.gis.cost_model import (
    infer_execution_policy,
    score_algorithm,
)

_MAX_REJECTIONS = 8       # evidence 有界：每个候选的拒绝理由最多记录 8 条
_MAX_FALLBACK_TRAIL = 8


class FallbackStep(BaseModel):
    """一次算法/能力级回退的记录（from/to/reason_code/evidence）。"""

    from_element: str
    to_element: str
    reason_code: str
    evidence: Dict[str, Any] = Field(default_factory=dict)


class AlgorithmResolution(BaseModel):
    """一次 capability 解析的完整裁决结果（进 Harness evidence）。"""

    capability: str
    status: Literal["resolved", "unavailable"] = "unavailable"
    algorithm: str = ""
    tool: str = ""
    reason: str = ""
    rejected: List[str] = Field(default_factory=list)     # 候选拒绝理由（有界）
    fallback_trail: List[FallbackStep] = Field(default_factory=list)
    fallback_candidates: List[str] = Field(default_factory=list)
    # ADR-0083（additive）：成本感知裁决的 evidence —— 策略、成本分与
    # 可解释 breakdown（空 = 单候选/无竞争，未走成本排序）。
    execution_policy: str = ""
    cost_score: Optional[int] = None
    cost_breakdown: str = ""


def _dominant_geometry(profile: Optional[Dict[str, Any]]) -> str:
    types = (profile or {}).get("geometryTypes")
    if not isinstance(types, list) or not types:
        return "unknown"
    point_family = {"Point", "MultiPoint"}
    line_family = {"LineString", "MultiLineString"}
    polygon_family = {"Polygon", "MultiPolygon"}
    s = {str(t) for t in types}
    if s & point_family:
        return "point"
    if s & polygon_family:
        return "polygon"
    if s & line_family:
        return "line"
    return "unknown"


def _profile_fields(profile: Optional[Dict[str, Any]]) -> Optional[set]:
    """profile 里已知的字段集合；fields 未知（descriptor 派生画像）时返回 None。"""
    prof = profile or {}
    fields = prof.get("fields")
    if not isinstance(fields, dict) or not fields:
        return None
    return set(fields.keys())


class AlgorithmResolver:
    """确定性 resolver（纯函数式，无 I/O、无 LLM、无数据加载）。"""

    def __init__(
        self,
        capabilities: Optional[CapabilityRegistry] = None,
        algorithms: Optional[AlgorithmRegistry] = None,
    ) -> None:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.capability_registry import get_capability_registry
        self.capabilities = capabilities or get_capability_registry()
        self.algorithms = algorithms or get_algorithm_registry()

    # ── 单候选静态检查 ────────────────────────────────────────────────
    def _check_candidate(
        self,
        algo: AlgorithmDescriptor,
        *,
        profile: Optional[Dict[str, Any]],
        available_tools: Optional[Any],
    ) -> tuple[str, str]:
        """返回 (tool, reason)；tool 为空表示拒绝，reason 是拒绝码。"""
        if algo.runtime_status != "native":
            return "", f"algorithm_not_native:{algo.id}"
        if not algo.tool_candidates:
            return "", f"no_tool_candidates:{algo.id}"
        if available_tools is not None:
            tool = next((t for t in algo.tool_candidates if t in available_tools), "")
            if not tool:
                return "", f"tool_unavailable:{algo.id}"
        else:
            tool = algo.tool_candidates[0]
        # 以下检查只在 profile 提供了对应事实时生效（descriptor 派生画像
        # 的 fields/几何可能未知——未知不等于不满足）。
        if profile is not None:
            geom = _dominant_geometry(profile)
            if algo.geometry_requirements and geom != "unknown" and \
                    geom not in algo.geometry_requirements:
                return "", f"geometry_mismatch:{algo.id}:input={geom}"
            if algo.min_features is not None:
                count = profile.get("featureCount")
                if isinstance(count, (int, float)) and int(count) < algo.min_features:
                    return "", (
                        f"insufficient_features:{algo.id}:"
                        f"{int(count)}<{algo.min_features}"
                    )
            # ADR-0083：渲染上限门 —— 声明了 max_features_hint 的算法
            # （原生渲染通道，如 density.visual.heatmap 的 FETCH_FEATURE_CAP）
            # 在超限时拒绝，触发算法/能力级 fallback（聚合通道）。
            if algo.max_features_hint is not None:
                count = profile.get("featureCount")
                if isinstance(count, (int, float)) and int(count) > algo.max_features_hint:
                    return "", (
                        f"over_render_cap:{algo.id}:"
                        f"{int(count)}>{algo.max_features_hint}"
                    )
            if algo.required_fields:
                known = _profile_fields(profile)
                if known is not None:
                    missing = [f for f in algo.required_fields if f not in known]
                    if missing:
                        return "", f"missing_fields:{algo.id}:{','.join(missing)}"
        return tool, ""

    def _candidate_reason(
        self, algo: AlgorithmDescriptor, tool: str, profile: Optional[Dict[str, Any]],
    ) -> str:
        parts: List[str] = ["native", f"tool={tool}"]
        if profile is not None:
            geom = _dominant_geometry(profile)
            if geom != "unknown":
                parts.append(f"{geom}_geometry")
            count = profile.get("featureCount")
            if isinstance(count, (int, float)):
                parts.append(f"feature_count={int(count)}")
        parts.append(f"priority={algo.priority}")
        return " + ".join(parts)

    # ── 主入口 ───────────────────────────────────────────────────────
    def resolve(
        self,
        capability: str,
        *,
        profile: Optional[Dict[str, Any]] = None,
        available_tools: Optional[Any] = None,
        policy_hint: str = "",
        export: bool = False,
        _visited: Optional[set[str]] = None,
    ) -> AlgorithmResolution:
        visited = set(_visited or ())
        if capability in visited:
            return AlgorithmResolution(
                capability=capability,
                status="unavailable",
                reason="fallback_cycle_detected",
            )
        visited.add(capability)
        cap = self.capabilities.get(capability)
        if cap is None:
            return AlgorithmResolution(
                capability=capability,
                status="unavailable",
                reason="capability_not_registered",
            )
        if cap.status != "native":
            return AlgorithmResolution(
                capability=capability,
                status="unavailable",
                reason=f"capability_{cap.status}",
            )

        candidates = self.algorithms.algorithms_for_capability(capability)
        if not candidates:
            return AlgorithmResolution(
                capability=capability,
                status="unavailable",
                reason="no_algorithm_for_capability",
            )

        # ADR-0083：ExecutionPolicy 自动推断（规模 > 导出语境 > 定量输出 >
        # 小数据 > 默认均衡）；hint 仅来自 planner 语境，用户不选。
        feature_count = None
        if profile is not None:
            fc = profile.get("featureCount")
            if isinstance(fc, (int, float)):
                feature_count = int(fc)
        policy = infer_execution_policy(
            feature_count=feature_count,
            export=export,
            deterministic_output=bool(getattr(cap, "deterministic", False)),
            policy_hint=policy_hint,
        )

        rejected: List[str] = []
        eligible: List[tuple[AlgorithmDescriptor, str]] = []
        for algo in candidates:
            tool, why = self._check_candidate(
                algo, profile=profile, available_tools=available_tools)
            if tool:
                eligible.append((algo, tool))
            else:
                rejected.append(why)

        if eligible:
            # 成本感知选择：排序键（priority, 策略加权成本分, id）——
            # registry 声明的 priority 仍是主偏好序（既有默认解析的前缀
            # 兼容承诺不变，如 service_area → isochrone_analysis），成本
            # 模型在同 priority 竞争者间裁决并提供 policy/scale evidence；
            # 大规模切换由 max_features_hint 硬门 + capability fallback
            # 承担。单候选直通（cost evidence 留空表示无竞争）。
            scored = []
            for algo, tool in eligible:
                score, breakdown = score_algorithm(algo, policy=policy)
                scored.append((algo.priority, score, algo.id, algo, tool, breakdown))
            if len(scored) > 1:
                scored.sort(key=lambda t: (t[0], t[1], t[2]))
            _, best_score, _, best, best_tool, best_bd = scored[0]
            reason = self._candidate_reason(best, best_tool, profile)
            contested = len(scored) > 1
            if contested:
                reason += (
                    f" + policy={policy} cost[{best_score}:{best_bd}]"
                )
            return AlgorithmResolution(
                capability=capability,
                status="resolved",
                algorithm=best.id,
                tool=best_tool,
                reason=reason,
                rejected=rejected[:_MAX_REJECTIONS],
                execution_policy=policy if contested else "",
                cost_score=best_score if contested else None,
                cost_breakdown=best_bd if contested else "",
            )

        # 候选全拒 → 算法级 fallback 链（from/to/reason_code/evidence）。
        trail: List[FallbackStep] = []
        for algo in candidates:
            for fb_id in algo.fallback_algorithms:
                fb = self.algorithms.get(fb_id)
                if fb is None or fb.runtime_status != "native":
                    continue
                tool, why = self._check_candidate(
                    fb, profile=profile, available_tools=available_tools)
                if tool:
                    trail.append(FallbackStep(
                        from_element=algo.id,
                        to_element=fb.id,
                        reason_code=rejected[0].split(":", 1)[0] if rejected else "INELIGIBLE",
                        evidence={"first_rejection": rejected[0] if rejected else ""},
                    ))
                    return AlgorithmResolution(
                        capability=capability,
                        status="resolved",
                        algorithm=fb.id,
                        tool=tool,
                        reason=self._candidate_reason(fb, tool, profile),
                        rejected=rejected[:_MAX_REJECTIONS],
                        fallback_trail=trail[:_MAX_FALLBACK_TRAIL],
                        fallback_candidates=[fb.id],
                    )
        # 能力级 fallback（如 grid_binning → density_surface）：目标能力可
        # 运行时记录为 fallback 建议，但本能力保持 unavailable（诚实报告；
        # 实际降级由 recipe eligibility / planner 图层回退执行）。
        for fb_cap in cap.fallback_capabilities:
            fb_resolution = self.resolve(
                fb_cap, profile=profile, available_tools=available_tools,
                policy_hint=policy_hint, export=export, _visited=visited)
            if fb_resolution.status == "resolved":
                trail.append(FallbackStep(
                    from_element=capability,
                    to_element=fb_cap,
                    reason_code=rejected[0].split(":", 1)[0] if rejected else "INELIGIBLE",
                    evidence={
                        "first_rejection": rejected[0] if rejected else "",
                        "fallback_tool": fb_resolution.tool,
                    },
                ))
                return AlgorithmResolution(
                    capability=capability,
                    status="unavailable",
                    reason=(
                        f"capability_fallback_available:{fb_cap}; "
                        + (rejected[0] if rejected else "no_eligible_algorithm")
                    ),
                    rejected=rejected[:_MAX_REJECTIONS],
                    fallback_trail=trail[:_MAX_FALLBACK_TRAIL],
                    fallback_candidates=[fb_cap],
                )

        return AlgorithmResolution(
            capability=capability,
            status="unavailable",
            reason=rejected[0] if rejected else "no_eligible_algorithm",
            rejected=rejected[:_MAX_REJECTIONS],
        )


_resolver: Optional[AlgorithmResolver] = None


def get_algorithm_resolver() -> AlgorithmResolver:
    global _resolver
    if _resolver is None:
        _resolver = AlgorithmResolver()
    return _resolver


def reset_algorithm_resolver() -> None:
    global _resolver
    _resolver = None
