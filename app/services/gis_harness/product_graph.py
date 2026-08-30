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
        owed = sum(
            1
            for n in self.nodes
            if n.status in (S_PENDING, S_FAILED)
        )
        tail = f" — {owed} owed" if owed else ""
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
    for comp in ((mapspec or {}).get("layout") or {}).get("components") or []:
        if not isinstance(comp, dict):
            continue
        ctype = str(comp.get("type") or "")
        kind = _STAT_KINDS.get(ctype)
        if kind is None:
            continue
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
