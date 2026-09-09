"""Typed Workflow DAG —— 类型化工作流图（Semantic Workflow Compiler V4）。

把「capability 字符串列表」的 plan 投影升级为**类型化**工作流图：节点
端口携带 artifact 语义类型、几何族、CRS 要求与单位要求；边是端口到端口
的类型化连接；节点可声明条件分支、回退关系与并行安全。

红线：

- 类型事实全部引用既有单一事实源：artifact 语义类型 ⊆
  ArtifactTypeRegistry（geometry_kind 来自注册描述）、CRS/单位要求引用
  算法层 AlgorithmDescriptor 与 scientific precondition 词表（不另造
  词表）；
- 本模块只构建与校验图，不执行、不调度（执行归 Harness runtime）；
- 全部确定性：同输入同图，零 LLM、零 I/O；产物可序列化且有界。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

#: 节点种类（稳定词表）。
TYPED_NODE_KINDS = (
    "data_input",    # 数据角色输入（role resolution 物化）
    "transform",     # 显式修复/变换 step（数据资格 remediation 物化）
    "analysis",      # 能力/方法分析节点
    "subworkflow",   # 组合/嵌套工作流引用（package id）
    "output",        # 产品/工件输出
)

#: 端口 CRS 要求词表 —— 引用算法层 CRSSpatialClass Literal（crs_safety.py
#: 单一事实源，经 get_args 展开为词表）；空 = 无 CRS 要求。
from typing import get_args as _get_args

from app.lib.gis.crs_safety import CRSSpatialClass  # noqa: E402

TYPED_CRS_REQUIREMENTS = _get_args(CRSSpatialClass)

#: 通用工件类型：接受任意 feature_set 输入的宽端口。
_PORT_TYPE_ANY_FEATURE = "feature_collection"

_MAX_GRAPH_NODES = 64
_MAX_GRAPH_EDGES = 128


class TypedPort(BaseModel):
    """类型化端口：一个节点的类型化输入/输出。"""
    name: str
    artifact_type: str = ""            # ⊆ ArtifactTypeRegistry（构建期校验）
    geometry_kind: str = "unknown"     # point/line/polygon/raster/table/network/unknown
    crs_requirement: str = ""          # ⊆ TYPED_CRS_REQUIREMENTS
    unit_requirement: str = ""         # 有界文本（来自 AlgorithmDescriptor.unit_requirements）
    required: bool = True

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name[:48],
            "artifact_type": self.artifact_type[:48],
            "geometry_kind": self.geometry_kind[:16],
            "crs_requirement": self.crs_requirement[:48],
            "unit_requirement": self.unit_requirement[:48],
            "required": self.required,
        }


class TypedWorkflowNode(BaseModel):
    """类型化工作流节点。"""
    node_id: str                       # "<kind>:<capability|role|artifact>"
    kind: str                          # ⊆ TYPED_NODE_KINDS
    capability: str = ""               # analysis 节点 ⊆ CapabilityRegistry
    algorithm_id: str = ""             # 方法裁决的算法（⊆ AlgorithmRegistry）
    method_id: str = ""                # ⊆ methodology registry
    role: str = ""                     # data_input 节点的数据角色
    subworkflow_package_id: str = ""   # subworkflow 节点的 package 引用
    inputs: List[TypedPort] = Field(default_factory=list)
    outputs: List[TypedPort] = Field(default_factory=list)
    depends_on: Tuple[str, ...] = ()   # 上游 node_id（结构依赖）
    conditional: str = ""              # 条件分支码（空 = 无条件）
    fallback_of: str = ""              # 该节点是谁的回退（空 = 非回退）
    parallel_safe: bool = False        # 无副作用/输入独立 → 可并行
    optional: bool = False
    parameters: Tuple[str, ...] = ()   # 该节点拥有的工作流参数名（recompute 消费）

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id[:64],
            "kind": self.kind,
            "capability": self.capability[:64],
            "algorithm_id": self.algorithm_id[:64],
            "method_id": self.method_id[:64],
            "role": self.role[:32],
            "depends_on": list(self.depends_on[:8]),
            "conditional": self.conditional[:64],
            "fallback_of": self.fallback_of[:64],
            "parallel_safe": self.parallel_safe,
            "optional": self.optional,
            "parameters": [{"name": p[:48]} for p in self.parameters[:8]],
            "inputs": [p.to_bounded_dict() for p in self.inputs[:6]],
            "outputs": [p.to_bounded_dict() for p in self.outputs[:6]],
        }


class TypedWorkflowEdge(BaseModel):
    """类型化边：from 端口 → to 端口。"""
    from_node: str
    from_port: str
    to_node: str
    to_port: str

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "from": f"{self.from_node[:48]}.{self.from_port[:32]}",
            "to": f"{self.to_node[:48]}.{self.to_port[:32]}",
        }


class TypedWorkflowGraph(BaseModel):
    """类型化工作流图（可校验、可序列化、有界）。"""
    nodes: List[TypedWorkflowNode] = Field(default_factory=list)
    edges: List[TypedWorkflowEdge] = Field(default_factory=list)
    primary_output: str = ""           # 主产品输出 node_id
    validation_violations: List[str] = Field(default_factory=list)

    def node(self, node_id: str) -> Optional[TypedWorkflowNode]:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def downstream(self, node_id: str) -> List[str]:
        """直接下游（边视图）。"""
        return sorted({e.to_node for e in self.edges if e.from_node == node_id})

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [n.to_bounded_dict() for n in self.nodes[:_MAX_GRAPH_NODES]],
            "edges": [e.to_bounded_dict() for e in self.edges[:_MAX_GRAPH_EDGES]],
            "primary_output": self.primary_output[:64],
            "validation_violations": [
                v[:120] for v in self.validation_violations[:8]],
        }


# ── 端口类型兼容性（结构性；科学性由资格层/算法层负责）──────────────────

def _artifact_geometry(artifact_type: str) -> str:
    """artifact 类型 → 几何族（ArtifactTypeRegistry 注册描述；未知 → unknown）。"""
    from app.lib.gis.artifacts import get_artifact_type_registry
    reg = get_artifact_type_registry()
    desc = reg.get(artifact_type) if hasattr(reg, "get") else None
    return desc.geometry_kind if desc is not None else "unknown"


def ports_compatible(src: TypedPort, dst: TypedPort) -> bool:
    """边两端的类型兼容裁决（确定性，宽松方向：宽端口收窄类型）。

    - dst.artifact_type 为空（未约束）→ 兼容；
    - dst 为通用要素宽端口 → 接受任意 feature_set 类源；
    - 否则 artifact_type 必须相等；
    - 几何：任一端 unknown 或相等 → 兼容。
    """
    if not dst.artifact_type:
        return True
    if src.artifact_type and src.artifact_type == dst.artifact_type:
        pass
    elif dst.artifact_type == _PORT_TYPE_ANY_FEATURE:
        from app.lib.gis.artifacts import get_artifact_type_registry
        reg = get_artifact_type_registry()
        desc = reg.get(src.artifact_type) if src.artifact_type else None
        if desc is not None and desc.category != "feature_set":
            return False
    elif src.artifact_type:
        return False
    if src.geometry_kind != "unknown" and dst.geometry_kind != "unknown" \
            and src.geometry_kind != dst.geometry_kind:
        return False
    return True


# ── 图校验 ───────────────────────────────────────────────────────────────

def validate_typed_dag(graph: TypedWorkflowGraph) -> List[str]:
    """结构性校验，返回违规列表（空 = 通过）。纯函数。

    - 引用完整性：depends_on / 边端点必须指向存在节点；
    - 边类型兼容：ports_compatible；
    - 有向无环；
    - 主输出可达：所有无出边的 output 节点均可从 data_input 到达；
    - 词表：kind ⊆ TYPED_NODE_KINDS。
    """
    violations: List[str] = []
    ids = [n.node_id for n in graph.nodes]
    if len(ids) != len(set(ids)):
        violations.append("TYPED_DAG_DUPLICATE_NODE_ID")
    by_id = {n.node_id: n for n in graph.nodes}
    for n in graph.nodes:
        if n.kind not in TYPED_NODE_KINDS:
            violations.append(f"TYPED_DAG_UNKNOWN_KIND:{n.node_id}:{n.kind}")
        for dep in n.depends_on:
            if dep not in by_id:
                violations.append(f"TYPED_DAG_DANGLING_DEP:{n.node_id}:{dep}")
        if n.fallback_of and n.fallback_of not in by_id:
            violations.append(f"TYPED_DAG_DANGLING_FALLBACK:{n.node_id}")
    for e in graph.edges:
        src, dst = by_id.get(e.from_node), by_id.get(e.to_node)
        if src is None or dst is None:
            violations.append(f"TYPED_DAG_DANGLING_EDGE:{e.from_node}:{e.to_node}")
            continue
        sp = next((p for p in src.outputs if p.name == e.from_port), None)
        dp = next((p for p in dst.inputs if p.name == e.to_port), None)
        if sp is None or dp is None:
            violations.append(
                f"TYPED_DAG_UNKNOWN_PORT:{e.from_node}.{e.from_port}:"
                f"{e.to_node}.{e.to_port}")
        elif not ports_compatible(sp, dp):
            violations.append(
                f"TYPED_DAG_PORT_TYPE_MISMATCH:{e.from_node}.{e.from_port}"
                f"({sp.artifact_type}/{sp.geometry_kind})>"
                f"{e.to_node}.{e.to_port}({dp.artifact_type}/{dp.geometry_kind})")

    # 有向无环（DFS 三色）
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in ids}
    adj: Dict[str, List[str]] = {}
    for e in graph.edges:
        adj.setdefault(e.from_node, []).append(e.to_node)

    def _visit(nid: str) -> None:
        color[nid] = GRAY
        for nxt in adj.get(nid, ()):
            c = color.get(nxt, BLACK)
            if c == GRAY:
                violations.append(f"TYPED_DAG_CYCLE:{nid}>{nxt}")
            elif c == WHITE:
                _visit(nxt)
        color[nid] = BLACK

    for nid in ids:
        if color[nid] == WHITE:
            _visit(nid)

    # 主输出可达性：从所有 data_input 出发的可达集
    reachable: set = set()

    def _reach(nid: str) -> None:
        if nid in reachable:
            return
        reachable.add(nid)
        for nxt in adj.get(nid, ()):
            _reach(nxt)

    for n in graph.nodes:
        if n.kind == "data_input":
            _reach(n.node_id)
    for n in graph.nodes:
        if n.kind == "output" and n.node_id not in reachable:
            violations.append(f"TYPED_DAG_UNREACHABLE_OUTPUT:{n.node_id}")
    if graph.primary_output and graph.primary_output not in by_id:
        violations.append(
            f"TYPED_DAG_DANGLING_PRIMARY_OUTPUT:{graph.primary_output}")
    return violations


# ── 构建（确定性：plan steps + 方法裁决 + 数据角色 → typed 图）───────────

def _analysis_ports(
    capability: str, algorithm_id: str,
) -> Tuple[List[TypedPort], List[TypedPort]]:
    """从算法描述符投影类型化端口（算法层 = 端口类型单一事实源）。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    reg = get_algorithm_registry()
    algo = reg.get(algorithm_id) if algorithm_id else None
    if algo is None:
        for candidate in reg.algorithms_for_capability(capability):
            algo = candidate
            break
    if algo is None:
        in_p = [TypedPort(name="input", artifact_type="",
                          geometry_kind="unknown")]
        out_p = [TypedPort(name="output", artifact_type="")]
        return in_p, out_p
    input_types = list(algo.input_artifact_types[:2]) or [""]
    inputs = [
        TypedPort(
            name="input" if len(input_types) == 1 else f"input{i + 1}",
            artifact_type=t,
            geometry_kind=_artifact_geometry(t) if t else "unknown",
            crs_requirement=str(algo.crs_class or ""),
            unit_requirement=str(algo.unit_requirements or "")[:48],
        )
        for i, t in enumerate(input_types)
    ]
    out_type = algo.output_artifact_type or ""
    outputs = [TypedPort(
        name="output", artifact_type=out_type,
        geometry_kind=_artifact_geometry(out_type) if out_type else "unknown",
    )]
    return inputs, outputs


