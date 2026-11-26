"""模型导入转换器单测（from_project_workflow / from_session_plan_steps /
from_template / slugify_name）。

覆盖面：capability step → analysis 节点结构对账（含依赖边/共享数据输入/
汇点 output 链）、tool-only step 进 skipped 披露、session plan 复用
workflow_promotion（最小 available 行 + blockers 拒绝）、模板实例化
（template_id 传递 + 参数保留 + 深拷贝隔离）、slugify 确定性。
"""
from __future__ import annotations

from typing import Any, Dict, List

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
from app.services.model_authoring.imports import (
    from_project_workflow,
    from_session_plan_steps,
    from_template,
    slugify_name,
)


@pytest.fixture(scope="module", autouse=True)
def _loaded_registries():
    from app.lib.gis.artifacts import get_artifact_type_registry
    from app.lib.gis.capability_registry import get_capability_registry

    get_artifact_type_registry().load_builtins()
    get_capability_registry().load_builtins()


# ── from_project_workflow ────────────────────────────────────────────────

def _two_step_graph_spec() -> Dict[str, Any]:
    return {
        "steps": [
            {
                "step_id": "fetch_pois",
                "tool_name": "webgis_poi_query",
                "capability": "poi_query",
                "args_template": {"limit": 10},
                "input_roles": {"geojson": "primary_dataset"},
                "dependencies": [],
            },
            {
                "step_id": "aggregate_by_district",
                "tool_name": "webgis_admin_aggregation",
                "capability": "admin_aggregation",
                "args_template": {"bound_ref": "art_1"},
                "input_roles": {"geojson": "primary_dataset"},
                "dependencies": ["fetch_pois"],
            },
        ],
    }


def test_from_project_workflow_structure() -> None:
    """capability step → analysis 节点；共享 subject 数据输入；依赖边与汇点 output。"""
    doc, skipped = from_project_workflow(
        _two_step_graph_spec(), name="POI 聚合", source_ref="wf-123")
    assert skipped == []
    assert doc.source.kind == "project_workflow"
    assert doc.source.ref == "wf-123"
    kinds = {n.node_id: n.kind for n in doc.nodes}
    # 共享首个输入角色（primary_dataset 非 DATA_ROLES → 宽 subject）。
    assert kinds == {
        "data_subject": "data_input",
        "fetch_pois": "analysis",
        "aggregate_by_district": "analysis",
        "output_aggregate_by_district": "output",
    }
    by_id = {n.node_id: n for n in doc.nodes}
    assert by_id["fetch_pois"].capability == "poi_query"
    assert by_id["fetch_pois"].params.values["limit"].value == 10
    assert by_id["aggregate_by_district"].capability == "admin_aggregation"
    assert "bound_ref" not in by_id["aggregate_by_district"].params.values
    edge_pairs = {(e.from_node, e.from_port, e.to_node, e.to_port)
                  for e in doc.edges}
    assert edge_pairs == {
        ("data_subject", "subject", "fetch_pois", "in"),
        ("fetch_pois", "out", "aggregate_by_district", "in"),
        ("aggregate_by_district", "out",
         "output_aggregate_by_district", "in"),
    }
    assert doc.output_node_ids == ["output_aggregate_by_district"]


def test_imported_document_validates_clean() -> None:
    """导入产物过静态验证：无 error 级 finding（与 validate 联动对账）。"""
    from app.services.model_authoring.contracts import SEVERITY_ERROR
    from app.services.model_authoring.validate import validate_document

    doc, _skipped = from_project_workflow(
        _two_step_graph_spec(), name="POI 聚合", source_ref="wf-123")
    errors = [f for f in validate_document(doc)
              if f.severity == SEVERITY_ERROR]
    assert errors == []


def test_tool_only_step_disclosed_in_skipped() -> None:
    """无 capability 的 tool-only step 不静默丢弃：进 skipped 披露。"""
    spec = {
        "steps": [
            {"step_id": "raw_tool", "tool_name": "raw_tool_x",
             "args_template": {"zoom": 12}},
            _two_step_graph_spec()["steps"][0],
        ],
    }
    doc, skipped = from_project_workflow(spec, name="m", source_ref="wf-9")
    assert skipped == [{
        "step_id": "raw_tool",
        "reason": "no capability (tool-only step not convertible)",
    }]
    node_ids = [n.node_id for n in doc.nodes]
    assert "raw_tool" not in node_ids
    assert "fetch_pois" in node_ids


def test_unconverted_dependency_disclosed() -> None:
    """依赖指向被跳过的 step → 边丢弃且 skipped 披露。"""
    spec = {
        "steps": [
            {"step_id": "raw_tool", "tool_name": "raw_tool_x"},
            {"step_id": "consumer", "tool_name": "t",
             "capability": "admin_aggregation",
             "dependencies": ["raw_tool"]},
        ],
    }
    doc, skipped = from_project_workflow(spec, name="m", source_ref="wf-9")
    reasons = [s["reason"] for s in skipped if s["step_id"] == "consumer"]
    assert any("raw_tool" in r for r in reasons)
    assert not any(e.from_node == "raw_tool" for e in doc.edges)


