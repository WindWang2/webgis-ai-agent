"""Workflow V6 — DAG 邻接/上游/端口对齐纯函数（P4 补强：machine.py 图面）。"""
from __future__ import annotations

from app.services.workflow_runtime import machine as M


def _dag() -> dict:
    return {
        "nodes": [
            {"node_id": "data:subject"},
            {"node_id": "cap:buffer", "depends_on": ["data:subject"]},
            {"node_id": "cap:overlay", "depends_on": ["data:subject"]},
            {"node_id": "output:zone", "depends_on": ["cap:buffer", "cap:overlay"]},
        ],
        "edges": [
            {"from": "data:subject.value", "to": "cap:buffer.input"},
            {"from": "data:subject.value", "to": "cap:overlay.base"},
            {"from": "cap:buffer.result", "to": "output:zone.features"},
            {"from": "cap:overlay.result", "to": "output:zone.overlay"},
        ],
    }


def test_build_adjacency_covers_edges_and_depends_on() -> None:
    adj = M.build_adjacency(_dag())
    assert set(adj["data:subject"]) == {"cap:buffer", "cap:overlay"}
    assert set(adj["cap:buffer"]) == {"output:zone"}
    assert adj.get("output:zone", []) == []  # 汇点无出边不建键


def test_adjacency_dedupes_node_ids_and_ignores_unknown() -> None:
    dag = {
        "nodes": [{"node_id": "a"}],
        "edges": [{"from": "a.out", "to": "a.in"}, {"from": "ghost.x", "to": "a.in"}],
    }
    adj = M.build_adjacency(dag)
    # 自环（a→a）保留；未知端点的裸串不可归位时按原文保留但可断言不指向 a。
    assert adj.get("a") == ["a"]


def test_upstream_of_is_inverse() -> None:
    dag = _dag()
    up = M.upstream_of(dag)
    assert set(up["output:zone"]) == {"cap:buffer", "cap:overlay"}
    assert set(up["cap:buffer"]) == {"data:subject"}
    assert "output:zone" not in up.get("cap:buffer", [])


def test_upstream_ports_strips_node_prefix() -> None:
    ports = M.upstream_ports(_dag())
    assert ("data:subject", "input") in ports["cap:buffer"]
    assert ("cap:buffer", "features") in ports["output:zone"]
    assert ("cap:overlay", "overlay") in ports["output:zone"]


def test_upstream_ports_handles_bare_endpoints() -> None:
    dag = {
        "nodes": [{"node_id": "a"}, {"node_id": "b"}],
        "edges": [{"from": "a", "to": "b"}],
    }
    ports = M.upstream_ports(dag)
    assert ports["b"] == [("a", "")]


def test_empty_dag_is_safe() -> None:
    assert M.build_adjacency({}) == {}
    assert M.upstream_of({}) == {}
    assert M.upstream_ports({}) == {}
