"""性能/规模面测试（WP6）：作者面上限与编译器规模防御。

覆盖面（单次冷跑，time.perf_counter，阈值 < 2s —— 不做统计基线）：

- 20 节点链图文档（data_input + 16×buffer 串链 + analysis + cartography +
  output = 20 节点 19 边，位于编辑器上限 32/64 内）：validate + compile
  全程零 error 且 < 2s；
- 100 节点 wire 图直构（文档上限 32 放不下 → 直构 TypedWorkflowGraph 测
  编译器规模面）：``to_bounded_dict`` + ``WorkflowPackage`` 指纹 < 2s；
  同时断言运行时 64 节点上界的**有界防御**（to_bounded_dict 静默截断到
  64 节点 —— 上限之上限不炸调用方，由 wire 面诚实呈现有界视图）；
- 33 节点 ModelDocument 被 pydantic 拒绝（编辑器契约：作者上限 32 <
  运行时上限 64，文档层先拦）。

断言消息携带实测毫秒（超标时可直接定位慢相）。
"""
from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from app.schemas.model_authoring_schema import (
    BoundedValue,
    ModelDocument,
    ModelEdge,
    ModelNode,
    ModelNodeParams,
    ModelParamSpec,
)
from app.services.gis_harness.workflow_v4.package import (
    WorkflowPackage,
    _canonical_fingerprint,
)
from app.services.gis_harness.workflow_v4.typed_dag import (
    _MAX_GRAPH_NODES,
    TypedPort,
    TypedWorkflowEdge,
    TypedWorkflowGraph,
    TypedWorkflowNode,
)
from app.services.model_authoring.compile import compile_document
from app.services.model_authoring.contracts import SEVERITY_ERROR
from app.services.model_authoring.validate import validate_document

PERF_BUDGET_S = 2.0


# ── 构造帮助 ─────────────────────────────────────────────────────────────

def _chain_doc(n_buffers: int = 15) -> ModelDocument:
    """链图：data_input + n_buffers×buffer 串链 + analysis + cartography +
    两个 output 汇点（n_buffers=15 时恰 20 节点）。"""
    nodes: list[ModelNode] = [
        ModelNode(node_id="input_fc", kind="data_input", role="subject"),
    ]
    edges: list[ModelEdge] = []
    prev, prev_port = "input_fc", "subject"
    for i in range(n_buffers):
        nid = f"buf_{i:02d}"
        nodes.append(ModelNode(
            node_id=nid, kind="transform", transform_op="buffer",
            params=ModelNodeParams(values={
                "distance": BoundedValue(value=10 + i),
                "unit": BoundedValue(value="m"),
            })))
        edges.append(ModelEdge(from_node=prev, from_port=prev_port,
                               to_node=nid, to_port="in"))
        prev, prev_port = nid, "out"
    nodes.append(ModelNode(node_id="agg", kind="analysis",
                           capability="category_breakdown",
                           params=ModelNodeParams(values={
                               "field": BoundedValue(value="category")})))
    edges.append(ModelEdge(from_node=prev, from_port="out",
                           to_node="agg", to_port="in"))
    nodes.append(ModelNode(node_id="render", kind="cartography",
                           params=ModelNodeParams(values={
                               "title": BoundedValue(value="链图"),
                               "field": BoundedValue(value="category")})))
    edges.append(ModelEdge(from_node="input_fc", from_port="subject",
                           to_node="render", to_port="in"))
    nodes.append(ModelNode(node_id="sink", kind="output"))
    edges.append(ModelEdge(from_node="render", from_port="out",
                           to_node="sink", to_port="in"))
    nodes.append(ModelNode(node_id="stats_sink", kind="output"))
    edges.append(ModelEdge(from_node="agg", from_port="out",
                           to_node="stats_sink", to_port="in"))
    return ModelDocument(
        name="20 节点链图规模样张",
        source={"kind": "manual", "ref": ""},
        params=[ModelParamSpec(
            name="unused_opt", kind="number", required=False,
            default=BoundedValue(value=1))],
        nodes=nodes,
        edges=edges,
        output_node_ids=["sink", "stats_sink"],
    )


def _wire_graph_100() -> TypedWorkflowGraph:
    """直构 100 节点 wire 图（超过运行时 64 上界 —— 规模面样张）。"""
    nodes = [
        TypedWorkflowNode(
            node_id=f"n{i:03d}", kind="transform",
            inputs=[TypedPort(name="in", artifact_type="feature_collection")],
            outputs=[TypedPort(name="out", artifact_type="feature_collection")],
            depends_on=(f"n{i - 1:03d}",) if i else ())
        for i in range(100)
    ]
    edges = [
        TypedWorkflowEdge(from_node=f"n{i:03d}", from_port="out",
                          to_node=f"n{i + 1:03d}", to_port="in")
        for i in range(99)
    ]
    return TypedWorkflowGraph(nodes=nodes, edges=edges,
                              primary_output="n099",
                              validation_violations=[])