def test_invalid_role_maps_to_subject() -> None:
    """非法角色值（不在 DATA_ROLES）→ 宽 subject（不编造词表外角色）。"""
    spec = {"steps": [{
        "step_id": "fetch_pois", "tool_name": "t",
        "capability": "poi_query",
        "input_roles": {"geojson": "primary_dataset"},
    }]}
    doc, skipped = from_project_workflow(spec, name="m", source_ref="r")
    assert skipped == []
    data_nodes = [n for n in doc.nodes if n.kind == "data_input"]
    assert [n.role for n in data_nodes] == ["subject"]


def test_empty_graph_spec_raises() -> None:
    """空/缺 steps 的 graph_spec → ValueError（fail-closed）。"""
    with pytest.raises(ValueError):
        from_project_workflow({"steps": []}, name="m", source_ref="r")
    with pytest.raises(ValueError):
        from_project_workflow({}, name="m", source_ref="r")


# ── from_session_plan_steps（复用 workflow_promotion）────────────────────

def test_from_session_plan_rows() -> None:
    """最小 available 行 → doc；args 经 promotion._clean_args 同纪律净化。"""
    rows: List[Dict[str, Any]] = [{
        "capability": "poi_query",
        "resolved_tool": "webgis_poi_query",
        "status": "available",
        "purpose": "获取 POI",
        "params": {"limit": 10, "bound_ref": "art_1",
                   "resolved_algorithm": "poi.local"},
        "depends_on": [],
    }]
    doc = from_session_plan_steps(rows, name="计划导入", source_ref="plan-7")
    assert doc.source.kind == "session_plan"
    assert doc.source.ref == "plan-7"
    analysis = [n for n in doc.nodes if n.kind == "analysis"]
    assert len(analysis) == 1
    assert analysis[0].capability == "poi_query"
    assert analysis[0].params.values["limit"].value == 10
    assert "bound_ref" not in analysis[0].params.values
    assert "resolved_algorithm" not in analysis[0].params.values
    # 汇点 output 已链（诚实完整性）。
    assert doc.output_node_ids


def test_session_plan_blockers_rejected() -> None:
    """blockers 非空（failed 行）→ ValueError，消息含 blockers。"""
    rows = [{"capability": "poi_query", "status": "failed"}]
    with pytest.raises(ValueError) as excinfo:
        from_session_plan_steps(rows, name="m", source_ref="plan-7")
    assert "blockers" in str(excinfo.value)
    assert "failed" in str(excinfo.value)


def test_session_plan_without_capability_rows_rejected() -> None:
    """无可导入 capability 行 → ValueError（不静默产空图）。"""
    with pytest.raises(ValueError):
        from_session_plan_steps([], name="m", source_ref="plan-7")


# ── from_template ────────────────────────────────────────────────────────

def _template_doc() -> ModelDocument:
    return ModelDocument(
        name="Facility Aggregation",
        source=ModelSource(kind="manual"),
        nodes=[
            ModelNode(node_id="facilities", kind="data_input", role="facility"),
            ModelNode(node_id="aggregate", kind="analysis",
                      capability="admin_aggregation",
                      params=ModelNodeParams(values={
                          "min_score": BoundedValue(value="$param:threshold")})),
            ModelNode(node_id="model_output", kind="output"),
        ],
        edges=[
            ModelEdge(from_node="facilities", from_port="facility",
                      to_node="aggregate", to_port="in"),
            ModelEdge(from_node="aggregate", from_port="out",
                      to_node="model_output", to_port="in"),
        ],
        params=[ModelParamSpec(
            name="threshold", kind="number", required=False,
            default=BoundedValue(value=3))],
        output_node_ids=["model_output"],
    )


def test_from_template_instance() -> None:
    """模板实例化：name 覆盖、source=template、template_id 传递、节点/参数原样。"""
    template = _template_doc()
    instance = from_template(template, name="我的版")
    assert instance.name == "我的版"
    assert instance.source.kind == "template"
    assert instance.template_id == "facilityaggregation"
    assert instance.source.ref == "facilityaggregation"
    # 节点/参数原样保留（参数是模板的一部分）。
    assert [n.node_id for n in instance.nodes] == \
        [n.node_id for n in template.nodes]
    assert instance.params[0].name == "threshold"
    assert instance.params[0].default.value == 3
    assert instance.output_node_ids == ["model_output"]


def test_from_template_is_deep_copy() -> None:
    """实例与模板零共享可变状态（deepcopy 隔离）。"""
    template = _template_doc()
    instance = from_template(template, name="fork")
    instance.params[0].default = BoundedValue(value=99)
    instance.nodes[1].params.values["extra"] = BoundedValue(value=1)
    assert template.params[0].default.value == 3
    assert "extra" not in template.nodes[1].params.values


def test_from_template_uses_explicit_template_id() -> None:
    """模板自身已带 template_id → 实例继承该 id（不重新 slugify）。"""
    template = _template_doc()
    template.template_id = "agg_v2"
    instance = from_template(template, name="fork")
    assert instance.template_id == "agg_v2"


# ── slugify_name ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("", "model"),
    ("Facility Aggregation", "facilityaggregation"),
    ("POI 半径分析", "poimmmm"),          # 非 ASCII → "m"，空白剔除
    ("9lives", "m9lives"),                # 首字符须为字母
    ("AB-CD_12", "abcd_12"),
    ("a", "amm"),                         # 填充到最小长度 3
])
def test_slugify_name_deterministic(raw: str, expected: str) -> None:
    assert slugify_name(raw) == expected


def test_slugify_name_truncates_to_40() -> None:
    assert len(slugify_name("x" * 80)) == 40
    assert slugify_name("x" * 80) == "x" * 40
