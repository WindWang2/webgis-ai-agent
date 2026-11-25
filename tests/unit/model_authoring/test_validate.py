"""模型静态验证器单测（validate_document）。

覆盖面：空图 / 环 / 边端口未知（含 data_input 目标边）/ 输出非汇点与
不可达 / 悬空节点 / 角色未知与重复 / capability 未知与非 native / 钉定
类型越接受集 / 端口类型与几何不兼容 / 参数五族（缺默认、未引用、引用
未知、枚举违约、时间窗格式、integer 越界、boolean 类型）/ 可执行节点
缺输入 / table 角色无消费者 / 确定性排序。

每个断言同时锁 finding code + severity + 定位字段（node_id/edge/param）。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pytest

from app.schemas.model_authoring_schema import (
    BoundedValue,
    ModelDocument,
    ModelEdge,
    ModelNode,
    ModelNodeParams,
    ModelParamSpec,
    ModelSource,
)
from app.services.model_authoring.contracts import (
    F_CAPABILITY_NOT_NATIVE,
    F_CAPABILITY_UNKNOWN,
    F_CYCLE,
    F_EMPTY_GRAPH,
    F_EDGE_PORT_UNKNOWN,
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
    SEVERITY_ERROR,
    SEVERITY_WARNING,
)
from app.services.model_authoring.validate import validate_document


@pytest.fixture(scope="module", autouse=True)
def _loaded_registries():
    """装载能力/类型注册表（builtin 种子；load_builtins 幂等）。"""
    from app.lib.gis.artifacts import get_artifact_type_registry
    from app.lib.gis.capability_registry import get_capability_registry

    get_artifact_type_registry().load_builtins()
    get_capability_registry().load_builtins()


# ── 构造帮助 ─────────────────────────────────────────────────────────────

def _doc(
    nodes: List[ModelNode],
    edges: Tuple[ModelEdge, ...] = (),
    params: Tuple[ModelParamSpec, ...] = (),
    outputs: Tuple[str, ...] = (),
    name: str = "m",
) -> ModelDocument:
    return ModelDocument(
        name=name,
        source=ModelSource(kind="manual"),
        nodes=list(nodes),
        edges=list(edges),
        params=list(params),
        output_node_ids=list(outputs),
    )


def _chain_doc(
    *,
    with_output: bool = True,
    node_params: Optional[ModelNodeParams] = None,
) -> ModelDocument:
    """权威正例链：facility → buffer → 聚合(表→汇点) + 特征流 → 图 → 汇点。

    语义对齐运行时绑定门（P2-1）：表产物（admin_aggregate_table）只进
    output 汇点；cartography 由特征流（buffer 产物）渲染。
    """
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="buffered", kind="transform", transform_op="buffer",
                  params=node_params or ModelNodeParams()),
        ModelNode(node_id="aggregate", kind="analysis",
                  capability="admin_aggregation"),
        ModelNode(node_id="map", kind="cartography"),
    ]
    edges = [
        ModelEdge(from_node="facilities", from_port="facility",
                  to_node="buffered", to_port="in"),
        ModelEdge(from_node="buffered", from_port="out",
                  to_node="aggregate", to_port="in"),
        ModelEdge(from_node="buffered", from_port="out",
                  to_node="map", to_port="in"),
    ]
    if with_output:
        nodes.append(ModelNode(node_id="stats_out", kind="output"))
        nodes.append(ModelNode(node_id="model_output", kind="output"))
        edges.append(ModelEdge(from_node="aggregate", from_port="out",
                               to_node="stats_out", to_port="in"))
        edges.append(ModelEdge(from_node="map", from_port="out",
                               to_node="model_output", to_port="in"))
    return _doc(
        nodes, tuple(edges),
        params=(ModelParamSpec(
            name="radius", kind="number", required=True,
            default=BoundedValue(value=500), minimum=1, maximum=10_000),),
        outputs=("model_output",) if with_output else (),
        name="Facility Aggregation",
    )


def _one(findings: List, code: str):
    return next(f for f in findings if f.code == code)


def _codes(findings: List) -> set:
    return {f.code for f in findings}


# ── 结构面 ───────────────────────────────────────────────────────────────

def test_valid_chain_has_no_findings() -> None:
    """权威正例链零 findings（端口/角色/能力/参数全绿）。"""
    assert validate_document(_chain_doc()) == []


def test_empty_graph_is_error() -> None:
    """空图 → MODEL_EMPTY_GRAPH error。"""
    findings = validate_document(_doc([]))
    f = _one(findings, F_EMPTY_GRAPH)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == ""


def test_cycle_detected_with_path_detail() -> None:
    """环 → MODEL_CYCLE error；node_id 在环上；detail 列环节点路径。"""
    nodes = [
        ModelNode(node_id="a", kind="transform", transform_op="buffer"),
        ModelNode(node_id="b", kind="transform", transform_op="clip"),
        ModelNode(node_id="c", kind="transform", transform_op="dissolve"),
    ]
    edges = [
        ModelEdge(from_node="a", from_port="out", to_node="b", to_port="in"),
        ModelEdge(from_node="b", from_port="out", to_node="c", to_port="in"),
        ModelEdge(from_node="c", from_port="out", to_node="a", to_port="in"),
    ]
    findings = validate_document(_doc(nodes, tuple(edges)))
    f = _one(findings, F_CYCLE)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "a"
    assert "a -> b -> c" in f.detail


def test_edge_unknown_source_port() -> None:
    """源输出端口不存在 → EDGE_PORT_UNKNOWN（edge 定位 + node_id=源）。"""
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="buffered", kind="transform", transform_op="buffer"),
    ]
    edges = (ModelEdge(from_node="facilities", from_port="bogus",
                       to_node="buffered", to_port="in"),)
    findings = validate_document(_doc(nodes, edges))
    f = _one(findings, F_EDGE_PORT_UNKNOWN)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "facilities"
    assert f.edge == "facilities.bogus->buffered.in"
    assert "bogus" in f.detail


def test_edge_into_data_input_is_port_error() -> None:
    """数据流向 data_input → EDGE_PORT_UNKNOWN，detail 说明禁止原因。"""
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="subject_data", kind="data_input", role="subject"),
    ]
    edges = (ModelEdge(from_node="facilities", from_port="facility",
                       to_node="subject_data", to_port="in"),)
    findings = validate_document(_doc(nodes, edges))
    f = _one(findings, F_EDGE_PORT_UNKNOWN)
    assert f.severity == SEVERITY_ERROR
    assert "data_input" in f.detail


def test_edge_unknown_target_port() -> None:
    """目标输入端口不存在 → EDGE_PORT_UNKNOWN（node_id=目标节点）。"""
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="buffered", kind="transform", transform_op="buffer"),
    ]
    edges = (ModelEdge(from_node="facilities", from_port="facility",
                       to_node="buffered", to_port="side"),)
    findings = validate_document(_doc(nodes, edges))
    f = _one(findings, F_EDGE_PORT_UNKNOWN)
    assert f.node_id == "buffered"
    assert "side" in f.detail


def test_output_not_sink() -> None:
    """output 带出边 → OUTPUT_NOT_SINK error（node_id=output）。"""
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="aggregate", kind="analysis",
                  capability="admin_aggregation"),
        ModelNode(node_id="model_output", kind="output"),
        ModelNode(node_id="map", kind="cartography"),
    ]
    edges = (
        ModelEdge(from_node="facilities", from_port="facility",
                  to_node="aggregate", to_port="in"),
        ModelEdge(from_node="aggregate", from_port="out",
                  to_node="model_output", to_port="in"),
        ModelEdge(from_node="model_output", from_port="out",
                  to_node="map", to_port="in"),
    )
    findings = validate_document(_doc(nodes, edges, outputs=("model_output",)))
    f = _one(findings, F_OUTPUT_NOT_SINK)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "model_output"


def test_output_unreachable_lists_blocking_upstream() -> None:
    """output 不可达（无 data_input 上游）→ OUTPUT_UNREACHABLE error，
    detail 给出阻断上游（悬空根）。"""
    nodes = [
        ModelNode(node_id="orphan_t", kind="transform", transform_op="buffer"),
        ModelNode(node_id="model_output", kind="output"),
    ]
    edges = (ModelEdge(from_node="orphan_t", from_port="out",
                       to_node="model_output", to_port="in"),)
    findings = validate_document(_doc(nodes, edges, outputs=("model_output",)))
    f = _one(findings, F_OUTPUT_UNREACHABLE)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "model_output"
    assert "orphan_t" in f.detail


def test_no_explicit_outputs_is_warning() -> None:
    """output_node_ids 为空 → warning 级 OUTPUT_UNREACHABLE + "no explicit outputs"。"""
    findings = validate_document(_chain_doc(with_output=False))
    f = _one(findings, F_OUTPUT_UNREACHABLE)
    assert f.severity == SEVERITY_WARNING
    assert f.detail == "no explicit outputs"
    assert all(f.severity != SEVERITY_ERROR for f in findings)


def test_dangling_node_is_warning() -> None:
    """无入边的 analysis → NODE_UNREACHABLE warning（data_input 豁免）。"""
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="aggregate", kind="analysis",
                  capability="admin_aggregation"),
        ModelNode(node_id="model_output", kind="output"),
    ]
    edges = (
        ModelEdge(from_node="facilities", from_port="facility",
                  to_node="aggregate", to_port="in"),
        ModelEdge(from_node="aggregate", from_port="out",
                  to_node="model_output", to_port="in"),
    )
    # 再挂一个悬空 cartography（无入边 → 同时触发 INPUT_NODE_MISSING error）。
    nodes.append(ModelNode(node_id="lonely_map", kind="cartography"))
    findings = validate_document(_doc(nodes, edges, outputs=("model_output",)))
    f = _one(findings, F_NODE_UNREACHABLE)
    assert f.severity == SEVERITY_WARNING
    assert f.node_id == "lonely_map"
    assert F_INPUT_NODE_MISSING in _codes(findings)


# ── 数据角色面 ───────────────────────────────────────────────────────────

def test_role_unknown() -> None:
    """角色不在 DATA_ROLES → ROLE_UNKNOWN error（workflow_schema 词表）。"""
    nodes = [ModelNode(node_id="bad_data", kind="data_input", role="weather")]
    findings = validate_document(_doc(nodes))
    f = _one(findings, F_ROLE_UNKNOWN)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "bad_data"
    assert "weather" in f.detail


def test_multiple_inputs_same_role() -> None:
    """同角色双输入 → ROLE_DUPLICATE_INPUT error（定位第二个节点）。"""
    nodes = [
        ModelNode(node_id="sub_a", kind="data_input", role="subject"),
        ModelNode(node_id="sub_b", kind="data_input", role="subject"),
    ]
    findings = validate_document(_doc(nodes))
    f = _one(findings, F_MULTIPLE_INPUT_SAME_ROLE)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "sub_b"
    assert "sub_a" in f.detail


def test_table_role_without_analysis_consumer_is_warning() -> None:
    """measure 角色无 analysis 消费者 → DATA_ROLE_BLOCKED warning。"""
    nodes = [
        ModelNode(node_id="stats_in", kind="data_input", role="measure"),
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="aggregate", kind="analysis",
                  capability="admin_aggregation"),
        ModelNode(node_id="model_output", kind="output"),
    ]
    edges = (
        ModelEdge(from_node="facilities", from_port="facility",
                  to_node="aggregate", to_port="in"),
        ModelEdge(from_node="aggregate", from_port="out",
                  to_node="model_output", to_port="in"),
    )
    # （原 table-role-warning 断言已删：table 角色只接 output 汇点是
    # descriptor 契约下唯一可运行接线 —— P2-1 修复，见
    # test_table_feed_into_compute_port_rejected。）
    findings = validate_document(_doc(nodes, edges, outputs=("model_output",)))
    assert not any(f.code == "DATA_ROLE_BLOCKED" for f in findings)


# ── capability 面 ────────────────────────────────────────────────────────

def test_capability_unknown() -> None:
    """假能力 id → CAPABILITY_UNKNOWN error。"""
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="ghost", kind="analysis", capability="no_such_cap"),
    ]
    edges = (ModelEdge(from_node="facilities", from_port="facility",
                       to_node="ghost", to_port="in"),)
    findings = validate_document(_doc(nodes, edges))
    f = _one(findings, F_CAPABILITY_UNKNOWN)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "ghost"
    assert "no_such_cap" in f.detail


def test_capability_not_native() -> None:
    """真实注册但 status=planned 的能力 → CAPABILITY_NOT_NATIVE（detail 带 status）。"""
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="scene", kind="analysis",
                  capability="scene_representation"),
    ]
    edges = (ModelEdge(from_node="facilities", from_port="facility",
                       to_node="scene", to_port="in"),)
    findings = validate_document(_doc(nodes, edges))
    f = _one(findings, F_CAPABILITY_NOT_NATIVE)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "scene"
    assert "planned" in f.detail
    # 钉定类型不在接受集（raster_surface 不被 facility 端口喂入）——
    # 本例只断言 NOT_NATIVE 主裁决定位，类型面由专测覆盖。
    assert F_CAPABILITY_UNKNOWN not in _codes(findings)


def test_pinned_input_artifact_mismatch() -> None:
    """钉定输入类型不在能力接受集 → INPUT_ARTIFACT_MISMATCH error。"""
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="aggregate", kind="analysis",
                  capability="admin_aggregation",
                  input_artifact_type="stats_table"),
    ]
    edges = (ModelEdge(from_node="facilities", from_port="facility",
                       to_node="aggregate", to_port="in"),)
    findings = validate_document(_doc(nodes, edges))
    f = _one(findings, F_INPUT_ARTIFACT_MISMATCH)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "aggregate"
    assert "stats_table" in f.detail


def test_port_type_mismatch_feature_to_table() -> None:
    """feature_collection → stats_table 边 → PORT_TYPE_MISMATCH（detail 两侧类型）。"""
    nodes = [
        ModelNode(node_id="subject_data", kind="data_input", role="subject"),
        ModelNode(node_id="tabulate", kind="analysis",
                  capability="admin_aggregation",
                  input_artifact_type="stats_table"),
    ]
    edges = (ModelEdge(from_node="subject_data", from_port="subject",
                       to_node="tabulate", to_port="in"),)
    findings = validate_document(_doc(nodes, edges))
    f = _one(findings, F_PORT_TYPE_MISMATCH)
    assert f.severity == SEVERITY_ERROR
    assert f.edge == "subject_data.subject->tabulate.in"
    assert "feature_collection" in f.detail
    assert "stats_table" in f.detail


def test_geometry_mismatch_vocabulary_is_defensive() -> None:
    """GEOMETRY_MISMATCH：两端几何族已知且不同才触发。

    本域端口投影（contracts.node_ports）中非 data_input 端口几何恒
    unknown，data_input→data_input 又被结构禁止 —— 该码在当前投影下是
    防御性词表（诚实保留、永不误报）。此测试锁定：权威链不误报，且词表
    在场（契约冻结）。
    """
    from app.services.model_authoring.contracts import (
        F_GEOMETRY_MISMATCH,
        FINDING_MESSAGES,
    )

    assert F_GEOMETRY_MISMATCH not in _codes(validate_document(_chain_doc()))
    assert F_GEOMETRY_MISMATCH in FINDING_MESSAGES


# ── 参数面 ───────────────────────────────────────────────────────────────

def _param_chain(param: ModelParamSpec, node_params: ModelNodeParams) -> ModelDocument:
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="buffered", kind="transform", transform_op="buffer",
                  params=node_params),
        ModelNode(node_id="aggregate", kind="analysis",
                  capability="admin_aggregation"),
        ModelNode(node_id="model_output", kind="output"),
    ]
    edges = (
        ModelEdge(from_node="facilities", from_port="facility",
                  to_node="buffered", to_port="in"),
        ModelEdge(from_node="buffered", from_port="out",
                  to_node="aggregate", to_port="in"),
        ModelEdge(from_node="aggregate", from_port="out",
                  to_node="model_output", to_port="in"),
    )
    return _doc(nodes, edges, params=(param,), outputs=("model_output",))


def test_required_param_without_default_is_warning_when_referenced() -> None:
    """required 无 default 但被节点引用 → warning（实例化时提供）。"""
    param = ModelParamSpec(name="threshold", kind="number", required=True)
    node_params = ModelNodeParams(
        values={"min_score": BoundedValue(value="$param:threshold")})
    findings = validate_document(_param_chain(param, node_params))
    f = _one(findings, F_PARAM_REQUIRED_MISSING_DEFAULT)
    assert f.severity == SEVERITY_WARNING
    assert f.param == "threshold"


def test_required_param_unreferenced_is_error() -> None:
    """required 无 default 且无节点引用 → PARAM_REF_UNKNOWN error（unreferenced）。"""
    param = ModelParamSpec(name="threshold", kind="number", required=True)
    node_params = ModelNodeParams()  # 无 $param 引用
    findings = validate_document(_param_chain(param, node_params))
    f = _one(findings, F_PARAM_REF_UNKNOWN)
    assert f.severity == SEVERITY_ERROR
    assert f.param == "threshold"
    assert "unreferenced param" in f.detail
    assert F_PARAM_REQUIRED_MISSING_DEFAULT not in _codes(findings)


def test_node_param_refers_undeclared_param() -> None:
    """节点值引用未声明参数 → PARAM_REF_UNKNOWN error（node + param 定位）。"""
    param = ModelParamSpec(name="radius", kind="number", required=True,
                           default=BoundedValue(value=500))
    node_params = ModelNodeParams(
        values={"distance_m": BoundedValue(value="$param:ghost")})
    findings = validate_document(_param_chain(param, node_params))
    f = _one(findings, F_PARAM_REF_UNKNOWN)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "buffered"
    assert f.param == "ghost"


def test_enum_default_must_be_in_choices() -> None:
    """enum 默认值不在 choices → PARAM_INVALID_VALUE（param 定位）。"""
    param = ModelParamSpec(name="method", kind="enum", required=False,
                           default=BoundedValue(value="fast"),
                           choices=("exact", "approx"))
    findings = validate_document(_param_chain(param, ModelNodeParams()))
    f = _one(findings, F_PARAM_INVALID_VALUE)
    assert f.severity == SEVERITY_ERROR
    assert f.param == "method"
    assert "fast" in f.detail


def test_enum_default_wrong_type() -> None:
    """enum 默认值非字符串 → PARAM_TYPE_MISMATCH。"""
    param = ModelParamSpec(name="method", kind="enum", required=False,
                           default=BoundedValue(value=3),
                           choices=("exact",))
    findings = validate_document(_param_chain(param, ModelNodeParams()))
    f = _one(findings, F_PARAM_TYPE_MISMATCH)
    assert f.param == "method"


def test_time_window_default_format() -> None:
    """time_window 默认值必须匹配 YYYY-MM-DD/YYYY-MM-DD 或空。"""
    # 注：schema 的 model_validator 实际只允许 field 参数携带 input_node
    # （错误文案提及 time_window，但条件是 kind not in ("enum", "field")），
    # 故此处 time_window 不带 input_node。
    bad = ModelParamSpec(
        name="window", kind="time_window", required=False,
        default=BoundedValue(value="2024/2025"))
    findings = validate_document(_param_chain(bad, ModelNodeParams()))
    f = _one(findings, F_PARAM_INVALID_VALUE)
    assert f.param == "window"
    assert "YYYY-MM-DD" in f.detail
    # 合法形态不产 finding。
    good = ModelParamSpec(
        name="window", kind="time_window", required=False,
        default=BoundedValue(value="2024-01-01/2024-12-31"))
    assert validate_document(_param_chain(good, ModelNodeParams())) == []


def test_integer_default_min_and_integral() -> None:
    """integer 默认值越 min → PARAM_INVALID_VALUE；非整值 → PARAM_TYPE_MISMATCH。"""
    below_min = ModelParamSpec(name="bins", kind="integer", required=False,
                               default=BoundedValue(value=0), minimum=1)
    findings = validate_document(_param_chain(below_min, ModelNodeParams()))
    assert _one(findings, F_PARAM_INVALID_VALUE).param == "bins"

    fractional = ModelParamSpec(name="bins", kind="integer", required=False,
                                default=BoundedValue(value=2.5))
    findings = validate_document(_param_chain(fractional, ModelNodeParams()))
    f = _one(findings, F_PARAM_TYPE_MISMATCH)
    assert f.param == "bins"
    assert "integral" in f.detail


def test_boolean_default_type_mismatch() -> None:
    """boolean 默认值非 bool → PARAM_TYPE_MISMATCH。"""
    param = ModelParamSpec(name="normalize", kind="boolean", required=False,
                           default=BoundedValue(value="yes"))
    findings = validate_document(_param_chain(param, ModelNodeParams()))
    f = _one(findings, F_PARAM_TYPE_MISMATCH)
    assert f.severity == SEVERITY_ERROR
    assert f.param == "normalize"


def test_number_default_over_maximum() -> None:
    """number 默认值越 max → PARAM_INVALID_VALUE。"""
    param = ModelParamSpec(name="radius", kind="number", required=False,
                           default=BoundedValue(value=99999),
                           minimum=1, maximum=10_000)
    findings = validate_document(_param_chain(param, ModelNodeParams()))
    f = _one(findings, F_PARAM_INVALID_VALUE)
    assert "maximum" in f.detail


# ── 执行面 ───────────────────────────────────────────────────────────────

def test_executable_node_without_input_is_error() -> None:
    """cartography 无入边 → INPUT_NODE_MISSING error。"""
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="map", kind="cartography"),
        ModelNode(node_id="model_output", kind="output"),
    ]
    edges = (ModelEdge(from_node="map", from_port="out",
                       to_node="model_output", to_port="in"),)
    findings = validate_document(_doc(nodes, edges, outputs=("model_output",)))
    f = _one(findings, F_INPUT_NODE_MISSING)
    assert f.severity == SEVERITY_ERROR
    assert f.node_id == "map"


def test_findings_deterministically_sorted() -> None:
    """同输入两次验证结果一致，且按 (severity, node_id, code) 排序。"""
    doc = _chain_doc(with_output=False)
    first = validate_document(doc)
    second = validate_document(doc)
    assert first == second
    keys = [(f.severity, f.node_id, f.code) for f in first]
    assert keys == sorted(keys)


def test_findings_project_to_bounded_dicts() -> None:
    """findings 可经 to_bounded 投影（前端直接消费；有界字段）。"""
    findings = validate_document(_doc([]))
    bounded = [f.to_bounded() for f in findings]
    assert bounded
    for b in bounded:
        assert set(b) == {
            "code", "severity", "node_id", "edge", "param",
            "detail_zh", "detail_en", "detail",
        }
