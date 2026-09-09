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
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from app.tools.descriptor import SideEffectClass, ToolStatus
from app.tools.registry import ToolRegistry
from app.services.chat.schema_compression import compress_schema, schema_bytes
from app.services.chat.tool_retrieval import RetrievalHit, rank_tools, v4_retrieval_enabled

logger = logging.getLogger(__name__)

#: 语义检索注入点：``TOOL_RETRIEVAL_SEMANTIC="module:callable"``。
#: callable 签名 ``(registry, query: str, top_k: int) -> Sequence[RetrievalHit]``。
#: 未设置 / 加载失败 / 调用异常 → 词法 baseline（绝不让投影失败）。
#: kill-switch（W12a）：spec 取 off/0/disabled/none（大小写/空白不敏感）
#: 或 ``GIS_TOOL_SEMANTIC=0`` → 语义路径整体缺席，静默返回 None（不打
#: warning —— 关掉就是关掉，不算故障），选择逐位回退词法。
_SEMANTIC_RETRIEVER_SPEC = os.getenv("TOOL_RETRIEVAL_SEMANTIC", "").strip()

#: kill-switch 的 spec 取值（调用期 strip + lower 后命中）。
_SEMANTIC_OFF_SPECS = frozenset({"off", "0", "disabled", "none"})

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

# ---------------------------------------------------------------------------
# V4 rerank（ADR-0104 决策 5）：contract 过滤**之前**的纯确定性重排阶段。
# 全部信号 additive、有界、逐项可解释（score_components 进选择结果供离线
# 评测）；rerank 只能调序/补充上下文候选，**绝不授予可见性** —— tier-3 /
# 生命周期 / 角色策略闸（_contract_filter）原样后置，破坏性确认仍只在
# dispatch 期（registry._dispatch_impl + 路由/Pi 桥）。
# Kill switch：GIS_TOOL_RETRIEVAL_V4=0 → 整个阶段跳过，行为与 V3 逐位一致。
# ---------------------------------------------------------------------------
_RERANK_PHASE_PREFERRED = 3.0     # workflow phase 的 preferred_tools（既有真相）
_RERANK_ARTIFACT_MATCH = 2.0      # session 工件语义类型 ↔ 工具 input/accepted 类型
_RERANK_ARTIFACT_CAP = 4.0
_RERANK_CRS_MATCH = 0.5
_RERANK_CRS_AGNOSTIC = 0.25
_RERANK_CRS_MISMATCH = -1.0
_RERANK_SCALE_MATCH = 1.0
_RERANK_SCALE_MISMATCH = -1.0
_RERANK_BUDGET_HEAVY = -1.0       # 紧字节预算下 slow/heavy 降权
_RERANK_BUDGET_LIGHT = 0.25
_RERANK_DETERMINISTIC = 0.25      # 已声明 deterministic=True 的温和偏好
_RERANK_FAILURE = -2.0            # 会话近期失败降权（只降不剔）
_RERANK_FAILURE_CAP = -4.0
_RERANK_FALLBACK_BOOST = 1.5      # 失败工具的 fallback_tool 小幅加分
_RERANK_CONTINUATION = 2.5        # sticky/续作工具加分

#: 会话信号有界化（payload 永有界）
_MAX_SESSION_ARTIFACT_TYPES = 32
_MAX_RECENT_OUTCOMES = 16
_MAX_CONTINUATION_TOOLS = 16
_MAX_COMPONENT_TOOLS = 64         # score_components 记录的工具数上限
_TIGHT_BYTE_BUDGET = 8000         # 低于该值视为紧预算（heavy/slow 降权）

#: session_artifact_types 的限定词条法：除语义类型外，调用方可携带
#: "crs:<value>" / "scale:<small|medium|large>" 限定词（小写）作为
#: CRS 兼容性与数据规模证据；缺席 → 对应信号贡献为零。
_CRS_QUALIFIER_PREFIX = "crs:"
_SCALE_QUALIFIER_PREFIX = "scale:"
_SCALE_ORDER = {"small": 0, "medium": 1, "large": 2}


