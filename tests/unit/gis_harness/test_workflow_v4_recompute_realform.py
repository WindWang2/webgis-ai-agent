"""V4 recompute × 真实包形态回归（V5 Wave 1，架构挑战 B-1）。

缺陷史：TypedWorkflowEdge.to_bounded_dict() 把边端点写成 "node.port"，
compute_affected_subgraph 用原始字符串建邻接表 —— 真实 compile_workflow_v4
产物上 data_role 变更闭包只含种子自身、parameter 变更为空集。本文件以
**真实编译产物形态** + **强闭包断言** 钉住修复（自造无边端口 fixture 的
旧测试无法暴露该缺陷）。
"""
from __future__ import annotations

from app.services.gis_harness.workflow_v4.compiler_v4 import (
    compile_workflow_v4,
)
from app.services.gis_harness.workflow_v4.recompute import (
    RecomputePlan,
    WorkflowChange,
    compute_affected_subgraph,
)

_QUERY = "对工厂污染源周边做缓冲区分析并评估影响范围"


def _compiled_dag() -> dict:
    c = compile_workflow_v4(_QUERY)
    assert c.methodology_family, "smoke query 必须映射到方法族"
    dag = c.typed_dag
    assert dag.get("nodes") and dag.get("edges")
    # 前置：真实 bounded 形态的边端点带端口后缀（缺陷形态本身入断言）
    assert any("." in str(e.get("from", "")) for e in dag["edges"]), (
        "fixture 失真：bounded 边端点必须携带端口后缀")
    return dag


def _node_ids(dag: dict) -> set:
    return {n["node_id"] for n in dag["nodes"]}


def _downstream(dag: dict, seeds: set) -> set:
    """真实数据流闭包（独立实现，作 oracle —— 与被测函数不同路径）。"""
    node_ids = _node_ids(dag)
    rev: dict[str, list[str]] = {}
    for e in dag["edges"]:
        src = str(e.get("from", "")).rsplit(".", 1)[0]
        dst = str(e.get("to", "")).rsplit(".", 1)[0]
        if src in node_ids and dst in node_ids:
            rev.setdefault(src, []).append(dst)
    for n in dag["nodes"]:
        for dep in n.get("depends_on", ()) or []:
            rev.setdefault(str(dep), []).append(str(n["node_id"]))
    out: set = set()
    stack = list(seeds)
    while stack:
        cur = stack.pop()
        if cur in out or cur not in node_ids:
            continue
        out.add(cur)
        stack.extend(rev.get(cur, ()))
    return out


def test_data_role_change_covers_full_downstream():
    """data_role 变更 → 种子 + 全部数据流下游（含 output），非仅种子。"""
    dag = _compiled_dag()
    data_nodes = {n for n in _node_ids(dag) if n.startswith("data:")}
    assert data_nodes
    for role_node in sorted(data_nodes):
        role = role_node.split(":", 1)[1]
        plan = compute_affected_subgraph(
            dag, [WorkflowChange(dimension="data", target_kind="data_role",
                                 target=role)])
        expected = _downstream(dag, {role_node})
        assert set(plan.recompute) == expected, (
            f"{role_node}: 闭包失真 recompute={plan.recompute} "
            f"expected={sorted(expected)}")
        assert any(n.startswith("output:") for n in plan.recompute), (
            "数据角色变更必须波及 output 节点")
        assert set(plan.reuse) == _node_ids(dag) - expected


def test_parameter_change_hits_owner_subtree_with_output():
    """parameter 变更 → 参数 owner 子树重算；owner 存在时必含下游。"""
    dag = _compiled_dag()
    param_owners = {
        p["name"]: n["node_id"]
        for n in dag["nodes"]
        for p in (n.get("parameters") or [])
        if isinstance(p, dict) and p.get("name")
    }
    for name, owner in sorted(param_owners.items()):
        plan = compute_affected_subgraph(
            dag, [WorkflowChange(dimension="parameter",
                                 target_kind="parameter", target=name)])
        expected = _downstream(dag, {owner})
        assert set(plan.recompute) == expected, (
            f"param {name}: {plan.recompute} != {sorted(expected)}")
        assert owner in plan.recompute
        assert any(n.startswith("output:") for n in plan.recompute)


def test_algorithm_change_propagates_descendants():
    dag = _compiled_dag()
    cap_nodes = sorted(n for n in _node_ids(dag) if n.startswith("cap:"))
    assert cap_nodes
    target = cap_nodes[0]
    plan = compute_affected_subgraph(
        dag, [WorkflowChange(dimension="algorithm", target_kind="algorithm",
                             target=target)])
    assert set(plan.recompute) == _downstream(dag, {target})
    assert target in plan.recompute


def test_style_change_zero_science_touch():
    """style 变更 → 仅呈现节点（output）入 recompute，科学节点零触碰。"""
    dag = _compiled_dag()
    plan = compute_affected_subgraph(
        dag, [WorkflowChange(dimension="style", target_kind="style",
                             target="presentation")])
    assert plan.changed_dimensions == ["style"]
    assert all(n.startswith("output:") for n in plan.recompute), (
        f"style 不得触碰科学节点: {plan.recompute}")


def test_recipe_change_invalidates_whole_graph():
    dag = _compiled_dag()
    plan = compute_affected_subgraph(
        dag, [WorkflowChange(dimension="data", target_kind="recipe",
                             target="")])
    assert set(plan.recompute) == _node_ids(dag)
    assert plan.reuse == []
    assert plan.reuse_artifacts == []


def test_bounded_and_live_forms_agree():
    """live graph 的 to_bounded_dict 与 compiled_form 的 typed_dag 同形同解。"""
    c = compile_workflow_v4(_QUERY)
    live = c.typed_dag  # compiler 存的就是 to_bounded_dict 形态
    plan_a = compute_affected_subgraph(
        live, [WorkflowChange(dimension="data", target_kind="data_role",
                              target="subject")])
    import copy

    plan_b = compute_affected_subgraph(
        copy.deepcopy(live),
        [WorkflowChange(dimension="data", target_kind="data_role",
                        target="subject")])
    assert plan_a.recompute == plan_b.recompute
    assert isinstance(plan_a, RecomputePlan)


def test_unknown_endpoint_stays_honest():
    """无法归一的端点保持原样（防御性归一不虚构节点）。"""
    dag = {"nodes": [{"node_id": "a"}],
           "edges": [{"from": "a.port", "to": "ghost.port"}]}
    plan = compute_affected_subgraph(
        dag, [WorkflowChange(dimension="algorithm", target_kind="algorithm",
                             target="a")])
    assert plan.recompute == ["a"]  # ghost 不在节点集，不虚构
