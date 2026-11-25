"""模型作者域静态验证器（ModelDocument → List[ModelFinding]）。

纯函数、确定性：同输入同 findings（排序键 (severity, node_id, code)）。
验证面：

- 结构：空图 / 环 / 边端口存在性 / 输出汇点与可达性 / 悬空节点；
- 数据角色：role ⊆ workflow_schema.DATA_ROLES（惰性导入，单一事实源）；
- capability：registry 存在性 / native 状态 / 钉定输入类型 ∈ 接受集
  （接受集经本模块惰性只读投影，不改 contracts.py）；
- 参数：必填缺省、$param 引用、默认值类型与声明约束（min/max/枚举/
  时间窗/单位）；
- 执行面：可执行节点必须有数据入边；table 族角色必须有 analysis 消费者。

error 级 finding 阻断编译（compile.py 是唯一消费者守门员）；warning 级
只披露不阻断。端口类型事实全部经 contracts.node_ports 投影（单一来源），
本模块不实现第二份端口语义。
"""
from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple

from app.schemas.model_authoring_schema import (
    EXECUTABLE_TRANSFORM_OPS,
    ModelDocument,
    ModelEdge,
    ModelNode,
    ModelParamSpec,
)
from app.services.model_authoring.contracts import (
    F_CAPABILITY_NOT_NATIVE,
    F_CAPABILITY_UNKNOWN,
    F_CYCLE,
    F_DATA_ROLE_BLOCKED,
    F_EMPTY_GRAPH,
    F_EDGE_PORT_UNKNOWN,
    F_GEOMETRY_MISMATCH,
    F_INPUT_ARTIFACT_MISMATCH,
    F_INPUT_NODE_MISSING,
    F_MULTIPLE_INPUT_SAME_ROLE,
    F_NODE_UNREACHABLE,
    F_OUTPUT_NOT_SINK,
    F_OUTPUT_UNREACHABLE,
    F_PARAM_INVALID_VALUE,
    F_PARAM_REF_UNKNOWN,
    F_PARAM_REQUIRED_MISSING_DEFAULT,
    F_PARAM_TYPE_MISMATCH,
    F_PORT_TYPE_MISMATCH,
    F_ROLE_UNKNOWN,
    F_TRANSFORM_OP_UNKNOWN,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    ModelFinding,
    capability_status,
    node_input_ports,
    node_output_ports,
    ports_compatible,
)

#: 节点静态值引用模型参数的前缀（schema 同词）。
_PARAM_REF_PREFIX = "$param:"

#: time_window 默认值形态：ISO-8601 区间 "start/end"。
_TIME_WINDOW_RE = re.compile(r"^\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}$")

#: table 族数据角色（stats_table 端口；无 analysis 消费者 → warning 披露）。
_TABLE_ROLES = ("measure", "denominator", "population")


def _edge_label(edge: ModelEdge) -> str:
    """边定位标签（wire 契约同形：from.port->to.port）。"""
    return f"{edge.from_node}.{edge.from_port}->{edge.to_node}.{edge.to_port}"


def _find_cycle(nodes: List[ModelNode], edges: List[ModelEdge]) -> List[str]:
    """DFS 三色找环（doc 顺序迭代 → 确定性）。

    返回环节点序列（首尾同为回头点）；无环返回 []。节点 ≤32，递归深度
    有界（作者上限保证，见 schema._nodes_cap）。
    """
    adj: Dict[str, List[str]] = {}
    for e in edges:
        adj.setdefault(e.from_node, []).append(e.to_node)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {n.node_id: WHITE for n in nodes}
    stack: List[str] = []
    found: List[str] = []

    def _visit(nid: str) -> bool:
        color[nid] = GRAY
        stack.append(nid)
        for nxt in adj.get(nid, ()):
            state = color.get(nxt, BLACK)
            if state == GRAY:
                idx = stack.index(nxt)
                found.extend(stack[idx:] + [nxt])
                return True
            if state == WHITE and _visit(nxt):
                return True
        stack.pop()
        color[nid] = BLACK
        return False

    for n in nodes:
        if color[n.node_id] == WHITE and _visit(n.node_id):
            break
    return found


