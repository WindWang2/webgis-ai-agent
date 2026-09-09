"""Workflow V4 —— 参数/重算/diff/获取/制图义务 单测（Wave 7-10）。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


from app.services.gis_harness.workflow_v4.acquisition import (
    plan_acquisition,
    plan_role_acquisition,
)
from app.services.gis_harness.workflow_v4.cartography import (
    CARTO_APPROXIMATE_UNCERTAINTY_DISPLAY,
    CARTO_DENSITY_NO_RAW_COUNT,
    CARTO_RATE_REQUIRES_DENOMINATOR,
    CARTO_SMALL_N_CHOROPLETH,
    evaluate_cartographic_obligations,
)
from app.services.gis_harness.workflow_v4.parameters import (
    extract_workflow_parameters,
    resolve_workflow_parameters,
)
from app.services.gis_harness.workflow_v4.recompute import (
    WorkflowChange,
    compute_affected_subgraph,
)


# ── Wave 7：参数 ─────────────────────────────────────────────────────────

def test_extract_parameters_from_contract_registry() -> None:
    """参数来自 parameter contracts 单一事实源（克里金契约）。"""
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )
    method = get_methodology_registry().method("interp.ordinary_kriging")
    params = extract_workflow_parameters(method, node_id="cap:block_kriging")
    names = {p.name for p in params}
    assert {"resolution", "variogram_model"} <= names
    assert all(p.node_id == "cap:block_kriging" for p in params)


def test_resolve_parameter_provenance_and_fallback() -> None:
    params = [SimpleNamespace(
        name="bandwidth", value_type="number", default=500.0,
        minimum=10.0, maximum=10000.0, enum_values=(), unit="meters",
        data_dependent_default="", node_id="n1", description="")]
    # user 覆盖优先
    r = resolve_workflow_parameters(params, user_values={"bandwidth": 800.0})
    assert r[0].provenance == "user" and r[0].value == 800.0
    # hint 次之
    r = resolve_workflow_parameters(params, hint_values={"bandwidth": 600.0})
    assert r[0].provenance == "hint"
    # 非法用户值 → 回落默认 + 披露（不阻塞、不静默接受）
    r = resolve_workflow_parameters(params, user_values={"bandwidth": -5})
    assert r[0].provenance == "recipe_default" and r[0].value == 500.0
    assert r[0].disclosure
    # 无值 → 默认
    r = resolve_workflow_parameters(params)
    assert r[0].provenance == "recipe_default" and r[0].disclosure == ""


# ── Wave 8：受影响子图 ───────────────────────────────────────────────────

_DAG = {
    "nodes": [
        {"node_id": "data:subject", "kind": "data_input", "depends_on": []},
        {"node_id": "cap:density", "kind": "analysis", "depends_on": []},
        {"node_id": "cap:aggregate", "kind": "analysis",
         "depends_on": ["cap:density"]},
        {"node_id": "output:density_surface", "kind": "output",
         "depends_on": []},
    ],
    "edges": [
        {"from": "data:subject", "to": "cap:density"},
        {"from": "cap:density", "to": "cap:aggregate"},
        {"from": "cap:density", "to": "output:density_surface"},
    ],
}


def test_input_role_change_propagates_downstream_only() -> None:
    plan = compute_affected_subgraph(_DAG, [
        WorkflowChange(dimension="data", target_kind="data_role",
                       target="subject")])
    assert set(plan.recompute) == {"data:subject", "cap:density",
                                   "cap:aggregate", "output:density_surface"}
    assert plan.reuse == []


def test_bounded_port_edges_propagate_closure() -> None:
    """回归：真实 to_bounded_dict 形状（边端点带 port 后缀 "node.port"）。

    修复前：邻接直接用原始端点，闭包在 live bounded 形状下静默断链
    （下游永不污染，reuse 被错误判安全）—— V6 Phase-0 后审计发现。
    """
    dag = {
        "nodes": [
            {"node_id": "data:subject", "kind": "data_input", "depends_on": []},
            {"node_id": "cap:density", "kind": "analysis", "depends_on": []},
            {"node_id": "output:density_surface", "kind": "output",
             "depends_on": []},
        ],
        "edges": [
            {"from": "data:subject.data", "to": "cap:density.input"},
            {"from": "cap:density.output",
             "to": "output:density_surface.product"},
        ],
    }
    plan = compute_affected_subgraph(dag, [
        WorkflowChange(dimension="data", target_kind="data_role",
                       target="subject")])
    assert set(plan.recompute) == {"data:subject", "cap:density",
                                   "output:density_surface"}
    assert plan.reuse == []


def test_upstream_change_keeps_unaffected_nodes_reusable() -> None:
    dag = dict(_DAG)
    dag["nodes"] = _DAG["nodes"] + [
        {"node_id": "cap:unrelated", "kind": "analysis", "depends_on": []}]
    plan = compute_affected_subgraph(dag, [
        WorkflowChange(dimension="data", target_kind="data_role",
                       target="subject")])
    assert "cap:unrelated" in plan.reuse
    assert "cap:density" in plan.recompute


def test_parameter_change_affects_only_owner_subtree() -> None:
    """真实 to_bounded_dict 形状（node.parameters 由 compiler 注入）。"""
    dag = {
        "nodes": [
            {"node_id": "cap:a", "kind": "analysis", "depends_on": [],
             "parameters": [{"name": "bandwidth"}]},
            {"node_id": "cap:b", "kind": "analysis", "depends_on": [],
             "parameters": []},
        ],
        "edges": [{"from": "cap:a", "to": "cap:b"}],
    }
    plan = compute_affected_subgraph(dag, [
        WorkflowChange(dimension="parameter", target_kind="parameter",
                       target="bandwidth")])
    assert set(plan.recompute) == {"cap:a", "cap:b"}


def test_real_compilation_parameters_flow_into_dag_nodes() -> None:
    """review MAJOR-6：编译管线的参数必须真实注入 owning 节点
    （recompute parameter 维度在生产路径可达，非死路径）。"""
    from app.services.gis_harness.workflow_v4.compiler_v4 import (
        compile_workflow_v4,
    )
    c = compile_workflow_v4(
        "根据监测站 PM2.5 数据生成全市浓度表面",
        profile={"featureCount": 200, "geometryTypes": ["Point"],
                 "crs": "EPSG:32648",
                 "fields": {"pm25": {"type": "number"}}},
    )
    if c.method_qualification.get("selected_id") != "interp.ordinary_kriging":
        pytest.skip("方法族路由变化 —— 参数注入仅对 kriging 断言")
    param_nodes = [
        n for n in c.typed_dag.get("nodes", []) if n.get("parameters")]
    assert param_nodes, "选中方法的参数必须注入 owning 节点"
    names = {p["name"] for n in param_nodes for p in n["parameters"]}
    assert "resolution" in names and "variogram_model" in names


def test_style_change_no_scientific_recompute() -> None:
    plan = compute_affected_subgraph(_DAG, [
        WorkflowChange(dimension="style", target_kind="style",
                       target="output:density_surface")])
    assert plan.recompute == ["output:density_surface"]
    assert any("呈现态" in e for e in plan.explanations)


def test_recipe_change_invalidates_whole_graph() -> None:
    plan = compute_affected_subgraph(_DAG, [
        WorkflowChange(dimension="data", target_kind="recipe", target="")])
    assert len(plan.recompute) == len(_DAG["nodes"])
    assert plan.reuse == []


# ── Wave 9：diff → recompute 桥接 ────────────────────────────────────────

def _package_for(query: str):
    from app.services.gis_harness.workflow_v4.compiler_v4 import (
        compile_workflow_v4,
    )
    from app.services.gis_harness.workflow_v4.package import (
        emit_workflow_package,
    )
    c = compile_workflow_v4(query, profile={
        "featureCount": 120, "geometryTypes": ["Point"]})
    return c, emit_workflow_package(c)


def test_diff_no_change_and_algorithm_change() -> None:
    from app.services.gis_harness.workflow_v4.diff import diff_workflow_packages
    c, p = _package_for("成都小学的分布情况")
    same = diff_workflow_packages(p, p)
    assert same.entries == [] and same.recompute_dimensions == []

    # 参数默认值变化 → parameter diff 条目 + parameter 维度
    cf = p.compiled_form.copy()
    cf["typed_dag"] = {
        "nodes": [{"node_id": "cap:a", "kind": "analysis",
                   "algorithm_id": "x.v1", "depends_on": []}],
        "edges": [], "primary_output": "",
    }
    nf = p.compiled_form.copy()
    nf["typed_dag"] = {
        "nodes": [{"node_id": "cap:a", "kind": "analysis",
                   "algorithm_id": "x.v2", "depends_on": []}],
        "edges": [], "primary_output": "",
    }
    p_old = p.model_copy(update={"compiled_form": cf})
    p_new = p.model_copy(update={"compiled_form": nf})
    d = diff_workflow_packages(p_old, p_new)
    assert any(e.kind == "algorithm_substitution" for e in d.entries)
    assert "algorithm" in d.recompute_dimensions
    # diff 的 change 可以直接喂 recompute
    plan = compute_affected_subgraph(
        nf["typed_dag"], [e.change for e in d.entries])
    assert plan.recompute == ["cap:a"]


def test_diff_obligation_disclosure_change() -> None:
    from app.services.gis_harness.workflow_v4.diff import diff_workflow_packages
    _, p = _package_for("成都小学的分布情况")
    cf = p.compiled_form.copy()
    cf["completion_contract"] = dict(
        cf.get("completion_contract") or {}, required_disclosures=["X_NEW"])
    p_new = p.model_copy(update={"compiled_form": cf})
    d = diff_workflow_packages(p, p_new)
    assert any(e.kind == "obligation_change" for e in d.entries)


# ── Wave 10：获取声明 + 制图义务 ─────────────────────────────────────────

def test_acquisition_declares_ordered_alternatives_never_fetch() -> None:
    plan = plan_role_acquisition("denominator", "unresolved",
                                 missing_policy="block")
    channels = [a.channel for a in plan.alternatives]
    assert channels[0] == "local" and channels[-1] == "synthetic_demo"
    assert all(not a.feasible for a in plan.alternatives
               if a.channel == "local")
    demo = plan.alternatives[-1]
    assert not demo.feasible  # 未显式 opt-in → 恒不可行
    plan2 = plan_role_acquisition("denominator", "unresolved",
                                  allow_synthetic_demo=True)
    assert plan2.alternatives[-1].feasible
    assert "演示" in plan2.alternatives[-1].disclosure


def test_acquisition_bound_role_local_feasible() -> None:
    plan = plan_acquisition([SimpleNamespace(
        role="subject", status="bound", acquisition="local",
        missing_policy="block")])
    assert plan.roles[0].feasible_channels == ["local", "derived"]
    # user_upload 恒为可行候选（是否真实提供归 runtime）
    assert not plan.synthetic_demo_allowed


def test_carto_rate_requires_denominator_blocks_on_missing() -> None:
    method = SimpleNamespace(method_id="density.admin_rate",
                             approximate=False)
    obls = evaluate_cartographic_obligations(
        method, role_states={"denominator": "blocked"})
    rate = next(o for o in obls
                if o.rule_code == CARTO_RATE_REQUIRES_DENOMINATOR)
    assert rate.on_violation == "block_method"
    assert rate.disclosure  # 阻断必须披露


def test_carto_small_n_and_approximate_rules() -> None:
    method = SimpleNamespace(method_id="distribution.point_distribution",
                             approximate=True)
    obls = evaluate_cartographic_obligations(
        method, role_states={}, profile={"regionCount": 2})
    codes = {o.rule_code for o in obls}
    assert CARTO_SMALL_N_CHOROPLETH in codes
    assert CARTO_APPROXIMATE_UNCERTAINTY_DISPLAY in codes
    small = next(o for o in obls
                 if o.rule_code == CARTO_SMALL_N_CHOROPLETH)
    assert small.on_violation == "degrade_with_disclosure"


def test_carto_density_no_raw_count_blocks_choropleth_secondary() -> None:
    """定量密度方法 + 显式 choropleth 次级表达 → block（不得以原始计数填色）。"""
    method = SimpleNamespace(method_id="density.kernel_surface",
                             approximate=False)
    obls = evaluate_cartographic_obligations(
        method, role_states={},
        secondary_cartography=("administrative_choropleth",))
    rule = next(o for o in obls
                if o.rule_code == CARTO_DENSITY_NO_RAW_COUNT)
    assert rule.on_violation == "block_method"
    # 无显式 choropleth 次级 → 仅 warn
    obls2 = evaluate_cartographic_obligations(method, role_states={})
    rule2 = next(o for o in obls2
                 if o.rule_code == CARTO_DENSITY_NO_RAW_COUNT)
    assert rule2.on_violation == "warn"


def test_carto_deterministic() -> None:
    method = SimpleNamespace(method_id="density.kernel_surface",
                             approximate=False)
    a = evaluate_cartographic_obligations(method, role_states={})
    b = evaluate_cartographic_obligations(method, role_states={})
    assert [o.to_bounded_dict() for o in a] == \
        [o.to_bounded_dict() for o in b]


def test_diff_change_targets_are_real_nodes_no_noop() -> None:
    """review MAJOR-5：method/family/obligation 变化的 change.target 必须
    投影到真实节点 —— diff→recompute 桥接无 no-op。"""
    from app.services.gis_harness.workflow_v4.diff import diff_workflow_packages
    _, p_old = _package_for("成都市小学分布情况")
    _, p_new = _package_for("分析成都便利店的空间密度")
    d = diff_workflow_packages(p_old, p_new)
    assert d.entries
    node_ids = {n.get("node_id")
                for n in p_new.compiled_form["typed_dag"].get("nodes", [])}
    targeted = [e for e in d.entries
                if e.change.target_kind in ("algorithm", "output")]
    assert targeted, "方法面变化必须产生节点级 target"
    dead = [e for e in targeted
            if e.change.target and e.change.target not in node_ids
            and e.change.target_kind != "recipe"]
    assert dead == [], f"悬空 target（no-op）: {[e.path for e in dead]}"
    # 全链桥接：changes 喂 recompute 必须产生非空重算集（recipe 变化除外）
    plan = compute_affected_subgraph(
        p_new.compiled_form["typed_dag"], [e.change for e in d.entries])
    assert plan.recompute


def test_diff_parameter_change_detected_and_recomputes_owner() -> None:
    """review MAJOR-5/6：参数变化 diff → parameter 维度 → owner 子树重算。"""
    from app.services.gis_harness.workflow_v4.diff import diff_workflow_packages
    _, p = _package_for("根据监测站 PM2.5 数据生成全市浓度表面")
    assert p.compiled_form.get("parameters"), "kriging 编译必须有解析参数"
    cf = json.loads(json.dumps(p.compiled_form))
    for prm in cf["parameters"]:
        if prm.get("name") == "resolution":
            prm["value"] = 9
            prm["provenance"] = "user"
    p_new = p.model_copy(update={"compiled_form": cf})
    d = diff_workflow_packages(p, p_new)
    pchg = [e for e in d.entries if e.kind == "parameter_change"]
    assert pchg and pchg[0].change.target == "resolution"
    plan = compute_affected_subgraph(
        p_new.compiled_form["typed_dag"], [e.change for e in pchg])
    # 参数 owner 节点必须真实重算（node.parameters 由 compiler 注入）
    assert any(n.startswith("cap:") for n in plan.recompute), plan.recompute


def test_blocked_stages_surface_in_package() -> None:
    """review MINOR-9：blocked 阶段显式进入包 compiled form。"""
    _, p = _package_for("成都市小学分布情况")
    assert "blocked_stages" in p.compiled_form
    assert isinstance(p.compiled_form["blocked_stages"], list)
