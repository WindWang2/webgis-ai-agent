"""Goal → Product Graph 投影（ADR-0085）。

用户目标（如『成都小学分布情况』）不应被压扁成单个 heatmap —— 完整地图
产品是 facets 的集合：

    Goal
    ├── map_layer facets（primary/secondary 角色层）
    ├── analysis facets（能力行）
    ├── statistics / chart / annotation facets（MapSpec 组件）
    ├── export（模板导出画像）
    └── narrative（Pi 的叙述性回答）

本模块把 SessionPlan 章节 + MapSpec **投影**为结构化产品图，供
[GIS Plan] 披露、测试与后续 per-facet 完成度使用。

不变式（ADR-0076/0085）：ProductGraph 是**派生只读投影** —— 输入是
章节扁平行 / MapSpec / 完成块，绝不持久化、绝不成为第二计划真相；节点
状态全部回读既有事实（行状态 / 图层在场与启用 / 组件 enabled / map_product
块）。SessionPlan 仍是唯一的持久计划真相。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 节点种类（有限集合）
KIND_MAP_LAYER = "map_layer"
KIND_ANALYSIS = "analysis"
KIND_STATISTICS = "statistics"
KIND_CHART = "chart"
KIND_ANNOTATION = "annotation"
KIND_LEGEND = "legend"
KIND_INSET = "inset"
KIND_EXPORT = "export"
KIND_NARRATIVE = "narrative"

# 节点状态（投影自既有事实，非新状态机）
S_DONE = "done"
S_PENDING = "pending"
S_FAILED = "failed"
S_READY = "ready"
S_OFF = "off"  # 用户显式关闭（组件 enabled=false）

_ROW_STATUS_TO_NODE = {
    "available": S_DONE,
    "done": S_DONE,
    "complete": S_DONE,
    "pending": S_PENDING,
    "ready": S_PENDING,
    "running": S_PENDING,
    "failed": S_FAILED,
    "unavailable": S_FAILED,
    "voided": S_OFF,
    "skipped": S_OFF,
}

_STAT_KINDS = {
    "statistics_panel": KIND_STATISTICS,
    "chart_panel": KIND_CHART,
    "annotation": KIND_ANNOTATION,
    # v2（§18）：图例族 / 插图组件同样投影为 facet —— 多图层地图的
    # legend:heatmap / legend:district / inset 在产品图上可分辨。
    # legend facet 的必需性由 ProductFacetContract 决定（组合模板
    # required 槽位 → legend_required；缺省 informational —— 图例槽位
    # 本身多为 conditional）；状态仍投影自组件 enabled。
    "legend": KIND_LEGEND,
    "categorical_legend": KIND_LEGEND,
    "continuous_colorbar": KIND_LEGEND,
    "inset_map": KIND_INSET,
}

# chart/statistics 产物可复用的上游 artifact 语义类型（registry output
# 词表的子集 —— 表/聚合类；大要素集不作为 chart 最小重计算输入）。
# 单一定义：product_lineage / facet 依赖边共用（不建第二份词表）。
CHART_INPUT_ARTIFACT_TYPES = frozenset({
    "stats_table",
    "admin_aggregate_table",
    "od_matrix",
    "grid_aggregate",
})


@dataclass
class ProductNode:
    """产品图节点（derived，bounded）。"""

    node_id: str
    kind: str
    key: str
    label: str
    status: str = S_PENDING
    artifact_ref: str = ""
    inputs: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def _safe_contract(chapter: Optional[Dict[str, Any]]) -> "ProductFacetContract":
    """契约派生（容错包装）：失败 → 空契约，绝不阻断投影。"""
    try:
        from app.services.gis_harness.product_facets import (
            EMPTY_FACET_CONTRACT,
            derive_facet_contract,
        )

        return derive_facet_contract(chapter)
    except Exception:  # noqa: BLE001 — 投影降级，不虚构 required
        return EMPTY_FACET_CONTRACT


@dataclass
class ProductGraph:
    """目标 → 产品结构（纯投影；不持久化）。"""

    goal: str = ""
    recipe_id: str = ""
    nodes: List[ProductNode] = field(default_factory=list)

    def by_kind(self, kind: str) -> List[ProductNode]:
        return [n for n in self.nodes if n.kind == kind]

    @property
    def facets(self) -> List[ProductNode]:
        """产品 facets：地图层 + 统计/图表/注记 + 图例/插图（分析是供给，
        不算 facet）。"""
        return [
            n
            for n in self.nodes
            if n.kind in (
                KIND_MAP_LAYER, KIND_STATISTICS, KIND_CHART, KIND_ANNOTATION,
                KIND_LEGEND, KIND_INSET,
            )
        ]

    def summary_line(self) -> str:
        """Pi 投影行（单行、有界）：facet 构成 + 完成度概览。"""
        if not self.nodes:
            return ""
        parts: List[str] = []
        for kind, label in (
            (KIND_MAP_LAYER, "map"),
            (KIND_STATISTICS, "stats"),
            (KIND_CHART, "chart"),
            (KIND_ANNOTATION, "note"),
            (KIND_LEGEND, "legend"),
            (KIND_INSET, "inset"),
        ):
            nodes = self.by_kind(kind)
            if nodes:
                done = sum(1 for n in nodes if n.status in (S_DONE, S_READY))
                parts.append(f"{label} {done}/{len(nodes)}")
        if not parts:
            return ""
        # P9 §17：owed 尾巴点名 facet kind（"chart owed"），不再是无差别
        # "1 owed" —— Pi 直接看见欠哪个产品面。仍单行有界（≤3 类）。
        owed_kinds: List[str] = []
        for kind, label in (
            (KIND_MAP_LAYER, "map"),
            (KIND_STATISTICS, "stats"),
            (KIND_CHART, "chart"),
            (KIND_ANNOTATION, "note"),
            (KIND_LEGEND, "legend"),
            (KIND_INSET, "inset"),
            (KIND_ANALYSIS, "analysis"),
        ):
            n = sum(
                1
                for node in self.nodes
                if node.kind == kind and node.status in (S_PENDING, S_FAILED)
            )
            if n:
                owed_kinds.append(f"{label}×{n}" if n > 1 else label)
        tail = ""
        if owed_kinds:
            tail = f" — {', '.join(owed_kinds[:3])} owed"
        elif any(n.status in (S_PENDING, S_FAILED) for n in self.nodes):
            tail = " — 1 owed"  # narrative/export 等非 facet 类欠账
        return f"[Products] {' · '.join(parts)}{tail}"


def _row_status(raw: Any) -> str:
    return _ROW_STATUS_TO_NODE.get(str(raw or ""), S_PENDING)


def _layer_status(
    planned: Dict[str, Any], spec_layer_ids: set[str]
) -> str:
    if planned.get("enabled") is False:
        return S_OFF
    if str(planned.get("layer_id") or "") in spec_layer_ids:
        return S_DONE
    return S_PENDING


def build_product_graph(
    chapter: Optional[Dict[str, Any]],
    mapspec: Optional[Dict[str, Any]] = None,
    *,
    contract: Optional["ProductFacetContract"] = None,
) -> ProductGraph:
    """章节 + MapSpec → 产品图（纯函数；任一输入缺失按空处理）。

    ``contract``（可选）：产品 facet 契约（intent/recipe/composition →
    必需性）。缺省时内部派生（容错：派生失败 → 空契约，不虚构）。
    """
    if contract is None:
        contract = _safe_contract(chapter)
    graph = ProductGraph(
        goal=str((chapter or {}).get("query") or ""),
        recipe_id=str((chapter or {}).get("recipe_id") or ""),
    )
    if not isinstance(chapter, dict):
        return graph

    spec_layers = [
        ly
        for ly in ((mapspec or {}).get("layers") or [])
        if isinstance(ly, dict)
    ]
    spec_layer_ids = {str(ly.get("id") or "") for ly in spec_layers}
    provenance_by_ref: Dict[str, str] = {}
    for ly in spec_layers:
        prov = ly.get("provenance")
        ref = prov.get("result_ref") if isinstance(prov, dict) else None
        if isinstance(ref, str) and ref:
            provenance_by_ref[ref] = str(ly.get("id") or "")

    # analysis facets：能力行（requirement + step 同 capability 去重合并）
    analysis_nodes: Dict[str, ProductNode] = {}
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        cap = str(row.get("capability") or "")
        if not cap:
            continue
        status = _row_status(row.get("status"))
        ref = str(row.get("bound_ref") or "")
        existing = analysis_nodes.get(cap)
        if existing is None:
            analysis_nodes[cap] = ProductNode(
                node_id=f"{KIND_ANALYSIS}:{cap}",
                kind=KIND_ANALYSIS,
                key=cap,
                label=cap,
                status=status,
                artifact_ref=ref,
            )
        else:
            # 同能力两行（requirement/step）：取更强状态（failed > pending > done）
            rank = {S_FAILED: 3, S_PENDING: 2, S_DONE: 1, S_OFF: 0, S_READY: 1}
            if rank.get(status, 0) > rank.get(existing.status, 0):
                existing.status = status
            if ref and not existing.artifact_ref:
                existing.artifact_ref = ref
    graph.nodes.extend(analysis_nodes.values())

    # map_layer facets：章节计划层（状态 = 在场/启用投影）
    for planned in chapter.get("map_layers") or []:
        if not isinstance(planned, dict):
            continue
        lid = str(planned.get("layer_id") or "")
        if not lid:
            continue
        role = str(planned.get("role") or "primary")
        graph.nodes.append(
            ProductNode(
                node_id=f"{KIND_MAP_LAYER}:{lid}",
                kind=KIND_MAP_LAYER,
                key=lid,
                label=lid,
                status=_layer_status(planned, spec_layer_ids),
                metadata={"role": role},
            )
        )

    # 组件 facets：statistics / chart / annotation / legend 族 / inset
    # （状态 = enabled 投影；图例/插图是信息性 facet —— 见 _STAT_KINDS 注）
    chart_seen = False
    legend_seen = False
    for comp in ((mapspec or {}).get("layout") or {}).get("components") or []:
        if not isinstance(comp, dict):
            continue
        ctype = str(comp.get("type") or "")
        kind = _STAT_KINDS.get(ctype)
        if kind is None:
            continue
        if kind == KIND_CHART and comp.get("enabled") is not False:
            chart_seen = True
        if kind == KIND_LEGEND and comp.get("enabled") is not False:
            legend_seen = True
        cid = str(comp.get("id") or ctype)
        options = comp.get("options") or {}
        chart_ref = options.get("chartRef")
        bound_layer = str(options.get("layerId") or "")
        metadata: Dict[str, Any] = {}
        if bound_layer:
            metadata["layer_id"] = bound_layer
        label = cid
        if kind == KIND_LEGEND and bound_layer:
            # 多图例可分辨：legend:{layer}（§18 的 legend:heatmap 形态）
            label = f"{cid}@{bound_layer}"
        graph.nodes.append(
            ProductNode(
                node_id=f"{kind}:{cid}",
                kind=kind,
                key=cid,
                label=label,
                status=S_DONE if comp.get("enabled") is not False else S_OFF,
                artifact_ref=str(chart_ref) if isinstance(chart_ref, str) else "",
                metadata=metadata,
            )
        )

    # 应然构成合成（facet contract）：契约判定为产品必需的组件族缺席 →
    # 合成 pending 节点 —— 产品图反映"应然构成"，不只是"恰好存在的组件"。
    # 输入链：intent.task + recipe.export_profile + composition required
    # 槽位（product_facets.derive_facet_contract；旧 export_profile 读取
    # 面由契约内部兼容保留）。
    # - chart：export_profile.chart / required 槽位为真而无 enabled chart；
    # - legend：组合模板把图例族声明为 required 槽位（density_map 的
    #   colorbar、statistical_map 的 legend），且已有主题层落 MapSpec
    #   （无层不欠图例 —— 诚实护栏，不给空地图虚构欠账）。
    if contract.chart_required and not chart_seen:
        graph.nodes.append(
            ProductNode(
                node_id=f"{KIND_CHART}:required",
                kind=KIND_CHART,
                key="chart-required",
                label="chart-required",
                status=S_PENDING,
            )
        )
    if (
        contract.legend_required
        and not legend_seen
        and any(
            n.kind == KIND_MAP_LAYER and n.status == S_DONE
            for n in graph.nodes
        )
    ):
        graph.nodes.append(
            ProductNode(
                node_id=f"{KIND_LEGEND}:required",
                kind=KIND_LEGEND,
                key="legend-required",
                label="legend-required",
                status=S_PENDING,
            )
        )

    # export facet：模板导出画像（信息性 —— 导出动作本身不由计划真相追踪）
    export_profile = (chapter.get("template_selection") or {}).get("export_profile")
    if isinstance(export_profile, dict) and export_profile.get("formats"):
        graph.nodes.append(
            ProductNode(
                node_id=f"{KIND_EXPORT}:default",
                kind=KIND_EXPORT,
                key="default",
                label=",".join(str(f) for f in export_profile["formats"][:3]),
                status=S_READY,
            )
        )

    # narrative facet：完成块在场即 done（叙述由 Pi 产出，完成块是它的代理）
    map_product = chapter.get("map_product")
    graph.nodes.append(
        ProductNode(
            node_id=f"{KIND_NARRATIVE}:goal",
            kind=KIND_NARRATIVE,
            key="goal",
            label=graph.goal[:48] or "goal",
            status=(
                S_DONE
                if isinstance(map_product, dict)
                and map_product.get("status") == "complete"
                else S_PENDING
            ),
        )
    )

    # 供给边：analysis.ref → layer（MapSpec provenance 实证；无 spec 时
    # 单主层兜底：全部分析供给 primary 层）。
    layer_nodes = {n.key: n for n in graph.by_kind(KIND_MAP_LAYER)}
    for node in analysis_nodes.values():
        target = provenance_by_ref.get(node.artifact_ref)
        if target and target in layer_nodes:
            node.inputs.append(f"{KIND_MAP_LAYER}:{target}")
    if not provenance_by_ref:
        primary = next(
            (n for n in graph.by_kind(KIND_MAP_LAYER) if n.metadata.get("role") == "primary"),
            None,
        )
        if primary is not None:
            for node in analysis_nodes.values():
                if node.status == S_DONE:
                    node.inputs.append(primary.node_id)

    return graph


# ── Per-facet completion（P9 / ADR-0085 Future work 落地）────────────────
#
# 仍是**派生只读投影**：输入只有章节行 / MapSpec / ref descriptor / render
# observation / map_product 块 —— 无新状态机、无持久化。facet status 词表
# 与 map completion 对齐（complete/pending/failed/off/needs_repair）。

FS_COMPLETE = "complete"
FS_PENDING = "pending"
FS_FAILED = "failed"
FS_NEEDS_REPAIR = "needs_repair"
FS_OFF = "off"

# 必需性信号：模板 export_profile.chart=True → chart facet required
# （Scenario M 的"chart 欠着"判定源；statistics 无独立必需信号 —— 组件
# 在场即 facet，缺席不虚构 required）。

_NODE_TO_FACET_STATUS = {
    S_DONE: FS_COMPLETE,
    S_READY: FS_COMPLETE,
    S_PENDING: FS_PENDING,
    S_FAILED: FS_FAILED,
    S_OFF: FS_OFF,
}


@dataclass
class ProductFacetCompletion:
    """单 facet 完成度（derived / bounded）。"""

    facet_id: str
    kind: str
    key: str
    label: str
    status: str = FS_PENDING
    required: bool = True
    capability_ids: List[str] = field(default_factory=list)
    artifact_ref: str = ""
    layer_ids: List[str] = field(default_factory=list)
    component_ids: List[str] = field(default_factory=list)
    # 供给依赖（facet_id 列表，有界）：map_layer ← 产出它的 analysis 行；
    # chart/statistics ← 表/聚合类 analysis 行（完整 artifact 级血缘在
    # product_lineage —— 这里只投影章节内可实证的供给边）。
    dependencies: List[str] = field(default_factory=list)
    # bbox 全部来自既有 ref descriptor / MapSpec source bounds 元数据 ——
    # 不可知即 None（绝不虚构，P9 §15）。
    bbox: Optional[List[float]] = None
    # 渲染证据（仅当 observation 匹配 revision 时非空：verified/issues；
    # 无证据 → "" 不虚构）。
    render_status: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facet_id": self.facet_id,
            "kind": self.kind,
            "key": self.key[:64],
            "status": self.status,
            "required": self.required,
            "capability_ids": list(self.capability_ids[:4]),
            "artifact_ref": self.artifact_ref[:64],
            "layer_ids": list(self.layer_ids[:4]),
            "component_ids": list(self.component_ids[:4]),
            "dependencies": list(self.dependencies[:4]),
            "bbox": self.bbox,
            "render_status": self.render_status,
        }


def _descriptor_bbox(descriptors: Optional[Dict[str, Any]], ref: str) -> Optional[List[float]]:
    """ref descriptor 的合法 bbox（4 元、经纬有序），否则 None。"""
    if not ref or not descriptors:
        return None
    desc = descriptors.get(ref)
    bbox = desc.get("bbox") if isinstance(desc, dict) else None
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        return None
    try:
        w, s, e, n = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    except (TypeError, ValueError):
        return None
    return [w, s, e, n] if (w <= e and s <= n) else None


def _spec_source_ref(mapspec: Optional[Dict[str, Any]], source_id: str) -> str:
    """spec source 的 ref 指针（bbox 描述符检索键）。"""
    sources = (mapspec or {}).get("sources")
    src = None
    if isinstance(sources, dict):
        src = sources.get(source_id)
    elif isinstance(sources, list):
        src = next(
            (s for s in sources if isinstance(s, dict) and str(s.get("id") or "") == source_id),
            None,
        )
    if not isinstance(src, dict):
        return ""
    for key in ("ref", "ref_id"):
        val = src.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _spec_source_bounds(mapspec: Optional[Dict[str, Any]], source_id: str) -> Optional[List[float]]:
    """spec source 自带 bounds（栅格/影像源），否则 None。"""
    sources = (mapspec or {}).get("sources")
    src = None
    if isinstance(sources, dict):
        src = sources.get(source_id)
    elif isinstance(sources, list):
        src = next(
            (s for s in sources if isinstance(s, dict) and str(s.get("id") or "") == source_id),
            None,
        )
    if not isinstance(src, dict):
        return None
    bounds = src.get("bounds") or src.get("bbox")
    if not (isinstance(bounds, (list, tuple)) and len(bounds) == 4):
        return None
    try:
        w, s, e, n = (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
    except (TypeError, ValueError):
        return None
    return [w, s, e, n] if (w <= e and s <= n) else None


def build_facet_completion(
    chapter: Optional[Dict[str, Any]],
    mapspec: Optional[Dict[str, Any]] = None,
    *,
    descriptors: Optional[Dict[str, Any]] = None,
    observation: Optional[Dict[str, Any]] = None,
    current_revision: int = 0,
    contract: Optional["ProductFacetContract"] = None,
) -> List[ProductFacetCompletion]:
    """章节 + MapSpec + 既有证据 → per-facet 完成度（纯函数，零 IO）。

    状态全部回读既有事实（行状态 / 图层在场启用 / 组件 enabled / 渲染
    观察）；render 证据只在 observation 匹配当前 revision 时参与 ——
    否则留空（不虚构 verified，也不把 unknown 判成失败）。facet 必需性
    （``required``）由 ProductFacetContract 决定（缺省派生，容错空契约）。
    """
    if not isinstance(chapter, dict):
        return []
    if contract is None:
        contract = _safe_contract(chapter)
    graph = build_product_graph(chapter, mapspec, contract=contract)
    spec_layers = {
        str(ly.get("id") or ""): ly
        for ly in ((mapspec or {}).get("layers") or [])
        if isinstance(ly, dict)
    }
    layer_rows = {
        str(ly.get("layer_id") or ""): ly
        for ly in (chapter.get("map_layers") or [])
        if isinstance(ly, dict) and ly.get("layer_id")
    }

    # 渲染证据：revision 匹配才有效（P9 §9 防护在投影层同样生效）。
    render_matched = False
    observed_by_id: Dict[str, Dict[str, Any]] = {}
    if isinstance(observation, dict) and observation.get("source") == "frontend_runtime":
        try:
            from app.services.gis_harness.render_observation import observation_revision

            render_matched = (
                observation_revision(observation) is not None
                and observation_revision(observation) == int(current_revision or 0)
            )
        except (TypeError, ValueError):
            render_matched = False
        if render_matched:
            for entry in observation.get("layers") or []:
                if not isinstance(entry, dict):
                    continue
                for key in ("id", "runtime_store_id"):
                    val = str(entry.get(key) or "")
                    if val and val not in observed_by_id:
                        observed_by_id[val] = entry

    facets: List[ProductFacetCompletion] = []
    # 表/聚合类能力行（chart/statistics 的供给上游 —— 依赖边用；词表
    # 单源于 capability registry 的 output_artifact_types，容错降级空）。
    table_caps = _analysis_rows_with_outputs(chapter)
    for node in graph.nodes:
        status = _NODE_TO_FACET_STATUS.get(node.status, FS_PENDING)
        facet = ProductFacetCompletion(
            facet_id=node.node_id,
            kind=node.kind,
            key=node.key,
            label=node.label[:64],
            status=status,
            required=True,
            artifact_ref=node.artifact_ref,
        )
        if node.kind == KIND_ANALYSIS:
            facet.capability_ids = [node.key]
            facet.bbox = _descriptor_bbox(descriptors, node.artifact_ref)
        elif node.kind == KIND_MAP_LAYER:
            row = layer_rows.get(node.key) or {}
            cap = str(row.get("source_capability") or "")
            facet.capability_ids = [cap] if cap else []
            facet.layer_ids = [node.key]
            if cap:
                facet.dependencies = [f"{KIND_ANALYSIS}:{cap}"]
            layer = spec_layers.get(node.key)
            if layer is not None:
                src_ref = _spec_source_ref(mapspec, str(layer.get("source") or ""))
                facet.bbox = (
                    _descriptor_bbox(descriptors, src_ref)
                    or _spec_source_bounds(mapspec, str(layer.get("source") or ""))
                )
            if render_matched and status == FS_COMPLETE:
                entry = observed_by_id.get(node.key)
                try:
                    runtime_count = int(entry.get("runtime_layer_count") or 0) if entry else 0
                except (TypeError, ValueError):
                    runtime_count = 0
                if runtime_count <= 0:
                    facet.render_status = "issues"
                    facet.status = FS_NEEDS_REPAIR
                else:
                    facet.render_status = "verified"
        elif node.kind in (KIND_STATISTICS, KIND_CHART):
            facet.component_ids = [node.key]
            # 供给依赖：表/聚合类 analysis facet（chart/statistics 可从既有
            # 统计产物重生成 —— 「chart 欠账 ≠ 重查数据」的产品图表达；
            # annotation 是文本注记，不从统计表派生 —— 不挂该边）。
            facet.dependencies = [
                f"{KIND_ANALYSIS}:{cap}" for cap in table_caps[:4]
            ]
            # v2（Scenario F）：chart facet 的 chartRef 在 ref descriptor
            # 里查无证据（artifact 缺失/过期被逐出）→ needs_repair —— 只
            # 该 chart 面板欠修，不把整图判死。descriptors 缺席（无证据
            # 输入）→ 不虚构，维持 enabled 投影状态。
            if (
                node.kind == KIND_CHART
                and node.artifact_ref
                and descriptors
                and node.artifact_ref not in descriptors
            ):
                facet.status = FS_NEEDS_REPAIR
                facet.render_status = "issues"
            if node.kind == KIND_CHART:
                facet.required = contract.chart_required
        elif node.kind == KIND_ANNOTATION:
            facet.component_ids = [node.key]
            # v2（Scenario F）：chart facet 的 chartRef 在 ref descriptor
            # 里查无证据（artifact 缺失/过期被逐出）→ needs_repair —— 只
            # 该 chart 面板欠修，不把整图判死。descriptors 缺席（无证据
            # 输入）→ 不虚构，维持 enabled 投影状态。
            if (
                node.kind == KIND_CHART
                and node.artifact_ref
                and descriptors
                and node.artifact_ref not in descriptors
            ):
                facet.status = FS_NEEDS_REPAIR
                facet.render_status = "issues"
            if node.kind == KIND_CHART:
                facet.required = contract.chart_required
        elif node.kind in (KIND_LEGEND, KIND_INSET):
            # 必需性由契约决定：组合模板 required 槽位（density_map 的
            # colorbar）→ required；缺省 informational —— 在场即构成，
            # 缺席不欠（conditional 槽位语义）。
            facet.required = contract.legend_required if node.kind == KIND_LEGEND else False
            facet.component_ids = [node.key]
            facet.layer_ids = [node.metadata["layer_id"]] if node.metadata.get("layer_id") else []
        elif node.kind == KIND_EXPORT:
            facet.required = False  # 信息性 facet（导出动作不由计划真相追踪）
        facets.append(facet)

    return facets


def _analysis_rows_with_outputs(chapter: Dict[str, Any]) -> List[str]:
    """产出表/聚合类 artifact 的能力行 capability 列表（依赖边用，有界）。

    词表单源于 capability registry 的 ``output_artifact_types`` ∩
    ``CHART_INPUT_ARTIFACT_TYPES``；registry 不可用/行缺能力 → 空列表
    （依赖边缺席是诚实降级，不影响状态投影）。
    """
    caps: List[str] = []
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        registry = get_capability_registry()
    except Exception:  # noqa: BLE001 — 依赖边是增值投影，降级不中断
        return caps
    seen: set = set()
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        cap = str(row.get("capability") or "")
        if not cap or cap in seen:
            continue
        seen.add(cap)
        desc = registry.get(cap)
        outputs = set(getattr(desc, "output_artifact_types", None) or []) if desc else set()
        if outputs & CHART_INPUT_ARTIFACT_TYPES:
            caps.append(cap)
    return caps[:8]


def facet_owed_line(facets: List[ProductFacetCompletion]) -> str:
    """下一动作建议的简短依据（bounded；不选择 tool —— capability 层语义）。"""
    owed = [f for f in facets if f.status in (FS_PENDING, FS_FAILED, FS_NEEDS_REPAIR) and f.required]
    if not owed:
        return ""
    first = owed[0]
    return f"{first.kind}:{first.key}"[:64]
