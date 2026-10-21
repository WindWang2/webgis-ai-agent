"""Workflow V6 — ready_set 派发集语义（P4 补强：R1-C1 同波并发防护面）。"""
from __future__ import annotations

from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import machine as M


def _dag() -> dict:
    return {
        "nodes": [
            {"node_id": "n.a"},
            {"node_id": "n.b"},
            {"node_id": "n.c", "depends_on": ["n.a", "n.b"]},
            {"node_id": "n.d", "depends_on": ["n.c"]},
        ],
        "edges": [],
    }


def test_root_nodes_are_ready() -> None:
    dag = _dag()
    ready = M.ready_set(dag, {})
    assert ready == ["n.a", "n.b"]  # 声明序稳定


def test_downstream_waits_for_all_upstreams() -> None:
    dag = _dag()
    states = {"n.a": C.NodeState.SUCCEEDED, "n.b": C.NodeState.PENDING}
    # n.c 等 n.b；n.b 自身无上游，PENDING 可派发。
    assert M.ready_set(dag, states) == ["n.b"]
    states["n.b"] = C.NodeState.SUCCEEDED
    assert M.ready_set(dag, states) == ["n.c"]


def test_skipped_upstream_counts_as_settled() -> None:
    dag = _dag()
    states = {"n.a": C.NodeState.SKIPPED, "n.b": C.NodeState.SUCCEEDED}
    assert "n.c" in M.ready_set(dag, states)


def test_running_upstream_blocks_downstream() -> None:
    # R1-C1：RUNNING 上游未结算 —— 下游与上游同波并发会读旧产物。
    dag = _dag()
    states = {"n.a": C.NodeState.RUNNING, "n.b": C.NodeState.SUCCEEDED}
    assert M.ready_set(dag, states) == []


def test_failed_upstream_blocks_downstream() -> None:
    dag = _dag()
    states = {"n.a": C.NodeState.FAILED, "n.b": C.NodeState.SUCCEEDED}
    assert "n.c" not in M.ready_set(dag, states)


def test_ready_state_always_dispatchable() -> None:
    dag = _dag()
    states = {"n.a": C.NodeState.READY}
    assert "n.a" in M.ready_set(dag, states)


def test_skip_decided_excludes_nodes() -> None:
    dag = _dag()
    ready = M.ready_set(dag, {}, skip_decided={"n.a"})
    assert "n.a" not in ready
    assert ready == ["n.b"]


def test_same_input_same_order() -> None:
    dag = _dag()
    states = {"n.a": C.NodeState.SUCCEEDED, "n.b": C.NodeState.READY}
    a = M.ready_set(dag, states)
    b = M.ready_set(dag, dict(states))
    # n.c 的上游 n.b 仍是 READY（未结算）—— n.c 不可同波派发（R1-C1）。
    assert a == b == ["n.b"]
