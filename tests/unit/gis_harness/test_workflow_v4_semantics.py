"""Workflow V4 —— 参数/重算/diff/获取/制图义务 单测（Wave 7-10）。"""
from __future__ import annotations

from types import SimpleNamespace


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
    dag = {
        "nodes": [
            {"node_id": "cap:a", "kind": "analysis",
             "parameters": [{"name": "bandwidth"}]},
            {"node_id": "cap:b", "kind": "analysis", "depends_on": []},
        ],
        "edges": [{"from": "cap:a", "to": "cap:b"}],
    }
    plan = compute_affected_subgraph(dag, [
        WorkflowChange(dimension="parameter", target_kind="parameter",
                       target="bandwidth")])
    assert set(plan.recompute) == {"cap:a", "cap:b"}


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


def test_diff_no_change_and_parameter_change() -> None:
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
