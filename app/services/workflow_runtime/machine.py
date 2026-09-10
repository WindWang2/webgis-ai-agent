"""Workflow Runtime V5 —— 纯函数节点状态机与 DAG 就绪计算。

只有本模块裁决「转移是否合法」与「哪些节点 READY」；store 落地 CAS、
driver 驱动推进。O(V+E)、确定性、零 I/O。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app.services.workflow_runtime.contracts import (
    NodeState,
    validate_transition,
)


def build_adjacency(dag: Dict[str, Any]) -> Dict[str, List[str]]:
    """compiled typed_dag dict → {node_id: [直接下游]}（数据流边 ∪ 结构依赖）。"""
    node_ids = {str(n.get("node_id", "")) for n in dag.get("nodes") or []}
    node_ids.discard("")

    def _norm(endpoint: str) -> str:
        ep = str(endpoint or "")
        if ep in node_ids:
            return ep
        head, sep, _tail = ep.rpartition(".")
        return head if sep and head and head in node_ids else ep

    adjacency: Dict[str, List[str]] = {}
    for e in dag.get("edges") or []:
        src, dst = _norm(e.get("from", "")), _norm(e.get("to", ""))
        if src and dst:
            adjacency.setdefault(src, []).append(dst)
    for n in dag.get("nodes") or []:
        for dep in n.get("depends_on", ()) or []:
            adjacency.setdefault(str(dep), []).append(
                str(n.get("node_id", "")))
    return adjacency


def upstream_of(dag: Dict[str, Any]) -> Dict[str, List[str]]:
    """compiled typed_dag dict → {node_id: [直接上游]}。"""
    downstream = build_adjacency(dag)
    upstream: Dict[str, List[str]] = {}
    for src, dsts in downstream.items():
        for dst in dsts:
            upstream.setdefault(dst, []).append(src)
    return upstream


def upstream_ports(dag: Dict[str, Any]) -> Dict[str, List]:
    """{node_id: [(src, to_port)]}（按边序；to_port 剥节点前缀）。

    bounded 边形态 ``"to": "node.port"`` → port 取 rsplit；与节点声明
    的输入端口名对齐 —— fan-in ≥2 时按位置对齐会错配（R1-m6）。
    """
    node_ids = {str(n.get("node_id", "")) for n in dag.get("nodes") or []}
    node_ids.discard("")
    out: Dict[str, List] = {}
    for e in dag.get("edges") or []:
        dst_raw = str(e.get("to", ""))
        head, sep, port = dst_raw.rpartition(".")
        dst = head if sep and head in node_ids else dst_raw
        src_raw = str(e.get("from", ""))
        shead, ssep, _sport = src_raw.rpartition(".")
        src = shead if ssep and shead in node_ids else src_raw
        if dst and src:
            out.setdefault(dst, []).append((src, port if sep else ""))
    return out


def ready_set(
    dag: Dict[str, Any],
    node_states: Dict[str, str],
    *,
    skip_decided: Optional[Set[str]] = None,
) -> List[str]:
    """可派发集：READY 节点 ∪（PENDING 且全部直接上游 ∈ {SUCCEEDED, SKIPPED}）。

    READY 语义 = 「已可派发但尚未 RUNNING」（含绑定预置位），一律可派发；
    RUNNING 转移 CAS 互斥保证不重复派发。
    ``skip_decided``：显式不参与调度的节点（如已被取消分支遮蔽）。
    返回按 dag 声明序稳定排序（确定性；同输入同序）。
    """
    upstream = upstream_of(dag)
    ok = {NodeState.SUCCEEDED, NodeState.SKIPPED}
    declared: List[str] = [
        str(n.get("node_id", "")) for n in dag.get("nodes") or []
    ]
    excluded = skip_decided or set()
    out: List[str] = []
    for nid in declared:
        if not nid or nid in excluded:
            continue
        st = node_states.get(nid, NodeState.PENDING)
        if st not in (NodeState.READY, NodeState.PENDING):
            continue
        # READY 与 PENDING 同规：全部直接上游结算（SUCCEEDED/SKIPPED）
        # 才可派发 —— 否则会与上游重算同波并发，读取旧产物（R1-C1）。
        deps = upstream.get(nid, ())
        if all(node_states.get(d) in ok for d in deps):
            out.append(nid)
    return out


def downstream_closure(dag: Dict[str, Any], seeds: Set[str]) -> Set[str]:
    """seeds 的全部（传递）下游（取消/失效传播用；环安全）。"""
    adjacency = build_adjacency(dag)
    out: Set[str] = set()
    stack = list(seeds)
    while stack:
        cur = stack.pop()
        for nxt in adjacency.get(cur, ()):
            if nxt not in out:
                out.add(nxt)
                stack.append(nxt)
    return out


def upstream_closure(dag: Dict[str, Any], seeds: Set[str]) -> Set[str]:
    """seeds 的全部（传递）上游（分支工作流 keep-set 用；环安全）。"""
    upstream = upstream_of(dag)
    out: Set[str] = set()
    stack = list(seeds)
    while stack:
        cur = stack.pop()
        for prv in upstream.get(cur, ()):
            if prv not in out:
                out.add(prv)
                stack.append(prv)
    return out


def check_transition(from_state: str, to_state: str) -> Optional[str]:
    """转移合法性（contracts 裁决的直通门；测试/调用方单入口）。"""
    return validate_transition(from_state, to_state)
