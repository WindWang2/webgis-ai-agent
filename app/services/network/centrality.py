"""
Network Centrality Service Component (Foundation V2 · A4).

Computes node centrality metrics over the network graph:

- degree: in + out degree (DiGraph semantics — a one-way street counts
  asymmetrically, documented, not silently symmetrized);
- closeness: networkx distance-corrected closeness on the chosen edge weight
  (travel_time_s / length_m). For DiGraphs networkx uses IN-reach semantics
  (``G.reverse()``): the score measures how easily a node is *reached* by
  others. Disconnected nodes are scaled by their reachable fraction
  (wf_improved) — a node nobody reaches (or that reaches nobody) scores 0;
- betweenness: EXACT Brandes for n ≤ 2000 nodes; beyond that a k-sample
  (k=500, seed=42) approximation with explicit ``betweenness_mode="sampled"``
  disclosure — the backend_variants declared on the descriptor genuinely
  switch here;
- edge_betweenness: EXACT only for graphs with ≤ 1500 edges; beyond that an
  honest ResourceScaleMismatch refusal (no fake sampling).

Scale guards run BEFORE any allocation: node cap 20000. Output rows are
capped at 5000 (top-k by primary metric) with explicit trim disclosure.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import networkx as nx

from app.lib.gis.scientific_errors import ResourceScaleMismatch, UnsupportedMethod

from app.services.network.models import CentralityResult

# 规模护栏（先拒绝后分配 —— ResourceScaleMismatch 发生在任何计算之前）：
# - 节点上限：Brandes 邻接扫描 O(n·m)，2 万节点的真实路网已超出交互预算；
# - 精确介数阈值：n≤2000 全图 Brandes；超出切 k=500 固定种子采样（可复现）；
# - 边介数精确阈值：O(m·(n+m)) 且输出 m 维 —— 1500 边外诚实拒绝（不假采样）；
# - 输出行上限：LLM 语境预算（与 network_tools 的 Fetch-on-Demand 同思路）。
_NODE_CAP = 20000
_EXACT_BETWEENNESS_NODES = 2000
_SAMPLE_K = 500
_SAMPLE_SEED = 42
_EXACT_EDGE_BETWEENNESS_EDGES = 1500
_OUTPUT_ROW_CAP = 5000

_VALID_METRICS = ("degree", "closeness", "betweenness", "edge_betweenness", "all")
# weight 参数（工具面）→ 图边权字段（graph_builder 恒写两列）。
_WEIGHT_FIELDS = {"travel_time": "travel_time_s", "length": "length_m"}


def _parse_metrics(metrics: str) -> List[str]:
    m = str(metrics).lower()
    if m == "all":
        return ["degree", "closeness", "betweenness", "edge_betweenness"]
    if m not in _VALID_METRICS:
        raise UnsupportedMethod(
            f"未知中心性指标 {metrics!r}（合法：degree|closeness|betweenness|edge_betweenness|all）",
            correction_hint="choose one of degree, closeness, betweenness, edge_betweenness, all",
        )
    return [m]


class NetworkCentralityService:
    """Node centrality over the network graph with honest scale semantics."""

    def network_centrality(
        self,
        graph: nx.DiGraph,
        metrics: str = "all",
        weight: str = "travel_time",
        node_limit: Optional[int] = None,
    ) -> CentralityResult:
        """Computes centrality metrics; scale guards fire before any compute.

        Args:
            graph: NetworkX DiGraph (builder output carries travel_time_s + length_m).
            metrics: 'degree|closeness|betweenness|edge_betweenness|all'.
            weight: 'travel_time' (travel_time_s) or 'length' (length_m).
            node_limit: hard node cap; None = module default (read at call
                time so tests can shrink it).
        """
        wanted = _parse_metrics(metrics)
        if weight not in _WEIGHT_FIELDS:
            raise UnsupportedMethod(
                f"未知权重字段 {weight!r}（合法：travel_time|length）",
                correction_hint="use weight='travel_time' (seconds) or 'length' (meters)",
            )
        weight_field = _WEIGHT_FIELDS[weight]
        if node_limit is None:
            node_limit = _NODE_CAP

        node_count = graph.number_of_nodes()
        edge_count = graph.number_of_edges()

        # ── 护栏先行：任何分配/计算之前拒绝超规模请求 ────────────────
        if node_count > node_limit:
            raise ResourceScaleMismatch(
                f"网络中心性的节点数 {node_count} 超出上限 {node_limit}"
                "（Brandes 邻接扫描 O(n·m)，超限请求必须分子网/裁剪后重试）",
                estimated=f"nodes={node_count}",
                limit=f"nodes<={node_limit}",
            )

        betweenness_needed = "betweenness" in wanted
        edge_betweenness_needed = "edge_betweenness" in wanted

        if edge_betweenness_needed and edge_count > _EXACT_EDGE_BETWEENNESS_EDGES:
            raise ResourceScaleMismatch(
                f"edge_betweenness 需要逐边精确 Brandes（O(m·(n+m))）：边数 {edge_count} "
                f"超出精确上限 {_EXACT_EDGE_BETWEENNESS_EDGES}；不做假采样 —— 请去掉 "
                f"edge_betweenness 或分子网重试",
                estimated=f"edges={edge_count}",
                limit=f"edges<={_EXACT_EDGE_BETWEENNESS_EDGES}",
            )

        metrics_out: List[str] = []
        degree: Dict[Any, int] = {}
        closeness: Dict[Any, float] = {}
        betweenness: Dict[Any, float] = {}
        edge_betweenness: Dict[Any, float] = {}

        if "degree" in wanted:
            # DiGraph 语义：入度 + 出度（单行路段双向计数不对称，如实呈现）。
            degree = dict(graph.degree())
            metrics_out.append("degree")

        if "closeness" in wanted:
            # 距离校正（带权）接近中心性；DiGraph 为入向语义（networkx 对
            # 有向图 reverse 后计算 —— 度量被到达的容易程度）。不连通按可
            # 达集合占比缩放（wf_improved）：无人到达/不达他人的节点为 0，
            # 显式语义，非误差。
            closeness = nx.closeness_centrality(G=graph, distance=weight_field)
            metrics_out.append("closeness")

        betweenness_mode = "not_computed"
        sample_k: Optional[int] = None
        if betweenness_needed:
            if node_count <= _EXACT_BETWEENNESS_NODES:
                betweenness = nx.betweenness_centrality(G=graph, weight=weight_field, normalized=True)
                betweenness_mode = "exact"
            else:
                # 采样 Brandes：固定种子 42 —— 逐次可复现的估计值（非精确），
                # 模式在 betweenness_mode 诚实披露。
                betweenness = nx.betweenness_centrality(
                    G=graph, weight=weight_field, normalized=True,
                    k=_SAMPLE_K, seed=_SAMPLE_SEED,
                )
                betweenness_mode = "sampled"
                sample_k = _SAMPLE_K
            metrics_out.append("betweenness")

        if edge_betweenness_needed:
            # networkx 的边介数以**边**为键（逐边精确 Brandes）；节点级输出
            # 聚合 = 关联边边介数之和（显式语义，非逐点精确值）。
            eb_edges = nx.edge_betweenness_centrality(G=graph, weight=weight_field, normalized=True)
            edge_betweenness = {node: 0.0 for node in graph.nodes}
            for (u, v), val in eb_edges.items():
                edge_betweenness[u] = edge_betweenness.get(u, 0.0) + val
                edge_betweenness[v] = edge_betweenness.get(v, 0.0) + val
            metrics_out.append("edge_betweenness")

        records: List[Dict[str, Any]] = []
        for node in graph.nodes:
            rec: Dict[str, Any] = {"node_id": node}
            if degree:
                rec["degree"] = int(degree.get(node, 0))
            if closeness:
                rec["closeness"] = round(float(closeness.get(node, 0.0)), 6)
            if betweenness:
                rec["betweenness"] = round(float(betweenness.get(node, 0.0)), 6)
            if edge_betweenness:
                rec["edge_betweenness"] = round(float(edge_betweenness.get(node, 0.0)), 6)
            records.append(rec)

        # 输出行上限：按主指标降序（主指标 = 介数 > 接近 > 度）裁剪，
        # 裁剪事实 + 原始行数显式披露 —— 绝不静默截断。
        primary = "betweenness" if betweenness else ("closeness" if closeness else "degree")
        records.sort(key=lambda r: (-float(r.get(primary, 0.0)), str(r["node_id"])))
        output_rows_total = len(records)
        trimmed = output_rows_total > _OUTPUT_ROW_CAP
        if trimmed:
            records = records[:_OUTPUT_ROW_CAP]

        summary: Dict[str, Any] = {
            "metrics": metrics_out,
            "weight_field": weight_field,
            "primary_metric": primary,
            "betweenness_mode": betweenness_mode,
            "sample_k": sample_k,
            "output_rows_trimmed": trimmed,
            "max_degree": max(degree.values()) if degree else 0,
        }
        if closeness:
            summary["max_closeness"] = round(max(closeness.values()), 6)
        if betweenness:
            summary["max_betweenness"] = round(max(betweenness.values()), 6)

        return CentralityResult(
            metrics=metrics_out,
            weight_field=weight_field,
            node_count=node_count,
            edge_count=edge_count,
            betweenness_mode=betweenness_mode,
            sample_k=sample_k,
            node_records=records,
            output_rows_total=output_rows_total,
            output_row_cap=_OUTPUT_ROW_CAP,
            summary=summary,
        )