def _v4_context_present(ctx: ToolSelectionContext) -> bool:
    """V4 排序证据门：有上下文证据才启用 V4 排序，否则精确 V3 行为。

    V4 排序的全部增量信号都消费**上下文证据**（workflow phase、字节预算、
    会话工件/结果/续作信号）。三者全缺席时任何 rerank 分量都只能是凭空
    编造 —— 此时词法层同样回落 V3 打分（enriched=False），选择结果与
    V3 逐位一致（golden corpora 集合恒等）。
    """
    return bool(
        ctx.session_artifact_types
        or ctx.recent_tool_outcomes
        or ctx.continuation_tools
        or ctx.workflow_stage
        or ctx.byte_budget is not None
    )


def recent_failure_hints(
    tool_names: Optional[Sequence[str]] = None,
    *,
    max_hints: int = _MAX_RECENT_OUTCOMES,
) -> Tuple[Dict[str, Any], ...]:
    """tool_metrics 聚合器 → ``recent_tool_outcomes`` 形状的**只读**提示。

    进程级（非会话级）失败信号源：``tool_metrics.aggregator_snapshot`` 的
    ``error_count``/``cancelled_count`` 映射为
    ``{"tool", "ok": False, "failure_class": "aggregator_error"|"cancelled"}``
    （failed 工具的 fallback_tool 由此在 rerank 中获得小幅加分）。会话级
    精确 failure_class（含 no_progress）由调用方的会话账本提供 —— 本辅助
    只兜底无会话账本的调用方。确定性：按工具名排序、截断有界。
    """
    try:
        from app.services.tool_metrics import aggregator_snapshot

        snap = aggregator_snapshot()
    except Exception:  # noqa: BLE001 — 指标面缺席按空（绝不阻断）
        return ()
    names = sorted(tool_names) if tool_names is not None else sorted(snap)
    out: List[Dict[str, Any]] = []
    for name in names:
        stats = snap.get(name)
        if not stats:
            continue
        cancelled = int(stats.get("cancelled_count") or 0)
        errors = int(stats.get("error_count") or 0)
        if errors > 0:
            out.append({"tool": name, "ok": False,
                        "failure_class": "aggregator_error"})
        elif cancelled > 0:
            out.append({"tool": name, "ok": False, "failure_class": "cancelled"})
        if len(out) >= max_hints:
            break
    return tuple(out)

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
    """一次动态面选择的全部上下文输入（全部可选，缺省退化为核心面）。

    V4（ADR-0104 决策 5）新增三个可选会话信号字段（尾部追加，构造兼容）：
    - ``session_artifact_types``：会话工件语义类型（可含 "crs:x"/"scale:y"
      限定词，见 _CRS_QUALIFIER_PREFIX）；缺席 → 工件/CRS/规模信号为零。
    - ``recent_tool_outcomes``：近期工具结果 ``{"tool","ok","failure_class"}``
      （有界 ≤16）；缺席 → 先验失败/续作反馈为零。
    - ``continuation_tools``：续作/sticky 工具名（有界 ≤16）。
    字段缺席时 rerank 相应分量为零 —— 行为与 V3 一致。
    """

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
    # --- V4 会话信号（可选；rerank 消费，contract 闸不因此放宽）---
    session_artifact_types: Tuple[str, ...] = ()
    recent_tool_outcomes: Tuple[Mapping[str, Any], ...] = ()
    continuation_tools: Tuple[str, ...] = ()


@dataclass
class SurfaceSelection:
    """选择结果（投影的符号层；schema 组装交给 project()）。"""

    names: List[str] = field(default_factory=list)
    reasons: Dict[str, List[str]] = field(default_factory=dict)
    dropped: Dict[str, str] = field(default_factory=dict)
    retriever: str = "lexical"
    selection_trace: Dict[str, Any] = field(default_factory=dict)
    # V4：每工具 rerank 分量（signal → delta，有界）；V3 模式恒为空。
    score_components: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "names": list(self.names),
            "retriever": self.retriever,
            "reasons": {k: list(v) for k, v in self.reasons.items()},
            "dropped": dict(self.dropped),
            "selection_trace": dict(self.selection_trace),
            "score_components": {
                k: dict(v) for k, v in self.score_components.items()
            },
        }


