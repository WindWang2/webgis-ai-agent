"""Workflow V4 —— Typed Workflow DAG 单测（Wave 3）。

覆盖：端口类型事实引用（ArtifactTypeRegistry/AlgorithmDescriptor）、边类型
兼容裁决、图校验（悬空/环/不可达输出）、确定性构建、有界序列化。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.artifacts import get_artifact_type_registry
from app.services.gis_harness.workflow_v4.typed_dag import (
    TYPED_NODE_KINDS,
    TypedPort,
    TypedWorkflowEdge,
    TypedWorkflowGraph,
    TypedWorkflowNode,
    build_typed_dag,
    ports_compatible,
    validate_typed_dag,
)


@pytest.fixture(scope="module", autouse=True)
def _loaded_registries():
    get_algorithm_registry().load_builtins()
    get_artifact_type_registry().load_builtins()


def _method(**kw) -> SimpleNamespace:
    base = dict(method_id="density.kernel_surface",
                output_artifacts=("density_surface",),
                requires_roles=("subject",),
                capabilities=("kde_density", "analytical_density"))
    base.update(kw)
    return SimpleNamespace(**base)


def _roles(*states: str) -> list:
    mapping = {"subject": "bound", "denominator": "unresolved",
               "boundary": "bound"}
    return [
        SimpleNamespace(role=role, status=state)
        for role, state in mapping.items()
        if not states or state != "degraded" and role in states or not states
    ]


def test_port_geometry_comes_from_artifact_registry() -> None:
    """端口几何族来自 ArtifactTypeRegistry 注册描述（非本地词表）。"""
    assert get_artifact_type_registry().get("density_surface").geometry_kind == "polygon"
    from app.services.gis_harness.workflow_v4.typed_dag import _artifact_geometry
    assert _artifact_geometry("point_feature_set") == "point"
    assert _artifact_geometry("not_a_registered_type") == "unknown"


def test_ports_compatible_rules() -> None:
    wide = TypedPort(name="in", artifact_type="feature_collection",
                     geometry_kind="unknown")
    point_src = TypedPort(name="out", artifact_type="point_feature_set",
                          geometry_kind="point")
    assert ports_compatible(point_src, wide)
    # 宽端口不接受非 feature_set 类（surface 类）
    surface_src = TypedPort(name="out", artifact_type="density_surface",
                            geometry_kind="polygon")
    assert not ports_compatible(surface_src, wide)
    # 显式类型必须相等
    assert ports_compatible(
        surface_src, TypedPort(name="in", artifact_type="density_surface"))
    assert not ports_compatible(
        point_src, TypedPort(name="in", artifact_type="density_surface"))


def test_build_from_plan_steps_is_valid() -> None:
    steps = [
        SimpleNamespace(capability="analytical_density", status="pending",
                        resolved_algorithm="spatial.kde.surface",
                        depends_on=[], optional=False),
        SimpleNamespace(capability="admin_aggregation", status="pending",
                        resolved_algorithm="", depends_on=["analytical_density"],
                        optional=True),
    ]
    roles = [SimpleNamespace(role="subject", status="bound"),
             SimpleNamespace(role="denominator", status="unresolved")]
    g = build_typed_dag(
        steps, data_roles=roles, selected_method=_method(),
        extra_transforms=[{"operation": "reproject", "role": "subject"}],
    )
    assert g.validation_violations == []
    kinds = {n.kind for n in g.nodes}
    assert kinds <= set(TYPED_NODE_KINDS)
    assert "data:subject" in [n.node_id for n in g.nodes]
    assert "transform:reproject:subject" in [n.node_id for n in g.nodes]
    # 修复链：role → transform → analysis
    edge_pairs = [(e.from_node, e.to_node) for e in g.edges]
    assert ("data:subject", "transform:reproject:subject") in edge_pairs
    assert ("transform:reproject:subject", "cap:analytical_density") in edge_pairs
    # 主输出 = 选中方法的首个产出
    assert g.primary_output == "output:density_surface"


def test_algorithm_ports_project_crs_and_unit_requirements() -> None:
    """端口 CRS 类直接引用算法层 crs_class（crs_safety 词表）。"""
    from app.services.gis_harness.workflow_v4.typed_dag import _analysis_ports
    _, kriging_out = _analysis_ports("block_kriging", "interpolation.kriging")
    kriging_in = _analysis_ports("block_kriging", "interpolation.kriging")[0]
    assert any(p.crs_requirement == "PROJECTED_REQUIRED" for p in kriging_in)
    assert kriging_out[0].artifact_type == "terrain_surface"


def test_validator_catches_cycle_dangling_and_unreachable() -> None:
    n1 = TypedWorkflowNode(node_id="cap:a", kind="analysis",
                           inputs=[TypedPort(name="input")],
                           outputs=[TypedPort(name="output")])
    n2 = TypedWorkflowNode(node_id="cap:b", kind="analysis",
                           inputs=[TypedPort(name="input")],
                           outputs=[TypedPort(name="output")])
    out = TypedWorkflowNode(
        node_id="output:stats_table", kind="output",
        inputs=[TypedPort(name="product", artifact_type="stats_table")])
    dangling = TypedWorkflowNode(node_id="cap:c", kind="analysis",
                                 depends_on=("cap:ghost",))
    bad_kind = TypedWorkflowNode(node_id="cap:d", kind="weird")

    cyc = TypedWorkflowGraph(nodes=[n1, n2], edges=[
        TypedWorkflowEdge(from_node="cap:a", from_port="output",
                          to_node="cap:b", to_port="input"),
        TypedWorkflowEdge(from_node="cap:b", from_port="output",
                          to_node="cap:a", to_port="input"),
    ])
    assert any(v.startswith("TYPED_DAG_CYCLE") for v in validate_typed_dag(cyc))

    dang = TypedWorkflowGraph(nodes=[dangling, out], edges=[])
    assert any(v.startswith("TYPED_DAG_DANGLING_DEP")
               for v in validate_typed_dag(dang))
    assert any(v.startswith("TYPED_DAG_UNREACHABLE_OUTPUT")
               for v in validate_typed_dag(dang))

    unk = TypedWorkflowGraph(nodes=[bad_kind], edges=[])
    assert any(v.startswith("TYPED_DAG_UNKNOWN_KIND")
               for v in validate_typed_dag(unk))


def test_validator_rejects_input_budget_overflow_at_compile_boundary() -> None:
    """有效上游与声明端口均受 6 输入预算约束，超限不得静默截断。"""
    def _fan_in(count: int) -> TypedWorkflowGraph:
        sources = [
            TypedWorkflowNode(
                node_id=f"data:{i}", kind="data_input",
                outputs=[TypedPort(name="output")])
            for i in range(count)
        ]
        target = TypedWorkflowNode(
            node_id="cap:fan_in", kind="analysis",
            inputs=[TypedPort(name="input")],
            outputs=[TypedPort(name="output")],
            depends_on=tuple(n.node_id for n in sources))
        edges = [
            TypedWorkflowEdge(from_node=n.node_id, from_port="output",
                              to_node=target.node_id, to_port="input")
            for n in sources
        ]
        return TypedWorkflowGraph(nodes=[*sources, target], edges=edges)

    assert not any(v.startswith("TYPED_DAG_INPUT_LIMIT")
                   for v in validate_typed_dag(_fan_in(6)))
    assert any(v.startswith("TYPED_DAG_INPUT_LIMIT:cap:fan_in:7>6")
               for v in validate_typed_dag(_fan_in(7)))

    too_many_ports = TypedWorkflowGraph(nodes=[TypedWorkflowNode(
        node_id="cap:ports", kind="analysis",
        inputs=[TypedPort(name=f"input{i}") for i in range(7)])])
    assert any(v.startswith("TYPED_DAG_INPUT_LIMIT:cap:ports:7>6")
               for v in validate_typed_dag(too_many_ports))


def test_build_deterministic_and_bounded() -> None:
    steps = [SimpleNamespace(capability="analytical_density", status="pending",
                             resolved_algorithm="spatial.kde.surface",
                             depends_on=[], optional=False)]
    roles = [SimpleNamespace(role="subject", status="bound")]
    kw = dict(analysis_steps=steps, data_roles=roles,
              selected_method=_method(),
              extra_transforms=[{"operation": "reproject", "role": "subject"}])
    a = build_typed_dag(**kw).to_bounded_dict()
    b = build_typed_dag(**kw).to_bounded_dict()
    assert a == b
    assert len(json.dumps(a, ensure_ascii=False)) < 16_000


def test_multi_input_ports_all_get_data_flow_suppliers() -> None:
    """多输入算法节点：cap 依赖生成数据流边，每个声明端口都有供给。

    回归（P1）：历史上角色边只接 ``inputs[0].name``、cap→cap 依赖仅作
    ``depends_on`` 元数据（不生成数据流边）→ 第二个声明端口在运行时拿不到
    descriptor（driver ``_gather_inputs`` 只认边视图）→ 绑定门判
    MISSING_INPUT → 节点 BLOCKED、实例 FAILED。
    """
    steps = [
        SimpleNamespace(capability="poi_query", status="pending",
                        resolved_algorithm="", depends_on=[], optional=False),
        SimpleNamespace(capability="service_area", status="pending",
                        resolved_algorithm="",
                        depends_on=["poi_query", "point_profile"],
                        optional=False),
        SimpleNamespace(capability="point_profile", status="pending",
                        resolved_algorithm="", depends_on=["poi_query"],
                        optional=False),
    ]
    roles = [SimpleNamespace(role="network", status="bound")]
    method = _method(method_id="network.service_area",
                     output_artifacts=("service_area",),
                     requires_roles=("network",),
                     capabilities=("service_area",))
    g = build_typed_dag(steps, data_roles=roles, selected_method=method)
    assert g.validation_violations == []
    edge_pairs = {(e.from_node, e.to_node, e.to_port) for e in g.edges}
    # cap 依赖供给落在类型精确的声明端口（poi_query 出 poi_feature_set →
    # input1；point_profile 出 point_feature_set → input2）
    assert ("cap:poi_query", "cap:service_area", "input1") in edge_pairs
    assert ("cap:point_profile", "cap:service_area", "input2") in edge_pairs
    assert ("cap:poi_query", "cap:point_profile", "input1") in edge_pairs
    # 无端口悬空：每个声明输入端口都是某条数据流边的 to_port
    for n in g.nodes:
        if n.kind != "analysis":
            continue
        fed = {e.to_port for e in g.edges if e.to_node == n.node_id}
        assert {p.name for p in n.inputs} <= fed, n.node_id
    # 确定性不回归
    again = build_typed_dag(steps, data_roles=roles, selected_method=method)
    assert again.to_bounded_dict() == g.to_bounded_dict()


def test_compiled_multi_input_nodes_pass_binding_gate() -> None:
    """真实编译产物（accessibility_analysis）过 driver 边视图 + 绑定门。

    端到端回归（P1）：按 ``driver._gather_inputs`` 的同一种收集逻辑
    （``machine.upstream_ports`` 边视图）为每个声明端口取 descriptor，
    ``verify_node_bindings`` 必须全过 —— 修复前 ``cap:service_area.input2``
    恒缺 descriptor（MISSING_INPUT → BLOCKED）。
    """
    from app.services.gis_harness.workflow_v4.compiler_v4 import (
        compile_workflow_v4,
    )
    from app.services.workflow_runtime import machine as M
    from app.services.workflow_runtime.binding import verify_node_bindings

    c = compile_workflow_v4("分析公园的服务区可达性",
                            recipe_id="accessibility_analysis")
    dag = c.typed_dag
    assert dag.get("nodes") and dag["validation_violations"] == []
    # 上游节点声明产出 → 运行态 descriptor 形态（artifact_type + 几何族 +
    # 非空计数；CRS/单位缺省 = unknown 诚实放行）
    desc_by_node: Dict[str, Any] = {}
    for n in dag["nodes"]:
        outs = n.get("outputs") or []
        if outs:
            desc_by_node[n["node_id"]] = {
                "artifact_type": str(outs[0].get("artifact_type") or ""),
                "feature_count": 3, "geometry_types": ["Point"],
            }
    upstream = M.upstream_ports(dag)
    for n in dag["nodes"]:
        if n.get("kind") != "analysis":
            continue
        port_names = [str(p["name"]) for p in n.get("inputs") or []]
        port_descs: Dict[str, Any] = {}
        for idx, (up, to_port) in enumerate(
                upstream.get(n["node_id"], [])[:4]):
            port = to_port if to_port in port_names else (
                port_names[idx] if idx < len(port_names)
                else f"input{idx + 1}")
            port_descs[port] = desc_by_node.get(up)
        # 回归点：每个声明端口都拿到 descriptor（input2 曾恒缺）
        assert set(port_names) <= set(port_descs), n["node_id"]
        ns = SimpleNamespace(
            node_id=n["node_id"],
            inputs=[SimpleNamespace(**p) for p in n.get("inputs") or []],
            outputs=[])
        verdict = verify_node_bindings(ns, port_descs)
        assert verdict.ok, (
            n["node_id"], sorted(port_descs),
            [f"{v.port}:{v.code}" for v in verdict.violations])