def build_typed_dag(
    analysis_steps: Sequence[Any],
    *,
    data_roles: Sequence[Any],
    selected_method: Any = None,
    extra_transforms: Sequence[Dict[str, Any]] = (),
    extra_roles: Sequence[str] = (),
    primary_output_artifact: str = "",
) -> TypedWorkflowGraph:
    """从 plan 分析步骤 + 数据角色解析 + 方法裁决确定性构建 typed DAG。

    - 每个 resolved/bound 数据角色 → data_input 节点（role 产出端口类型
      取角色绑定的 capability 产出；未知 → 宽端口）；
    - 每个分析步骤 → analysis 节点（方法候选选中时带 method_id /
      algorithm_id；端口从算法描述符投影）；
    - remediation transform（auto_applicable）→ transform 节点；
    - ``extra_roles``（方法族角色诉求）并入规划期输入（unknown 态入图
      —— 家族级数据需求是方法论声明的"应考虑"集合）；
    - 选中方法的 output_artifacts → output 节点（有 plan 真实产出者才
      入边，防类型失配）；
    - 边：角色/变换 → 方法声明消费它的分析节点（数据流），结构
      depends_on 仅作顺序约束元数据。
    """
    nodes: List[TypedWorkflowNode] = []
    edges: List[TypedWorkflowEdge] = []

    method_outputs: List[str] = list(getattr(selected_method, "output_artifacts", ()) or [])
    method_id = str(getattr(selected_method, "method_id", "") or "")
    method_roles: Tuple[str, ...] = tuple(
        getattr(selected_method, "requires_roles", ()) or ())

    # ── data_input 节点 ────────────────────────────────────────────────
    # 输入 = 角色解析 ∪ 选中方法声明消费的角色（方法论声明数据需求；
    # 未解析角色按 unknown 入图 —— 规划期 unknown 是诚实事实）。
    resolved_roles: Dict[str, str] = {}
    for r in data_roles:
        role = str(getattr(r, "role", "") or (r.get("role") if isinstance(r, dict) else ""))
        status = str(getattr(r, "status", "") or (r.get("status") if isinstance(r, dict) else ""))
        if role and status != "degraded":
            resolved_roles[role] = status or "unknown"
    for role in method_roles:
        resolved_roles.setdefault(role, "unknown")
    for role in extra_roles:
        resolved_roles.setdefault(str(role), "unknown")
    present_roles: List[str] = []
    for role in sorted(resolved_roles):
        present_roles.append(role)
        nodes.append(TypedWorkflowNode(
            node_id=f"data:{role}", kind="data_input", role=role,
            outputs=[TypedPort(
                name="data", artifact_type="",
                geometry_kind="unknown", required=True,
            )],
            parallel_safe=True,
        ))

    # ── transform 节点（remediation 物化；role → transform 链）────────
    transform_for_role: Dict[str, str] = {}
    for t in extra_transforms:
        op = str(t.get("operation") or "")
        if not op:
            continue
        role = str(t.get("role") or "")
        node_id = f"transform:{op}:{role}"
        nodes.append(TypedWorkflowNode(
            node_id=node_id, kind="transform",
            role=role, parallel_safe=False,
            inputs=[TypedPort(name="input", artifact_type="")],
            outputs=[TypedPort(name="output", artifact_type="")],
            depends_on=(f"data:{role}",) if role else (),
        ))
        if role:
            edges.append(TypedWorkflowEdge(
                from_node=f"data:{role}", from_port="data",
                to_node=node_id, to_port="input"))
            # 同角色多个修复取最后一个（声明序 = 修复管线序）
            transform_for_role[role] = node_id

    # ── analysis 节点 ─────────────────────────────────────────────────
    def _role_source(role: str) -> Optional[str]:
        """role 的供给节点：有修复链走链尾，否则走原始 data_input。"""
        if role in transform_for_role:
            return transform_for_role[role]
        return f"data:{role}" if role in present_roles else None

    for s in analysis_steps:
        capability = str(getattr(s, "capability", "") or s.get("capability", ""))
        if not capability:
            continue
        status = str(getattr(s, "status", "") or s.get("status", ""))
        if status == "unavailable":
            continue
        alg = str(getattr(s, "resolved_algorithm", "") or
                  (s.get("resolved_algorithm") if isinstance(s, dict) else "") or "")
        raw_deps = tuple(
            str(d) for d in (getattr(s, "depends_on", None) or
                             (s.get("depends_on") if isinstance(s, dict) else []) or ())
        )
        deps = tuple(f"cap:{d}" for d in raw_deps)
        inputs, outputs = _analysis_ports(capability, alg)
        optional = bool(getattr(s, "optional", False) or
                        (s.get("optional") if isinstance(s, dict) else False))
        nodes.append(TypedWorkflowNode(
            node_id=f"cap:{capability}", kind="analysis",
            capability=capability, algorithm_id=alg, method_id=method_id,
            inputs=inputs, outputs=outputs, depends_on=deps,
            optional=optional,
            parallel_safe=len(deps) <= 1 and not optional,
        ))
        # 方法声明角色消费 → role/transform 供给边（方法契约驱动接线）
        for role in method_roles:
            src = _role_source(role)
            if src is not None:
                edges.append(TypedWorkflowEdge(
                    from_node=src, from_port=(
                        "output" if src.startswith("transform:")
                        else "data"),
                    to_node=f"cap:{capability}", to_port=inputs[0].name))
        # 结构依赖 = 执行顺序约束（仅节点元数据，不生成数据流边 —— 类型
        # 兼容只在真实数据流边上裁决；悬空依赖由 validate_typed_dag 拦截）。

    # ── 连通性兜底（MAJOR-4）：方法未声明 requires_roles 时，无任何
    # 数据流入边的 analysis 节点接入全部角色供给 —— 保证图从输入侧可达
    # （确定性：按节点声明序处理）。方法声明角色优先，兜底只补零入度。
    def _has_incoming(node_id: str) -> bool:
        return any(e.to_node == node_id for e in edges)

    if not method_roles:
        all_sources = [
            src for src in (
                _role_source(role) for role in sorted(resolved_roles)
            ) if src is not None
        ]
        for n in [x for x in nodes if x.kind == "analysis"]:
            if _has_incoming(n.node_id) or not all_sources or not n.inputs:
                continue
            for src in all_sources:
                edges.append(TypedWorkflowEdge(
                    from_node=src, from_port=(
                        "output" if src.startswith("transform:")
                        else "data"),
                    to_node=n.node_id, to_port=n.inputs[0].name))

    # ── output 节点 ───────────────────────────────────────────────────
    # 产出者裁决：artifact 的产出 analysis 节点 = 其解析算法的
    # output_artifact_type 匹配者（plan 真实产出，防类型失配边）。
    analysis_nodes_for_outputs = [n for n in nodes if n.kind == "analysis"]

    def _producer_for(artifact: str) -> Optional[TypedWorkflowNode]:
        return next(
            (n for n in nodes if n.kind == "analysis"
             and n.outputs and n.outputs[0].artifact_type == artifact),
            None,
        )

    emitted_output_ids: List[str] = []
    for art in method_outputs[:6]:
        producer = _producer_for(art)
        if producer is None:
            continue  # plan 不产出该 artifact：诚实跳过（不造失配边）
        nodes.append(TypedWorkflowNode(
            node_id=f"output:{art}", kind="output",
            inputs=[TypedPort(name="product", artifact_type=art,
                              geometry_kind=_artifact_geometry(art))],
        ))
        edges.append(TypedWorkflowEdge(
            from_node=producer.node_id, from_port="output",
            to_node=f"output:{art}", to_port="product"))
        emitted_output_ids.append(f"output:{art}")
    if not emitted_output_ids and analysis_nodes_for_outputs:
        # 方法产出与 plan 无一对应 → 主输出挂 plan 尾节点（最后声明 =
        # 产品链末端），端口类型取其真实产出。
        tail = analysis_nodes_for_outputs[-1]
        tail_art = tail.outputs[0].artifact_type if tail.outputs else ""
        tail_id = f"output:{tail_art or 'product'}"
        nodes.append(TypedWorkflowNode(
            node_id=tail_id, kind="output",
            inputs=[TypedPort(
                name="product", artifact_type=tail_art,
                geometry_kind=_artifact_geometry(tail_art) if tail_art else "unknown",
            )],
        ))
        edges.append(TypedWorkflowEdge(
            from_node=tail.node_id, from_port="output",
            to_node=tail_id, to_port="product"))
        emitted_output_ids.append(tail_id)

    # 主输出 = 实际 emit 的第一个 output 节点（MAJOR-3：不指向幽灵节点）；
    # 显式指定且真实存在时优先。
    preferred = f"output:{primary_output_artifact}" if primary_output_artifact else ""
    primary = (
        preferred if preferred in emitted_output_ids
        else (emitted_output_ids[0] if emitted_output_ids else "")
    )

    graph = TypedWorkflowGraph(
        nodes=nodes[:_MAX_GRAPH_NODES],
        edges=edges[:_MAX_GRAPH_EDGES],
        primary_output=primary,
    )
    graph.validation_violations = validate_typed_dag(graph)
    return graph
