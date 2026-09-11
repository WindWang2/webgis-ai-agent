"""Unified Capability Graph —— Harness V8 统一能力投影（ADR-0136）。

V7 基线（capability_descriptors.py）已把 capability/algorithm/template/
component 投影为统一描述符并驱动结构化检索；V8 把同一「只读派生投影」
纪律扩展为**带关系的图**，并把 Model / Workflow / ExecutionBackend /
Provider 纳入一等节点 —— Agent 规划第一次拥有单一的
「我现在有哪些能力（以及它们之间怎么连接）」视图。

契约（不建第 N+1 个事实源）：

- 图**不复制 registry 内容**：节点只存 identity（id/kind/source_registry）
  + 检索所需的有界摘要；字段按需从 source registry 读。
- 关系词表封闭（GraphEdge.relation，GRAPH_RELATIONS）。
- 缓存纪律（§28）：source registry fingerprint → graph fingerprint →
  immutable cached projection。同指纹零重建（结构测试钉死）。
- 检索有界确定性：build 上限 + 排序稳定（by kind, id）。
- 机器闸：validate_graph() 输出 dangling/duplicate/contradiction 报告
  （接入 registry_validation 与 preflight 轨道）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def v8_capability_graph_enabled() -> bool:
    """kill switch（默认开；=0 回退 V7 行为）。"""
    return os.getenv("GIS_CAPABILITY_GRAPH_V8", "1") not in ("0", "false", "False")


# ── kind / relation 封闭词表 ────────────────────────────────────────────

KIND_CAPABILITY = "capability"
KIND_ALGORITHM = "algorithm"
KIND_TOOL = "tool"
KIND_MODEL = "model"
KIND_WORKFLOW = "workflow"
KIND_METHODOLOGY = "methodology"
KIND_TEMPLATE = "template"
KIND_COMPONENT = "component"
KIND_ARTIFACT_TYPE = "artifact_type"
KIND_EXECUTION_BACKEND = "execution_backend"
KIND_PROVIDER = "provider"

GRAPH_KINDS: FrozenSet[str] = frozenset({
    KIND_CAPABILITY, KIND_ALGORITHM, KIND_TOOL, KIND_MODEL, KIND_WORKFLOW,
    KIND_METHODOLOGY, KIND_TEMPLATE, KIND_COMPONENT, KIND_ARTIFACT_TYPE,
    KIND_EXECUTION_BACKEND, KIND_PROVIDER,
})

REL_IMPLEMENTED_BY = "implemented_by"      # capability → algorithm
REL_EXPOSED_BY = "exposed_by"              # algorithm → tool
REL_ACCEPTS = "accepts"                    # algorithm → artifact_type
REL_PRODUCES = "produces"                  # algorithm → artifact_type
REL_INVOKES = "invokes"                    # tool → provider
REL_IMPLEMENTS = "implements"              # model/tool → capability
REL_REQUIRES = "requires"                  # workflow/template → capability
REL_RUNS_ON = "runs_on"                    # model → execution_backend
REL_CONTAINS = "contains"                  # workflow → algorithm/tool
REL_COMPOSED_OF = "composed_of"            # template → component
REL_BINDS_TO = "binds_to"                  # component → artifact_type
REL_FALLBACK_TO = "fallback_to"            # capability → capability
REL_EXECUTED_BY = "executed_by"            # algorithm → execution_backend

GRAPH_RELATIONS: FrozenSet[str] = frozenset({
    REL_IMPLEMENTED_BY, REL_EXPOSED_BY, REL_ACCEPTS, REL_PRODUCES,
    REL_INVOKES, REL_IMPLEMENTS, REL_REQUIRES, REL_RUNS_ON, REL_CONTAINS,
    REL_COMPOSED_OF, REL_BINDS_TO, REL_FALLBACK_TO, REL_EXECUTED_BY,
})

#: 构建有界预算（§28：bounded）。超过即截断并在 issues 披露（不静默）。
MAX_NODES = 4096
MAX_EDGES = 20000
MAX_INDEX_CAPS = 512
MAX_INDEX_ALGOS = 640
MAX_INDEX_TOOLS = 640
MAX_INDEX_MODELS = 256
MAX_INDEX_ARTIFACTS = 128


class GraphNode:
    """图节点：identity + 有界检索摘要（零业务语义复制）。"""

    __slots__ = ("id", "kind", "source_registry", "label", "corpus", "extras")

    def __init__(
        self,
        id: str,
        kind: str,
        source_registry: str,
        label: str = "",
        corpus: str = "",
        extras: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.id = str(id)[:128]
        self.kind = kind
        self.source_registry = source_registry
        self.label = str(label)[:96]
        self.corpus = str(corpus)[:400].lower()
        self.extras = extras or {}

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id, "kind": self.kind,
            "source_registry": self.source_registry, "label": self.label,
        }
        if self.extras:
            out["extras"] = dict(list(self.extras.items())[:8])
        return out


class GraphEdge:
    """图边：封闭关系词表 + 双端节点键（kind:id）。"""

    __slots__ = ("src", "relation", "dst")

    def __init__(self, src: str, relation: str, dst: str) -> None:
        self.src = src
        self.relation = relation
        self.dst = dst

    def to_dict(self) -> Dict[str, str]:
        return {"src": self.src, "relation": self.relation, "dst": self.dst}


class GraphIssue:
    """validate_graph 的机器可读发现。"""

    def __init__(self, code: str, detail: str, *, severity: str = "warning") -> None:
        self.code = code
        self.detail = detail
        self.severity = severity

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


class CapabilityGraph:
    """不可变投影（构建后只读；重建 = 重新 build）。"""

    def __init__(
        self,
        nodes: Dict[str, GraphNode],
        edges: List[GraphEdge],
        source_fingerprint: str,
        issues: List[GraphIssue],
    ) -> None:
        self._nodes = nodes
        self._edges = edges
        self.source_fingerprint = source_fingerprint
        self.build_issues = issues
        self._out: Dict[str, List[Tuple[str, str]]] = {}
        self._in: Dict[str, List[Tuple[str, str]]] = {}
        for e in edges:
            self._out.setdefault(e.src, []).append((e.relation, e.dst))
            self._in.setdefault(e.dst, []).append((e.relation, e.src))

    # ── 查询 API（有界确定性）─────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def node(self, key: str) -> Optional[GraphNode]:
        return self._nodes.get(key)

    def has(self, kind: str, id: str) -> bool:
        return f"{kind}:{id}" in self._nodes

    def nodes_by_kind(self, kind: str) -> List[GraphNode]:
        prefix = f"{kind}:"
        return [n for k, n in sorted(self._nodes.items()) if k.startswith(prefix)]

    def neighbors(self, key: str, relation: Optional[str] = None) -> List[str]:
        entries = self._out.get(key, [])
        if relation is None:
            return [d for _, d in entries]
        return [d for r, d in entries if r == relation]

    def reverse_neighbors(self, key: str, relation: Optional[str] = None) -> List[str]:
        entries = self._in.get(key, [])
        if relation is None:
            return [s for _, s in entries]
        return [s for r, s in entries if r == relation]

    # ── V8.2 核心查询 ────────────────────────────────────────────────

    def models_for_capability(self, capability_id: str) -> List[GraphNode]:
        """capability → 候选 model（implements 入边，确定性按 id 排序）。"""
        keys = self.reverse_neighbors(
            f"{KIND_CAPABILITY}:{capability_id}", REL_IMPLEMENTS)
        nodes = [self._nodes[k] for k in keys if k in self._nodes]
        return sorted(nodes, key=lambda n: n.id)

    def tools_for_capability(self, capability_id: str) -> List[str]:
        """capability →（algorithm implemented_by → exposed_by）去重工具面。"""
        tools: List[str] = []
        for algo_key in self.neighbors(
                f"{KIND_CAPABILITY}:{capability_id}", REL_IMPLEMENTED_BY):
            for tool_key in self.neighbors(algo_key, REL_EXPOSED_BY):
                node = self._nodes.get(tool_key)
                if node is not None and node.id not in tools:
                    tools.append(node.id)
        # 直接 implements 的工具（modelops 工具标签）也计入
        for tool_key in self.reverse_neighbors(
                f"{KIND_CAPABILITY}:{capability_id}", REL_IMPLEMENTS):
            node = self._nodes.get(tool_key)
            if node is not None and node.kind == KIND_TOOL and node.id not in tools:
                tools.append(node.id)
        return sorted(tools)

    def fallback_chain(self, kind: str, id: str) -> List[str]:
        """节点声明序 fallback 链（有界）。"""
        return [k.split(":", 1)[1] for k in self.neighbors(
            f"{kind}:{id}", REL_FALLBACK_TO)][:4]

    def fingerprint(self) -> str:
        payload = {
            "nodes": sorted(
                (k, n.source_registry, n.label) for k, n in self._nodes.items()),
            "edges": sorted((e.src, e.relation, e.dst) for e in self._edges),
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_fingerprint": self.source_fingerprint,
            "graph_fingerprint": self.fingerprint(),
            "nodes": [n.to_dict() for _, n in sorted(self._nodes.items())],
            "edges": [e.to_dict() for e in self._edges],
        }


# ── source registry 指纹（图缓存键）────────────────────────────────────


def _fingerprint_of(items: Iterable[str]) -> str:
    blob = json.dumps(sorted(items), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def source_fingerprints() -> Dict[str, str]:
    """各 source registry 的内容指纹；缺席段记 'absent'（诚实披露）。"""
    fps: Dict[str, str] = {}

    def _fp(name: str, ids: Optional[Iterable[str]]) -> None:
        try:
            fps[name] = _fingerprint_of(ids) if ids is not None else "absent"
        except Exception:  # noqa: BLE001
            fps[name] = "absent"

    try:
        from app.lib.gis.capability_registry import get_capability_registry
        _fp("capability_registry", get_capability_registry().all_ids)
    except Exception:  # noqa: BLE001
        _fp("capability_registry", None)
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        _fp("algorithm_registry", list(getattr(
            get_algorithm_registry(), "_by_id", {}) or {}))
    except Exception:  # noqa: BLE001
        _fp("algorithm_registry", None)
    try:
        from app.lib.gis.runtime_manifest import get_runtime_manifest
        _fp("runtime_manifest", [str(get_runtime_manifest().fingerprint)])
    except Exception:  # noqa: BLE001
        _fp("runtime_manifest", None)
    try:
        from app.services.modelops.registry import ModelRegistryStore
        store = ModelRegistryStore()
        try:
            store.load()
        except Exception:  # noqa: BLE001 — 未初始化目录按缺席
            pass
        records = getattr(store, "_records", {}) or {}
        _fp("modelops_registry", [f"{k[1]}@{k[2]}" for k in records.keys()])
    except Exception:  # noqa: BLE001
        _fp("modelops_registry", None)
    return fps


# ── 构建（source registries → derived projection）───────────────────────


def _lazy_tool_registry() -> Optional[Any]:
    """manifest 同款惰性注册表（lifespan 注入前/测试直构可用）。"""
    try:
        from app.tools import init_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        init_tools(reg)
        return reg
    except Exception:  # noqa: BLE001
        logger.debug("[CapabilityGraph] tool registry init failed", exc_info=True)
        return None


def build_capability_graph() -> CapabilityGraph:
    """source registries → 统一能力图（确定性；残缺段记 issue 不静默）。"""
    nodes: Dict[str, GraphNode] = {}
    edges: List[GraphEdge] = []
    issues: List[GraphIssue] = []

    def _add(node: GraphNode) -> None:
        if len(nodes) >= MAX_NODES:
            if not any(i.code == "node_budget_exceeded" for i in issues):
                issues.append(GraphIssue(
                    "node_budget_exceeded",
                    f"graph truncated at {MAX_NODES} nodes"))
            return
        if node.key in nodes:
            issues.append(GraphIssue(
                "duplicate_identity",
                f"{node.key} declared by {node.source_registry} and "
                f"{nodes[node.key].source_registry}", severity="error"))
            return
        nodes[node.key] = node

    def _edge(src_kind: str, src_id: str, relation: str,
              dst_kind: str, dst_id: str) -> None:
        if len(edges) >= MAX_EDGES:
            return
        edges.append(GraphEdge(f"{src_kind}:{src_id}", relation,
                               f"{dst_kind}:{dst_id}"))

    # 1) capability registry
    try:
        from app.lib.gis.capability_registry import get_capability_registry
        caps = get_capability_registry()
        for cid in caps.all_ids[:MAX_INDEX_CAPS]:
            d = caps.get(cid)
            if d is None:
                continue
            _add(GraphNode(
                cid, KIND_CAPABILITY, "capability_registry",
                label=str(d.name or cid),
                corpus=" ".join(filter(None, [
                    cid, str(d.name or ""), str(d.description or ""),
                    str(d.domain), str(d.category)])),
                extras={"domain": str(d.domain), "category": str(d.category)},
            ))
            for fb in (d.fallback_capabilities or [])[:4]:
                _edge(KIND_CAPABILITY, cid, REL_FALLBACK_TO,
                      KIND_CAPABILITY, fb)
    except Exception:  # noqa: BLE001
        issues.append(GraphIssue("source_unavailable",
                                 "capability registry unavailable"))
        logger.debug("[CapabilityGraph] capability registry unavailable",
                     exc_info=True)

    # 2) algorithm registry
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        algos = get_algorithm_registry()
        algo_ids = sorted(getattr(algos, "_by_id", {}).keys())
        for aid in algo_ids[:MAX_INDEX_ALGOS]:
            d = algos.get(aid)
            if d is None:
                continue
            _add(GraphNode(
                aid, KIND_ALGORITHM, "algorithm_registry",
                label=str(d.name or aid),
                corpus=" ".join(filter(None, [
                    aid, str(d.name or ""), str(d.category or ""),
                    str(d.algorithm_family or ""), " ".join(d.tags or ())])),
                extras={"complexity": str(d.complexity or "")},
            ))
            for cap in (d.capabilities or [])[:8]:
                _edge(KIND_CAPABILITY, cap, REL_IMPLEMENTED_BY,
                      KIND_ALGORITHM, aid)
            for t in (d.tool_candidates or [])[:8]:
                _edge(KIND_ALGORITHM, aid, REL_EXPOSED_BY, KIND_TOOL, t)
            if getattr(d, "output_artifact_type", None):
                _edge(KIND_ALGORITHM, aid, REL_PRODUCES,
                      KIND_ARTIFACT_TYPE, str(d.output_artifact_type))
            for at in (getattr(d, "input_artifact_types", None) or [])[:4]:
                _edge(KIND_ALGORITHM, aid, REL_ACCEPTS,
                      KIND_ARTIFACT_TYPE, str(at))
    except Exception:  # noqa: BLE001
        issues.append(GraphIssue("source_unavailable",
                                 "algorithm registry unavailable"))

    # 3) tool registry（manifest 同款惰性初始化；指纹经 runtime_manifest
    #    进入缓存键，工具面变化 → 图重建）
    reg = _lazy_tool_registry()
    if reg is not None:
        try:
            for name in sorted(reg.list_tools())[:MAX_INDEX_TOOLS]:
                meta = reg.metadata(name) or {}
                _add(GraphNode(
                    name, KIND_TOOL, "tool_registry",
                    label=str(name),
                    corpus=" ".join(filter(None, [
                        name, str(meta.get("description", ""))[:120]])),
                    extras={
                        "tier": meta.get("tier", 1),
                        "execution_policy": str(getattr(
                            meta.get("execution_policy"), "value",
                            meta.get("execution_policy") or "")),
                        "latency_class": str(meta.get("latency_class", "")),
                        "memory_class": str(meta.get("memory_class", "")),
                    },
                ))
                for cap in (meta.get("capabilities") or [])[:6]:
                    if cap:
                        _edge(KIND_TOOL, name, REL_IMPLEMENTS,
                              KIND_CAPABILITY, str(cap))
        except Exception:  # noqa: BLE001
            issues.append(GraphIssue("source_unavailable",
                                     "tool registry projection failed"))

    # 4) ModelOps registry（V8.2：Model 一等能力实体）
    try:
        from app.services.modelops.registry import ModelRegistryStore
        store = ModelRegistryStore()
        try:
            store.load()
        except Exception:  # noqa: BLE001
            pass
        records = getattr(store, "_records", {}) or {}
        for key in sorted(records.keys())[:MAX_INDEX_MODELS]:
            scope_key, model_id, version = key
            desc = records[key].descriptor
            _add(GraphNode(
                f"{model_id}@{version}", KIND_MODEL, "modelops_registry",
                label=f"{model_id}@{version}",
                corpus=" ".join(filter(None, [
                    model_id, version, " ".join(desc.task_types),
                    str(desc.provider_ref)])),
                extras={
                    "task_types": list(desc.task_types)[:6],
                    "input_bands": int(desc.input_bands),
                    "provider_ref": str(desc.provider_ref),
                    "owner_scope_key": str(scope_key),
                    "min_m_per_px": (
                        desc.spatial.resolution_range.min_m_per_px
                        if desc.spatial and desc.spatial.resolution_range
                        else None),
                    "max_m_per_px": (
                        desc.spatial.resolution_range.max_m_per_px
                        if desc.spatial and desc.spatial.resolution_range
                        else None),
                },
            ))
            for cap in _modelops_task_capabilities(desc.task_types):
                _edge(KIND_MODEL, f"{model_id}@{version}", REL_IMPLEMENTS,
                      KIND_CAPABILITY, cap)
            if desc.provider_ref:
                _edge(KIND_MODEL, f"{model_id}@{version}", REL_RUNS_ON,
                      KIND_EXECUTION_BACKEND, str(desc.provider_ref))
    except Exception:  # noqa: BLE001
        issues.append(GraphIssue("source_unavailable",
                                 "modelops registry unavailable"))
        logger.debug("[CapabilityGraph] modelops registry unavailable",
                     exc_info=True)

    # 5) artifact types（dangling 校验的落点）
    try:
        from app.lib.gis.artifacts import get_artifact_type_registry
        arts = get_artifact_type_registry()
        for at in sorted(getattr(arts, "_by_id", {}).keys())[:MAX_INDEX_ARTIFACTS]:
            _add(GraphNode(at, KIND_ARTIFACT_TYPE, "artifact_registry",
                           label=str(at)))
    except Exception:  # noqa: BLE001
        pass  # 缺席时 validate 以 info 披露（见 validate_graph）

    # 6) execution backends / providers（边终点去重投影为节点）
    for kind in (KIND_EXECUTION_BACKEND, KIND_PROVIDER):
        seen: List[str] = []
        for e in edges:
            if e.dst.startswith(f"{kind}:"):
                eid = e.dst.split(":", 1)[1]
                if eid not in seen:
                    seen.append(eid)
        for eid in sorted(seen):
            _add(GraphNode(eid, kind, "derived", label=eid))

    fp_blob = json.dumps(source_fingerprints(), sort_keys=True)
    return CapabilityGraph(
        nodes, edges,
        source_fingerprint=hashlib.sha256(
            fp_blob.encode("utf-8")).hexdigest()[:32],
        issues=issues,
    )


#: modelops task_type → capability 词汇的稳定映射（词汇双轨由这一张表
#: 收敛，不另建第二事实源；capability 侧真源在
#: app/lib/gis/capabilities/modelops.py 域包）。
_MODELOPS_TASK_TO_CAPABILITY: Dict[str, str] = {
    "semantic_segmentation": "model_image_segmentation",
    "promptable_segmentation": "model_image_segmentation",
    "object_detection": "model_object_detection",
    "instance_segmentation": "model_instance_segmentation",
    "super_resolution": "model_super_resolution",
    "change_detection": "model_change_detection",
    "sar_optical_fusion": "model_change_detection",
    "temporal_forecast": "model_temporal_forecast",
    "temporal_classification": "model_temporal_classification",
    "embedding": "model_embedding",
}


def _modelops_task_capabilities(task_types: Iterable[str]) -> List[str]:
    seen: List[str] = []
    for t in task_types:
        cap = _MODELOPS_TASK_TO_CAPABILITY.get(str(t))
        if cap and cap not in seen:
            seen.append(cap)
    return seen[:4]


# ── 缓存（fingerprint → immutable projection；同指纹零重建）────────────

_graph_cache: Dict[str, Any] = {"fingerprint": None, "graph": None}
_build_counter: List[int] = [0]
_graph_lock = threading.RLock()


def get_capability_graph() -> CapabilityGraph:
    """进程级缓存投影：source 指纹未变 → 同一实例（零重建）。"""
    with _graph_lock:
        fp = hashlib.sha256(
            json.dumps(source_fingerprints(), sort_keys=True).encode("utf-8")
        ).hexdigest()[:32]
        cached = _graph_cache["graph"]
        if _graph_cache["fingerprint"] == fp and cached is not None:
            return cached
        _build_counter[0] += 1
        graph = build_capability_graph()
        _graph_cache["fingerprint"] = fp
        _graph_cache["graph"] = graph
        return graph


def reset_capability_graph() -> None:
    """测试隔离：丢弃缓存（下一个 get 重建）。"""
    with _graph_lock:
        _graph_cache["fingerprint"] = None
        _graph_cache["graph"] = None


def graph_build_count_for_tests() -> int:
    """结构测试钩子：真实重建次数（缓存命中不计数）。"""
    return _build_counter[0]


# ── 校验（机器闸：接入 registry_validation / preflight）────────────────


def validate_graph(graph: Optional[CapabilityGraph] = None) -> List[GraphIssue]:
    """dangling / duplicate / contradiction 的机器可读报告。"""
    g = graph or get_capability_graph()
    issues: List[GraphIssue] = list(g.build_issues)
    node_keys = set(g._nodes.keys())  # noqa: SLF001 — 同模块只读
    for key, node in g._nodes.items():  # noqa: SLF001
        if node.kind not in GRAPH_KINDS:
            issues.append(GraphIssue(
                "invalid_kind",
                f"{key} kind {node.kind!r} outside vocabulary",
                severity="error"))
    seen_edges = set()
    for e in g._edges:  # noqa: SLF001
        if e.relation not in GRAPH_RELATIONS:
            issues.append(GraphIssue(
                "invalid_relation",
                f"{e.src} -{e.relation}-> {e.dst} outside vocabulary",
                severity="error"))
            continue
        sig = (e.src, e.relation, e.dst)
        if sig in seen_edges:
            issues.append(GraphIssue("duplicate_edge", str(sig)))
            continue
        seen_edges.add(sig)
        for end in (e.src, e.dst):
            if end not in node_keys:
                issues.append(GraphIssue(
                    "dangling_endpoint",
                    f"{e.src} -{e.relation}-> {e.dst}: endpoint {end} "
                    "not a node"))
    if not g.nodes_by_kind(KIND_ARTIFACT_TYPE):
        issues.append(GraphIssue(
            "artifact_types_absent",
            "artifact registry not projected; produces/accepts edges "
            "unverifiable", severity="info"))
    return issues


__all__ = [
    "CapabilityGraph", "GraphNode", "GraphEdge", "GraphIssue",
    "GRAPH_KINDS", "GRAPH_RELATIONS",
    "build_capability_graph", "get_capability_graph",
    "reset_capability_graph", "validate_graph",
    "v8_capability_graph_enabled", "graph_build_count_for_tests",
    "source_fingerprints",
]
