"""Dynamic Tool Surface V3（ADR-0103）—— 上下文感知的工具面投影。

V2（tool_surface_v2.py）解决的是「既有 catalog 结果的 additive 补强」；
V3 解决的是完整选择管线：

    Query/Task/Workflow Context
        → Capability Retrieval（词法 baseline + 可插拔语义检索）
        → Candidate Tools（descriptor 能力/域/标签语料）
        → Contract Filter（生命周期/tier/角色安全策略）
        → Rank & Select（10-30 个，确定性 tie-break）
        → Compact Surface（schema 压缩，交 Pi / engine）

不变式（与 V2 同门）：
- ToolRegistry 是唯一执行真相 —— 本模块只产出**投影**（names + reasons），
  schema 一律从 registry 现取，绝不手写第二清单；
- 词法检索是可靠 baseline：无 embedding provider、无网络时管线完整工作；
  语义检索只能经由环境变量显式注入（module:attr），失败降级词法并留痕；
- 同输入必同输出（排序 tie-break 用工具名），选择过程全量可解释
  （reasons / dropped / retriever / scores 进 trace 与评测断言面）；
- 安全红线：tier-3 / destructive 永不经本管线进入模型可见面（显式语义
  通道不变）；角色策略可进一步收紧（如 corpus worker 拿不到地图变更工具）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from app.tools.descriptor import SideEffectClass, ToolStatus
from app.tools.registry import ToolRegistry
from app.services.chat.schema_compression import compress_schema, schema_bytes
from app.services.chat.tool_retrieval import RetrievalHit, rank_tools

logger = logging.getLogger(__name__)

#: 语义检索注入点：``TOOL_RETRIEVAL_SEMANTIC="module:callable"``。
#: callable 签名 ``(registry, query: str, top_k: int) -> Sequence[RetrievalHit]``。
#: 未设置 / 加载失败 / 调用异常 → 词法 baseline（绝不让投影失败）。
_SEMANTIC_RETRIEVER_SPEC = os.getenv("TOOL_RETRIEVAL_SEMANTIC", "").strip()

#: 投影规模（goal §三：10-30 个）
DEFAULT_K_MIN = 10
DEFAULT_K_MAX = 30

#: 核心前门工具：无论检索结果如何都在面上（Tier-1 会话骨架）。
CORE_TOOL_NAMES: Tuple[str, ...] = (
    "webgis_map_intent",
    "webgis_map_product",
    "webgis_cartography_status",
    "list_available_tools",
)

#: 检索语料加权：capability 精确命中 > tags/domains > 描述
_CAPABILITY_HIT_SCORE = 9.0
_ALGORITHM_HIT_SCORE = 6.0
_DATA_PROFILE_HIT_SCORE = 4.0
_PREFERRED_BOOST = 5.0
_SURFACE_HINT_BOOST = 3.0

#: 角色 → 允许的副作用类（Surface 安全过滤，§十二）。
#: 未列出的角色 = 全部允许（tier-3 仍被生命周期闸拦住）。
ROLE_SIDE_EFFECT_POLICY: Dict[str, FrozenSet[str]] = {
    # 高吞吐低成本角色：只读 + 产出，不给地图/外部变更面
    "corpus_worker": frozenset({
        SideEffectClass.PURE.value,
        SideEffectClass.DETERMINISTIC_COMPUTE.value,
        SideEffectClass.CACHEABLE_READ.value,
        SideEffectClass.ARTIFACT_CREATION.value,
    }),
    "doc_crosscheck": frozenset({
        SideEffectClass.PURE.value,
        SideEffectClass.DETERMINISTIC_COMPUTE.value,
        SideEffectClass.CACHEABLE_READ.value,
    }),
    "descriptor_enrichment": frozenset({
        SideEffectClass.PURE.value,
        SideEffectClass.DETERMINISTIC_COMPUTE.value,
        SideEffectClass.CACHEABLE_READ.value,
    }),
    "static_analysis": frozenset({
        SideEffectClass.PURE.value,
        SideEffectClass.DETERMINISTIC_COMPUTE.value,
    }),
}


@dataclass(frozen=True)
class ToolSelectionContext:
    """一次动态面选择的全部上下文输入（全部可选，缺省退化为核心面）。"""

    user_message: str = ""
    task_type: str = ""                        # analysis | cartography | data_access | report | ...
    workflow_stage: str = ""                   # gis_harness ToolSurface phase
    active_capabilities: Tuple[str, ...] = ()  # SessionPlan 进行中的 capability id
    declared_domains: FrozenSet[str] = frozenset()
    data_profile_domains: Tuple[str, ...] = () # 会话数据画像域（dataset/chinese/...）
    map_state_summary: str = ""                # 小型地图状态摘要文本（层数/视口）
    role: str = "execution"
    k_min: int = DEFAULT_K_MIN
    k_max: int = DEFAULT_K_MAX
    byte_budget: Optional[int] = None


@dataclass
class SurfaceSelection:
    """选择结果（投影的符号层；schema 组装交给 project()）。"""

    names: List[str] = field(default_factory=list)
    reasons: Dict[str, List[str]] = field(default_factory=dict)
    dropped: Dict[str, str] = field(default_factory=dict)
    retriever: str = "lexical"
    selection_trace: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "names": list(self.names),
            "retriever": self.retriever,
            "reasons": {k: list(v) for k, v in self.reasons.items()},
            "dropped": dict(self.dropped),
            "selection_trace": dict(self.selection_trace),
        }


def _load_semantic_retriever():
    if not _SEMANTIC_RETRIEVER_SPEC:
        return None
    try:
        module_name, _, attr = _SEMANTIC_RETRIEVER_SPEC.partition(":")
        module = __import__(module_name, fromlist=[attr])
        return getattr(module, attr)
    except Exception:  # noqa: BLE001 — 注入失败降级词法，不阻断
        logger.warning(
            "[ToolSurfaceV3] semantic retriever %r failed to load; falling back to lexical",
            _SEMANTIC_RETRIEVER_SPEC, exc_info=True,
        )
        return None


class DynamicToolSurface:
    """确定性动态工具面选择器（对单个 registry 实例工作）。"""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._semantic = _load_semantic_retriever()

    # ------------------------------------------------------------------
    # 查询构造：intent + task + stage + capabilities + data/map 语境
    # ------------------------------------------------------------------
    def _composite_query(self, ctx: ToolSelectionContext) -> str:
        parts = [ctx.user_message or ""]
        if ctx.task_type:
            parts.append(ctx.task_type.replace("_", " "))
        if ctx.workflow_stage:
            parts.append(ctx.workflow_stage.replace("_", " "))
        if ctx.active_capabilities:
            parts.extend(ctx.active_capabilities)
        if ctx.data_profile_domains:
            parts.extend(ctx.data_profile_domains)
        # 地图状态只取有界摘要文本（调用方保证小型；此处再钳一次长度）
        if ctx.map_state_summary:
            parts.append(ctx.map_state_summary[:512])
        return " ".join(p for p in parts if p)[:4096]

    # ------------------------------------------------------------------
    # Contract 过滤：生命周期 + tier + 角色副作用策略
    # ------------------------------------------------------------------
    def _contract_filter(self, name: str, ctx: ToolSelectionContext) -> Optional[str]:
        """返回 drop 原因；None = 通过。"""
        try:
            desc = self.registry.descriptor(name)
        except KeyError:
            return "unknown"
        if not desc.model_visible:
            return f"lifecycle:{desc.status.value}"
        if desc.status is ToolStatus.EXTERNAL_UNAVAILABLE:
            return "lifecycle:external_unavailable"
        if int(desc.tier) >= 3 or desc.effective_security_tier >= 3:
            return "security:tier3"
        allowed = ROLE_SIDE_EFFECT_POLICY.get(ctx.role)
        if allowed is not None and desc.side_effect.value not in allowed:
            return f"role_policy:{ctx.role}:{desc.side_effect.value}"
        return None

    # ------------------------------------------------------------------
    # 主选择管线
    # ------------------------------------------------------------------
    def select(self, ctx: ToolSelectionContext) -> SurfaceSelection:
        selection = SurfaceSelection()
        k_max = max(1, int(ctx.k_max))
        k_min = max(0, min(int(ctx.k_min), k_max))

        # 1) 核心前门（无理由可裁）
        names: List[str] = []
        for core in CORE_TOOL_NAMES:
            drop = self._contract_filter(core, ctx)
            if drop:
                selection.dropped[core] = f"core:{drop}"
                continue
            names.append(core)
            selection.reasons.setdefault(core, []).append("core")

        # 2) surface 提示（workflow phase 的 preferred_tools —— 既有真相）
        #    由调用方把 preferred_tools 塞进 ctx.workflow_stage 检索词；
        #    显式传入走 SurfaceRequest.surface（兼容 V2 路径）。

        # 3) capability → tool 候选（algorithm registry 反查视图）
        cap_hits: Dict[str, List[str]] = {}
        if ctx.active_capabilities:
            try:
                from app.lib.gis.algorithm_registry import get_algorithm_registry

                cap_tool_map = get_algorithm_registry().capability_tool_map()
                for cap in ctx.active_capabilities:
                    for tool in cap_tool_map.get(cap, ()):
                        cap_hits.setdefault(tool, []).append(cap)
            except Exception:  # noqa: BLE001
                logger.debug("[ToolSurfaceV3] capability lookup failed", exc_info=True)

        query = self._composite_query(ctx)

        # 4) 检索（语义可选，词法必做 —— baseline 永远在）
        scores: Dict[str, float] = {}
        lexical_hits: List[RetrievalHit] = []
        if query:
            boosts: Dict[str, float] = {}
            for tool, caps in cap_hits.items():
                boosts[tool] = boosts.get(tool, 0.0) + _CAPABILITY_HIT_SCORE * len(caps)
            try:
                lexical_hits = rank_tools(
                    self.registry, query, boosts=boosts, top_k=k_max * 3,
                )
                for hit in lexical_hits:
                    scores[hit.name] = scores.get(hit.name, 0.0) + hit.score
                    selection.reasons.setdefault(hit.name, []).append(
                        f"lexical(score={hit.score:.1f},matched={list(hit.matched[:4])})"
                    )
            except Exception:  # noqa: BLE001
                logger.debug("[ToolSurfaceV3] lexical retrieval failed", exc_info=True)

        selection.retriever = "lexical"
        if self._semantic is not None and query:
            try:
                semantic_hits = list(self._semantic(self.registry, query, k_max * 2))
                selection.retriever = f"semantic:{_SEMANTIC_RETRIEVER_SPEC}"
                for hit in semantic_hits:
                    scores[hit.name] = scores.get(hit.name, 0.0) + float(hit.score)
                    selection.reasons.setdefault(hit.name, []).append(
                        f"semantic(score={float(hit.score):.1f})"
                    )
            except Exception:  # noqa: BLE001 — 语义检索失败降级词法
                logger.debug("[ToolSurfaceV3] semantic retrieval failed; lexical served",
                             exc_info=True)
                selection.retriever = "lexical"

        # 5) capability 命中直接入候选（检索 miss 的兜底 —— id 是精确真相）
        for tool, caps in cap_hits.items():
            scores[tool] = scores.get(tool, 0.0) + _CAPABILITY_HIT_SCORE * len(caps)
            selection.reasons.setdefault(tool, []).append(f"capability:{','.join(caps)}")

        # 6) 数据画像域 boost（会话里已有的数据类型 → 相关工具优先）
        if ctx.data_profile_domains:
            for name in self.registry.list_tools():
                try:
                    desc = self.registry.descriptor(name)
                except KeyError:
                    continue
                if set(desc.domains) & set(ctx.data_profile_domains):
                    scores[name] = scores.get(name, 0.0) + _DATA_PROFILE_HIT_SCORE
                    if scores[name] > 0:
                        selection.reasons.setdefault(name, []).append(
                            "data_profile:" + ",".join(sorted(set(desc.domains) & set(ctx.data_profile_domains)))
                        )

        # 7) contract 过滤 + 排序 + 规模控制
        candidates: List[Tuple[str, float]] = []
        for name, score in scores.items():
            if name in names:
                continue
            drop = self._contract_filter(name, ctx)
            if drop:
                selection.dropped[name] = drop
                continue
            candidates.append((name, score))
        candidates.sort(key=lambda t: (-t[1], t[0]))  # 分数降序，tie 按名（确定性）

        for name, score in candidates:
            if len(names) >= k_max:
                selection.dropped.setdefault(name, "k_max")
                continue
            names.append(name)
            selection.reasons.setdefault(name, []).append(f"selected(score={score:.1f})")

        # 规模下限：检索不中时不再强行凑数（k_min 是提示不是配额 —— 宁缺勿滥，
        # 模型有 list_available_tools 两跳通道）
        selection.names = names
        selection.selection_trace = {
            "k_requested": (k_min, k_max),
            "query_chars": len(query),
            "capability_candidates": len(cap_hits),
            "lexical_hits": len(lexical_hits),
            "selected": len(names),
        }
        return selection

    # ------------------------------------------------------------------
    # 投影：选择 → 模型可见 schema（压缩 + 字节预算 + 指纹）
    # ------------------------------------------------------------------
    def project(
        self,
        ctx: ToolSelectionContext,
        *,
        compress: str = "compact",
    ) -> Dict[str, Any]:
        """选择 + schema 组装（返回 ToolSurfaceProjection 形状的 dict）。"""
        from app.tools.descriptor import canonical_json, _short_digest

        selection = self.select(ctx)
        schemas: List[Dict[str, Any]] = []
        used_bytes = 0
        budget_dropped: Dict[str, str] = dict(selection.dropped)
        for name in selection.names:
            subset = self.registry.get_schemas_subset({name})
            if not subset:
                budget_dropped.setdefault(name, "schema_missing")
                continue
            s = subset[0]
            if compress != "none":
                try:
                    summary = self.registry.descriptor(name).summary or None
                except KeyError:
                    summary = None
                s = compress_schema(s, level=compress, summary=summary)
            size = schema_bytes(s)
            if ctx.byte_budget is not None and used_bytes + size > ctx.byte_budget:
                budget_dropped[name] = "byte_budget"
                continue
            used_bytes += size
            schemas.append(s)

        fingerprint = _short_digest(canonical_json([
            [s["function"]["name"], self.registry.schema_fingerprint(s["function"]["name"]) or ""]
            for s in schemas
        ]))
        return {
            "schemas": schemas,
            "reasons": selection.reasons,
            "dropped": budget_dropped,
            "bytes_used": used_bytes,
            "fingerprint": fingerprint,
            "retriever": selection.retriever,
            "selection_trace": selection.selection_trace,
        }


def get_dynamic_surface(registry: Optional[ToolRegistry] = None) -> DynamicToolSurface:
    """模块级便捷入口（每次调用轻量构造；语义注入点按 env 惰性加载）。"""
    if registry is None:
        from app.agent_pi_bridge import get_tool_registry
        registry = get_tool_registry()
    return DynamicToolSurface(registry)
