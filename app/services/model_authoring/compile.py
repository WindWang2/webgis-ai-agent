"""模型编译器（ModelDocument → TypedWorkflowGraph + WorkflowPackage）。

流程：validate_document 守门（任一 error 级 finding → 拒绝编译，返回
None）→ 作者节点/边投影为 typed DAG（端口事实全部经 contracts.node_ports
单一投影）→ 节点静态参数落默认值（``$param:`` 引用替换为参数 default；
required 无 default 的引用键省略 —— 运行时由 adapter 经 node_params 注入）
→ 固化为 WorkflowPackage（包 id ``umodel_<slug>``；指纹复用
``workflow_v4.package._canonical_fingerprint`` 的 canonical JSON sha256，
保持单一指纹实现）。

transform 节点的 wire node_id 采用运行时权威格式
``transform:<op>:<author_node_id>``（driver 的 geocompute 适配器以
``node_id.split(":")[1]`` 解析算子，node_id 是算子的**唯一**事实源）；
其余 kind 维持作者 slug。作者 slug 词法不含冒号，故
``wire.rsplit(":", 1)[1]`` 可逆恢复作者 id；CompiledModel.node_id_map
（author→wire）是实例化层/投影层的权威桥接。

红线：

- ``layout`` 是纯 UI 坐标，不进 compiled_form（科学指纹不受编辑坐标影响）；
- 端口/角色/能力事实只引用既有 registry 投影，不实现第二份科学语义；
- 全部确定性：同输入同图同指纹（reproducibility 红线）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.model_authoring_schema import ModelDocument, ModelNode
from app.services.gis_harness.workflow_v4.package import (
    WORKFLOW_PACKAGE_SCHEMA_VERSION,
    WorkflowPackage,
    _canonical_fingerprint,  # noqa: PLC2701 — 单一指纹实现复用（不另造第二份 sha256 算法）
    parse_semver,
)
from app.services.gis_harness.workflow_v4.typed_dag import (
    _MAX_GRAPH_EDGES,  # noqa: PLC2701 — 图硬上界单一事实源（typed_dag）
    _MAX_GRAPH_NODES,  # noqa: PLC2701
    TypedPort,
    TypedWorkflowEdge,
    TypedWorkflowGraph,
    TypedWorkflowNode,
)
from app.services.model_authoring.contracts import (
    SEVERITY_ERROR,
    ModelFinding,
    node_ports,
    role_port_default,
)
from app.services.model_authoring.validate import _PARAM_REF_PREFIX, validate_document

#: 作者模型包 id 前缀（store 层与 PackageRegistry 共用词表）。
MODEL_PACKAGE_PREFIX = "umodel_"

#: 调用方 slug 词法：^[a-z][a-z0-9_]{2,40}$（非法 raise ValueError）。
_MODEL_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

#: 节点静态参数 wire 帽（schema 同界：≤16 键；字符串值 ≤256 字符）。
_MAX_NODE_PARAMS = 16

#: transform 节点 wire node_id 前缀（运行时权威格式
#: ``transform:<op>:<author>``；op 解析 = node_id.split(":")[1]）。
_TRANSFORM_WIRE_PREFIX = "transform:"

#: roundtrip 投影显式声明的有损项（注记不进 typed wire）。
_ROUNDTRIP_LOSSY = ("layout", "title", "description")


@dataclass
class CompiledModel:
    """编译产物：typed 图 + 不可变包 + 门禁 findings（error = None 产物）。

    ``node_id_map``：author node_id → wire node_id（transform 节点为
    ``transform:<op>:<author>``，其余为恒等映射）。实例化层与投影层的
    权威桥接；作者 slug 不含冒号，wire 侧亦可 ``rsplit(":", 1)[1]`` 反解。
    """

    typed_graph: TypedWorkflowGraph
    package: WorkflowPackage
    findings: List[ModelFinding] = field(default_factory=list)
    node_id_map: Dict[str, str] = field(default_factory=dict)


def _referenced_params(node: ModelNode) -> Tuple[str, ...]:
    """节点静态参数引用的模型参数名（去重排序 → 确定性）。"""
    names = set()
    for bv in node.params.values.values():
        v = bv.value
        if isinstance(v, str) and v.startswith(_PARAM_REF_PREFIX):
            name = v[len(_PARAM_REF_PREFIX):]
            if name:
                names.add(name)
    return tuple(sorted(names))


def _resolve_wire_params(
    node: ModelNode, declared: Dict[str, Any]
) -> Dict[str, Any]:
    """节点参数 → wire params（$param 引用落 default；无 default 省略键）。"""
    out: Dict[str, Any] = {}
    for key, bv in list(node.params.values.items())[:_MAX_NODE_PARAMS]:
        v = bv.value
        if isinstance(v, str) and v.startswith(_PARAM_REF_PREFIX):
            spec = declared.get(v[len(_PARAM_REF_PREFIX):])
            if spec is None or spec.default is None:
                # required 无 default：运行时经 node_params 注入，键省略。
                continue
            v = spec.default.value
        if isinstance(v, str):
            v = v[:256]
        out[key] = v
    return out


def _wire_node_id(node: ModelNode) -> str:
    """作者节点 → wire node_id。

    transform 采用运行时权威格式 ``transform:<op>:<author>``（driver 的
    geocompute 适配器 ``node_executable_op`` 以 ``split(":")[1]`` 解析
    算子 —— node_id 是算子唯一事实源）；其余 kind 恒等（driver 对这些
    kind 不解析 id 段）。作者 slug 不含冒号，``rsplit(":", 1)[1]`` 可逆。
    """
    if node.kind == "transform":
        return f"{_TRANSFORM_WIRE_PREFIX}{node.transform_op}:{node.node_id}"
    return node.node_id


def _topo_order(nodes: List[TypedWorkflowNode], edges: List[TypedWorkflowEdge]) -> List[str]:
    """确定性拓扑序（Kahn；同层按 doc 顺序）。环已在 validate 阻断，此处
    兜底补齐（残环按 doc 序附加，绝不丢节点）。"""
    indeg = {n.node_id: 0 for n in nodes}
    adj: Dict[str, List[str]] = {}
    for e in edges:
        adj.setdefault(e.from_node, []).append(e.to_node)
        indeg[e.to_node] = indeg.get(e.to_node, 0) + 1
    ready = [n.node_id for n in nodes if indeg[n.node_id] == 0]
    order: List[str] = []
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for nxt in adj.get(nid, ()):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    if len(order) < len(nodes):
        placed = set(order)
        order.extend(n.node_id for n in nodes if n.node_id not in placed)
    return order


def _typed_node(
    node: ModelNode,
    edges: List[Tuple[str, str, str]],
    id_map: Dict[str, str],
) -> TypedWorkflowNode:
    """作者节点 → TypedWorkflowNode（kind 直映；cartography 是 driver 的
    独立分派 kind，wire 契约 kind 为自由 str，直接携带）。

    node_id 用 wire 形态（见 ``_wire_node_id``）；depends_on/边引用一律
    经 id_map 投影为 wire id，图内引用自洽。
    """
    ins, outs = node_ports(node)
    if node.kind == "data_input":
        at, gk = role_port_default(node.role)
        inputs: List[TypedPort] = []
        outputs = [TypedPort(name=node.role, artifact_type=at, geometry_kind=gk)]
    else:
        inputs = [
            TypedPort(name=p["name"], artifact_type=p["artifact_type"],
                      geometry_kind=p["geometry_kind"], required=True)
            for p in ins
        ]
        outputs = [
            TypedPort(name=p["name"], artifact_type=p["artifact_type"],
                      geometry_kind=p["geometry_kind"], required=True)
            for p in outs
        ]
    depends_on = tuple(sorted({
        id_map[f] for f, _t, _p in edges if _t == node.node_id
    }))[:8]
    return TypedWorkflowNode(
        node_id=id_map[node.node_id],
        kind=node.kind,
        capability=node.capability,
        role=node.role,
        inputs=inputs,
        outputs=outputs,
        depends_on=depends_on,
        optional=node.optional,
        parameters=_referenced_params(node),
    )


def compile_document(
    doc: ModelDocument,
    *,
    model_slug: str,
    version: str = "1.0.0",
) -> Tuple[Optional[CompiledModel], List[ModelFinding]]:
    """作者模型 → (CompiledModel | None, findings)。

    - ``model_slug`` 必须匹配 ``^[a-z][a-z0-9_]{2,40}$``（非法 ValueError）；
    - ``version`` 必须是严格 semver（非法 ValueError）；
    - validate 出现任一 error 级 finding → ``(None, findings)``（fail-closed，
      绝不半编译）。
    """
    if not _MODEL_SLUG_RE.fullmatch(model_slug or ""):
        raise ValueError(
            f"model_slug {model_slug!r} must match ^[a-z][a-z0-9_]{{2,40}}$")
    if parse_semver(version) is None:
        raise ValueError(f"version {version!r} is not strict semver")

    findings = validate_document(doc)
    if any(f.severity == SEVERITY_ERROR for f in findings):
        return None, findings

    declared = {p.name: p for p in doc.params}
    edge_triples = [(e.from_node, e.to_node, e.to_port or "in") for e in doc.edges]

    # author → wire node_id 桥（transform = transform:<op>:<author>）；
    # 先全量建表，depends_on/边引用统一经它投影（图内引用自洽）。
    id_map = {n.node_id: _wire_node_id(n) for n in doc.nodes}
    t_nodes = [_typed_node(n, edge_triples, id_map) for n in doc.nodes]
    t_edges = [
        TypedWorkflowEdge(
            from_node=id_map[e.from_node], from_port=e.from_port,
            to_node=id_map[e.to_node], to_port=resolved_to,
        )
        for e, (_f, _t, resolved_to) in zip(doc.edges, edge_triples)
    ]
    # 作者上限 32 节点 / 64 边保证 ≤ 运行时硬上界；防御性断言（超界是
    # 契约破坏，必须炸而不是静默截断）。
    assert len(t_nodes) <= _MAX_GRAPH_NODES, "authored graph exceeds runtime node cap"
    assert len(t_edges) <= _MAX_GRAPH_EDGES, "authored graph exceeds runtime edge cap"

    if doc.output_node_ids:
        primary = doc.output_node_ids[0]
    else:
        topo = _topo_order(t_nodes, t_edges)
        primary = topo[-1] if topo else ""
    graph = TypedWorkflowGraph(
        nodes=t_nodes, edges=t_edges, primary_output=primary,
        validation_violations=[],
    )

    # wire 形态 = typed graph 的 bounded dict，节点 dict 补 params
    # （WorkflowNode TypedDict 声明了 params 字段；to_bounded_dict 不含，
    # 由编译层补充 —— 运行时 adapter 经 node_params 消费）。
    wire = graph.to_bounded_dict()
    inv_map = {w: a for a, w in id_map.items()}
    wire_nodes: List[Dict[str, Any]] = []
    for t in t_nodes:
        author = next(n for n in doc.nodes if inv_map[t.node_id] == n.node_id)
        d = t.to_bounded_dict()
        d["params"] = _resolve_wire_params(author, declared)
        wire_nodes.append(d)
    wire["nodes"] = wire_nodes

    fingerprint = doc.document_fingerprint()
    compiled_form: Dict[str, Any] = {
        "schema_version": WORKFLOW_PACKAGE_SCHEMA_VERSION,
        "model": {
            "model_slug": model_slug,
            "name": doc.name[:120],
            "description": doc.description[:300],
            "source_kind": doc.source.kind,
            "source_ref": doc.source.ref[:255],
            "template_id": doc.template_id[:120],
            "document_fingerprint": fingerprint,
        },
        "typed_dag": wire,
        "parameters": [
            {
                "name": p.name,
                "kind": p.kind,
                "required": p.required,
                "default": (p.default.value if p.default is not None else None),
                "unit": p.unit,
            }
            for p in doc.params[:16]
        ],
        "model_fingerprint": fingerprint,
    }
    package = WorkflowPackage(
        package_id=(MODEL_PACKAGE_PREFIX + model_slug)[:64],
        version=version,
        methodology_family="authored_model",
        recipe_fingerprint="",
        methodology_fingerprint="",
        compiled_form=compiled_form,
    )
    package.fingerprint = _canonical_fingerprint(package.compiled_form)
    return (
        CompiledModel(
            typed_graph=graph, package=package, findings=findings,
            node_id_map=id_map,
        ),
        findings,
    )


def roundtrip_projection(compiled: CompiledModel) -> Dict[str, Any]:
    """编译产物 → 作者可读投影（UI 双向编辑对账）。

    从 typed wire 图还原 node_id / kind / capability|role|op / params /
    端口类型与边，全部以**作者 id** 表达（经 CompiledModel.node_id_map
    反解；transform 算子从 wire node_id 第二段读取 —— 与 driver 的
    ``split(":")[1]`` 同一解析，单一事实源）。**不做** ModelDocument 还原
    —— 有损项（layout/title/description 等注记）显式记录在 ``lossy``，
    不伪装无损。
    """
    wire = compiled.package.compiled_form.get("typed_dag") or {}
    inv_map = {w: a for a, w in compiled.node_id_map.items()}

    def _author(node_id: str) -> str:
        return inv_map.get(node_id, node_id)

    nodes: List[Dict[str, Any]] = []
    for n in wire.get("nodes") or []:
        params = n.get("params") or {}
        wire_id = n.get("node_id", "")
        entry: Dict[str, Any] = {
            "node_id": _author(wire_id),
            "kind": n.get("kind", ""),
            "params": dict(params),
            "inputs": [
                {"name": p.get("name", ""), "artifact_type": p.get("artifact_type", "")}
                for p in (n.get("inputs") or [])
            ],
            "outputs": [
                {"name": p.get("name", ""), "artifact_type": p.get("artifact_type", "")}
                for p in (n.get("outputs") or [])
            ],
        }
        kind = entry["kind"]
        if kind == "analysis":
            entry["capability"] = n.get("capability", "")
        elif kind == "data_input":
            entry["role"] = n.get("role", "")
        elif kind == "transform":
            # 与 driver.node_executable_op 同一解析（split(":")[1]）。
            entry["op"] = wire_id.split(":")[1] if wire_id.count(":") >= 1 else ""
        nodes.append(entry)
    edges = []
    for e in (wire.get("edges") or []):
        f_node, _, f_port = str(e.get("from", "")).rpartition(".")
        t_node, _, t_port = str(e.get("to", "")).rpartition(".")
        edges.append({
            "from": f"{_author(f_node)}.{f_port}",
            "to": f"{_author(t_node)}.{t_port}",
        })
    return {
        "primary_output": wire.get("primary_output", ""),
        "nodes": nodes,
        "edges": edges,
        "lossy": list(_ROUNDTRIP_LOSSY),
    }


__all__ = [
    "MODEL_PACKAGE_PREFIX",
    "CompiledModel",
    "compile_document",
    "roundtrip_projection",
]
