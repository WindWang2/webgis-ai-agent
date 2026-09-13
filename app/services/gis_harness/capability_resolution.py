"""Capability Resolution —— 方向 3 的能力层规划原语（ADR-0181）。

回答任务书的两个问题分工：

- **Planner/LLM 先回答**「当前目标需要哪些能力」→ ``GoalRequirements``
  （capability id 词表，recipe/intent 派生 —— capability registry 是
  唯一能力词表事实源）；
- **Harness 再回答**「当前 Situation 下哪个 provider 最合适」→
  ``resolve_capabilities(goal_requirements, situation)``。

设计约束（承袭 ADR-0137 的只读投影纪律，不建第 N+1 事实源）：

- 纯函数门面：provider 候选来自 capability graph（tools/models 面，
  M1 起含 workflow/template 消费面）；资格判定复用 qualification_v8
  （offline/auth_tier/budget 为 V1 additive 面）；排序承袭
  candidate_planner_v8 的确定性 tie-break（score, kind, id）。
- provider 选择因子全部披露（factors），**无隐藏降级**：fallback /
  degraded 替代必须携带 reason code 出现在证据里。
- Situation 载体 = ``QualificationContext``（V8 既有六面 + V1 三面
  offline/auth_tier/budget），不另造情境类型。
- LLM 不参与裁决 —— 本模块输出是**计划证据**（可序列化、有界、
  deterministic），执行仍走 ToolRegistry/ToolDispatchService 单一管线。

方向 4/5 接口（并行安全，recon §7）：
- 方向 4（typed tool surface）：``describe_capability`` /
  ``list_capabilities`` 是只读查询协议 —— 不接管 pi surface 的字节
  预算与激活面；
- 方向 5（execution graph）：``CapabilityResolution.to_dict()`` 输出
  capability requirements + provider choice 纯数据，不含调度语义。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.capability_graph import (
    KIND_CAPABILITY,
    KIND_MODEL,
    KIND_TOOL,
    CapabilityGraph,
    get_capability_graph,
)
from app.services.gis_harness.candidate_planner_v8 import reliability_penalty_v8
from app.services.gis_harness.qualification_v8 import (
    QualificationContext,
    QualificationResult,
    QualificationStatus,
    estimate_for_node,
    qualify_node,
)

__all__ = [
    "GoalRequirements", "ProviderCandidate", "CapabilityDecision",
    "CapabilityResolution", "resolve_capabilities",
    "capability_status", "build_situation", "situation_from_profile",
    "describe_capability", "list_capabilities",
    "capability_planning_v1_enabled",
]


def capability_planning_v1_enabled() -> bool:
    """kill switch（默认开；=0 时生产接线退回逐位既有行为）。"""
    return os.getenv("GIS_CAPABILITY_PLANNING_V1", "1") not in ("0", "false", "False")


# ── 契约 ──────────────────────────────────────────────────────────────────


class GoalRequirements(BaseModel):
    """目标的能力需求（capability 词表，非工具名 —— 稳定契约）。"""

    capability_ids: List[str] = Field(default_factory=list)
    optional_ids: List[str] = Field(default_factory=list)
    task_hint: str = ""
    max_candidates: int = 8

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_ids": list(self.capability_ids[:16]),
            "optional_ids": list(self.optional_ids[:16]),
            "task_hint": self.task_hint[:64],
        }


@dataclass
class ProviderCandidate:
    """单 provider 候选：资格 + 排序因子全披露（无隐藏降级）。"""

    kind: str
    id: str
    qualification: QualificationResult
    latency_class: str = "medium"
    score: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "id": self.id,
            "status": self.qualification.status,
            "reasons": [r.to_dict() for r in self.qualification.reasons[:4]],
            "latency_class": self.latency_class,
            "score": round(self.score, 3),
            "factors": {k: round(v, 3) for k, v in self.factors.items()},
        }


@dataclass
class CapabilityDecision:
    """单能力的解析结论（资格 + 候选 + 替代 + 缺口，全部可解释）。"""

    capability_id: str
    required: bool
    status: str = QualificationStatus.UNKNOWN
    providers: List[ProviderCandidate] = field(default_factory=list)
    rejected: List[ProviderCandidate] = field(default_factory=list)
    degraded_alternatives: List[Dict[str, Any]] = field(default_factory=list)
    missing: List[Dict[str, str]] = field(default_factory=list)
    make_available: List[str] = field(default_factory=list)
    why: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability_id,
            "required": self.required,
            "status": self.status,
            "why": self.why,
            "providers": [p.to_dict() for p in self.providers[:8]],
            "rejected": [p.to_dict() for p in self.rejected[:4]],
            "degraded_alternatives": self.degraded_alternatives[:4],
            "missing": self.missing[:4],
            "make_available": [h[:96] for h in self.make_available[:4]],
        }


@dataclass
class CapabilityResolution:
    """一次能力解析的完整证据（可序列化；执行不经由本对象）。"""

    goal: GoalRequirements
    situation_digest: Dict[str, Any] = field(default_factory=dict)
    decisions: List[CapabilityDecision] = field(default_factory=list)
    conflicts: List[Dict[str, str]] = field(default_factory=list)
    deterministic: bool = True

    @property
    def status_summary(self) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for d in self.decisions:
            summary[d.status] = summary.get(d.status, 0) + 1
        return summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal.to_dict(),
            "situation": dict(self.situation_digest),
            "status_summary": dict(self.status_summary),
            "conflicts": list(self.conflicts[:4]),
            "decisions": [d.to_dict() for d in self.decisions[:16]],
        }

    def to_bounded_context(self, max_bytes: int = 2048) -> str:
        """LLM 上下文投影（有界字节；超限按优先级截断 —— 决策先于替代）。"""
        import json as _json

        lines: List[str] = []
        for d in self.decisions:
            best = d.providers[0] if d.providers else None
            head = f"{d.capability_id}[{d.status}]"
            if best is not None:
                head += f" -> {best.kind}:{best.id} (score {best.score:.2f})"
            lines.append(head)
            for r in d.make_available[:2]:
                lines.append(f"  ? {r}")
        blob = "\n".join(lines)
        if len(blob.encode("utf-8")) <= max_bytes:
            return blob
        while lines and len("\n".join(lines).encode("utf-8")) > max_bytes:
            lines.pop()
        return "\n".join(lines)


# ── Situation 构造辅助（生产接线入口）────────────────────────────────────


def build_situation(
    *,
    task_hint: str = "",
    profile: Optional[Dict[str, Any]] = None,
    base: Optional[QualificationContext] = None,
    offline: Optional[bool] = None,
    auth_tier: Optional[int] = None,
    budget_cost_class: str = "",
    owner_scope_key: str = "",
) -> QualificationContext:
    """从既有事实构造 Situation（缺席面保持 unknown，不猜）。

    ``base`` 提供六面上下文（调用方已有时复用）；``profile`` 是 Spatial
    Meta Profile（camelCase resolver 形态或 snake_case 均兼容）。
    """
    ctx = QualificationContext(
        task_hint=task_hint or (base.task_hint if base else ""),
        owner_scope_key=owner_scope_key or (base.owner_scope_key if base else ""),
    )
    if base is not None:
        for f in ("geometry_kinds", "crs", "crs_is_geographic", "field_names",
                  "feature_count", "raster_bands", "resolution_m_per_px",
                  "sensor", "temporal_inputs", "data_bytes", "map_layer_count",
                  "dependency_available", "credentials_present",
                  "gpu_available", "vram_bytes", "memory_bytes",
                  "max_latency_class"):
            # review P2：is not None（非 truthy）—— feature_count=0 /
            # resolution=0.0（地理 CRS 标记）/ crs_is_geographic=False 是
            # 观察事实，不得静默丢弃。
            val = getattr(base, f, None)
            if val is not None:
                setattr(ctx, f, val)
    if profile:
        _apply_profile(ctx, profile)
    ctx.offline = offline if offline is not None else (
        base.offline if base is not None else None)
    ctx.auth_tier = auth_tier if auth_tier is not None else (
        base.auth_tier if base is not None else None)
    ctx.budget_cost_class = budget_cost_class or (
        base.budget_cost_class if base is not None else "")
    return ctx


def situation_from_profile(
    profile: Optional[Dict[str, Any]],
    *,
    base: Optional[QualificationContext] = None,
    task_hint: str = "",
) -> QualificationContext:
    """Spatial Meta Profile → Situation（finalize 阶段的数据事实面）。"""
    return build_situation(task_hint=task_hint, profile=profile, base=base)


def _apply_profile(ctx: QualificationContext, profile: Dict[str, Any]) -> None:
    """profile → 上下文事实（有界；键形态 camelCase/snake_case 兼容）。"""

    def _get(*keys: str) -> Any:
        for k in keys:
            if k in profile and profile[k] is not None:
                return profile[k]
        return None

    geom = _get("geometry", "geometryKind", "geometry_types")
    if isinstance(geom, str) and geom:
        ctx.geometry_kinds = [geom[:32]]
    elif isinstance(geom, list) and geom:
        ctx.geometry_kinds = [str(g)[:32] for g in geom[:4]]
    crs = _get("crs")
    if isinstance(crs, str) and crs:
        ctx.crs = crs[:48]
        lowered = crs.lower()
        if "4326" in lowered or "crs84" in lowered or "geographic" in lowered:
            ctx.crs_is_geographic = True
            ctx.resolution_m_per_px = 0.0
    fields = _get("fields", "fieldNames")
    if isinstance(fields, dict):
        ctx.field_names = [str(k)[:64] for k in list(fields.keys())[:24]]
    elif isinstance(fields, list):
        ctx.field_names = [str(f)[:64] for f in fields[:24]]
    count = _get("featureCount", "feature_count", "point_count", "num_features")
    if isinstance(count, (int, float)):
        ctx.feature_count = int(count)
    bands = _get("bands", "raster_bands", "bandCount")
    if isinstance(bands, (int, float)):
        ctx.raster_bands = int(bands)
    res = _get("resolution_m_per_px", "resolution")
    if isinstance(res, (int, float)) and res >= 0:
        ctx.resolution_m_per_px = float(res)
    data_bytes = _get("data_bytes", "bytes", "size_bytes")
    if isinstance(data_bytes, (int, float)) and data_bytes > 0:
        ctx.data_bytes = int(data_bytes)


# ── 排序因子（V1 provider 选择规则，D8）─────────────────────────────────

_LATENCY_RANK = {"fast": 0, "medium": 1, "slow": 2}
_COST_RANK = {"light": 0, "medium": 1, "heavy": 2}
_SCALE_RANK = {"small": 0, "medium": 1, "large": 2, "unknown": 1}


def _data_scale_tier(situation: QualificationContext) -> str:
    if situation.data_bytes is not None and situation.data_bytes > 512 * 1024 * 1024:
        return "large"
    if situation.feature_count is not None and situation.feature_count > 100_000:
        return "large"
    return "medium"


def _provider_candidates(
    capability_id: str,
    situation: QualificationContext,
    graph: CapabilityGraph,
    session_id: str = "",
) -> Tuple[List[ProviderCandidate], List[ProviderCandidate]]:
    """capability → (ranked providers, rejected providers)。

    排序（score 越小越好，确定性 tie-break by (score, kind, id)）：
    latency 档位 + degraded 罚 0.5 + 可靠性罚分（既有 ledger 语义）+
    offline 场景本地加成 −0.25 + destructive 副作用罚 0.25 + 数据规模
    适配罚 0.25（scale_class=small 撞上 large 数据）。
    """
    nodes: List[Any] = []
    for tool_id in graph.tools_for_capability(capability_id):
        node = graph.node(f"{KIND_TOOL}:{tool_id}")
        if node is not None:
            nodes.append(node)
    for model in graph.models_for_capability(capability_id):
        nodes.append(model)

    offline = bool(situation.offline)
    large_data = _data_scale_tier(situation) == "large"
    ranked: List[ProviderCandidate] = []
    rejected: List[ProviderCandidate] = []
    for node in nodes:
        qual = qualify_node(node, situation, graph)
        est = estimate_for_node(node)
        cand = ProviderCandidate(
            kind=node.kind, id=node.id, qualification=qual,
            latency_class=est.latency_class,
        )
        factors: Dict[str, float] = {
            "latency_rank": float(_LATENCY_RANK.get(est.latency_class, 1)),
        }
        if qual.status == QualificationStatus.DEGRADED:
            factors["degraded_penalty"] = 0.5
        penalty = reliability_penalty_v8(f"{node.kind}:{node.id}", session_id)
        if penalty:
            factors["reliability_penalty"] = penalty
        network = node.extras.get("network")
        if offline and network is False:
            factors["offline_local_bonus"] = -0.25
        side_effect = str(node.extras.get("side_effect", "")).lower()
        if "destructive" in side_effect:
            factors["destructive_penalty"] = 0.25
        # review P3：弃用 provider 罚分 —— 解析面优先 canonical 后继
        # （图上 deprecation_of → fallback_to 边的排序面兑现；当前在线
        # registry 无弃用工具，先落机制）。
        if str(node.extras.get("status", "")).lower() == "deprecated":
            factors["deprecated_penalty"] = 0.5
        scale = str(node.extras.get("scale_class", "unknown")).lower()
        if large_data and scale == "small":
            factors["scale_mismatch_penalty"] = 0.25
        cand.factors = factors
        cand.score = sum(factors.values())
        if qual.status == QualificationStatus.INELIGIBLE:
            rejected.append(cand)
        else:
            ranked.append(cand)
    ranked.sort(key=lambda c: (c.score, c.kind, c.id))
    return ranked, rejected


#: 状态聚合序（好 → 差）：任一 eligible 即能力可用。
_STATUS_ORDER = (
    QualificationStatus.ELIGIBLE,
    QualificationStatus.DEGRADED,
    QualificationStatus.UNKNOWN,
    QualificationStatus.INELIGIBLE,
)


def capability_status(
    capability_id: str,
    situation: QualificationContext,
    *,
    graph: Optional[CapabilityGraph] = None,
    session_id: str = "",
) -> Tuple[str, List[ProviderCandidate], List[ProviderCandidate]]:
    """capability 在 Situation 下的聚合资格（select_candidates 第 12 层
    与 resolve_capabilities 共用的单点语义）。"""
    g = graph or get_capability_graph()
    if not g.has(KIND_CAPABILITY, capability_id):
        return QualificationStatus.UNKNOWN, [], []
    ranked, rejected = _provider_candidates(
        capability_id, situation, g, session_id=session_id)
    if not ranked and not rejected:
        return QualificationStatus.UNKNOWN, [], []
    for status in _STATUS_ORDER:
        for cand in ranked:
            if cand.qualification.status == status:
                return status, ranked, rejected
    return QualificationStatus.INELIGIBLE, ranked, rejected


# ── 核心原语（C4）─────────────────────────────────────────────────────────

_MAX_FALLBACK_DEPTH = 2


def resolve_capabilities(
    goal_requirements: GoalRequirements,
    situation: QualificationContext,
    *,
    graph: Optional[CapabilityGraph] = None,
    session_id: str = "",
) -> CapabilityResolution:
    """``resolve_capabilities(goal, situation) -> ranked candidates``。

    只负责能力层（不含 LLM narration）；同输入同输出（deterministic）；
    每个决策携带 why / rejected / missing / make_available（C7 契约）。
    """
    g = graph or get_capability_graph()
    resolution = CapabilityResolution(
        goal=goal_requirements,
        situation_digest=situation.to_dict(),
    )
    required_set = list(dict.fromkeys(goal_requirements.capability_ids))
    optional_set = {
        c for c in goal_requirements.optional_ids if c not in required_set}

    # 能力级互斥披露（registry 声明面，M1 conflicts_with 边）
    for cap in required_set:
        for other in g.conflicts_of_capability(cap):
            if other in required_set and other != cap:
                a, b = sorted((cap, other))
                resolution.conflicts.append({
                    "code": "capability_conflict",
                    "capabilities": f"{a}|{b}",
                    "hint": "split the goal or drop one capability",
                })
    resolution.conflicts = sorted(
        resolution.conflicts, key=lambda c: c["capabilities"])

    for cap in required_set:
        resolution.decisions.append(_resolve_one(
            cap, required=True, situation=situation, graph=g,
            session_id=session_id, max_candidates=goal_requirements.max_candidates))
    for cap in sorted(optional_set):
        resolution.decisions.append(_resolve_one(
            cap, required=False, situation=situation, graph=g,
            session_id=session_id, max_candidates=goal_requirements.max_candidates))
    return resolution


def _resolve_one(
    capability_id: str,
    *,
    required: bool,
    situation: QualificationContext,
    graph: CapabilityGraph,
    session_id: str,
    max_candidates: int,
) -> CapabilityDecision:
    decision = CapabilityDecision(
        capability_id=capability_id, required=required)
    if not graph.has(KIND_CAPABILITY, capability_id):
        decision.status = QualificationStatus.UNKNOWN
        decision.why = "capability_not_in_graph"
        decision.missing.append({
            "check": "registry",
            "observed": capability_id,
            "expected": "registered capability id",
        })
        return decision

    status, ranked, rejected = capability_status(
        capability_id, situation, graph=graph, session_id=session_id)
    decision.status = status
    decision.providers = ranked[:max_candidates]
    decision.rejected = rejected[:4]
    decision.why = (
        f"{'required' if required else 'optional'} capability"
        + (f" (task={situation.task_hint[:48]})" if situation.task_hint else ""))

    # what-would-make-available：来自全部候选（含被拒 provider）的修复
    # 提示 —— 失格能力必须披露「怎么才能可用」（无隐藏降级的另一半）。
    for cand in ranked:
        for r in cand.qualification.reasons:
            if r.hint and r.hint not in decision.make_available:
                decision.make_available.append(r.hint)
    for cand in rejected:
        for r in cand.qualification.reasons:
            if r.hint and r.hint not in decision.make_available:
                decision.make_available.append(r.hint)
    if status == QualificationStatus.UNKNOWN:
        for cand in rejected:
            for r in cand.qualification.reasons:
                decision.missing.append({
                    "check": r.check,
                    "observed": r.observed,
                    "expected": r.expected,
                })
                break  # 每 provider 一条（有界）
        if not decision.providers:
            decision.make_available.append(
                "no provider declared for this capability — register an "
                "algorithm/tool that implements it")

    # 无隐藏降级：不可用/降级时显式解析 fallback 链替代（有界深度）。
    if status in (QualificationStatus.INELIGIBLE, QualificationStatus.DEGRADED):
        decision.degraded_alternatives = _fallback_alternatives(
            capability_id, situation, graph, session_id=session_id)
    return decision


def _fallback_alternatives(
    capability_id: str,
    situation: QualificationContext,
    graph: CapabilityGraph,
    *,
    session_id: str,
    depth: int = 0,
) -> List[Dict[str, Any]]:
    if depth >= _MAX_FALLBACK_DEPTH:
        return []
    alternatives: List[Dict[str, Any]] = []
    for fb in graph.fallback_chain(KIND_CAPABILITY, capability_id):
        fb_status, fb_ranked, _ = capability_status(
            fb, situation, graph=graph, session_id=session_id)
        best = fb_ranked[0] if fb_ranked else None
        alternatives.append({
            "capability": fb,
            "status": fb_status,
            "best_provider": (
                {"kind": best.kind, "id": best.id, "score": round(best.score, 3)}
                if best is not None else None),
            "depth": depth + 1,
        })
    return alternatives


# ── 只读查询协议（方向 4 接口；方向 5 消费 to_dict）──────────────────────


def describe_capability(
    capability_id: str,
    *,
    situation: Optional[QualificationContext] = None,
    graph: Optional[CapabilityGraph] = None,
) -> Optional[Dict[str, Any]]:
    """capability 的有界只读画像（词表 + provider 面 + 互斥 + fallback）。

    ``situation`` 提供时附聚合资格结论（同为只读，不产生任何副作用）。
    """
    g = graph or get_capability_graph()
    node = g.node(f"{KIND_CAPABILITY}:{capability_id}")
    if node is None:
        return None
    out: Dict[str, Any] = {
        "id": capability_id,
        "label": node.label,
        "domain": node.extras.get("domain", ""),
        "category": node.extras.get("category", ""),
        "status": node.extras.get("status", ""),
        "deterministic": node.extras.get("deterministic"),
        "offline_capable": node.extras.get("offline_capable"),
        "providers": g.capability_providers(capability_id),
        "fallbacks": g.fallback_chain(KIND_CAPABILITY, capability_id),
        "conflicts": g.conflicts_of_capability(capability_id),
    }
    if situation is not None:
        status, ranked, _ = capability_status(
            capability_id, situation, graph=g)
        out["situation_status"] = status
        out["best_provider"] = (
            {"kind": ranked[0].kind, "id": ranked[0].id}
            if ranked else None)
    return out


def list_capabilities(
    *,
    domain: str = "",
    query: str = "",
    limit: int = 48,
    graph: Optional[CapabilityGraph] = None,
) -> List[Dict[str, Any]]:
    """capability 词表的有界只读枚举（domain 过滤 + 词法 query 命中）。"""
    g = graph or get_capability_graph()
    q = query.lower().strip()
    out: List[Dict[str, Any]] = []
    for node in g.nodes_by_kind(KIND_CAPABILITY):
        if domain and str(node.extras.get("domain", "")) != domain:
            continue
        if q and q not in node.corpus and q not in node.id:
            continue
        out.append({
            "id": node.id,
            "label": node.label,
            "domain": node.extras.get("domain", ""),
            "status": node.extras.get("status", ""),
        })
        if len(out) >= max(1, min(int(limit), 128)):
            break
    return out