def _reachable_from_inputs(nodes: List[ModelNode], edges: List[ModelEdge]) -> Set[str]:
    """从全部 data_input 前向可达的节点集（边方向 from → to）。"""
    adj: Dict[str, List[str]] = {}
    for e in edges:
        adj.setdefault(e.from_node, []).append(e.to_node)
    seen: Set[str] = set()
    stack = [n.node_id for n in nodes if n.kind == "data_input"]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend(adj.get(nid, ()))
    return seen


def _blocking_upstream(
    node_id: str, nodes: List[ModelNode], edges: List[ModelEdge]
) -> List[str]:
    """不可达 output 的阻断上游：其祖先链中无入边且非 data_input 的根。"""
    radj: Dict[str, List[str]] = {}
    for e in edges:
        radj.setdefault(e.to_node, []).append(e.from_node)
    ancestors: Set[str] = set()
    stack = [node_id]
    while stack:
        nid = stack.pop()
        if nid in ancestors:
            continue
        ancestors.add(nid)
        stack.extend(radj.get(nid, ()))
    has_in = {e.to_node for e in edges}
    kinds = {n.node_id: n.kind for n in nodes}
    return sorted(
        nid for nid in ancestors
        if nid != node_id and nid not in has_in and kinds.get(nid) != "data_input"
    )


def _capability_accepted(capability: str) -> List[str]:
    """capability → 接受的输入 artifact 类型集（只读惰性投影）。

    与 contracts._cap_io 同一 registry 只读纪律（不复制语义，不修改
    contracts.py）；registry 不可用/未知 → 诚实空集（由
    CAPABILITY_UNKNOWN 报告，此处不再重复裁决）。
    """
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        desc = get_capability_registry().get(capability)
        if desc is None:
            return []
        return [str(t) for t in (desc.input_artifact_types or [])]
    except Exception:  # noqa: BLE001 — 只读投影，失败诚实空
        return []


def _data_roles() -> Tuple[str, ...]:
    """DATA_ROLES 惰性导入（workflow_schema 单一事实源）；失败 → 空词表
    （fail-closed：一切角色按未知裁决，绝不放行）。"""
    try:
        from app.services.gis_harness.workflow_schema import DATA_ROLES

        return tuple(DATA_ROLES)
    except Exception:  # noqa: BLE001 — fail-closed 空词表
        return ()


def _check_param_default(spec: ModelParamSpec, findings: List[ModelFinding]) -> None:
    """参数默认值合法性（按 kind 校验类型与声明约束）。

    - 类型不符 → F_PARAM_TYPE_MISMATCH；
    - 类型相符但违反声明约束（min/max/choices/时间窗/单位长度）
      → F_PARAM_INVALID_VALUE。
    """
    if spec.default is None:
        return
    value = spec.default.value
    kind = spec.kind

    def _type(detail: str) -> None:
        findings.append(ModelFinding(
            code=F_PARAM_TYPE_MISMATCH, severity=SEVERITY_ERROR,
            param=spec.name, detail=f"kind={kind}: {detail}"))

    def _invalid(detail: str) -> None:
        findings.append(ModelFinding(
            code=F_PARAM_INVALID_VALUE, severity=SEVERITY_ERROR,
            param=spec.name, detail=detail))

    if kind in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _type(f"default {value!r} is not numeric")
            return
        if kind == "integer" and isinstance(value, float) and not value.is_integer():
            _type(f"default {value!r} is not an integral value")
            return
        if spec.minimum is not None and value < spec.minimum:
            _invalid(f"default {value} < minimum {spec.minimum}")
        elif spec.maximum is not None and value > spec.maximum:
            _invalid(f"default {value} > maximum {spec.maximum}")
    elif kind == "enum":
        if not isinstance(value, str):
            _type(f"enum default {value!r} is not a string")
        elif value not in spec.choices:
            _invalid(f"default {value!r} not in choices {list(spec.choices)[:8]}")
    elif kind == "boolean":
        if not isinstance(value, bool):
            _type(f"boolean default {value!r} is not bool")
    elif kind == "time_window":
        if not isinstance(value, str):
            _type(f"time_window default {value!r} is not a string")
        elif value and not _TIME_WINDOW_RE.fullmatch(value):
            _invalid("default must be 'YYYY-MM-DD/YYYY-MM-DD' or empty")
    elif kind in ("unit", "string", "field"):
        if not isinstance(value, str):
            _type(f"{kind} default {value!r} is not a string")
        elif kind == "unit" and len(value) > 24:
            _invalid(f"unit default exceeds 24 chars: {len(value)}")
    # kind=string/field 其余约束（字段名存在性）是实例态事实，静态不裁决。


