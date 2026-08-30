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
}


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
        """产品 facets：地图层 + 统计/图表/注记（分析是供给，不算 facet）。"""
        return [
            n
            for n in self.nodes
            if n.kind in (KIND_MAP_LAYER, KIND_STATISTICS, KIND_CHART, KIND_ANNOTATION)
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
) -> ProductGraph:
    """章节 + MapSpec → 产品图（纯函数；任一输入缺失按空处理）。"""
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

    # 组件 facets：statistics / chart / annotation（状态 = enabled 投影）
    chart_seen = False
    for comp in ((mapspec or {}).get("layout") or {}).get("components") or []:
        if not isinstance(comp, dict):
            continue
        ctype = str(comp.get("type") or "")
        kind = _STAT_KINDS.get(ctype)
        if kind is None:
            continue
        if kind == KIND_CHART and comp.get("enabled") is not False:
            chart_seen = True
        cid = str(comp.get("id") or ctype)
        chart_ref = (comp.get("options") or {}).get("chartRef")
        graph.nodes.append(
            ProductNode(
                node_id=f"{kind}:{cid}",
                kind=kind,
                key=cid,
                label=cid,
                status=S_DONE if comp.get("enabled") is not False else S_OFF,
                artifact_ref=str(chart_ref) if isinstance(chart_ref, str) else "",
            )
        )

    # chart 必需信号（P9 Scenario M）：模板 export_profile.chart=True 而无
    # enabled chart 组件 → 合成 pending 节点 —— 产品图反映"应然构成"，不
    # 只是"恰好存在的组件"（派生事实仍是章节的 export_profile + MapSpec）。
    export_profile_early = (chapter.get("template_selection") or {}).get("export_profile")
    if (
        isinstance(export_profile_early, dict)
        and export_profile_early.get("chart")
        and not chart_seen
    ):
        graph.nodes.append(
            ProductNode(
                node_id=f"{KIND_CHART}:required",
                kind=KIND_CHART,
                key="chart-required",
                label="chart-required",
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
) -> List[ProductFacetCompletion]:
    """章节 + MapSpec + 既有证据 → per-facet 完成度（纯函数，零 IO）。

    状态全部回读既有事实（行状态 / 图层在场启用 / 组件 enabled / 渲染
    观察）；render 证据只在 observation 匹配当前 revision 时参与 ——
    否则留空（不虚构 verified，也不把 unknown 判成失败）。
    """
    if not isinstance(chapter, dict):
        return []
    graph = build_product_graph(chapter, mapspec)
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
        elif node.kind in (KIND_STATISTICS, KIND_CHART, KIND_ANNOTATION):
            facet.component_ids = [node.key]
        elif node.kind == KIND_EXPORT:
            facet.required = False  # 信息性 facet（导出动作不由计划真相追踪）
        facets.append(facet)

    return facets


def facet_owed_line(facets: List[ProductFacetCompletion]) -> str:
    """下一动作建议的简短依据（bounded；不选择 tool —— capability 层语义）。"""
    owed = [f for f in facets if f.status in (FS_PENDING, FS_FAILED, FS_NEEDS_REPAIR) and f.required]
    if not owed:
        return ""
    first = owed[0]
    return f"{first.kind}:{first.key}"[:64]
