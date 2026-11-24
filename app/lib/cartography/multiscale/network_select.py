"""网络连通性保持线取舍（WP2c）—— 等级取舍 + 连通性桥接修复。

定位（与既有引擎的分工）：

- ``generalize.py`` 的取舍通道按重要性词表逐要素保留/抑制 —— 对线网可能
  产生**断网**：被保留干线之间的联络段被抑制后，保留子网分裂成互不连通
  的分量（导航/总览语义下不可接受）；
- 本模块在等级取舍之上加**连通性桥接修复**：把保留子网用最少的被抑制段
  重新连通（BFS 最短桥接路径，确定性并列裁决）；超出预算或无路径时
  **诚实披露断连**，不静默假装连通。

图模型（诚实近似，非全拓扑引擎）：

- 节点 = 端点 ∪ 被 ≥2 要素共享的顶点（量化对齐，snap 可调）；要素路径
  在节点处切分为图边 —— T 型交会于中间顶点也能建边；
- 隔离要素（端点不与任何要素共享）自成一份量 —— 它的保留与否不影响
  连通性语义（披露 isolated_features 计数）；
- 保留判据与 ``generalize._value_is_kept`` 同语义（数值等级归一化匹配）；
  值缺席（unclassified）沿用 generalize 纪律 = 保留。
"""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict

from app.lib.cartography.multiscale.generalize import (
    _stable_feature_id,
    _value_is_kept,
)

#: 回执 bridges 明细上限。
_RECEIPT_BRIDGE_LIMIT = 64

#: 缺省顶点量化位数（≈亚纳米；脏数据可放粗）。
_DEFAULT_SNAP_DIGITS = 9


def _build_feature_edges(
    features: Sequence[Dict[str, Any]],
    snap_digits: int,
) -> List[List[Tuple[Tuple[float, float], Tuple[float, float]]]]:
    """线要素集 → 图边（节点 = 端点 ∪ 共享顶点；要素路径在节点处切分）。

    共享实现：``select_lines_with_connectivity`` 与 ``count_line_components``
    同一图语义（单一事实源，防双处漂移）。非线/坏几何 → 无边（不入图）。
    """
    vertex_freq: Dict[Tuple[float, float], int] = {}
    parsed_paths: List[List[Tuple[float, float]]] = []
    for feature in features:
        coords_raw = _feature_coords(feature)
        if coords_raw is None:
            parsed_paths.append([])
            continue
        if (coords_raw and isinstance(coords_raw[0], list) and coords_raw[0]
                and isinstance(coords_raw[0][0], list)):
            # MultiLineString：各段展开
            paths: List[Tuple[float, float]] = []
            for part in coords_raw:
                if isinstance(part, list):
                    paths.extend(_q(c, snap_digits) for c in part
                                 if isinstance(c, (list, tuple))
                                 and len(c) >= 2)
        else:
            paths = [_q(c, snap_digits) for c in coords_raw
                     if isinstance(c, (list, tuple)) and len(c) >= 2]
        parsed_paths.append(paths)
        for qc in paths:
            vertex_freq[qc] = vertex_freq.get(qc, 0) + 1

    def _is_node(qc: Tuple[float, float], pos: int, total: int) -> bool:
        return vertex_freq.get(qc, 0) >= 2 or pos == 0 or pos == total - 1

    edges_per_feature: List[List[Tuple[Tuple[float, float], Tuple[float, float]]]] = []
    for path in parsed_paths:
        edges: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        if len(path) >= 2:
            start = 0
            for pos in range(1, len(path)):
                if _is_node(path[pos], pos, len(path)):
                    if pos > start:
                        edges.append((path[start], path[pos]))
                    start = pos
        edges_per_feature.append(edges)
    return edges_per_feature


def count_line_components(
    features: Sequence[Dict[str, Any]],
    *,
    snap_digits: int = _DEFAULT_SNAP_DIGITS,
) -> int:
    """线要素集合的连通分量数（QA oracle 用；全要素视作保留）。

    非线/坏几何不计入分量（无边不入图）；空集 = 0。
    """
    edges_per_feature = _build_feature_edges(list(features), snap_digits)
    parent: Dict[Tuple[float, float], Tuple[float, float]] = {}

    def _find(x: Tuple[float, float]) -> Tuple[float, float]:
        r = x
        while parent.get(r, r) != r:
            r = parent[r]
        while parent.get(x, x) != x:
            parent[x], x = r, parent[x]
        return r

    for edges in edges_per_feature:
        for a, b in edges:
            parent.setdefault(a, a)
            parent.setdefault(b, b)
            ra, rb = _find(a), _find(b)
            if ra != rb:
                if ra <= rb:
                    parent[rb] = ra
                else:
                    parent[ra] = rb
    return len({_find(n) for n in list(parent)})