def _load_semantic_retriever():
    spec = (_SEMANTIC_RETRIEVER_SPEC or "").strip()
    if not spec or spec.lower() in _SEMANTIC_OFF_SPECS:
        return None
    if os.getenv("GIS_TOOL_SEMANTIC", "").strip() == "0":
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
    # V4 rerank：contract 过滤**前**的纯确定性上下文重排（ADR-0104 决策 5）
    # 全部信号 additive + 有界 + 可解释；kill switch 关闭 → 整段跳过。
    # ------------------------------------------------------------------
    @staticmethod
    def _phase_preferred_tools(stage: str) -> FrozenSet[str]:
        """workflow phase 的 preferred_tools（gis_harness 既有真相；缺席→空）。"""
        if not stage:
            return frozenset()
        try:
            from app.services.gis_harness.tool_surface import _PHASE_PREFERRED

            return frozenset(_PHASE_PREFERRED.get(stage, ()))
        except Exception:  # noqa: BLE001 — 表缺席按零贡献（不虚构）
            return frozenset()

    def _rerank(
        self,
        ctx: ToolSelectionContext,
        scores: Dict[str, float],
        selection: SurfaceSelection,
    ) -> None:
        """原地调整 scores（base + delta）；分量留痕 selection.score_components。

        rerank 只调序 / 注入上下文候选（continuation / fallback_tool），
        **绝不授予可见性**：注入候选同样经过后置 _contract_filter 与
        k_max / 字节预算闸。
        """
        components: Dict[str, Dict[str, float]] = {}

        def _add(name: str, signal: str, delta: float) -> None:
            if delta == 0.0:
                return
            scores[name] = scores.get(name, 0.0) + delta
            comp = components.setdefault(name, {})
            if len(comp) < 8:
                comp[signal] = round(comp.get(signal, 0.0) + delta, 4)

        # --- 会话信号有界化 + 限定词解析（crs:/scale:）---
        artifact_types: List[str] = []
        crs_hint = ""
        scale_hint = ""
        for entry in list(ctx.session_artifact_types)[:_MAX_SESSION_ARTIFACT_TYPES]:
            e = str(entry).strip().lower()
            if not e:
                continue
            if e.startswith(_CRS_QUALIFIER_PREFIX) and not crs_hint:
                crs_hint = e[len(_CRS_QUALIFIER_PREFIX):]
            elif e.startswith(_SCALE_QUALIFIER_PREFIX) and not scale_hint:
                scale_hint = e[len(_SCALE_QUALIFIER_PREFIX):]
            else:
                artifact_types.append(e)
        artifact_set = set(artifact_types)

        # --- 近期失败（只读消费 tool_metrics 语义；只降不剔）---
        fail_counts: Dict[str, int] = {}
        for out in list(ctx.recent_tool_outcomes)[:_MAX_RECENT_OUTCOMES]:
            try:
                tool = str(out.get("tool") or "").strip()
            except AttributeError:
                continue
            if not tool:
                continue
            fcls = str(out.get("failure_class") or "")
            if not out.get("ok", True) or fcls in ("no_progress", "suspicious"):
                fail_counts[tool] = min(fail_counts.get(tool, 0) + 1, 2)

        preferred = self._phase_preferred_tools(ctx.workflow_stage)
        tight_budget = ctx.byte_budget is not None and int(ctx.byte_budget) <= _TIGHT_BYTE_BUDGET

        # --- 上下文候选注入（仍受 contract filter + k_max 约束）---
        injected_reason: Dict[str, str] = {}
        continuation_set: FrozenSet[str] = frozenset(
            str(n).strip() for n in list(ctx.continuation_tools)[:_MAX_CONTINUATION_TOOLS]
            if str(n).strip()
        )
        for name in list(ctx.continuation_tools)[:_MAX_CONTINUATION_TOOLS]:
            n = str(name).strip()
            if n and n not in scores and n not in injected_reason:
                try:
                    self.registry.descriptor(n)
                except KeyError:
                    continue
                scores.setdefault(n, 0.0)
                injected_reason[n] = "rerank_candidate:continuation"
        fallback_targets: Dict[str, str] = {}
        for tool in fail_counts:
            try:
                fb = self.registry.descriptor(tool).fallback_tool
            except KeyError:
                continue
            if fb and fb != tool:
                fallback_targets.setdefault(fb, tool)
                if fb not in scores and fb not in injected_reason:
                    try:
                        self.registry.descriptor(fb)
                    except KeyError:
                        continue
                    scores.setdefault(fb, 0.0)
                    injected_reason[fb] = f"rerank_candidate:fallback_of:{tool}"

        # --- 逐候选打分量（descriptor 为唯一结构化真相）---
        for name in list(scores.keys()):
            try:
                desc = self.registry.descriptor(name)
            except KeyError:
                continue
            if name in preferred:
                _add(name, "phase_preferred", _RERANK_PHASE_PREFERRED)
            if artifact_set:
                accepted = {str(a).lower() for a in desc.input_artifacts} | {
                    str(r).lower() for r in desc.accepts_ref_types
                }
                hit = artifact_set & accepted
                if hit:
                    _add(name, "artifact_type",
                         min(_RERANK_ARTIFACT_CAP, _RERANK_ARTIFACT_MATCH * len(hit)))
            crs = str(desc.crs_semantics or "").strip().lower()
            if crs and crs_hint:
                if crs == crs_hint:
                    _add(name, "crs_match", _RERANK_CRS_MATCH)
                elif crs == "crs_agnostic":
                    _add(name, "crs_agnostic", _RERANK_CRS_AGNOSTIC)
                elif crs != "auto_project":
                    _add(name, "crs_mismatch", _RERANK_CRS_MISMATCH)
            sc = str(desc.scale_class or "").strip().lower()
            if sc in _SCALE_ORDER and scale_hint in _SCALE_ORDER:
                gap = abs(_SCALE_ORDER[sc] - _SCALE_ORDER[scale_hint])
                if gap == 0:
                    _add(name, "scale_match", _RERANK_SCALE_MATCH)
                elif gap >= 2:
                    _add(name, "scale_mismatch", _RERANK_SCALE_MISMATCH)
            if tight_budget:
                heavy = 0.0
                if str(desc.latency_class or "") == "slow":
                    heavy += _RERANK_BUDGET_HEAVY
                if str(desc.memory_class or "") == "heavy":
                    heavy += _RERANK_BUDGET_HEAVY
                if heavy:
                    _add(name, "budget_heavy", heavy)
                if str(desc.latency_class or "") == "fast":
                    _add(name, "budget_light", _RERANK_BUDGET_LIGHT)
            if desc.deterministic is True:
                _add(name, "deterministic", _RERANK_DETERMINISTIC)
            if name in fail_counts:
                _add(name, "prior_failure", _RERANK_FAILURE * fail_counts[name])
            if name in injected_reason:
                selection.reasons.setdefault(name, []).append(injected_reason[name])
            if name in continuation_set:
                _add(name, "continuation", _RERANK_CONTINUATION)
            if name in fallback_targets:
                _add(name, "fallback_boost", _RERANK_FALLBACK_BOOST)

        # --- 留痕（有界 + 确定）：每工具一条 rerank 理由 + 分量表 ---
        for name in sorted(components)[:_MAX_COMPONENT_TOOLS]:
            comp = components[name]
            total = sum(comp.values())
            selection.reasons.setdefault(name, []).append(
                f"rerank({','.join(sorted(comp))}|delta={total:+.1f})"
            )
        selection.score_components = {n: dict(components[n]) for n in sorted(components)[:_MAX_COMPONENT_TOOLS]}

    # ------------------------------------------------------------------
    # 主选择管线
    # ------------------------------------------------------------------
    def select(self, ctx: ToolSelectionContext) -> SurfaceSelection:
        selection = SurfaceSelection()
        k_max = max(1, int(ctx.k_max))
        k_min = max(0, min(int(ctx.k_min), k_max))
        # V4 排序证据门：kill switch 开 且 有上下文证据（phase/预算/会话
        # 信号）才启用 V4 词法增补 + rerank；否则与 V3 逐位一致。
        v4_ranking = v4_retrieval_enabled() and _v4_context_present(ctx)

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
                    enriched=v4_ranking,
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

        # 6.5) V4 rerank（ADR-0104 决策 5）：contract 过滤**之前**的确定性
        #      上下文重排（phase/artifact/CRS/规模/预算/确定性/失败反馈/
        #      续作）。证据门：kill switch 关 或 无任何 V4 上下文证据 →
        #      整段跳过（选择结果与 V3 逐位一致）。
        rerank_applied = False
        if v4_ranking:
            try:
                self._rerank(ctx, scores, selection)
                rerank_applied = True
            except Exception:  # noqa: BLE001 — rerank 故障退回 V3 序，绝不阻断
                logger.debug("[ToolSurfaceV3] v4 rerank failed; V3 order served",
                             exc_info=True)

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
        if rerank_applied:
            selection.selection_trace["rerank"] = {
                "tools_with_components": len(selection.score_components),
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
