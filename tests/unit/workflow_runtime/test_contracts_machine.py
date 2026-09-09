"""Workflow Runtime V5 —— contracts/machine 单元测试（Wave 2）。"""
from __future__ import annotations

import pytest

from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import machine as M


# ── 转移表正/负/边界 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("from_state,to_state", [
    (C.NodeState.PENDING, C.NodeState.READY),
    (C.NodeState.PENDING, C.NodeState.BLOCKED),
    (C.NodeState.READY, C.NodeState.RUNNING),
    (C.NodeState.READY, C.NodeState.STALE),
    (C.NodeState.RUNNING, C.NodeState.SUCCEEDED),
    (C.NodeState.RUNNING, C.NodeState.FAILED),
    (C.NodeState.RUNNING, C.NodeState.CANCELLED),
    (C.NodeState.FAILED, C.NodeState.READY),
    (C.NodeState.BLOCKED, C.NodeState.READY),
    (C.NodeState.SUCCEEDED, C.NodeState.STALE),
    (C.NodeState.STALE, C.NodeState.READY),
    (C.NodeState.STALE, C.NodeState.SUCCEEDED),   # 复用证明解除
    (C.NodeState.SKIPPED, C.NodeState.READY),      # 重新资格化
    (C.NodeState.RUNNING, C.NodeState.READY),      # 孤儿恢复专用（租约门控）
])
def test_legal_transitions(from_state, to_state):
    assert C.validate_transition(from_state, to_state) is None


@pytest.mark.parametrize("from_state,to_state", [
    (C.NodeState.PENDING, C.NodeState.SUCCEEDED),   # 跳级
    (C.NodeState.PENDING, C.NodeState.STALE),
    (C.NodeState.RUNNING, C.NodeState.STALE),        # quiescence 门保证不发生
    (C.NodeState.SUCCEEDED, C.NodeState.RUNNING),
    (C.NodeState.SUCCEEDED, C.NodeState.FAILED),
    (C.NodeState.SUCCEEDED, C.NodeState.CANCELLED),  # 终态不可取消
    (C.NodeState.CANCELLED, C.NodeState.READY),
    (C.NodeState.SKIPPED, C.NodeState.SUCCEEDED),
    (C.NodeState.FAILED, C.NodeState.SUCCEEDED),
    ("BOGUS", C.NodeState.READY),
])
def test_illegal_transitions(from_state, to_state):
    assert C.validate_transition(from_state, to_state) is not None


def test_ready_set_requires_all_upstreams_ok():
    dag = {
        "nodes": [{"node_id": "a"}, {"node_id": "b"}, {"node_id": "c"}],
        "edges": [
            {"from": "a.out", "to": "c.in"},   # bounded 端口形态
            {"from": "b.out", "to": "c.in"},
        ],
    }
    states = {"a": C.NodeState.SUCCEEDED, "b": C.NodeState.PENDING}
    # b 无上游（源节点）→ READY；c 因 b 未决未就绪
    assert M.ready_set(dag, states) == ["b"]
    states["b"] = C.NodeState.SUCCEEDED
    assert M.ready_set(dag, states) == ["c"]
    # 上游 SKIPPED 也放行（optional 链）：b 置 SKIPPED 后不再是 PENDING，
    # 只有 a 在就绪集；a 完成后 c 依赖 (SUCCEEDED, SKIPPED) 即就绪
    states["a"] = C.NodeState.PENDING
    states["b"] = C.NodeState.SKIPPED
    states["c"] = C.NodeState.PENDING
    assert M.ready_set(dag, states) == ["a"]
    states["a"] = C.NodeState.SUCCEEDED
    assert M.ready_set(dag, states) == ["c"]


def test_ready_set_declaration_order_stable():
    dag = {"nodes": [{"node_id": "z"}, {"node_id": "a"}], "edges": []}
    states = {"z": C.NodeState.PENDING, "a": C.NodeState.PENDING}
    assert M.ready_set(dag, states) == ["z", "a"]


def test_downstream_closure_cycle_safe():
    dag = {
        "nodes": [{"node_id": n} for n in ("a", "b", "c")],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "c"},
            {"from": "c", "to": "a"},  # 防御：环不炸
        ],
    }
    assert M.downstream_closure(dag, {"a"}) == {"b", "c", "a"}


# ── 实例级状态裁决 ───────────────────────────────────────────────────────

def _opt(node, optional):
    return {node: optional}


def test_instance_status_rules():
    f = C.instance_status_from_nodes
    assert f({}, optional_nodes={}) == C.InstanceStatus.RUNNING
    assert f({"a": C.NodeState.SUCCEEDED, "b": C.NodeState.SKIPPED},
             optional_nodes={"a": False, "b": True}) == C.InstanceStatus.SUCCEEDED
    # 非 optional FAILED → failed
    assert f({"a": C.NodeState.SUCCEEDED, "b": C.NodeState.FAILED},
             optional_nodes={"a": False, "b": False}) == C.InstanceStatus.FAILED
    # optional FAILED → 仍 running（可继续推进）
    assert f({"a": C.NodeState.RUNNING, "b": C.NodeState.FAILED},
             optional_nodes={"a": False, "b": True}) == C.InstanceStatus.RUNNING
    # 非 optional BLOCKED → failed（诚实终态，不悬挂）
    assert f({"a": C.NodeState.BLOCKED},
             optional_nodes={"a": False}) == C.InstanceStatus.FAILED


def test_changes_fingerprint_stable_and_order_insensitive():
    c1 = C.PendingChange(dimension="data", target_kind="data_role",
                         target="subject")
    c2 = C.PendingChange(dimension="data", target_kind="data_role",
                         target="subject", detail="x")
    # 同语义（同 dimension/kind/target）不同 detail → 不同指纹（detail 是语义）
    assert C.changes_fingerprint([c1]) != C.changes_fingerprint([c2])
    assert C.changes_fingerprint([c1, c2]) == C.changes_fingerprint([c2, c1])


def test_node_state_bounded_projection():
    st = C.NodeRuntimeState(instance_id="wi-1", node_id="cap:x",
                            state=C.NodeState.RUNNING,
                            binding={"big": "x" * 9000})
    d = st.to_bounded_dict()
    assert d["binding"]["_truncated"] is True
    assert len(d["node_id"]) <= 64
