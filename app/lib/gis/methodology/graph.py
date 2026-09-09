"""Methodology Knowledge Graph —— canonical registries 的只读类型化投影
（Epic 11 §5.B）。

把 taxonomy / methodology / capability / algorithm / artifact / map model /
component 用 typed relations 连成一张可校验、可指纹、可 diff 的图：

    task --requires_data_role--> role
    task --serves_category----> category
    category --serves_family--> family（透镜引用，反向 task 侧展开）
    family --supports_method-> method
    method --alternative_method-> method
    method --produces_artifact-> artifact_type
    method --requires_capability-> capability
    method --requires_algorithm-> algorithm
    method --recommended_visualization-> map_model
    method --requires_component-> component（taxonomy/ontology 期望投影）
    artifact_type --typical_visualization-> map_model（收编文档性字段）
    capability --typical_visualization-> map_model
    component --compatible_artifact-> artifact_type
    category --forbids_method----> method（invalid 知识）
    category --alternative_method-> method

红线（架构 01-architecture §0/§3.3）：

- **graph 不是第二 registry**：不存储任何 payload 复制，节点/边只持
  ``kind:id`` 引用；全部事实从 canonical registries + taxonomy/descriptor
  审定表**确定性投影**（同 workflow_families 投影先例）；
- 悬空引用 = build 失败（fail-closed），不产半截图；
- registry 变更 → 指纹变化 → 缓存失效重建；structural diff 只对边集；
- 有界：节点 O(注册表规模)，边 O(节点×关系)，单例缓存容量 1；
- 零 LLM、零 I/O。

LLM/agent 不直接消费图结构——经 ``service.py`` 门面出 bounded 投影。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

#: graph schema 版本（进指纹；边词表变更必须提升）。
GRAPH_SCHEMA_VERSION = 1

#: 边关系词表（封闭；冻结于架构 §3.3）。
EDGE_RELATIONS = (
    "requires_data_role",
    "serves_category",
    "supports_method",
    "alternative_method",
    "produces_artifact",
    "requires_capability",
    "requires_algorithm",
    "recommended_visualization",
    "requires_component",
    "typical_visualization",
    "compatible_artifact",
    "forbids_method",
    "consumes_artifact",
)

#: 节点 kind 词表（封闭）。
NODE_KINDS = (
    "category",
    "task",
    "family",
    "method",
    "capability",
    "algorithm",
    "artifact_type",
    "map_model",
    "component",
    "data_role",
)

#: build 输入包（注入；lib 不顶层 import services —— R1-F8）。
class GraphSources(BaseModel):
    """build_graph 的注册表注入包（全部 canonical 只读引用）。

    ``taxonomy``/``descriptors`` 是本包审定表；其余为 canonical 单例。
    调用方（service/registry_validation）在函数体内延迟获取。
    """
    taxonomy: Any
    descriptors: Any
    ontology: Any
    methodology: Any
    capabilities: Any
    algorithms: Any
    artifacts: Any
    map_models: Any
    components: Any

    model_config = ConfigDict(arbitrary_types_allowed=True)


class GraphNode(BaseModel):
    """图节点（kind:id 引用；不复制 payload）。"""
    kind: str
    id: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"


class GraphEdge(BaseModel):
    """一条类型化有向边（src/dst 为 node key）。"""
    src: str
    dst: str
    relation: str

    def to_bounded_dict(self) -> Dict[str, str]:
        return {"src": self.src[:96], "dst": self.dst[:96],
                "relation": self.relation[:32]}


class GraphDiff(BaseModel):
    """结构 diff：边集变化（新增/移除）。"""
    added: List[str] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.added and not self.removed

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "added": [a[:96] for a in self.added[:24]],
            "removed": [r[:96] for r in self.removed[:24]],
            "added_total": len(self.added),
            "removed_total": len(self.removed),
        }


class MethodologyGraph(BaseModel):
    """方法知识图（不可变投影产物）。"""

    nodes: Tuple[GraphNode, ...] = ()
    edges: Tuple[GraphEdge, ...] = ()
    fingerprint: str = ""
    source_fingerprints: Dict[str, str] = Field(default_factory=dict)
    schema_version: int = GRAPH_SCHEMA_VERSION

    # ── 查询 ─────────────────────────────────────────────────────────
    def neighbors(
        self, node_key: str, *, relation: str = "",
        direction: str = "out",
    ) -> List[str]:
        """邻接查询（线性扫描边表；图规模 ≤ ~3k 边，单查 ≤5ms 预算内）。"""
        out: List[str] = []
        for e in self.edges:
            if relation and e.relation != relation:
                continue
            if direction == "out" and e.src == node_key:
                out.append(e.dst)
            elif direction == "in" and e.dst == node_key:
                out.append(e.src)
        return sorted(set(out))

    def edges_of(self, *, relation: str = "") -> List[GraphEdge]:
        if not relation:
            return list(self.edges)
        return [e for e in self.edges if e.relation == relation]

    def has_node(self, key: str) -> bool:
        return any(n.key == key for n in self.nodes)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    # ── diff ─────────────────────────────────────────────────────────
    def diff(self, other: "MethodologyGraph") -> GraphDiff:
        mine = {(e.src, e.dst, e.relation) for e in self.edges}
        theirs = {(e.src, e.dst, e.relation) for e in other.edges}
        return GraphDiff(
            added=sorted(f"{s}-[{r}]->{d}" for s, d, r in theirs - mine),
            removed=sorted(f"{s}-[{r}]->{d}" for s, d, r in mine - theirs),
        )

    # ── 有界投影（LLM/agent 面；不出图结构本体）────────────────────
    def to_bounded_dict(self) -> Dict[str, Any]:
        rel_counts: Dict[str, int] = {}
        for e in self.edges:
            rel_counts[e.relation] = rel_counts.get(e.relation, 0) + 1
        return {
            "schema_version": self.schema_version,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "relation_counts": dict(sorted(rel_counts.items())[:14]),
            "fingerprint": self.fingerprint[:64],
        }


class GraphBuildError(RuntimeError):
    """图构建失败（悬空引用等完整性违规；fail-closed）。"""


def build_graph(sources: GraphSources) -> MethodologyGraph:
    """从 canonical registries + 审定表确定性投影知识图。

    先建节点引用集，再产边；每条边在落图前校验两端节点在引用集内
    —— 悬空即抛 ``GraphBuildError``（不产半截图）。
    """
    nodes: Dict[str, GraphNode] = {}
    raw_edges: List[GraphEdge] = []

    def _node(kind: str, id_: str) -> str:
        key = f"{kind}:{id_}"
        if key not in nodes:
            nodes[key] = GraphNode(kind=kind, id=id_)
        return key

    def _edge(src_key: str, dst_key: str, relation: str) -> None:
        if relation not in EDGE_RELATIONS:
            raise GraphBuildError(f"unknown edge relation: {relation}")
        raw_edges.append(GraphEdge(src=src_key, dst=dst_key, relation=relation))

    tax = sources.taxonomy
    methods_reg = sources.methodology
    ontology = sources.ontology
    artifacts = sources.artifacts
    models = sources.map_models

    def _model_exists(mid: str) -> bool:
        return models.resolve(mid) is not None

    # ── 类别 / 任务 / 族 / 方法（taxonomy + ontology + methodology）──
    for cid in tax.all_ids:
        cat = tax.get(cid)
        assert cat is not None
        cat_key = _node("category", cid)
        for fid in cat.methodology_family_ids:
            fam = methods_reg.family(fid)
            if fam is None:
                raise GraphBuildError(f"taxonomy[{cid}]: family {fid} 悬空")
            fam_key = _node("family", fid)
            _edge(cat_key, fam_key, "serves_category")
            for tid in fam.ontology_task_ids:
                task = ontology.get(tid)
                if task is None:
                    raise GraphBuildError(f"family[{fid}]: task {tid} 悬空")
                task_key = _node("task", tid)
                _edge(task_key, fam_key, "serves_category")
                for role in task.required_data_roles:
                    _edge(task_key, _node("data_role", role),
                          "requires_data_role")
                for mid in cat.invalid_method_ids:
                    if methods_reg.method(mid) is None:
                        raise GraphBuildError(
                            f"taxonomy[{cid}]: invalid method {mid} 悬空")
                    _edge(cat_key, _node("method", mid), "forbids_method")
        for tid in cat.ontology_task_ids:
            task = ontology.get(tid)
            if task is None:
                raise GraphBuildError(f"taxonomy[{cid}]: task {tid} 悬空")
            task_key = _node("task", tid)
            _edge(task_key, cat_key, "serves_category")
            for comp in task.component_expectations:
                if sources.components.get_by_type(comp) is None and \
                        not sources.components.has(comp):
                    raise GraphBuildError(
                        f"task[{tid}]: component expectation {comp} 未注册")
                _edge(task_key, _node("component", comp), "requires_component")
        for mid in cat.alternative_method_ids:
            if methods_reg.method(mid) is None:
                raise GraphBuildError(
                    f"taxonomy[{cid}]: alternative method {mid} 悬空")
            _edge(cat_key, _node("method", mid), "alternative_method")
        for mm in cat.recommended_visualizations:
            if not _model_exists(mm):
                raise GraphBuildError(
                    f"taxonomy[{cid}]: map model {mm} 悬空")
            _edge(cat_key, _node("map_model", mm), "recommended_visualization")

    # ── 方法（V4 候选表全量投影）────────────────────────────────────
    for fam in methods_reg.families():
        fam_key = _node("family", fam.family_id)
        for m in fam.candidate_methods:
            m_key = _node("method", m.method_id)
            _edge(fam_key, m_key, "supports_method")
            for cap in m.capabilities:
                if not sources.capabilities.has(cap):
                    raise GraphBuildError(
                        f"method[{m.method_id}]: capability {cap} 悬空")
                _edge(m_key, _node("capability", cap), "requires_capability")
            for aid in m.algorithm_ids:
                if not sources.algorithms.has(aid):
                    raise GraphBuildError(
                        f"method[{m.method_id}]: algorithm {aid} 悬空")
                _edge(m_key, _node("algorithm", aid), "requires_algorithm")
            for at in m.output_artifacts:
                if not artifacts.has(at):
                    raise GraphBuildError(
                        f"method[{m.method_id}]: artifact {at} 悬空")
                _edge(m_key, _node("artifact_type", at), "produces_artifact")

    # ── 方法增强层（alternatives + viz guidance）────────────────────
    desc_reg = sources.descriptors
    for mid in desc_reg.all_ids:
        d = desc_reg.get(mid)
        assert d is not None
        if methods_reg.method(mid) is None:
            raise GraphBuildError(f"descriptor[{mid}]: method 悬空")
        m_key = f"method:{mid}"
        for alt in d.alternatives:
            if methods_reg.method(alt) is None:
                raise GraphBuildError(
                    f"descriptor[{mid}]: alternative {alt} 悬空")
            _edge(m_key, _node("method", alt), "alternative_method")

    # ── artifact/capability → map model（收编文档性字段为图边）──────
    for at in artifacts.all_ids:
        desc = artifacts.get(at)
        assert desc is not None
        at_key = _node("artifact_type", at)
        for mm in desc.typical_map_models:
            if not _model_exists(mm):
                raise GraphBuildError(
                    f"artifact[{at}]: typical map model {mm} 悬空")
            _edge(at_key, _node("map_model", mm), "typical_visualization")
    for cap_id in sources.capabilities.all_ids:
        cap = sources.capabilities.get(cap_id)
        assert cap is not None
        cap_key = _node("capability", cap_id)
        for mm in cap.compatible_map_models:
            if not _model_exists(mm):
                raise GraphBuildError(
                    f"capability[{cap_id}]: map model {mm} 悬空")
            _edge(cap_key, _node("map_model", mm), "typical_visualization")
        for at in cap.output_artifact_types:
            if not artifacts.has(at):
                raise GraphBuildError(
                    f"capability[{cap_id}]: output artifact {at} 悬空")
            _edge(cap_key, _node("artifact_type", at), "produces_artifact")

    # ── component → artifact（组件目录兼容声明）─────────────────────
    for comp_id in sources.components.all_ids:
        comp = sources.components.get(comp_id)
        assert comp is not None
        comp_key = _node("component", comp_id)
        for at in comp.compatible_artifact_types:
            if not artifacts.has(at):
                raise GraphBuildError(
                    f"component[{comp_id}]: artifact {at} 悬空")
            _edge(comp_key, _node("artifact_type", at), "compatible_artifact")

    # ── 去重 + 完整性终检 + 指纹 ────────────────────────────────────
    seen: Set[Tuple[str, str, str]] = set()
    edges: List[GraphEdge] = []
    for e in raw_edges:
        if e.src not in nodes or e.dst not in nodes:
            raise GraphBuildError(
                f"dangling edge {e.src}-[{e.relation}]->{e.dst}")
        k = (e.src, e.dst, e.relation)
        if k in seen:
            continue
        seen.add(k)
        edges.append(e)
    edges.sort(key=lambda e: (e.relation, e.src, e.dst))
    ordered_nodes = tuple(sorted(nodes.values(), key=lambda n: n.key))

    source_fps = {
        "taxonomy": _fp(tax),
        "descriptors": _fp(desc_reg),
        "ontology": _fp(ontology),
        "methodology": _fp(methods_reg),
    }
    payload = {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "nodes": [n.key for n in ordered_nodes],
        "edges": [[e.src, e.relation, e.dst] for e in edges],
        "sources": source_fps,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return MethodologyGraph(
        nodes=ordered_nodes, edges=tuple(edges),
        fingerprint=fingerprint, source_fingerprints=source_fps,
    )


#: 进程内单例缓存（容量 1；key = 源指纹拼接；R1-F8 延迟获取注册表）。
_cache: Optional[Tuple[str, MethodologyGraph]] = None


def _fp(obj: Any) -> str:
    """registry 指纹统一读取（property 与 method 两种既有形态并存）。"""
    fp = getattr(obj, "fingerprint")
    return fp() if callable(fp) else str(fp)


def get_knowledge_graph(
    sources: Optional[GraphSources] = None, *,
    refresh: bool = False,
) -> MethodologyGraph:
    """图单例（fingerprint-keyed 缓存；sources 缺省延迟对接 canonical）。"""
    global _cache
    if sources is None:
        sources = _default_sources()
    key = json.dumps({
        "taxonomy": _fp(sources.taxonomy),
        "descriptors": _fp(sources.descriptors),
        "ontology": _fp(sources.ontology),
        "methodology": _fp(sources.methodology),
    }, sort_keys=True)
    if not refresh and _cache is not None and _cache[0] == key:
        return _cache[1]
    graph = build_graph(sources)
    _cache = (key, graph)
    return graph


def reset_knowledge_graph() -> None:
    global _cache
    _cache = None


def _default_sources() -> GraphSources:
    """canonical 单例延迟对接（函数体内 import，R1-F8）。"""
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.model_library import get_map_model_registry
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.artifacts import get_artifact_type_registry
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.methodology.descriptors import (
        get_method_descriptor_registry,
    )
    from app.lib.gis.methodology.taxonomy import get_task_taxonomy
    from app.services.gis_harness.gis_ontology import get_task_ontology
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )
    return GraphSources(
        taxonomy=get_task_taxonomy(),
        descriptors=get_method_descriptor_registry(),
        ontology=get_task_ontology(),
        methodology=get_methodology_registry(),
        capabilities=get_capability_registry(),
        algorithms=get_algorithm_registry(),
        artifacts=get_artifact_type_registry(),
        map_models=get_map_model_registry(),
        components=get_component_registry(),
    )


__all__ = [
    "GRAPH_SCHEMA_VERSION",
    "EDGE_RELATIONS",
    "NODE_KINDS",
    "GraphSources",
    "GraphNode",
    "GraphEdge",
    "GraphDiff",
    "MethodologyGraph",
    "GraphBuildError",
    "build_graph",
    "get_knowledge_graph",
    "reset_knowledge_graph",
]
