"""模型编译器单测（compile_document / roundtrip_projection）。

覆盖面：权威正例链的图/边/参数默认注入/$param 引用参数归属、包 id
前缀与 compiled_form 形态、指纹确定性（同输入同指纹；layout 免疫；
与 canonical JSON sha256 本地算法一致）、error finding 阻断编译、
slug/version 词法拒绝、无显式输出时的主输出拓扑兜底、roundtrip 投影。
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional, Tuple

import pytest

from app.schemas.model_authoring_schema import (
    BoundedValue,
    ModelDocument,
    ModelEdge,
    ModelLayoutPos,
    ModelNode,
    ModelNodeParams,
    ModelParamSpec,
    ModelSource,
)
from app.services.gis_harness.workflow_v4.package import (
    WORKFLOW_PACKAGE_SCHEMA_VERSION,
)
from app.services.model_authoring.compile import (
    MODEL_PACKAGE_PREFIX,
    compile_document,
    roundtrip_projection,
)
from app.services.model_authoring.contracts import (
    F_CYCLE,
    F_PARAM_REQUIRED_MISSING_DEFAULT,
    SEVERITY_ERROR,
)


@pytest.fixture(scope="module", autouse=True)
def _loaded_registries():
    from app.lib.gis.artifacts import get_artifact_type_registry
    from app.lib.gis.capability_registry import get_capability_registry

    get_artifact_type_registry().load_builtins()
    get_capability_registry().load_builtins()


# ── 构造帮助 ─────────────────────────────────────────────────────────────

def _chain_doc(
    *,
    layout: Optional[dict] = None,
    outputs: Tuple[str, ...] = ("model_output",),
    output_node: bool = True,
) -> ModelDocument:
    """权威正例链（P2-1 语义）：facility → buffer($param:radius) →
    admin_aggregation（表→统计汇点）+ 特征流 → cartography → 图汇点。"""
    nodes = [
        ModelNode(node_id="facilities", kind="data_input", role="facility"),
        ModelNode(node_id="buffered", kind="transform", transform_op="buffer",
                  params=ModelNodeParams(values={
                      "distance_m": BoundedValue(value="$param:radius")})),
        ModelNode(node_id="aggregate", kind="analysis",
                  capability="admin_aggregation"),
    ]
    edges = [
        ModelEdge(from_node="facilities", from_port="facility",
                  to_node="buffered", to_port="in"),
        ModelEdge(from_node="buffered", from_port="out",
                  to_node="aggregate", to_port="in"),
    ]
    if output_node:
        nodes.append(ModelNode(node_id="stats_out", kind="output"))
        edges.append(ModelEdge(from_node="aggregate", from_port="out",
                               to_node="stats_out", to_port="in"))
        nodes.append(ModelNode(node_id="map", kind="cartography"))
        edges.append(ModelEdge(from_node="buffered", from_port="out",
                               to_node="map", to_port="in"))
        if outputs:
            nodes.append(ModelNode(node_id="model_output", kind="output"))
            edges.append(ModelEdge(from_node="map", from_port="out",
                                   to_node="model_output", to_port="in"))
    return ModelDocument(
        name="Facility Aggregation",
        source=ModelSource(kind="manual"),
        nodes=nodes,
        edges=edges,
        params=[ModelParamSpec(
            name="radius", kind="number", required=True,
            default=BoundedValue(value=500), minimum=1, maximum=10_000)],
        output_node_ids=list(outputs),
        layout=layout or {},
    )


def _wire_node(wire: dict, node_id: str) -> dict:
    return next(n for n in wire["nodes"] if n["node_id"] == node_id)


# ── 正例编译 ─────────────────────────────────────────────────────────────

def test_compile_happy_path() -> None:
    """权威链编译：5 节点 4 边；$param 引用进 node.parameters；主输出显式。"""
    compiled, findings = compile_document(
        _chain_doc(), model_slug="facility_agg")
    assert compiled is not None
    assert not any(f.severity == SEVERITY_ERROR for f in findings)
    graph = compiled.typed_graph
    assert len(graph.nodes) == 6
    assert len(graph.edges) == 5
    assert graph.primary_output == "model_output"
    # data_input 输出端口 = 角色名（contracts.node_ports 契约直映）。
    src_edge = graph.edges[0]
    assert (src_edge.from_node, src_edge.from_port) == ("facilities", "facility")
    assert (src_edge.to_node, src_edge.to_port) == (
        "transform:buffer:buffered", "in")
    # transform 节点声明其引用的模型参数（recompute/实例化消费）。
    buffered = graph.node("transform:buffer:buffered")
    assert buffered is not None and buffered.parameters == ("radius",)
    assert buffered.kind == "transform"
    # analysis 输入端口缺省宽类型（运行时绑定门 _ANY_FEATURE_CATEGORIES
    # 同门：feature_collection 收特征族；真实 descriptor 由运行时复核）。
    aggregate = graph.node("aggregate")
    assert aggregate is not None
    assert aggregate.capability == "admin_aggregation"
    assert aggregate.inputs[0].artifact_type == "feature_collection"
    assert aggregate.outputs[0].artifact_type == "admin_aggregate_table"


def test_transform_wire_node_id_is_runtime_authoritative() -> None:
    """transform wire id = transform:<op>:<author>（driver split(":")[1] 即
    算子）；其余 kind 恒等；node_id_map author→wire 且 rsplit 可逆。"""
    compiled, _ = compile_document(_chain_doc(), model_slug="facility_agg")
    id_map = compiled.node_id_map
    assert set(id_map) == {"facilities", "buffered", "aggregate", "stats_out",
                           "map", "model_output"}
    assert id_map["buffered"] == "transform:buffer:buffered"
    # 非 transform kind：wire id = 作者 slug（driver 不解析其 id 段）。
    assert id_map["facilities"] == "facilities"
    assert id_map["aggregate"] == "aggregate"
    assert id_map["model_output"] == "model_output"
    # 映射单射 + rsplit 可逆（作者 slug 词法不含冒号）。
    assert len(set(id_map.values())) == len(id_map)
    for author, wire_id in id_map.items():
        recovered = wire_id.rsplit(":", 1)[1] if ":" in wire_id else wire_id
        assert recovered == author
    # driver 同款解析：split(":")[1] 恰为算子。
    wire_ids = [n.node_id for n in compiled.typed_graph.nodes]
    assert next(w for w in wire_ids if w.startswith("transform:")).split(":")[1] \
        == "buffer"


def test_node_params_defaults_injected_into_wire() -> None:
    """wire 节点 dict 补 params：$param 引用落 default（无保留键 —— node_id
    是 transform 算子的唯一事实源）。"""
    compiled, _findings = compile_document(_chain_doc(), model_slug="facility_agg")
    wire = compiled.package.compiled_form["typed_dag"]
    buffered = _wire_node(wire, "transform:buffer:buffered")
    assert buffered["params"] == {"distance_m": 500}
    assert buffered["node_id"] == "transform:buffer:buffered"
    # 静态值节点 params 原样（空）。
    assert _wire_node(wire, "aggregate")["params"] == {}
    # 其余 to_bounded_dict 键保持一致（typed wire 契约；引用为 wire id）。
    assert buffered["kind"] == "transform"
    assert buffered["depends_on"] == ["facilities"]
    wire_edge_pairs = {e["from"] for e in wire["edges"]}
    assert "transform:buffer:buffered.out" in wire_edge_pairs


def test_required_param_without_default_omits_wire_key() -> None:
    """required 无 default 的引用参数：wire params 键省略，parameters 保留。"""
    doc = _chain_doc()
    doc.params[0] = ModelParamSpec(name="radius", kind="number", required=True)
    compiled, findings = compile_document(doc, model_slug="facility_agg")
    assert compiled is not None  # warning 不阻断
    assert any(f.code == F_PARAM_REQUIRED_MISSING_DEFAULT for f in findings)
    wire = compiled.package.compiled_form["typed_dag"]
    assert "distance_m" not in _wire_node(wire, "transform:buffer:buffered")["params"]
    buffered = compiled.typed_graph.node("transform:buffer:buffered")
    assert buffered is not None and buffered.parameters == ("radius",)


def test_package_shape_and_id_prefix() -> None:
    """包 id = umodel_<slug>；compiled_form 携带 model 元数据与参数表。"""
    compiled, _ = compile_document(_chain_doc(), model_slug="facility_agg",
                                   version="1.2.3")
    pkg = compiled.package
    assert pkg.package_id == MODEL_PACKAGE_PREFIX + "facility_agg"
    assert pkg.version == "1.2.3"
    assert pkg.schema_version == WORKFLOW_PACKAGE_SCHEMA_VERSION
    assert pkg.methodology_family == "authored_model"
    assert pkg.recipe_fingerprint == ""
    assert pkg.methodology_fingerprint == ""
    form = pkg.compiled_form
    assert form["schema_version"] == WORKFLOW_PACKAGE_SCHEMA_VERSION
    model = form["model"]
    assert model["model_slug"] == "facility_agg"
    assert model["name"] == "Facility Aggregation"
    assert model["source_kind"] == "manual"
    assert model["document_fingerprint"]
    assert form["model_fingerprint"] == model["document_fingerprint"]
    params = form["parameters"]
    assert params == [{
        "name": "radius", "kind": "number", "required": True,
        "default": 500, "unit": "",
    }]


# ── 指纹纪律 ─────────────────────────────────────────────────────────────

def test_fingerprint_is_canonical_sha256_of_compiled_form() -> None:
    """指纹 = compiled_form 的 canonical JSON sha256（与 package.py 同算法）。"""
    compiled, _ = compile_document(_chain_doc(), model_slug="facility_agg")
    payload = json.dumps(
        compiled.package.compiled_form, sort_keys=True,
        ensure_ascii=False, separators=(",", ":"))
    assert compiled.package.fingerprint == \
        hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_fingerprint_deterministic_and_layout_immune() -> None:
    """同输入两次编译同指纹；仅 layout 变化不改指纹（UI 坐标不入科学面）。"""
    first, _ = compile_document(_chain_doc(), model_slug="facility_agg")
    second, _ = compile_document(_chain_doc(), model_slug="facility_agg")
    assert first.package.fingerprint == second.package.fingerprint
    laid_out = _chain_doc(layout={"buffered": ModelLayoutPos(x=10, y=20)})
    moved, _ = compile_document(laid_out, model_slug="facility_agg")
    assert moved.package.fingerprint == first.package.fingerprint


# ── 门禁与词法 ───────────────────────────────────────────────────────────

def test_error_finding_blocks_compile() -> None:
    """环 → error finding → 编译拒绝返回 None（fail-closed，不半编译）。"""
    doc = _chain_doc()
    doc.edges.append(ModelEdge(from_node="map", from_port="out",
                               to_node="buffered", to_port="in"))
    compiled, findings = compile_document(doc, model_slug="facility_agg")
    assert compiled is None
    cycle = next(f for f in findings if f.code == F_CYCLE)
    assert cycle.severity == SEVERITY_ERROR


@pytest.mark.parametrize("bad_slug", ["", "AB", "ab", "9lead", "has-dash",
                                      "has space", "x" * 42, "Ünïcode"])
def test_invalid_slug_raises(bad_slug: str) -> None:
    """slug 词法 ^[a-z][a-z0-9_]{2,40}$（总长 3–41）之外的输入一律 ValueError。"""
    with pytest.raises(ValueError):
        compile_document(_chain_doc(), model_slug=bad_slug)


def test_invalid_version_raises() -> None:
    """非严格 semver 的 version → ValueError。"""
    with pytest.raises(ValueError):
        compile_document(_chain_doc(), model_slug="facility_agg", version="1.0")


def test_primary_output_falls_back_to_topo_tail() -> None:
    """无显式输出 → 主输出 = 最后拓扑序节点（warning 不阻断编译）。"""
    doc = _chain_doc(outputs=(), output_node=False)
    compiled, findings = compile_document(doc, model_slug="facility_agg")
    assert compiled is not None
    assert compiled.typed_graph.primary_output == "aggregate"
    # 输出缺失是 warning（诚实披露），不是 error。
    assert not any(f.severity == SEVERITY_ERROR for f in findings)


# ── roundtrip 投影 ───────────────────────────────────────────────────────

def test_roundtrip_projection_restores_author_view() -> None:
    """投影全部以作者 id 表达（wire id 经 node_id_map 反解）；算子从 wire
    node_id 第二段读取（与 driver 同一解析）；有损项显式声明。"""
    doc = _chain_doc(layout={"buffered": ModelLayoutPos(x=5, y=5)})
    compiled, _ = compile_document(doc, model_slug="facility_agg")
    proj = roundtrip_projection(compiled)
    assert proj["primary_output"] == "model_output"
    nodes = {n["node_id"]: n for n in proj["nodes"]}
    assert set(nodes) == {"facilities", "buffered", "aggregate", "stats_out",
                          "map", "model_output"}
    assert nodes["facilities"]["role"] == "facility"
    assert nodes["aggregate"]["capability"] == "admin_aggregation"
    assert nodes["buffered"]["op"] == "buffer"
    assert nodes["buffered"]["params"] == {"distance_m": 500}
    assert nodes["facilities"]["outputs"][0]["artifact_type"] == "poi_feature_set"
    assert nodes["aggregate"]["inputs"][0]["artifact_type"] == "feature_collection"
    edges = proj["edges"]
    assert len(edges) == 5
    assert edges[0] == {"from": "facilities.facility",
                        "to": "buffered.in"}
    # 投影里不存在 wire 形态 id（作者视图纯净）。
    assert not any("transform:" in n["node_id"] for n in proj["nodes"])
    assert not any("transform:" in e["from"] or "transform:" in e["to"]
                   for e in edges)
    # 有损项诚实声明（layout/title/description 不入 typed wire）。
    assert set(proj["lossy"]) == {"layout", "title", "description"}


def test_compile_rejects_after_params_reference_unknown() -> None:
    """$param 引用未声明参数是 error → 编译拒绝（findings 随附）。"""
    doc = _chain_doc()
    doc.nodes[1] = ModelNode(
        node_id="buffered", kind="transform", transform_op="buffer",
        params=ModelNodeParams(values={
            "distance_m": BoundedValue(value="$param:ghost")}))
    compiled, findings = compile_document(doc, model_slug="facility_agg")
    assert compiled is None
    assert findings  # 含 PARAM_REF_UNKNOWN error
    assert any(f.code == "PARAM_REF_UNKNOWN" for f in findings)
