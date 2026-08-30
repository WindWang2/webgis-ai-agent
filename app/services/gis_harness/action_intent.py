"""GISActionIntent —— 执行侧/产品侧欠账的统一动作投影（ADR-0088 P1）。

ADR-0087 Future work 落地：ProductActionAdvisor（产品侧）与
PlanGraph.recommended_next（执行侧）的建议并轨为**单一确定性动作层**：

    Product debt / Execution debt / Render evidence debt
            ↓
    GISActionIntent（派生投影，不持久化 —— 无第二计划真相，ADR-0076）
            ↓
    Capability → Algorithm Resolver → Tool      （execution_mode=capability）
    GISMutationBatch / 确定性 runtime 修复       （execution_mode=runtime_repair）
    RenderObservation 重新采集                  （execution_mode=observation）
    Map Product Finalizer                       （execution_mode=finalization）

不变量（全部承袭既有 ADR，无新增例外）：

- Pi 仍是唯一 Agent Host：本模块是纯函数 / 只读 / 零 LLM / 零 IO ——
  输出只进 [GIS Plan] 投影行与测试，不构成 agent loop，不自调工具；
- 不持久化：同输入必同输出，复算即得；SessionPlan 仍是唯一计划真相；
- capability 字段只在 registry 能力真实存在时填写，绝不把 tool id
  冒充 capability（P9 §19 分层不被 shortcut）。

合并优先级（执行债 > 产品债 > 观察债，与 finalizer 状态语义对齐）：

    1. failed/unavailable mandatory 节点        → retry_capability
    2. ready 节点（DAG 欠执行，带 input_refs）  → run_capability
    3. pending map_layer（计划层未落 MapSpec）  → produce_layer
    4. render 缺席层（needs_repair facet）      → repair_runtime_layer
    5. pending chart（required）                → produce_chart
    6. pending/needs_repair statistics          → produce_statistics
    7. map_product render:stale（证据过期）     → reobserve
    8. narrative（产品未终验）                  → finalize_product
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from app.services.gis_harness.map_completion import RUNTIME_RENDER_CODES
from app.services.gis_harness.plan_graph import (
    PlanGraph,
    PlanNodeStatus,
    build_plan_graph,
)
from app.services.gis_harness.product_action import (
    ACTION_FINALIZE_PRODUCT,
    ACTION_PRODUCE_CHART,
    ACTION_PRODUCE_LAYER,
    ACTION_PRODUCE_STATISTICS,
    ACTION_REPAIR_LAYER_RENDER,
    ACTION_RETRY_ANALYSIS,
    ACTION_RUN_ANALYSIS,
    ProductActionRecommendation,
    advise_next_product_action,
)
from app.services.gis_harness.product_graph import (
    FS_COMPLETE,
    FS_NEEDS_REPAIR,
    KIND_ANALYSIS,
    KIND_MAP_LAYER,
    ProductFacetCompletion,
)

# ── 动作词表（有限集合；machine-readable）────────────────────────────
ACTION_RUN_CAPABILITY = "run_capability"
ACTION_RETRY_CAPABILITY = "retry_capability"
ACTION_REPAIR_RUNTIME_LAYER = "repair_runtime_layer"
ACTION_REASSERT_MAPSPEC = "reassert_mapspec"
# 产出/观察/终验通道沿用 advisor 词表（单一词表，不建同义词）。
ACTION_REOBSERVE = "reobserve"

# ── execution_mode：动作由哪条既有通道执行 ───────────────────────────
MODE_CAPABILITY = "capability"        # Capability → Algorithm Resolver → Tool（Pi 执行）
MODE_RUNTIME_REPAIR = "runtime_repair"  # 确定性 runtime 修复（harness 有界执行）
MODE_OBSERVATION = "observation"      # 前端 RenderObservation 重新采集
MODE_FINALIZATION = "finalization"    # Map Product Finalizer

# ── action_class：欠账类别（execution debt vs runtime repair debt 分离）──
CLASS_EXECUTION = "execution_debt"    # 重跑/补跑 capability（昂贵，Pi 裁决）
CLASS_PRODUCTION = "product_output"   # 从既有 artifact 派生产物（chart 等）
CLASS_RUNTIME_REPAIR = "runtime_repair_debt"  # desired state 正确、runtime 偏离
CLASS_OBSERVATION = "observation_debt"  # 证据过期/缺失，需要重新观察
CLASS_FINALIZATION = "finalization_debt"  # 产品面齐备、终验未落

_ACTION_MODE: Dict[str, str] = {
    ACTION_RUN_CAPABILITY: MODE_CAPABILITY,
    ACTION_RETRY_CAPABILITY: MODE_CAPABILITY,
    ACTION_PRODUCE_LAYER: MODE_CAPABILITY,
    ACTION_PRODUCE_CHART: MODE_CAPABILITY,
    ACTION_PRODUCE_STATISTICS: MODE_CAPABILITY,
    ACTION_REPAIR_RUNTIME_LAYER: MODE_RUNTIME_REPAIR,
    ACTION_REASSERT_MAPSPEC: MODE_RUNTIME_REPAIR,
    ACTION_REOBSERVE: MODE_OBSERVATION,
    ACTION_FINALIZE_PRODUCT: MODE_FINALIZATION,
}

_ACTION_CLASS: Dict[str, str] = {
    ACTION_RUN_CAPABILITY: CLASS_EXECUTION,
    ACTION_RETRY_CAPABILITY: CLASS_EXECUTION,
    ACTION_PRODUCE_LAYER: CLASS_EXECUTION,
    ACTION_PRODUCE_CHART: CLASS_PRODUCTION,
    ACTION_PRODUCE_STATISTICS: CLASS_PRODUCTION,
    ACTION_REPAIR_RUNTIME_LAYER: CLASS_RUNTIME_REPAIR,
    ACTION_REASSERT_MAPSPEC: CLASS_RUNTIME_REPAIR,
    ACTION_REOBSERVE: CLASS_OBSERVATION,
    ACTION_FINALIZE_PRODUCT: CLASS_FINALIZATION,
}

# advisor 动作 → 统一词表映射（语义不变，只换统一名；render 修复显式
# 命名为 runtime repair —— 它不经 capability/tool 通道执行）。
_ADVISOR_ACTION_MAP: Dict[str, str] = {
    ACTION_RUN_ANALYSIS: ACTION_RUN_CAPABILITY,
    ACTION_RETRY_ANALYSIS: ACTION_RETRY_CAPABILITY,
    ACTION_PRODUCE_LAYER: ACTION_PRODUCE_LAYER,
    ACTION_REPAIR_LAYER_RENDER: ACTION_REPAIR_RUNTIME_LAYER,
    ACTION_PRODUCE_CHART: ACTION_PRODUCE_CHART,
    ACTION_PRODUCE_STATISTICS: ACTION_PRODUCE_STATISTICS,
    ACTION_FINALIZE_PRODUCT: ACTION_FINALIZE_PRODUCT,
}

_MAX_ARTIFACT_INPUTS = 8


@dataclass
class GISActionIntent:
    """统一的下一步 GIS 动作（derived / bounded / serializable）。

    ``artifact_inputs`` 是**可复用的既有 artifact ref**（P4 最小重计算：
    只补欠账子树，能复用的输入随动作带给 Pi）—— 空列表表示无已知可复用
    输入，绝不虚构 ref。
    """

    facet_id: str
    kind: str
    action: str
    reason: str
    capability: str = ""       # registry capability id；无映射 → ""（不虚构）
    artifact_inputs: List[str] = field(default_factory=list)
    execution_mode: str = MODE_CAPABILITY
    action_class: str = CLASS_EXECUTION

    def to_dict(self) -> dict:
        return {
            "facet_id": self.facet_id,
            "kind": self.kind,
            "action": self.action,
            "capability": self.capability[:64],
            "artifact_inputs": [r[:64] for r in self.artifact_inputs[:_MAX_ARTIFACT_INPUTS]],
            "execution_mode": self.execution_mode,
            "action_class": self.action_class,
            "reason": self.reason[:120],
        }

    def projection_line(self) -> str:
        """单行有界投影（[GIS Plan] 块尾部；格式与 P9 兼容）。

        capability 在场 → ``[Next GIS Action] <capability>``（旧格式零漂移）；
        无 capability → ``[Next GIS Action] <kind>:<action>``；非 capability
        通道附加 ``<mode>`` 前缀 —— Pi 一眼看出该动作不经工具执行。
        产物债带存活上游 ref 时追加 ``(reuse: <ref>)``（P4：最小重计算的
        可复用输入直接进 Pi 视野 —— 只补产物，不重跑上游分析）。
        """
        if self.capability:
            target = self.capability
            if self.execution_mode != MODE_CAPABILITY:
                target = f"{self.execution_mode}:{target}"
        else:
            target = f"{self.kind}:{self.action}"
            if self.execution_mode not in (MODE_CAPABILITY, MODE_FINALIZATION):
                target = f"{self.execution_mode}:{target}"
        if self.artifact_inputs and self.action_class == CLASS_PRODUCTION:
            return f"[Next GIS Action] {target} (reuse: {self.artifact_inputs[0][:32]})"
        return f"[Next GIS Action] {target}"


def _from_plan_debt(
    chapter: Dict[str, Any],
    graph: Optional[PlanGraph],
) -> Optional[GISActionIntent]:
    """执行债：DAG failed/ready 节点（比产品欠账更优先 —— 下游全被阻塞）。

    与 ``PlanGraph.recommended_next`` 同源（单一计算源，不重推 ready）：
    failed 节点在 ``_evaluate`` 里不被翻 ready（等待重试），这里显式按
    failed > ready 排序；多节点时取「依赖最少、声明序」的确定性序。
    """
    if graph is None:
        try:
            graph = build_plan_graph(chapter)
        except Exception:  # noqa: BLE001 — 图构建失败退回产品债
            return None
    failed = [n for n in graph.nodes if n.status == PlanNodeStatus.failed]
    if failed:
        node = min(failed, key=lambda n: (len(n.depends_on), n.node_id))
        return GISActionIntent(
            facet_id=f"{KIND_ANALYSIS}:{node.capability}",
            kind=KIND_ANALYSIS,
            action=ACTION_RETRY_CAPABILITY,
            reason=f"analysis '{node.capability}' failed — retry or replan owed",
            capability=node.capability,
            artifact_inputs=list(node.input_refs[:_MAX_ARTIFACT_INPUTS]),
            execution_mode=MODE_CAPABILITY,
            action_class=CLASS_EXECUTION,
        )
    ready = [n for n in graph.nodes if n.status == PlanNodeStatus.ready]
    if ready:
        node = min(ready, key=lambda n: (len(n.depends_on), n.node_id))
        return GISActionIntent(
            facet_id=f"{KIND_ANALYSIS}:{node.capability}",
            kind=KIND_ANALYSIS,
            action=ACTION_RUN_CAPABILITY,
            reason=f"capability '{node.capability}' ready — run with bound inputs",
            capability=node.capability,
            artifact_inputs=list(node.input_refs[:_MAX_ARTIFACT_INPUTS]),
            execution_mode=MODE_CAPABILITY,
            action_class=CLASS_EXECUTION,
        )
    return None


def _from_product_debt(
    chapter: Optional[dict],
    facets: List[ProductFacetCompletion],
    lineage: Any = None,
) -> Optional[GISActionIntent]:
    """产品债：复用 ProductActionAdvisor 的确定性优先级（单一计算源）。

    ``lineage``（P4，可选）：facet → artifact 血缘投影。chart/statistics
    欠账时可携带**存活的上游 artifact ref** 作为 artifact_inputs（最小重
    计算：只补产物，不重跑上游分析）。
    """
    rec: Optional[ProductActionRecommendation] = advise_next_product_action(
        chapter, facets
    )
    if rec is None:
        return None
    action = _ADVISOR_ACTION_MAP.get(rec.action, rec.action)
    # P4 Scenario B 升级：render 缺席 facet 的血缘若显示 source artifact
    # 已死（expired/驱逐确认），runtime repair 无从修复 —— 升级为执行债
    # （重跑上游 capability），绝不建议 remount 死 artifact。
    if lineage is not None and action == ACTION_REPAIR_RUNTIME_LAYER:
        entry = getattr(lineage, "entries", {}).get(rec.facet_id)
        recompute = list(getattr(entry, "recompute_capabilities", None) or [])
        if recompute:
            return GISActionIntent(
                facet_id=rec.facet_id,
                kind=rec.kind,
                action=ACTION_RETRY_CAPABILITY,
                reason=(
                    f"layer '{rec.facet_id.rsplit(':', 1)[-1]}' source artifact "
                    f"expired — rerun {recompute[0]}"
                ),
                capability=recompute[0],
                execution_mode=MODE_CAPABILITY,
                action_class=CLASS_EXECUTION,
            )
    inputs: List[str] = []
    if lineage is not None and action in (
        ACTION_PRODUCE_CHART, ACTION_PRODUCE_STATISTICS
    ):
        inputs = list(getattr(lineage, "reusable_inputs", lambda *_: [])(
            rec.facet_id
        ))[:_MAX_ARTIFACT_INPUTS]
    return GISActionIntent(
        facet_id=rec.facet_id,
        kind=rec.kind,
        action=action,
        reason=rec.reason,
        capability=rec.capability,
        artifact_inputs=inputs,
        execution_mode=_ACTION_MODE.get(action, MODE_CAPABILITY),
        action_class=_ACTION_CLASS.get(action, CLASS_EXECUTION),
    )


def _from_render_evidence(
    facets: List[ProductFacetCompletion],
    map_product: Optional[Dict[str, Any]],
) -> Optional[GISActionIntent]:
    """观察债：render:stale（desired state complete 而证据过期）→ reobserve。

    只在产品债/执行债全无时兜底 —— stale 是瞬态（前端会再观察），不与
    真欠账竞争。``runtime_repair`` 通道自身负责 render issues（P2）。
    facet 靶取首个 map_layer facet（证据按层采集；不虚构 facet 状态）。
    """
    if not isinstance(map_product, dict):
        return None
    if str(map_product.get("status") or "") != "complete":
        return None
    if str(map_product.get("render_status") or "") != "stale":
        return None
    layer = next((f for f in facets if f.kind == KIND_MAP_LAYER), None)
    return GISActionIntent(
        facet_id=layer.facet_id if layer is not None else "map_layer:render",
        kind=KIND_MAP_LAYER if layer is not None else "render",
        action=ACTION_REOBSERVE,
        reason="map product final but render evidence stale — re-observation owed",
        execution_mode=MODE_OBSERVATION,
        action_class=CLASS_OBSERVATION,
    )


def _facets_with_render_debt(
    chapter: Dict[str, Any],
    facets: List[ProductFacetCompletion],
) -> List[ProductFacetCompletion]:
    """map_product 块的 render findings → facet needs_repair（副本投影）。

    turn 上下文投影路径没有 render observation（同步投影、零 IO）—— 但
    finalizer 持久化的 ``map_product`` 块本身记录了渲染级 error findings。
    把其中 render_layer_missing 的 target 层 facet 投影为 needs_repair
    （derive 自持久事实，不虚构），advisor 的修复债优先级才能在投影路径
    生效。块状态非 needs_repair 或无 render error 时零改动。
    """
    block = chapter.get("map_product")
    if not isinstance(block, dict) or str(block.get("status") or "") != "needs_repair":
        return facets
    targets = {
        str(i.get("target") or "")
        for i in (block.get("issues") or [])
        if isinstance(i, dict)
        and str(i.get("code") or "") in RUNTIME_RENDER_CODES
        and str(i.get("severity") or "") == "error"
    }
    if not targets:
        return facets
    out: List[ProductFacetCompletion] = []
    for f in facets:
        if f.kind == KIND_MAP_LAYER and f.key in targets and f.status == FS_COMPLETE:
            f = replace(f, status=FS_NEEDS_REPAIR, render_status="issues")
        out.append(f)
    return out


def resolve_next_gis_action(
    chapter: Optional[Dict[str, Any]],
    facets: List[ProductFacetCompletion],
    *,
    graph: Optional[PlanGraph] = None,
    lineage: Any = None,
) -> Optional[GISActionIntent]:
    """统一动作解析（确定性 / 只读 / 零 LLM / 零 IO）。

    合并三个欠账来源（同输入必同输出）：

        执行债（plan graph failed/ready）
          → 产品债（facet advisor 优先级）
          → 观察债（render:stale reobserve）

    无欠账 → None（零噪声）。``graph`` 缺省时从章节派生（纯投影）。
    """
    if not isinstance(chapter, dict):
        return None
    intent = _from_plan_debt(chapter, graph)
    if intent is not None:
        return intent
    # render 债投影：finalizer 持久化块里的 render findings → facet
    # needs_repair（无 observation IO 的投影路径也能看见 runtime 缺席）。
    facets = _facets_with_render_debt(chapter, facets)
    intent = _from_product_debt(chapter, facets, lineage)
    if intent is not None:
        return intent
    map_product = chapter.get("map_product")
    return _from_render_evidence(facets, map_product if isinstance(map_product, dict) else None)


def action_intent_projection(
    chapter: Optional[Dict[str, Any]],
    facets: List[ProductFacetCompletion],
    *,
    graph: Optional[PlanGraph] = None,
    lineage: Any = None,
) -> str:
    """[GIS Plan] 尾部的单行统一建议（无欠账 → 空串，零噪声）。"""
    intent = resolve_next_gis_action(chapter, facets, graph=graph, lineage=lineage)
    return intent.projection_line() if intent else ""


__all__ = [
    "GISActionIntent",
    "ACTION_RUN_CAPABILITY",
    "ACTION_RETRY_CAPABILITY",
    "ACTION_PRODUCE_LAYER",
    "ACTION_PRODUCE_CHART",
    "ACTION_PRODUCE_STATISTICS",
    "ACTION_REPAIR_RUNTIME_LAYER",
    "ACTION_REASSERT_MAPSPEC",
    "ACTION_REOBSERVE",
    "ACTION_FINALIZE_PRODUCT",
    "MODE_CAPABILITY",
    "MODE_RUNTIME_REPAIR",
    "MODE_OBSERVATION",
    "MODE_FINALIZATION",
    "CLASS_EXECUTION",
    "CLASS_PRODUCTION",
    "CLASS_RUNTIME_REPAIR",
    "CLASS_OBSERVATION",
    "CLASS_FINALIZATION",
    "resolve_next_gis_action",
    "action_intent_projection",
]