class ConnectivityResult(BaseModel):
    """连通性取舍产物：保留 FC + 回执（可序列化、确定性）。"""

    model_config = ConfigDict(extra="allow")

    feature_collection: Dict[str, Any]
    receipt: Dict[str, Any]


def _q(pt: Any, digits: int) -> Tuple[float, float]:
    return (round(float(pt[0]), digits), round(float(pt[1]), digits))


def _feature_coords(feature: Dict[str, Any]) -> Optional[List[Any]]:
    geom = feature.get("geometry")
    if not isinstance(geom, dict):
        return None
    t = geom.get("type")
    if t == "LineString":
        coords = geom.get("coordinates")
    elif t == "MultiLineString":
        # 多段线：各段独立成路径（节点共享仍在全局顶点频次里）
        parts = geom.get("coordinates")
        return parts if isinstance(parts, list) else None
    else:
        return None
    return coords if isinstance(coords, list) else None


def select_lines_with_connectivity(
    features: Sequence[Dict[str, Any]],
    *,
    importance_field: str,
    kept_values: Sequence[Any],
    mandatory_ids: FrozenSet[str] = frozenset(),
    snap_digits: int = _DEFAULT_SNAP_DIGITS,
    max_bridges: Optional[int] = None,
) -> ConnectivityResult:
    """线要素列表 → 连通性保持的保留 FC + 回执（确定性）。

    - ``importance_field`` × ``kept_values``：基础取舍（与 generalize 同
      语义：值缺席保留、mandatory user-wins）；
    - ``snap_digits``：节点量化位数（放粗可容忍微小错位端点）；
    - ``max_bridges``：桥接段预算；None = 不设上限；超预算 → 披露断连。
    """
    features = list(features)
    input_count = len(features)
    kept_list = list(kept_values)

    # ── 基础取舍裁决（与 generalize 同语义）──
    base_kept: List[bool] = []
    mandatory_saved = 0
    unclassified_kept = 0
    parse_failures: List[str] = []
    for index, feature in enumerate(features):
        fid = _stable_feature_id(feature, index)
        coords = _feature_coords(feature)
        if coords is None:
            base_kept.append(True)   # 非线/坏几何原样透传（诚实记账）
            parse_failures.append(fid)
            continue
        props = feature.get("properties") if isinstance(
            feature.get("properties"), dict) else {}
        value = props.get(importance_field) if importance_field else None
        if value is None:
            base_kept.append(True)
            unclassified_kept += 1
        elif _value_is_kept(value, kept_list):
            base_kept.append(True)
        elif fid in mandatory_ids:
            base_kept.append(True)
            mandatory_saved += 1
        else:
            base_kept.append(False)

    # ── 图构建（共享实现，见 _build_feature_edges）──
    feature_edges = _build_feature_edges(features, snap_digits)

    node_degree: Dict[Tuple[float, float], int] = {}
    for edges in feature_edges:
        for a, b in edges:
            node_degree[a] = node_degree.get(a, 0) + 1
            node_degree[b] = node_degree.get(b, 0) + 1

    # ── 保留子网分量（union-find on kept edges）──
    parent: Dict[Tuple[float, float], Tuple[float, float]] = {}

    def _find(x: Tuple[float, float]) -> Tuple[float, float]:
        r = x
        while parent.get(r, r) != r:
            r = parent[r]
        while parent.get(x, x) != x:
            parent[x], x = r, parent[x]
        return r

    def _union(a: Tuple[float, float], b: Tuple[float, float]) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            if ra <= rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    for index, edges in enumerate(feature_edges):
        if not base_kept[index]:
            continue
        for a, b in edges:
            parent.setdefault(a, a)
            parent.setdefault(b, b)
            _union(a, b)

    def _components() -> Dict[Tuple[float, float], List[Tuple[float, float]]]:
        comps: Dict[Tuple[float, float], List[Tuple[float, float]]] = {}
        for n in list(parent):
            comps.setdefault(_find(n), []).append(n)
        return comps

    # ── 连通性桥接修复（全队列 BFS：每轮合并起点分量的全部可达分量）──
    #
    # 语义：BFS 沿被抑制要素从最小根分量扩散（全队列耗尽），凡触达的
    # **他保留分量**按发现序重建最短路径并整要素重加 —— 每轮把起点分量
    # 与全部可达分量一次合并（迭代轮数 = 连通超图数 × 常数，非 O(K)）。
    # 只重加「分量到分量」的桥路径：挂空枝（free 端点不触达任何保留分量）
    # 与既有 BFS 语义一致不会被加。
    # 确定性：邻接按 (feature_id, node) 排序扩散、接触按发现序处理；
    # 预算（max_bridges）按发现序截断，超限诚实披露断连。
    components_before = len(_components()) if parent else 0
    bridges: List[Dict[str, Any]] = []
    bridges_added_count = 0
    budget = max_bridges if max_bridges is not None else 10 ** 9

    suppressed_adj: Dict[Tuple[float, float], List[Tuple[int, Tuple[float, float]]]] = {}
    for index, edges in enumerate(feature_edges):
        if base_kept[index]:
            continue
        for a, b in edges:
            suppressed_adj.setdefault(a, []).append((index, b))
            suppressed_adj.setdefault(b, []).append((index, a))

    while True:
        comps = _components()
        if len(comps) <= 1:
            break
        roots = sorted(comps.keys())
        start_root = roots[0]
        start_nodes = set(comps[start_root])
        other_roots = set(roots[1:])
        # 全队列 BFS（不提前停）：记录每个首达节点的来路
        dist: Dict[Tuple[float, float], int] = {n: 0 for n in start_nodes}
        came_edge: Dict[Tuple[float, float], Tuple[int, Tuple[float, float]]] = {}
        queue = deque(sorted(start_nodes))
        contacts: List[Tuple[float, float]] = []   # 首达的他分量节点（发现序）
        seen_roots: set = set()
        while queue:
            cur = queue.popleft()
            for fidx, nxt in sorted(suppressed_adj.get(cur, []),
                                    key=lambda t: (t[0], t[1])):
                if nxt in dist:
                    continue
                dist[nxt] = dist[cur] + 1
                came_edge[nxt] = (fidx, cur)
                nr = _find(nxt)
                if nr in other_roots and nr not in seen_roots:
                    seen_roots.add(nr)
                    contacts.append(nxt)
                queue.append(nxt)
        if not contacts or budget <= 0:
            break  # 无桥接路径或预算尽 → 诚实断连披露
        # 按发现序重建各接触分量的路径（跨接触去重保序）
        path_fids: List[int] = []
        for contact in contacts:
            cur = contact
            while cur not in start_nodes and cur in came_edge:
                fidx, prev = came_edge[cur]
                if fidx not in path_fids:
                    path_fids.append(fidx)
                cur = prev
        for fidx in path_fids:
            if budget <= 0:
                break
            base_kept[fidx] = True
            # 明细帽只截断**回执明细**（防病理放大），桥接本体继续到
            # 连通或无路径 —— 明细截断绝不改变取舍语义。
            if len(bridges) < _RECEIPT_BRIDGE_LIMIT:
                bridges.append({
                    "feature_id": _stable_feature_id(features[fidx], fidx),
                    "reason": "connectivity_bridge",
                })
            bridges_added_count += 1
            budget -= 1
            for a, b in feature_edges[fidx]:
                parent.setdefault(a, a)
                parent.setdefault(b, b)
                _union(a, b)

    final_components = len(_components()) if parent else 0
    kept_count = sum(1 for k in base_kept if k)
    suppressed_count = sum(1 for i, _f in enumerate(features)
                           if not base_kept[i])

    # ── 组装输出（保留序 = 输入序；被抑制者剔除）──
    out_features = [features[i] for i in range(input_count) if base_kept[i]]
    disclosures: List[str] = []
    truncated = bridges_added_count > len(bridges)
    if final_components > 1:
        disclosures.append(
            f"保留子网仍分裂为 {final_components} 个互不连通分量"
            "（桥接预算尽或无路径）—— 连通性未完全保持（诚实披露，"
            "不静默假装连通）")
    if bridges:
        disclosures.append(
            f"连通性桥接：{len(bridges)} 个被抑制段按最短路径重加"
            "（等级取舍之上保网络连通）")
    if parse_failures:
        disclosures.append(
            f"{len(parse_failures)} 个非线/坏几何要素原样透传（不参与图）")

    receipt = {
        "engine": "network_connectivity_v1",
        "importance_field": importance_field,
        "kept_values": kept_list[:16],
        "input_count": input_count,
        "kept_count": kept_count,
        "suppressed_count": suppressed_count,
        "mandatory_saved": mandatory_saved,
        "unclassified_kept": unclassified_kept,
        "parse_failures": len(parse_failures),
        "graph_nodes": len(parent),
        "junction_nodes": sum(1 for d in node_degree.values() if d >= 3),
        "components_before": components_before,
        "components_final": final_components,
        "bridges_added": bridges_added_count,
        "bridges_truncated": truncated,
        "fully_connected": final_components <= 1,
        "stats_match": kept_count + suppressed_count + len(parse_failures) == input_count,
        "bridges": bridges[:_RECEIPT_BRIDGE_LIMIT],
        "disclosures": disclosures,
    }
    return ConnectivityResult(
        feature_collection={"type": "FeatureCollection",
                            "features": out_features},
        receipt=receipt,
    )


__all__ = [
    "ConnectivityResult",
    "count_line_components",
    "select_lines_with_connectivity",
]
