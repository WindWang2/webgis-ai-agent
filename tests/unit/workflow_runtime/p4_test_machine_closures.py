"""Workflow V6 — 闭包计算与转移裁决门（P4 补强：machine.py closure/transition）。"""
from __future__ import annotations

from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import machine as M


def _diamond_dag() -> dict:
    return {
        "nodes": [
            {"node_id": "root"},
            {"node_id": "left", "depends_on": ["root"]},
            {"node_id": "right", "depends_on": ["root"]},
            {"node_id": "sink", "depends_on": ["left", "right"]},
        ],
        "edges": [],
    }


def test_downstream_closure_is_transitive() -> None:
    dag = _diamond_dag()
    closure = M.downstream_closure(dag, {"root"})
    assert closure == {"left", "right", "sink"}


def test_downstream_closure_from_mid_node_excludes_upstream() -> None:
    dag = _diamond_dag()
    assert M.downstream_closure(dag, {"left"}) == {"sink"}


def test_upstream_closure_is_transitive() -> None:
    dag = _diamond_dag()
    assert M.upstream_closure(dag, {"sink"}) == {"left", "right", "root"}


def test_closures_are_cycle_safe() -> None:
    dag = {
        "nodes": [
            {"node_id": "a"}, {"node_id": "b", "depends_on": ["a"]},
            {"node_id": "a2", "depends_on": ["b"]},
        ],
        "edges": [{"from": "a2.out", "to": "a.in"}],  # 环：a→b→a2→a
    }
    closure = M.downstream_closure(dag, {"a"})
    assert closure == {"b", "a2", "a"}


def test_empty_seeds_give_empty_closure() -> None:
    dag = _diamond_dag()
    assert M.downstream_closure(dag, set()) == set()
    assert M.upstream_closure(dag, set()) == set()


def test_check_transition_delegates_to_contracts() -> None:
    # 单入口直通门：合法/非法各一，契约层的具体合法表由 contracts 测试持有。
    assert M.check_transition(C.NodeState.RUNNING, C.NodeState.SUCCEEDED) is None
    assert M.check_transition("NOT_A_STATE", C.NodeState.SUCCEEDED) is not None