def validate_document(doc: ModelDocument) -> List[ModelFinding]:
    """ModelDocument → typed findings（确定性排序 (severity, node_id, code)）。

    纯函数：零 I/O、零 LLM；error 级阻断编译，warning 级仅披露。
    """
    findings: List[ModelFinding] = []
    nodes = doc.nodes
    edges = doc.edges
    by_id = {n.node_id: n for n in nodes}

    def _err(code: str, *, node_id: str = "", edge: str = "", param: str = "",
             detail: str = "") -> None:
        findings.append(ModelFinding(
            code=code, severity=SEVERITY_ERROR, node_id=node_id,
            edge=edge, param=param, detail=detail))

    def _warn(code: str, *, node_id: str = "", edge: str = "", param: str = "",
             detail: str = "") -> None:
        findings.append(ModelFinding(
            code=code, severity=SEVERITY_WARNING, node_id=node_id,
            edge=edge, param=param, detail=detail))

    # ── 1. 空图 ──────────────────────────────────────────────────────
    if not nodes:
        _err(F_EMPTY_GRAPH, detail="document has no nodes")

    # ── 2. 环（DFS 三色；node_id = 环上任一节点）──────────────────────
    cycle = _find_cycle(nodes, edges)
    if cycle:
        shown = " -> ".join(cycle[:5]) + (" -> ..." if len(cycle) > 5 else "")
        _err(F_CYCLE, node_id=cycle[0], detail=f"cycle: {shown}")

    # ── 3./9. 边端口存在性 + 端口类型/几何兼容 ─────────────────────────
    for e in edges:
        src = by_id[e.from_node]
        dst = by_id[e.to_node]
        label = _edge_label(e)
        if dst.kind == "data_input":
            _err(F_EDGE_PORT_UNKNOWN, node_id=e.from_node, edge=label,
                 detail=(f"edge targets data_input node {e.to_node!r}: "
                         "data cannot flow into a data input"))
            continue
        out_ports = node_output_ports(src)
        in_ports = node_input_ports(dst)
        if e.from_port not in out_ports:
            _err(F_EDGE_PORT_UNKNOWN, node_id=e.from_node, edge=label,
                 detail=(f"source output port {e.from_port!r} not in "
                         f"{sorted(out_ports)}"))
            continue
        to_port = e.to_port or "in"
        if to_port not in in_ports:
            _err(F_EDGE_PORT_UNKNOWN, node_id=e.to_node, edge=label,
                 detail=(f"target input port {to_port!r} not in "
                         f"{sorted(in_ports)}"))
            continue
        sp = out_ports[e.from_port]
        dp = in_ports[to_port]
        if not ports_compatible(sp["artifact_type"], dp["artifact_type"]):
            _err(F_PORT_TYPE_MISMATCH, node_id=e.from_node, edge=label,
                 detail=(f"{sp['artifact_type']} -> {dp['artifact_type']} "
                         f"({e.from_node}.{e.from_port} -> "
                         f"{e.to_node}.{to_port})"))
        sg, dg = sp["geometry_kind"], dp["geometry_kind"]
        if sg not in ("unknown", "table") and dg not in ("unknown", "table") \
                and sg != dg:
            _err(F_GEOMETRY_MISMATCH, node_id=e.from_node, edge=label,
                 detail=f"{sg} -> {dg}")

    # 可达性/度数（供检查 4/5/11/12 共用）
    reachable = _reachable_from_inputs(nodes, edges)
    incoming: Set[str] = {e.to_node for e in edges}
    has_outgoing: Set[str] = {e.from_node for e in edges}

    # ── 4. output_node_ids ───────────────────────────────────────────
    if not doc.output_node_ids:
        _warn(F_OUTPUT_UNREACHABLE, detail="no explicit outputs")
    else:
        for oid in doc.output_node_ids:
            if oid in has_outgoing:
                _err(F_OUTPUT_NOT_SINK, node_id=oid,
                     detail="output node has outgoing edges")
            if oid not in reachable:
                blocking = _blocking_upstream(oid, nodes, edges)
                shown = ", ".join(blocking[:5])
                more = f" (+{len(blocking) - 5} more)" if len(blocking) > 5 else ""
                _err(F_OUTPUT_UNREACHABLE, node_id=oid,
                     detail=("not reachable from any data_input; "
                             f"dangling upstream: {shown or '(none)'}{more}"))

    # ── 5. 悬空节点（非 data_input 且无入边）───────────────────────────
    for n in nodes:
        if n.kind != "data_input" and n.node_id not in incoming:
            _warn(F_NODE_UNREACHABLE, node_id=n.node_id,
                  detail="no incoming edges")

    # ── 6. data_input 角色 ───────────────────────────────────────────
    valid_roles = _data_roles()
    seen_roles: Dict[str, str] = {}
    for n in nodes:
        if n.kind != "data_input":
            continue
        if n.role not in valid_roles:
            _err(F_ROLE_UNKNOWN, node_id=n.node_id,
                 detail=f"role {n.role!r} not in DATA_ROLES")
        elif n.role in seen_roles:
            _err(F_MULTIPLE_INPUT_SAME_ROLE, node_id=n.node_id,
                 detail=(f"role {n.role!r} already declared by "
                         f"{seen_roles[n.role]}"))
        else:
            seen_roles[n.role] = n.node_id

    # ── 7./8. capability / transform 词表 ────────────────────────────
    for n in nodes:
        if n.kind == "analysis":
            status = capability_status(n.capability)
            if status == "":
                _err(F_CAPABILITY_UNKNOWN, node_id=n.node_id,
                     detail=f"capability {n.capability!r} not in registry")
                continue
            if status != "native":
                _err(F_CAPABILITY_NOT_NATIVE, node_id=n.node_id,
                     detail=f"capability {n.capability!r} status={status}")
            accepted = _capability_accepted(n.capability)
            if n.input_artifact_type and accepted \
                    and n.input_artifact_type not in accepted:
                _err(F_INPUT_ARTIFACT_MISMATCH, node_id=n.node_id,
                     detail=(f"pinned {n.input_artifact_type!r} not in "
                             f"accepted {accepted[:4]}"))
        elif n.kind == "transform":
            # schema 已限定词表；此处防御性再查（词表漂移时 fail-closed）。
            if n.transform_op not in EXECUTABLE_TRANSFORM_OPS:
                _err(F_TRANSFORM_OP_UNKNOWN, node_id=n.node_id,
                     detail=(f"op {n.transform_op!r} not in "
                             f"{list(EXECUTABLE_TRANSFORM_OPS)}"))

    # ── 10. 参数 ─────────────────────────────────────────────────────
    declared = {p.name: p for p in doc.params}
    referenced: Set[str] = set()
    for n in nodes:
        for _key, bv in n.params.values.items():
            v = bv.value
            if isinstance(v, str) and v.startswith(_PARAM_REF_PREFIX):
                name = v[len(_PARAM_REF_PREFIX):]
                if name not in declared:
                    _err(F_PARAM_REF_UNKNOWN, node_id=n.node_id, param=name,
                         detail="references undeclared model param")
                else:
                    referenced.add(name)
    for p in doc.params:
        if p.required and p.default is None:
            if p.name in referenced:
                _warn(F_PARAM_REQUIRED_MISSING_DEFAULT, param=p.name,
                      detail="required without default; provided at instantiation")
            else:
                _err(F_PARAM_REF_UNKNOWN, param=p.name,
                     detail="unreferenced param")
        _check_param_default(p, findings)

    # ── 11. 可执行节点缺数据入边 ──────────────────────────────────────
    for n in nodes:
        if n.kind in ("analysis", "transform", "cartography") \
                and n.node_id not in incoming:
            _err(F_INPUT_NODE_MISSING, node_id=n.node_id,
                 detail=f"{n.kind} node has no data input edge")

    # ── 12. table 族角色缺 analysis 消费者 ───────────────────────────
    for n in nodes:
        if n.kind == "data_input" and n.role in _TABLE_ROLES:
            consumed = any(
                e.from_node == n.node_id
                and by_id[e.to_node].kind == "analysis"
                for e in edges
            )
            if not consumed:
                _warn(F_DATA_ROLE_BLOCKED, node_id=n.node_id,
                      detail="table role has no analysis consumer")

    # 确定性排序：主键 (severity, node_id, code)，其余字段为稳定 tie-break。
    findings.sort(key=lambda f: (
        f.severity, f.node_id, f.code, f.edge, f.param, f.detail))
    return findings


__all__ = [
    "validate_document",
]