# ── 20 节点文档：validate + compile 冷跑 < 2s ────────────────────────────


class TestChainDocumentScale:
    def test_validate_and_compile_under_2s(self):
        doc = _chain_doc(15)
        assert len(doc.nodes) == 20
        assert len(doc.edges) == 19

        t0 = time.perf_counter()
        findings = validate_document(doc)
        errors = [f for f in findings if f.severity == SEVERITY_ERROR]
        compiled, _cf = compile_document(doc, model_slug="chain_scale",
                                         version="1.0.0")
        elapsed_s = time.perf_counter() - t0

        assert errors == [], [(e.code, e.node_id) for e in errors]
        assert compiled is not None
        assert elapsed_s < PERF_BUDGET_S, (
            f"validate+compile of 20-node chain took "
            f"{elapsed_s * 1000:.1f}ms (budget {PERF_BUDGET_S * 1000:.0f}ms)")

    def test_validate_under_2s_alone(self):
        """验证相单独计时（冷跑定位：validate 是纯函数，应在毫秒级）。"""
        doc = _chain_doc(15)
        t0 = time.perf_counter()
        findings = validate_document(doc)
        elapsed_s = time.perf_counter() - t0
        assert not any(f.severity == SEVERITY_ERROR for f in findings)
        assert elapsed_s < PERF_BUDGET_S, (
            f"validate of 20-node chain took {elapsed_s * 1000:.1f}ms "
            f"(budget {PERF_BUDGET_S * 1000:.0f}ms)")


# ── 编辑器契约：>32 节点文档在 pydantic 层被拒 ───────────────────────────


class TestDocumentCaps:
    def test_33_node_document_rejected(self):
        """编辑器契约：>32 节点文档在 pydantic 层被拒（32 是作者上限，
        运行时 64 上限是编译器/typed_dag 的事 —— 文档层先拦）。"""
        nodes = [
            ModelNode(node_id="input_fc", kind="data_input", role="subject"),
        ]
        nodes.extend(
            ModelNode(node_id=f"buf_{i:02d}", kind="transform",
                      transform_op="buffer")
            for i in range(28))
        nodes.extend([
            ModelNode(node_id="agg", kind="analysis",
                      capability="category_breakdown"),
            ModelNode(node_id="render", kind="cartography"),
            ModelNode(node_id="sink", kind="output"),
            ModelNode(node_id="stats_sink", kind="output"),
        ])
        assert len(nodes) == 33
        raw = {
            "schema_version": "1",
            "name": "33 节点越界样张",
            "source": {"kind": "manual", "ref": ""},
            "nodes": [n.model_dump(mode="json") for n in nodes],
            "edges": [{"from_node": "input_fc", "from_port": "subject",
                       "to_node": "buf_00", "to_port": "in"}],
            "output_node_ids": [],
        }
        with pytest.raises(ValidationError) as ei:
            ModelDocument.model_validate(raw)
        assert "nodes exceed 32" in str(ei.value)

    def test_100_node_wire_graph_bounded_projection(self):
        """100 节点 wire 图：to_bounded_dict + 包指纹 < 2s；有界防御在场。"""
        graph = _wire_graph_100()
        assert len(graph.nodes) == 100

        t0 = time.perf_counter()
        wire = graph.to_bounded_dict()
        package = WorkflowPackage(
            package_id="umodel_wire_scale_probe",
            version="1.0.0",
            methodology_family="authored_model",
            compiled_form={
                "schema_version": "4.0",
                "typed_dag": wire,
            },
        )
        package.fingerprint = _canonical_fingerprint(package.compiled_form)
        elapsed_s = time.perf_counter() - t0

        # 运行时 64 上限的有界防御：不炸调用方，wire 面诚实截断
        assert len(wire["nodes"]) == _MAX_GRAPH_NODES == 64
        assert len(wire["edges"]) == 99  # 99 < 128 边上界 → 全保留
        assert package.fingerprint, "fingerprint must be computed"
        assert elapsed_s < PERF_BUDGET_S, (
            f"100-node wire to_bounded_dict+fingerprint took "
            f"{elapsed_s * 1000:.1f}ms (budget {PERF_BUDGET_S * 1000:.0f}ms)")

    def test_fingerprint_deterministic_at_scale(self):
        """同 100 节点 wire 图两次投影/指纹一致（确定性红线不因规模退化）。"""
        fp_a = _canonical_fingerprint(
            {"typed_dag": _wire_graph_100().to_bounded_dict()})
        fp_b = _canonical_fingerprint(
            {"typed_dag": _wire_graph_100().to_bounded_dict()})
        assert fp_a == fp_b
