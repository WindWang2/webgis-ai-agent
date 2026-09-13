"""Unified Capability Graph —— Harness V8 统一能力投影（ADR-0137）。

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
import time
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
REL_REQUIRES = "requires"                  # workflow/template → capability; component → component
REL_RUNS_ON = "runs_on"                    # model → execution_backend
REL_CONTAINS = "contains"                  # workflow → algorithm/tool
REL_COMPOSED_OF = "composed_of"            # template/workflow → component
REL_BINDS_TO = "binds_to"                  # component/template → artifact_type
REL_FALLBACK_TO = "fallback_to"            # capability/workflow/tool → 同kind 后继
REL_EXECUTED_BY = "executed_by"            # algorithm → execution_backend
REL_CONFLICTS_WITH = "conflicts_with"      # capability/component → 同kind 互斥（V1）

GRAPH_RELATIONS: FrozenSet[str] = frozenset({
    REL_IMPLEMENTED_BY, REL_EXPOSED_BY, REL_ACCEPTS, REL_PRODUCES,
    REL_INVOKES, REL_IMPLEMENTS, REL_REQUIRES, REL_RUNS_ON, REL_CONTAINS,
    REL_COMPOSED_OF, REL_BINDS_TO, REL_FALLBACK_TO, REL_EXECUTED_BY,
    REL_CONFLICTS_WITH,
})

#: 构建有界预算（§28：bounded）。超过即截断并在 issues 披露（不静默）。
MAX_NODES = 4096
MAX_EDGES = 20000
MAX_INDEX_CAPS = 512
MAX_INDEX_ALGOS = 640
MAX_INDEX_TOOLS = 640
MAX_INDEX_MODELS = 256
MAX_INDEX_ARTIFACTS = 128
MAX_INDEX_RECIPES = 320
MAX_INDEX_TEMPLATES = 128
MAX_INDEX_COMPONENTS = 256
MAX_INDEX_ADAPTERS = 128


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
        """capability → 候选 model（implements 入边，确定性按 id 排序）。

        Review A RA-1：implements 关系同时承载 model→capability 与
        tool→capability 两类边 —— 必须按 kind 过滤，否则工具混入模型面。
        """
        keys = self.reverse_neighbors(
            f"{KIND_CAPABILITY}:{capability_id}", REL_IMPLEMENTS)
        nodes = [self._nodes[k] for k in keys
                 if k in self._nodes and self._nodes[k].kind == KIND_MODEL]
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

    # ── V1（ADR-0181）：四段 provider 面 + 互斥查询 ──────────────────

    def workflows_for_capability(self, capability_id: str) -> List[str]:
        """capability → 声明它的 recipe（requires 入边，确定性按 id）。"""
        keys = self.reverse_neighbors(
            f"{KIND_CAPABILITY}:{capability_id}", REL_REQUIRES)
        return sorted(k.split(":", 1)[1] for k in keys
                      if k in self._nodes and self._nodes[k].kind == KIND_WORKFLOW)

    def templates_for_capability(self, capability_id: str) -> List[str]:
        """capability → 消费它的产品模板（requires 入边）。"""
        keys = self.reverse_neighbors(
            f"{KIND_CAPABILITY}:{capability_id}", REL_REQUIRES)
        return sorted(k.split(":", 1)[1] for k in keys
                      if k in self._nodes and self._nodes[k].kind == KIND_TEMPLATE)

    def conflicts_of_capability(self, capability_id: str) -> List[str]:
        """capability 的互斥面（出边 ∪ 入边，确定性去重排序）。"""
        out = self.neighbors(f"{KIND_CAPABILITY}:{capability_id}", REL_CONFLICTS_WITH)
        inc = self.reverse_neighbors(
            f"{KIND_CAPABILITY}:{capability_id}", REL_CONFLICTS_WITH)
        seen: List[str] = []
        for key in list(out) + list(inc):
            cid = key.split(":", 1)[1]
            if cid not in seen:
                seen.append(cid)
        return sorted(seen)

    def capability_providers(self, capability_id: str) -> Dict[str, List[str]]:
        """capability → 全 provider 面（tool/model/workflow/template，有界）。

        adapter 段暂无声明级 capability 连线（ADS 数据供给走 retrieval
        语义），不进本查询 —— 出现声明面时在此扩展，不另建查询入口。
        """
        return {
            "tools": self.tools_for_capability(capability_id)[:8],
            "models": [n.id for n in self.models_for_capability(capability_id)][:8],
            "workflows": self.workflows_for_capability(capability_id)[:8],
            "templates": self.templates_for_capability(capability_id)[:8],
        }

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
    # ── V1（ADR-0181）：四段新来源入缓存键 ──────────────────────────
    try:
        from app.services.gis_harness.recipes import get_recipe_registry
        _fp("recipe_registry", get_recipe_registry().all_ids)
    except Exception:  # noqa: BLE001
        _fp("recipe_registry", None)
    try:
        from app.services.gis_harness.product_templates import (
            get_product_template_registry,
        )
        _fp("product_templates", get_product_template_registry().all_ids)
    except Exception:  # noqa: BLE001
        _fp("product_templates", None)
    try:
        from app.lib.cartography.component_registry import get_component_registry
        _fp("component_registry", get_component_registry().all_ids)
    except Exception:  # noqa: BLE001
        _fp("component_registry", None)
    try:
        from app.services.data_fabric.registry import get_registry
        adapters = get_registry()
        _fp("data_fabric_adapters", adapters.supported_source_types())
    except Exception:  # noqa: BLE001
        _fp("data_fabric_adapters", None)
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
            if not any(i.code == "edge_budget_exceeded" for i in issues):
                issues.append(GraphIssue(
                    "edge_budget_exceeded",
                    f"graph edges truncated at {MAX_EDGES}"))
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
                extras={
                    "domain": str(d.domain), "category": str(d.category),
                    "status": str(d.status), "version": str(d.version),
                    "deterministic": bool(d.deterministic),
                    "offline_capable": (
                        bool(d.offline_capable) if d.offline_capable is not None
                        else None),
                    "supports_large_data": bool(d.supports_large_data),
                },
            ))
            for fb in (d.fallback_capabilities or [])[:4]:
                _edge(KIND_CAPABILITY, cid, REL_FALLBACK_TO,
                      KIND_CAPABILITY, fb)
            for inc in (d.incompatible_with or [])[:6]:
                _edge(KIND_CAPABILITY, cid, REL_CONFLICTS_WITH,
                      KIND_CAPABILITY, inc)
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
                        # V1（ADR-0181）：资格/解析面的诚实声明投影
                        # （ToolDescriptor 既有字段，零复制零新声明）。
                        "status": str(meta.get("status", "")),
                        "version": str(meta.get("version", "")),
                        "side_effect": str(meta.get("side_effect", "")),
                        "network": meta.get("network"),
                        "deterministic": meta.get("deterministic"),
                        "idempotent": meta.get("idempotent"),
                        "scale_class": str(meta.get("scale_class", "")),
                        "cost": str(meta.get("cost", "")),
                        "security_tier": meta.get("security_tier"),
                        "required_permission": str(
                            meta.get("required_permission") or ""),
                        "deprecation_of": str(
                            meta.get("deprecation_of") or ""),
                    },
                ))
                for cap in (meta.get("capabilities") or [])[:6]:
                    if cap:
                        _edge(KIND_TOOL, name, REL_IMPLEMENTS,
                              KIND_CAPABILITY, str(cap))
                # 弃用链：DEPRECATED 工具 → canonical 后继（fallback_to
                # 语义：解析面优先 canonical —— deprecated_penalty 因子；
                # 目标不存在时由 dangling_endpoint warning 披露，非 fatal）。
                dep = str(meta.get("deprecation_of") or "")
                if dep:
                    _edge(KIND_TOOL, name, REL_FALLBACK_TO, KIND_TOOL, dep)
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
            # Review A RA-3：ModelOps 身份三元组含 owner scope —— 跨 scope
            # 同名 (model_id, version) 是合法注册态。节点 id 对齐 registry
            # listing 格式 `{skey}/{id}@{version}`，杜绝 duplicate_identity
            # 假告警与先到者顶替真源。
            _add(GraphNode(
                f"{scope_key}/{model_id}@{version}", KIND_MODEL, "modelops_registry",
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
            node_id = f"{scope_key}/{model_id}@{version}"
            for cap in _modelops_task_capabilities(desc.task_types):
                _edge(KIND_MODEL, node_id, REL_IMPLEMENTS,
                      KIND_CAPABILITY, cap)
            if desc.provider_ref:
                _edge(KIND_MODEL, node_id, REL_RUNS_ON,
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

    # 6) recipe registry（V1：workflow 一等节点 ——「怎么做」的能力面）
    #    requires → preferred/optional capability；fallback_to → 声明式
    #    降级链（ADR-0151 FallbackLink）；composed_of → 组件类型。
    try:
        from app.services.gis_harness.recipes import get_recipe_registry
        recipes = get_recipe_registry()
        for rid in recipes.all_ids[:MAX_INDEX_RECIPES]:
            r = recipes.get(rid)
            if r is None:
                continue
            _add(GraphNode(
                rid, KIND_WORKFLOW, "recipe_registry",
                label=str(r.name or rid),
                corpus=" ".join(filter(None, [
                    rid, str(r.name or ""), str(r.description or ""),
                    " ".join(r.intent_tasks or [])])),
                extras={
                    "priority": int(r.priority),
                    "schema_version": int(r.schema_version),
                    "primary_cartography": str(r.primary_cartography or ""),
                },
            ))
            seen_caps: List[str] = []
            for cap in list(r.preferred_analysis or []) + list(
                    r.optional_analysis or []):
                if cap and cap not in seen_caps and len(seen_caps) < 12:
                    seen_caps.append(cap)
            for cap in seen_caps:
                _edge(KIND_WORKFLOW, rid, REL_REQUIRES,
                      KIND_CAPABILITY, cap)
            for comp in (r.default_components or [])[:8]:
                _edge(KIND_WORKFLOW, rid, REL_COMPOSED_OF,
                      KIND_COMPONENT, str(comp))
            for link in (r.fallback_links or [])[:4]:
                _edge(KIND_WORKFLOW, rid, REL_FALLBACK_TO,
                      KIND_WORKFLOW, str(link.to))
    except Exception:  # noqa: BLE001
        issues.append(GraphIssue("source_unavailable",
                                 "recipe registry unavailable"))

    # 7) product templates（V1：template 一等节点 ——「产品形态」能力面）
    try:
        from app.services.gis_harness.product_templates import (
            get_product_template_registry,
        )
        templates = get_product_template_registry()
        for tid in templates.all_ids[:MAX_INDEX_TEMPLATES]:
            t = templates.get(tid)
            if t is None:
                continue
            _add(GraphNode(
                tid, KIND_TEMPLATE, "product_templates",
                label=str(t.name or tid),
                corpus=" ".join(filter(None, [
                    tid, str(t.name or ""), str(t.description or ""),
                    str(t.archetype or "")])),
                extras={
                    "archetype": str(t.archetype or ""),
                    "deprecated": bool(t.deprecated),
                    "priority": int(t.priority),
                    "template_version": str(t.template_version or ""),
                },
            ))
            seen_caps = []
            for role in (t.layer_roles or []):
                cap = str(getattr(role, "source_capability", "") or "")
                if cap and cap not in seen_caps and len(seen_caps) < 8:
                    seen_caps.append(cap)
            for cap in seen_caps:
                _edge(KIND_TEMPLATE, tid, REL_REQUIRES,
                      KIND_CAPABILITY, cap)
            for comp in (t.default_components or [])[:8]:
                _edge(KIND_TEMPLATE, tid, REL_COMPOSED_OF,
                      KIND_COMPONENT, str(comp))
            art = str((t.layer_roles[0].source_artifact if t.layer_roles else "")
                      or "")
            if art:
                _edge(KIND_TEMPLATE, tid, REL_BINDS_TO,
                      KIND_ARTIFACT_TYPE, art[:64])
    except Exception:  # noqa: BLE001
        issues.append(GraphIssue("source_unavailable",
                                 "product template registry unavailable"))

    # 8) map components（V1：component 一等节点 ——「组件」能力面，含互斥）
    try:
        from app.lib.cartography.component_registry import get_component_registry
        comps = get_component_registry()
        for comp_id in comps.all_ids[:MAX_INDEX_COMPONENTS]:
            c = comps.get(comp_id)
            if c is None:
                continue
            _add(GraphNode(
                comp_id, KIND_COMPONENT, "component_registry",
                label=str(c.name or c.name_zh or comp_id),
                corpus=" ".join(filter(None, [
                    comp_id, str(c.name or ""), str(c.name_zh or ""),
                    str(c.description or ""), str(c.semantic_role or ""),
                    " ".join((c.tags or [])[:6]),
                    " ".join((c.search_keywords_zh or [])[:6])])),
                extras={
                    "category": str(c.category or ""),
                    "semantic_role": str(c.semantic_role or ""),
                    "runtime_status": str(getattr(c, "runtime_status", "") or ""),
                    "deprecated": bool(c.deprecated),
                    "deprecated_by": str(c.deprecated_by or ""),
                },
            ))
            for dep in (c.dependencies or [])[:6]:
                _edge(KIND_COMPONENT, comp_id, REL_REQUIRES,
                      KIND_COMPONENT, str(dep))
            for conflict in (c.conflicts or [])[:6]:
                _edge(KIND_COMPONENT, comp_id, REL_CONFLICTS_WITH,
                      KIND_COMPONENT, str(conflict))
            for at in (c.compatible_artifact_types or [])[:6]:
                _edge(KIND_COMPONENT, comp_id, REL_BINDS_TO,
                      KIND_ARTIFACT_TYPE, str(at))
    except Exception:  # noqa: BLE001
        issues.append(GraphIssue("source_unavailable",
                                 "component registry unavailable"))

    # 9) data fabric adapters（V1：provider 节点 ——「数据供给」能力面）。
    #    推下（pushdown）旗标是资格声明面；capability 连线暂无声明源，
    #    不发明映射表 —— 出现声明面时在此补边（recon §7 决策）。
    try:
        from app.services.data_fabric.registry import get_registry as get_adapters
        adapter_reg = get_adapters()
        seen_adapters: set = set()
        for source_type in sorted(adapter_reg.supported_source_types()):
            spec = adapter_reg.resolve(source_type)
            if spec is None or spec.canonical in seen_adapters:
                continue
            seen_adapters.add(spec.canonical)
            if len(seen_adapters) > MAX_INDEX_ADAPTERS:
                break
            _add(GraphNode(
                spec.canonical, KIND_PROVIDER, "data_fabric_registry",
                label=str(spec.canonical),
                corpus=" ".join(filter(None, [
                    spec.canonical, " ".join(spec.names),
                    str(spec.notes or "")])),
                extras={
                    "supports_bbox": bool(spec.supports_bbox),
                    "supports_filter": bool(spec.supports_filter),
                    "supports_pagination": bool(spec.supports_pagination),
                    "supports_datetime": bool(spec.supports_datetime),
                    "supports_projection": bool(spec.supports_projection),
                    "is_raster_tile": bool(spec.is_raster_tile),
                    "is_demo": bool(spec.is_demo),
                },
            ))
    except Exception:  # noqa: BLE001
        issues.append(GraphIssue("source_unavailable",
                                 "data fabric adapter registry unavailable"))

    # 10) execution backends / providers（边终点去重投影为节点）
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
        started = time.perf_counter()
        _build_counter[0] += 1
        graph = build_capability_graph()
        _graph_cache["fingerprint"] = fp
        _graph_cache["graph"] = graph
        # review P2：首次构建/重建（冷启动 ~秒级）进 info 日志 —— 热路径
        # 延迟尖峰可观测；常规路径（缓存命中）零开销零日志。
        logger.info(
            "[CapabilityGraph] built %d nodes / %d edges in %.1f ms "
            "(fingerprint %s)", graph.node_count, graph.edge_count,
            (time.perf_counter() - started) * 1000.0, fp[:8])
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
    if not v8_capability_graph_enabled():
        return []  # kill switch：GIS_CAPABILITY_GRAPH_V8=0 关闸（Review B RB-2）
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
    issues.extend(_structural_audit(g))
    return issues


#: 结构审计遍历的"可环"关系。implements（tool/model → capability）是
#: **向上**的声明闭合边：工具声明与算法实现同一能力（capability →
#: implemented_by → algorithm → exposed_by → tool → implements → capability）
#: 是跨 registry 一致性，不是矛盾环 —— 不入环检查。conflicts 同理（对称
#: 声明语义）。artifact/backend/provider 是叶端。
_AUDIT_RELATIONS: FrozenSet[str] = frozenset({
    REL_IMPLEMENTED_BY, REL_EXPOSED_BY, REL_REQUIRES,
    REL_CONTAINS, REL_COMPOSED_OF, REL_BINDS_TO, REL_FALLBACK_TO,
    REL_RUNS_ON, REL_EXECUTED_BY, REL_INVOKES,
})
_AUDIT_MAX_FINDINGS = 512


def _structural_audit(g: "CapabilityGraph") -> List["GraphIssue"]:
    """V1（ADR-0181）结构审计：环 / 孤儿能力 / 不可达工具 / 无消费者
    artifact / 弃用暴露。全部 warning 级（D6：先可观测，再逐段收紧）；
    发现数有界（防巨型 registry 拖垮校验）。"""
    found: List[GraphIssue] = []

    def _emit(code: str, detail: str) -> None:
        if len(found) < _AUDIT_MAX_FINDINGS:
            found.append(GraphIssue(code, detail, severity="warning"))
        elif len(found) == _AUDIT_MAX_FINDINGS:
            found.append(GraphIssue(
                "audit_truncated",
                f"structural audit truncated at {_AUDIT_MAX_FINDINGS} findings",
                severity="warning"))

    # ── 环检测（迭代 DFS，三色标记；发现即报，路径有界）────────────
    adj: Dict[str, List[str]] = {}
    for e in g._edges:  # noqa: SLF001 — 同模块只读
        if e.relation in _AUDIT_RELATIONS and e.src != e.dst:
            adj.setdefault(e.src, []).append(e.dst)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {}
    for start in sorted(adj.keys()):
        if color.get(start, WHITE) != WHITE:
            continue
        stack = [(start, iter(sorted(adj.get(start, []))))]
        color[start] = GRAY
        path = [start]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if color.get(nxt, WHITE) == GRAY:
                    cyc = path[path.index(nxt):] if nxt in path else [node, nxt]
                    _emit("cycle_detected",
                          " -> ".join([*cyc[:6], nxt])
                          + f" (via {nxt.split(':', 1)[0]})")
                    # 收缩到该分支继续（不终止全图审计）
                elif color.get(nxt, WHITE) == WHITE:
                    color[nxt] = GRAY
                    path.append(nxt)
                    stack.append((nxt, iter(sorted(adj.get(nxt, [])))))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                path.pop()
                stack.pop()

    # ── 孤儿 capability：无任何 provider/引用入边（C0 unreachable 面）──
    referenced: Dict[str, int] = {}
    for e in g._edges:  # noqa: SLF001
        if e.relation in (REL_IMPLEMENTED_BY, REL_REQUIRES, REL_IMPLEMENTS,
                          REL_FALLBACK_TO, REL_CONFLICTS_WITH):
            dst = e.dst
            if dst.startswith(f"{KIND_CAPABILITY}:"):
                referenced[dst] = referenced.get(dst, 0) + 1
    for node in g.nodes_by_kind(KIND_CAPABILITY):
        key = f"{KIND_CAPABILITY}:{node.id}"
        if referenced.get(key, 0) == 0:
            has_impl = bool(g.reverse_neighbors(key, REL_IMPLEMENTED_BY)) or \
                bool(g.reverse_neighbors(key, REL_IMPLEMENTS))
            if not has_impl:
                _emit("orphan_capability",
                      f"capability {node.id} has no algorithm/tool provider "
                      "and is not referenced by any workflow/template/fallback")

    # ── 不可达 tool：无 exposed_by 入边也无 implements/fallback 入边 ──
    tool_in: Dict[str, int] = {}
    for e in g._edges:  # noqa: SLF001
        if e.dst.startswith(f"{KIND_TOOL}:") and e.relation in (
                REL_EXPOSED_BY, REL_IMPLEMENTS, REL_FALLBACK_TO):
            tool_in[e.dst] = tool_in.get(e.dst, 0) + 1
    for node in g.nodes_by_kind(KIND_TOOL):
        if tool_in.get(f"{KIND_TOOL}:{node.id}", 0) == 0:
            _emit("unreachable_tool",
                  f"tool {node.id} is not exposed by any algorithm and has "
                  "no capability/deprecation link")

    # ── 无消费者 artifact：只被 produces 指到、无人 accepts/binds ────
    if g.nodes_by_kind(KIND_ARTIFACT_TYPE):
        produced: Dict[str, int] = {}
        consumed: Dict[str, int] = {}
        for e in g._edges:  # noqa: SLF001
            if e.dst.startswith(f"{KIND_ARTIFACT_TYPE}:"):
                if e.relation == REL_PRODUCES:
                    produced[e.dst] = produced.get(e.dst, 0) + 1
                elif e.relation in (REL_ACCEPTS, REL_BINDS_TO):
                    consumed[e.dst] = consumed.get(e.dst, 0) + 1
        for at in sorted(produced.keys()):
            if consumed.get(at, 0) == 0:
                _emit("artifact_no_consumer",
                      f"artifact type {at.split(':', 1)[1]} is produced but "
                      "never accepted/bound by any node")

    # ── 弃用暴露：非弃用 algorithm → 弃用 tool（新 plan 选到弃用面）──
    for e in g._edges:  # noqa: SLF001
        if e.relation != REL_EXPOSED_BY or not e.dst.startswith(f"{KIND_TOOL}:"):
            continue
        tool_node = g.node(e.dst)
        if tool_node is None or str(tool_node.extras.get("status", "")) != "deprecated":
            continue
        src = g.node(e.src)
        if src is not None and str(src.extras.get("status", "")) != "deprecated":
            _emit("exposes_deprecated_tool",
                  f"{e.src} exposes deprecated tool "
                  f"{e.dst.split(':', 1)[1]} "
                  f"(canonical: {tool_node.extras.get('deprecation_of') or 'unspecified'})")
    return found


__all__ = [
    "CapabilityGraph", "GraphNode", "GraphEdge", "GraphIssue",
    "GRAPH_KINDS", "GRAPH_RELATIONS",
    "build_capability_graph", "get_capability_graph",
    "reset_capability_graph", "validate_graph",
    "v8_capability_graph_enabled", "graph_build_count_for_tests",
    "source_fingerprints",
    "REL_CONFLICTS_WITH",
]
