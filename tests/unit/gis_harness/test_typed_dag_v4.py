"""Workflow V4 —— Typed Workflow DAG 单测（Wave 3）。

覆盖：端口类型事实引用（ArtifactTypeRegistry/AlgorithmDescriptor）、边类型
兼容裁决、图校验（悬空/环/不可达输出）、确定性构建、有界序列化。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

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
