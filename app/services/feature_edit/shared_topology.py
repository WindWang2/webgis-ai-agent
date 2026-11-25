"""共享空间结构索引与跨要素约束编辑算子（goal-gis-topology-authoring）。

职责边界（与既有权威的关系）：
- **索引不是第二数据库**：对已物化 FC 构建有界、可重建的内存节点/边索引，
  每次预览按需重建；不持久化、不缓存跨请求、不旁路 journal。
- **匹配规则用户可选**（任务书 WP1：相同坐标不必然同一业务节点）：
  - ``exact``（默认）：原始 (x, y) 完全相等才算同一节点；
  - ``tolerance``：显式 tolerance_m（米，测地距离）且 ≤ MAX_SHARED_VERTEX_TOLERANCE_M；
    容差内命中多个不同节点 → typed 拒绝（shared_node_ambiguous），绝不静默就近。
    容差匹配不跨反经线（分桶按原始经度；跨反经线数据集须 exact 模式）。
  - ``level_field`` 守卫：声明字段值不同（如桥/隧道的 layer/level）的要素
    顶点永不合并；缺值与有值亦不合并。
- **支持/拒绝面**：Polygon/MultiPolygon（含洞环）、LineString/MultiLineString、
  Point；Z 坐标 move/connect 保留原 Z 语义（Z 路径的 insert/delete/reshape
  typed 拒绝）；4 元素（M）位置视为畸形剔除；无效 geometry 不进索引并披露。
- **传播纪律**：一次共享 op 触及的要素数 ≤ MAX_SHARED_TOUCHED_FEATURES；
  每个被触及要素一条 update delta（geometry_before/after 全量内嵌），
  undo/redo 由既有 journal 逆 delta 承载 —— 不新增执行路径。
- **检查不修复**：op 只做顶点级变更；重叠/缺口由声明规则检查
  （fe/topology.check_topology）在操作后形态上判定，绝不静默 make_valid。
"""
from __future__ import annotations

from app.contracts.geojson import GeoJSONFeatureCollection

from app.services.feature_edit.wire_types import EditFinding, FeatureDelta, OperationSummary

from shapely.geometry import LineString as _LS
from shapely.geometry import Point as _ShapelyPoint
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, cast

from app.services.feature_edit.contracts import (
    MAX_CONNECT_TOLERANCE_M,
    MAX_SHARED_INDEX_NODES,
    MAX_SHARED_TOUCHED_FEATURES,
    MAX_SHARED_VERTEX_TOLERANCE_M,
    WFID_PROP,
    finding,
)
from app.services.feature_edit.geometry_ops import (
    _validate_after,
    geodesic_distance_m,
)

__all__ = [
    "SharedTopologyIndex",
    "build_shared_index",
    "move_shared_vertex",
    "insert_shared_vertex",
    "delete_shared_vertex",
    "reshape_shared_edge",
    "connect_endpoint",
    "inspect_shared_structure",
    "vertex_delta_summary",
]

_MISSING = object()

# 容差合并的比较预算（病态密集聚类时诚实拒绝，不做无界扫描）
_MERGE_COMPARE_BUDGET = 2_000_000
# 单节点成员上限（病态聚类披露；超过即容差模式拒绝）
_MAX_NODE_MEMBERS = 256
# reshape 新路径点数上限（与 journal 顶点预算同量级）
_MAX_RESHAPE_PATH_POINTS = 1024


# ─── 几何路径枚举（坐标结构与 (addr, coords) 的双向映射） ─────────────────

def _iter_paths(geometry: Dict[str, Any]) -> List[Tuple[List[int], List[List[Any]]]]:
    """geometry.coordinates → [(addr, coords)]；addr 是定位该 path 的下标链。

    Point → ([], [p])；MultiPoint → 每个 p 一条 path（[i], [p]）；
    LineString → ([], coords)；MultiLineString → ([i], part)；
    Polygon → ([j], ring)；MultiPolygon → ([i, j], ring)。
    畸形坐标静默跳过（由调用方按 unsupported 披露）。
    """
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    out: List[Tuple[List[int], List[List[Any]]]] = []
    if not isinstance(coords, (list, tuple)):
        return out
    if gtype == "Point":
        p = _as_vertex(coords)
        if p is not None:
            out.append(([], [p]))
    elif gtype == "MultiPoint":
        for i, p in enumerate(coords):
            v = _as_vertex(p)
            if v is not None:
                out.append(([i], [v]))
    elif gtype == "LineString":
        path = _as_path(coords)
        if path:
            out.append(([], path))
    elif gtype == "MultiLineString":
        for i, part in enumerate(coords):
            path = _as_path(part)
            if path:
                out.append(([i], path))
    elif gtype == "Polygon":
        for j, ring in enumerate(coords):
            path = _as_path(ring)
            if path:
                out.append(([j], path))
    elif gtype == "MultiPolygon":
        for i, poly in enumerate(coords):
            if not isinstance(poly, (list, tuple)):
                continue
            for j, ring in enumerate(poly):
                path = _as_path(ring)
                if path:
                    out.append(([i, j], path))
    return out


def _as_vertex(node: Any) -> Optional[List[Any]]:
    if (
        isinstance(node, (list, tuple))
        and len(node) in (2, 3)
        and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in node[:2])
    ):
        return list(node)
    return None  # 4 元素（M）与畸形视为不支持


def _as_path(seq: Any) -> List[List[Any]]:
    if not isinstance(seq, (list, tuple)):
        return []
    out: List[List[Any]] = []
    for node in seq:
        v = _as_vertex(node)
        if v is not None:
            out.append(v)
    return out


# ─── 索引 ──────────────────────────────────────────────────────────────────

class _Node:
    __slots__ = ("nid", "x", "y", "members", "level")

    def __init__(self, nid: int, x: float, y: float) -> None:
        self.nid = nid
        self.x = x
        self.y = y
        # (wfid, path_i, addr_len, vertex_k)：addr_len 记录 addr 链长度（冗余但便于投影）
        self.members: List[Tuple[str, int, int, int]] = []
        self.level: Any = _MISSING


class _Edge:
    __slots__ = ("a", "b", "members")

    def __init__(self, a: int, b: int) -> None:
        self.a = a
        self.b = b
        # (wfid, path_i, addr_len, position_k)：该 path 中 a 在第 k 位；
        # 闭环收尾边的 b 在 0 位。
        self.members: List[Tuple[str, int, int, int]] = []


class SharedTopologyIndex:
    """有界可重建共享结构索引（exact / tolerance 两种匹配模式）。"""

    def __init__(self, *, tolerance_m: Optional[float], level_field: Optional[str]) -> None:
        self.tolerance_m = tolerance_m
        self.level_field = level_field
        self.nodes: Dict[int, _Node] = {}
        self.edges: Dict[Tuple[int, int], _Edge] = {}
        self.exact_keys: Dict[Tuple[float, float], int] = {}
        self._level_split: Dict[Tuple[float, float, str], int] = {}
        self._buckets: Dict[Tuple[int, int], List[int]] = {}
        self._cell_lat_deg = 0.0
        self.feature_wfids: List[str] = []
        # wfid → [(path_i, coords, addr, kind)]
        self.paths: Dict[str, List[Tuple[int, List[List[Any]], List[int], str]]] = {}
        self.excluded: Dict[str, int] = {}
        self.vertex_count = 0
        self._feature_level: Dict[str, Any] = {}
        # (wfid, path_i, k) → nid：vertex ref 的精确定址（exact/容差坐标查询
        # 无法区分同坐标多层级节点；成员身份查询恒唯一）。
        self.member_nid: Dict[Tuple[str, int, int], int] = {}

    # ── 构建 ──
    def _cell_lon_deg(self, lat: float) -> float:
        cos_lat = max(abs(math.cos(math.radians(max(min(lat, 85.0), -85.0)))), 1e-6)
        return (self.tolerance_m or 1.0) / (111_320.0 * cos_lat)

    def _bucket_insert(self, node: _Node) -> None:
        lon_cell = self._cell_lon_deg(node.y)
        cx = math.floor(node.x / max(lon_cell, 1e-12))
        cy = math.floor(node.y / max(self._cell_lat_deg, 1e-12))
        self._buckets.setdefault((int(cx), int(cy)), []).append(node.nid)

    def _lookup_candidates(self, x: float, y: float) -> List[int]:
        if self.tolerance_m is None:
            nid = self.exact_keys.get((x, y))
            return [nid] if nid is not None else []
        lon_cell = self._cell_lon_deg(y)
        cx = math.floor(x / max(lon_cell, 1e-12))
        cy = math.floor(y / max(self._cell_lat_deg, 1e-12))
        out: List[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                got = self._buckets.get((cx + dx, cy + dy))
                if got:
                    out.extend(nid for nid in got if nid in self.nodes)
        return out

    def node_at(
        self, x: float, y: float, tolerance_m: Optional[float] = None
    ) -> Tuple[Optional[_Node], List[_Node]]:
        """定位节点。返回 (唯一节点 | None, 歧义候选列表)。"""
        candidates = self._lookup_candidates(x, y)
        if self.tolerance_m is None:
            return (self.nodes.get(candidates[0]) if candidates else None), []
        tol = tolerance_m if tolerance_m is not None else self.tolerance_m
        unique: Dict[int, _Node] = {}
        for nid in candidates:
            node = self.nodes[nid]
            if geodesic_distance_m(x, y, node.x, node.y) <= (tol or 0.0):
                unique[nid] = node
        vals = list(unique.values())
        if len(vals) == 1:
            return vals[0], []
        if len(vals) > 1:
            return None, sorted(vals, key=lambda n: n.nid)
        return None, []

    def neighbors_of(self, wfids: Set[str]) -> List[Dict[str, Any]]:
        """目标要素的邻接受影响对象（shares_edge / incident_node / shares_vertex）。

        以节点/边成员关系判定（非 bbox 近似）；仅返回与目标**不同**的要素。
        """
        target = set(wfids)
        vertex_links: Dict[str, Set[str]] = {}
        edge_links: Dict[str, Set[str]] = {}
        for node in self.nodes.values():
            wf = {m[0] for m in node.members}
            if len(wf) < 2:
                continue
            inter = wf & target
            if not inter:
                continue
            for w in wf - inter:
                vertex_links.setdefault(w, set()).update(inter)
        for edge in self.edges.values():
            wf = {m[0] for m in edge.members}
            if len(wf) < 2:
                continue
            inter = wf & target
            if not inter:
                continue
            for w in wf - inter:
                edge_links.setdefault(w, set()).update(inter)
        out: List[Dict[str, Any]] = []
        for w in sorted(set(vertex_links) | set(edge_links)):
            relation = "shares_edge" if w in edge_links else "incident_node"
            out.append(
                {
                    "wfid": w,
                    "relation": relation,
                    "shared_with": sorted((edge_links if w in edge_links else vertex_links)[w]),
                }
            )
        return out

    def dangling_endpoints(self, limit: int = 32) -> List[Dict[str, Any]]:
        """线网悬挂端点（仅属于一条线的端点节点），辅助数字化定位用。"""
        line_end_count: Dict[int, int] = {}
        for node in self.nodes.values():
            count = 0
            for (wfid, path_i, _al, k) in node.members:
                paths = self.paths.get(wfid) or []
                if path_i >= len(paths):
                    continue
                _pi, coords, _addr, kind = paths[path_i]
                if kind == "line" and coords and (k == 0 or k == len(coords) - 1):
                    count += 1
            if count:
                line_end_count[node.nid] = count
        out: List[Dict[str, Any]] = []
        for nid in sorted(line_end_count):
            if line_end_count[nid] != 1:
                continue
            node = self.nodes[nid]
            for (wfid, _pi, _al, _k) in node.members[:1]:
                out.append({"wfid": wfid, "coordinate": [node.x, node.y]})
                break
            if len(out) >= limit:
                break
        return out


def build_shared_index(
    fc: GeoJSONFeatureCollection,
    *,
    tolerance_m: Any = None,
    level_field: Any = None,
) -> Tuple[Optional[SharedTopologyIndex], List[EditFinding]]:
    """构建共享结构索引（纯读；失败返回 typed findings，不做有损降级）。

    tolerance_m：None = exact（默认）；正数 = 容差匹配（米）。
    level_field：可选要素级字段名 —— 值不同的要素顶点永不合并。
    """
    findings: List[EditFinding] = []
    if tolerance_m is not None:
        if (
            not isinstance(tolerance_m, (int, float))
            or isinstance(tolerance_m, bool)
            or tolerance_m <= 0
        ):
            return None, [finding("unit_required", "shared index requires positive numeric tolerance_m in meters")]
        if float(tolerance_m) > MAX_SHARED_VERTEX_TOLERANCE_M:
            return None, [
                finding(
                    "invalid_op",
                    "shared vertex tolerance exceeds the bound",
                    tolerance_m=float(tolerance_m),
                    limit=MAX_SHARED_VERTEX_TOLERANCE_M,
                )
            ]
    if level_field is not None and (
        not isinstance(level_field, str) or not level_field or len(level_field) > 64
    ):
        return None, [finding("invalid_op", "level_field must be a short field name")]

    index = SharedTopologyIndex(
        tolerance_m=float(tolerance_m) if tolerance_m is not None else None,
        level_field=str(level_field) if level_field else None,
    )
    if index.tolerance_m is not None:
        index._cell_lat_deg = index.tolerance_m / 110_540.0

    exact_nodes: Dict[Tuple[float, float], _Node] = {}

    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        wfid = props.get(WFID_PROP) if isinstance(props, dict) else None
        if not isinstance(wfid, str):
            continue  # 未物化要素不进索引（调用方保证 preview 前已物化）
        geometry = feature.get("geometry")
        gtype = str(geometry.get("type") or "") if isinstance(geometry, dict) else ""
        if gtype not in ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"):
            index.excluded[wfid] = index.excluded.get(wfid, 0) + 1
            continue
        level: Any = _MISSING
        if index.level_field and isinstance(props, dict) and index.level_field in props:
            level = props[index.level_field]
        index._feature_level[wfid] = level
        paths_meta: List[Tuple[int, List[List[Any]], List[int], str]] = []
        try:
            addr_paths = _iter_paths(geometry)
        except Exception:
            index.excluded[wfid] = index.excluded.get(wfid, 0) + 1
            continue
        for path_i, (addr, coords) in enumerate(addr_paths):
            kind = "point" if gtype in ("Point", "MultiPoint") else ("ring" if gtype.endswith("Polygon") else "line")
            paths_meta.append((path_i, coords, list(addr), kind))
            closed = kind == "ring" and len(coords) >= 4 and coords[0] == coords[-1]
            n = len(coords) - 1 if closed else len(coords)
            for k in range(n):
                p = coords[k]
                index.vertex_count += 1
                x, y = float(p[0]), float(p[1])
                node = exact_nodes.get((x, y))
                if node is None:
                    node = _Node(len(index.nodes), x, y)
                    index.nodes[node.nid] = node
                    exact_nodes[(x, y)] = node
                    if index.tolerance_m is not None:
                        index._bucket_insert(node)
                    else:
                        index.exact_keys[(x, y)] = node.nid
                if node.level is _MISSING:
                    node.level = level
                elif node.level != level:
                    # 同坐标不同层级（桥/隧语义）：exact 模式下拆为独立节点。
                    # exact_keys 保持首写节点（确定性）—— 坐标查询命中先声明
                    # 要素的节点；后续层级节点经 vertex ref 精确寻址。
                    sep_key = (x, y, repr(level))
                    sep_nid = index._level_split.get(sep_key)
                    if sep_nid is None:
                        sep_node = _Node(len(index.nodes), x, y)
                        sep_node.level = level
                        index.nodes[sep_node.nid] = sep_node
                        index._level_split[sep_key] = sep_node.nid
                        if index.tolerance_m is not None:
                            index._bucket_insert(sep_node)
                        node = sep_node
                    else:
                        node = index.nodes[sep_nid]
                node.members.append((wfid, path_i, len(addr), k))
                index.member_nid[(wfid, path_i, k)] = node.nid
            if kind in ("ring", "line") and n >= 2:
                edge_count = n if closed else n - 1
                for k in range(edge_count):
                    p_a = coords[k]
                    k_next = k + 1 if k + 1 < len(coords) else 0
                    p_b = coords[k_next]
                    na = exact_nodes.get((float(p_a[0]), float(p_a[1])))
                    nb = exact_nodes.get((float(p_b[0]), float(p_b[1])))
                    if na is None or nb is None or na.nid == nb.nid:
                        continue
                    ekey = (min(na.nid, nb.nid), max(na.nid, nb.nid))
                    edge = index.edges.get(ekey)
                    if edge is None:
                        edge = _Edge(ekey[0], ekey[1])
                        index.edges[ekey] = edge
                    edge.members.append((wfid, path_i, len(addr), k))
        index.paths[wfid] = paths_meta
        if index.vertex_count > MAX_SHARED_INDEX_NODES:
            return None, [
                finding(
                    "index_too_large",
                    "shared structure index exceeds the vertex budget",
                    vertices=index.vertex_count,
                    limit=MAX_SHARED_INDEX_NODES,
                )
            ]

    if index.tolerance_m is not None:
        merge_findings = _merge_tolerance_nodes(index)
        findings.extend(merge_findings)
        if merge_findings:
            return None, findings

    if index.excluded:
        findings.append(
            finding(
                "invalid_geometry",
                "some features were excluded from the shared structure index (unsupported or malformed geometry)",
                excluded={w: c for w, c in sorted(index.excluded.items())[:8]},
            )
        )
    return index, findings


def _merge_tolerance_nodes(index: SharedTopologyIndex) -> List[EditFinding]:
    """容差模式：把容差内且层级兼容的节点并成组（union-find，预算内）。"""
    parent: Dict[int, int] = {nid: nid for nid in index.nodes}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    comparisons = 0
    tol = index.tolerance_m or 0.0
    nodes_sorted = sorted(index.nodes.values(), key=lambda n: (n.y, n.x, n.nid))
    for node in nodes_sorted:
        unique_node, others = index.node_at(node.x, node.y, tol)
        candidates = list(others) + ([unique_node] if unique_node is not None else [])
        for other in candidates:
            if other.nid <= node.nid:
                continue
            comparisons += 1
            if comparisons > _MERGE_COMPARE_BUDGET:
                return [
                    finding(
                        "index_too_large",
                        "tolerance merge exceeded the comparison budget; narrow the tolerance or dataset",
                        comparisons=comparisons,
                    )
                ]
            la = node.level
            lb = other.level
            if la != lb:
                continue  # 层级守卫：不同层级永不合并（桥/隧）
            if geodesic_distance_m(node.x, node.y, other.x, other.y) <= tol:
                ra, rb = find(node.nid), find(other.nid)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)

    groups: Dict[int, List[int]] = {}
    for nid in parent:
        groups.setdefault(find(nid), []).append(nid)
    if len(groups) == len(parent):
        return []  # 无合并发生

    remapped: Dict[int, _Node] = {}
    for root in sorted(groups):
        members = sorted(groups[root])
        if len(members) == 1:
            remapped[members[0]] = index.nodes[members[0]]
            continue
        if len(members) > _MAX_NODE_MEMBERS:
            return [
                finding(
                    "shared_node_ambiguous",
                    "tolerance cluster exceeds the node member bound; use exact matching",
                    cluster=len(members),
                    limit=_MAX_NODE_MEMBERS,
                )
            ]
        keep = index.nodes[members[0]]
        seen: Set[Tuple[str, int, int, int]] = set()
        for nid in members:
            for member in index.nodes[nid].members:
                if member not in seen:
                    seen.add(member)
                    keep.members.append(member)
        # 仅代表节点进入 nodes（alias 移除；桶/边/成员寻址均按 find() 归一，
        # _lookup_candidates 的存活过滤兜底清掉残留桶项）。
        remapped[members[0]] = keep
    index.nodes = remapped
    # 成员寻址表跟随合并（alias nid → 代表 nid）。
    index.member_nid = {key: find(nid) for key, nid in index.member_nid.items()}

    # 边端点重映射；合并后自环边（相邻顶点并为一组）消失。
    new_edges: Dict[Tuple[int, int], _Edge] = {}
    for ekey, edge in index.edges.items():
        a = find(edge.a)
        b = find(edge.b)
        if a == b:
            continue
        nk = (min(a, b), max(a, b))
        got = new_edges.get(nk)
        if got is None:
            got = _Edge(nk[0], nk[1])
            new_edges[nk] = got
        got.members.extend(edge.members)
    index.edges = new_edges
    # 桶重建：别名节点已并入代表节点，桶里只保留存活 id（_lookup_candidates
    # 的存活过滤兜底，这里重建保证查询面干净）。
    index._buckets = {}
    for node in index.nodes.values():
        index._bucket_insert(node)
    return []


# ─── op 实现的共用小件 ─────────────────────────────────────────────────────

def _resolve_wfid(index: SharedTopologyIndex, ref: Any) -> Optional[str]:
    if isinstance(ref, str) and ref in index.paths:
        return ref
    return None


def _feature_geometry(fc: GeoJSONFeatureCollection, wfid: str) -> Optional[Dict[str, Any]]:
    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        if isinstance(props, dict) and props.get(WFID_PROP) == wfid:
            geometry = feature.get("geometry")
            return geometry if isinstance(geometry, dict) else None
    return None


def _has_z(path_coords: Sequence[Sequence[Any]]) -> bool:
    return any(len(p) >= 3 for p in path_coords)


def _replace_vertex(coords: List[List[Any]], k: int, x: float, y: float) -> None:
    old = coords[k]
    if len(old) >= 3:
        coords[k] = [x, y, old[2]]  # move/connect 保留原顶点 Z
    else:
        coords[k] = [x, y]


def _geodesic_offset(x: float, y: float, dx_m: float, dy_m: float) -> Tuple[float, float]:
    from app.services.feature_edit.geometry_ops import _geod

    azimuth = math.degrees(math.atan2(dx_m, dy_m))
    magnitude = math.hypot(dx_m, dy_m)
    lon2, lat2, _ = _geod().fwd(x, y, azimuth, magnitude)
    return float(lon2), float(lat2)


def _touched_budget_ok(wfids: Set[str]) -> Optional[EditFinding]:
    if len(wfids) > MAX_SHARED_TOUCHED_FEATURES:
        return finding(
            "target_limit_exceeded",
            "shared edit touches too many features (journal undo-fidelity budget)",
            limit=MAX_SHARED_TOUCHED_FEATURES,
            touched=len(wfids),
        )
    return None


def _coord_of(node: Any) -> Optional[Tuple[float, float]]:
    if (
        isinstance(node, (list, tuple))
        and len(node) >= 2
        and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in node[:2])
    ):
        return (float(node[0]), float(node[1]))
    return None


def _coords_equal(ga: Dict[str, Any], gb: Dict[str, Any]) -> bool:
    return ga.get("type") == gb.get("type") and ga.get("coordinates") == gb.get("coordinates")


def _validated_shape(
    geometry: Dict[str, Any], findings: List[EditFinding], wfid: str
) -> Optional[BaseGeometry]:
    try:
        geom = shape(geometry)
    except Exception:
        findings.append(finding("geometry_invalid_after", "operation produced unparseable geometry", wfid=wfid))
        return None
    bad = _validate_after(geom)
    if bad is not None:
        bad = dict(bad)
        bad["wfid"] = wfid
        findings.append(cast(EditFinding, bad))
        return None
    return geom


def _members_by_feature(members: Sequence[Tuple[str, int, int, int]]) -> Dict[str, List[Tuple[str, int, int, int]]]:
    out: Dict[str, List[Tuple[str, int, int, int]]] = {}
    for m in members:
        out.setdefault(m[0], []).append(m)
    return out


def _apply_vertex_edit(
    fc: GeoJSONFeatureCollection,
    index: SharedTopologyIndex,
    by_feature: Dict[str, List[Tuple[str, int, int, int]]],
    findings: List[EditFinding],
    mutate: Any,
    *,
    z_allowed: bool,
    op_name: str,
) -> List[FeatureDelta]:
    """按要素应用顶点级变更（mutate(coords, kind, ref) -> bool 原地修改）。

    all-or-nothing（R1-F5）：任一触及要素拒绝（最少顶点/收尾边/点顶点等
    守卫）→ typed finding 指名拒绝要素并**放弃全部 delta** —— 绝不提交
    「部分要素改了、部分静默没改」的不对称结果。
    """
    deltas: List[FeatureDelta] = []
    refused: List[str] = []
    for wfid in sorted(by_feature):
        geometry = _feature_geometry(fc, wfid)
        if geometry is None:
            findings.append(finding("feature_not_found", f"feature missing for {op_name}", wfid=wfid))
            continue
        import copy as _copy

        new_geometry = _copy.deepcopy(geometry)
        applied = False
        seen_paths: Set[int] = set()
        for (_w, path_i, _al, k) in sorted(by_feature[wfid], key=lambda t: (t[1], t[3])):
            paths = index.paths.get(wfid) or []
            if path_i >= len(paths) or path_i in seen_paths:
                continue
            _pi, old_coords, addr, kind = paths[path_i]
            target_coords = _coords_at(new_geometry, addr)
            if target_coords is None or k >= len(target_coords):
                continue
            if _has_z(target_coords) and not z_allowed:
                findings.append(
                    finding("invalid_op", f"{op_name} on Z-coordinate path is not supported", wfid=wfid)
                )
                return []
            seen_paths.add(path_i)
            was_closed = kind == "ring" and len(target_coords) >= 4 and target_coords[0] == target_coords[-1]
            if mutate(target_coords, kind, (path_i, k)) is False:
                refused.append(wfid)
                continue
            # 环闭合同步：索引按逻辑顶点登记（闭环首尾只记 0 位），移动共享
            # 顶点后必须把闭合重复点同步为最新坐标，否则环开口。
            if was_closed and target_coords[0] != target_coords[-1]:
                target_coords[-1] = list(target_coords[0])
            applied = True
        if refused:
            break  # all-or-nothing：任一拒绝即放弃
        if not applied:
            continue
        geom_after = _validated_shape(new_geometry, findings, wfid)
        if geom_after is None:
            continue
        if _coords_equal(geometry, new_geometry):
            continue
        deltas.append({"wfid": wfid, "geometry_before": geometry, "geometry_after": new_geometry})
    if refused:
        findings.append(
            finding(
                "invalid_op",
                f"{op_name} refused for some touched features (all-or-nothing atomicity)",
                wfids=sorted(set(refused))[:8],
            )
        )
        return []
    return deltas


def _coords_at(geometry: Dict[str, Any], addr: Sequence[int]) -> Optional[List[List[Any]]]:
    node: Any = geometry.get("coordinates")
    for idx in addr:
        if not isinstance(node, (list, tuple)) or idx >= len(node):
            return None
        node = node[idx]
    if not isinstance(node, list):
        return None
    return node


def _same_coord(p: Sequence[Any], xy: Tuple[float, float]) -> bool:
    try:
        return float(p[0]) == xy[0] and float(p[1]) == xy[1]
    except Exception:
        return False


# ─── op：move_shared_vertex ────────────────────────────────────────────────

def move_shared_vertex(
    fc: GeoJSONFeatureCollection,
    index: SharedTopologyIndex,
    op: Dict[str, Any],
) -> Tuple[List[FeatureDelta], List[EditFinding], OperationSummary]:
    """移动一个共享节点：所有引用该节点的要素顶点同步移动（Z 保留）。

    op 形状：{op, at: [lon, lat] | {wfid, path_index, vertex_index},
             dx_m+dy_m | to: [lon, lat], tolerance_m?}
    """
    op_name = "move_shared_vertex"
    findings: List[EditFinding] = []
    node, loc_findings = _locate_node(index, op)
    findings.extend(loc_findings)
    if loc_findings or node is None:
        return [], findings, {"op": op_name}
    target = _node_target(node, op)
    if isinstance(target, dict):  # EditFinding 也是 dict —— 用键判别
        findings.append(cast(EditFinding, target))
        return [], findings, {"op": op_name}
    if isinstance(target, tuple):
        to_x, to_y = target
    else:
        findings.append(cast(EditFinding, target))
        return [], findings, {"op": op_name}
    touched = {m[0] for m in node.members}
    budget = _touched_budget_ok(touched)
    if budget is not None:
        findings.append(budget)
        return [], findings, {"op": op_name}

    def mutate(coords: List[List[Any]], kind: str, ref: Tuple[int, int]) -> bool:
        _replace_vertex(coords, ref[1], to_x, to_y)
        return True

    deltas = _apply_vertex_edit(
        fc, index, _members_by_feature(node.members), findings, mutate,
        z_allowed=True, op_name=op_name,
    )
    if not deltas and not findings:
        findings.append(finding("invalid_op", "move produces no coordinate change"))
        return [], findings, {"op": op_name}
    summary: OperationSummary = {
        "op": op_name,
        "node": [round(node.x, 9), round(node.y, 9)],
        "to": [round(to_x, 9), round(to_y, 9)],
        "members": len(node.members),
        "touched_features": len(touched),
    }
    return deltas, findings, summary


def _node_target(node: _Node, op: Dict[str, Any]) -> Any:
    """move 目标：to 绝对坐标（返回 tuple）或 dx_m/dy_m 测地偏移；错误返回 finding。"""
    to = op.get("to")
    if to is not None:
        c = _coord_of(to)
        if c is None:
            return finding("invalid_op", "to must be [lon, lat]")
        return c
    dx = op.get("dx_m")
    dy = op.get("dy_m")
    if (
        not isinstance(dx, (int, float))
        or not isinstance(dy, (int, float))
        or isinstance(dx, bool)
        or isinstance(dy, bool)
    ):
        return finding("unit_required", "move_shared_vertex requires to: [lon,lat] or numeric dx_m/dy_m (meters)")
    dx_f, dy_f = float(dx), float(dy)
    if dx_f == 0.0 and dy_f == 0.0:
        return finding("invalid_op", "move displacement is zero")
    magnitude = math.hypot(dx_f, dy_f)
    if magnitude > 10_000.0:
        return finding("invalid_op", "move displacement exceeds the safety bound", meters=magnitude)
    return _geodesic_offset(node.x, node.y, dx_f, dy_f)


# ─── op：insert_shared_vertex ──────────────────────────────────────────────

def insert_shared_vertex(
    fc: GeoJSONFeatureCollection,
    index: SharedTopologyIndex,
    op: Dict[str, Any],
) -> Tuple[List[FeatureDelta], List[EditFinding], OperationSummary]:
    """在边（共享边双侧同步 / 单侧边单侧）上插入顶点。

    op 形状：{op, edge: {between: [coordA, coordB]} | {near: coord, tolerance_m},
             at: [lon, lat], snap_to_edge?: bool(默认 true), tolerance_m?}
    snap_to_edge=true：at 投影到边（细分语义，形状不变）；
    snap_to_edge=false：at 原样插入（产生弯折，所有共享要素同步变形）。
    """
    op_name = "insert_shared_vertex"
    findings: List[EditFinding] = []
    members, edge_findings = _locate_edge(index, op)
    findings.extend(edge_findings)
    if edge_findings or members is None:
        return [], findings, {"op": op_name}

    pt = _coord_of(op.get("at"))
    if pt is None:
        findings.append(finding("invalid_op", "insert_shared_vertex requires at: [lon, lat]"))
        return [], findings, {"op": op_name}
    snap = op.get("snap_to_edge")
    snap = True if snap is None else bool(snap)
    tol = op.get("tolerance_m")
    tol_f: Optional[float] = None
    if tol is not None:
        if (
            not isinstance(tol, (int, float))
            or isinstance(tol, bool)
            or tol <= 0
            or tol > MAX_SHARED_VERTEX_TOLERANCE_M
        ):
            findings.append(finding("unit_required", "insert tolerance must be positive meters within the bound"))
            return [], findings, {"op": op_name}
        tol_f = float(tol)

    seg_coords = _edge_segment_coords(index, members)
    if len(seg_coords) < 2:
        findings.append(finding("shared_node_not_found", "edge member path unavailable"))
        return [], findings, {"op": op_name}
    seg = _LS(seg_coords)
    raw_pt = _ShapelyPoint(pt[0], pt[1])
    if snap:
        projected = seg.interpolate(seg.project(raw_pt))
        ix, iy = float(projected.x), float(projected.y)
        if tol_f is not None:
            d = geodesic_distance_m(pt[0], pt[1], ix, iy)
            if d > tol_f:
                findings.append(
                    finding(
                        "invalid_op",
                        "insert point deviates from the edge beyond tolerance",
                        distance_m=round(d, 3),
                        tolerance_m=tol_f,
                    )
                )
                return [], findings, {"op": op_name}
    else:
        ix, iy = pt[0], pt[1]

    touched = {m[0] for m in members}
    budget = _touched_budget_ok(touched)
    if budget is not None:
        findings.append(budget)
        return [], findings, {"op": op_name}

    def mutate(coords: List[List[Any]], kind: str, ref: Tuple[int, int]) -> bool:
        k = ref[1]
        closed = kind == "ring" and len(coords) >= 4 and coords[0] == coords[-1]
        insert_idx = k + 1
        if closed and k == len(coords) - 1:
            insert_idx = len(coords) - 1  # 收尾边：插在闭合点之前，保持闭合
        if insert_idx > len(coords):
            return False
        coords.insert(insert_idx, [ix, iy])
        return True

    deltas = _apply_vertex_edit(
        fc, index, _members_by_feature(members), findings, mutate,
        z_allowed=False, op_name=op_name,
    )
    if not deltas and not findings:
        findings.append(finding("invalid_op", "insert produced no geometry change"))
        return [], findings, {"op": op_name}
    summary: OperationSummary = {
        "op": op_name,
        "inserted_at": [round(ix, 9), round(iy, 9)],
        "snapped_to_edge": bool(snap),
        "touched_features": len(touched),
    }
    return deltas, findings, summary


# ─── op：delete_shared_vertex ──────────────────────────────────────────────

def delete_shared_vertex(
    fc: GeoJSONFeatureCollection,
    index: SharedTopologyIndex,
    op: Dict[str, Any],
) -> Tuple[List[FeatureDelta], List[EditFinding], OperationSummary]:
    """删除一个共享节点：所有引用要素同步删除；环删除后自动再闭合。"""
    op_name = "delete_shared_vertex"
    findings: List[EditFinding] = []
    node, loc_findings = _locate_node(index, op)
    findings.extend(loc_findings)
    if loc_findings or node is None:
        return [], findings, {"op": op_name}
    touched = {m[0] for m in node.members}
    budget = _touched_budget_ok(touched)
    if budget is not None:
        findings.append(budget)
        return [], findings, {"op": op_name}

    def mutate(coords: List[List[Any]], kind: str, ref: Tuple[int, int]) -> bool:
        k = ref[1]
        closed = kind == "ring" and len(coords) >= 4 and coords[0] == coords[-1]
        if kind == "point":
            return False  # 点要素的唯一定点不可删
        if closed and len(coords) - 1 < 4:
            return False  # 环删除后至少 3 个不同顶点（4 位置含闭合）
        if kind == "line" and len(coords) - 1 < 2:
            return False
        coords.pop(k)
        if kind == "ring" and coords[0] != coords[-1]:
            coords.append(list(coords[0]))  # 再闭合
        return True

    deltas = _apply_vertex_edit(
        fc, index, _members_by_feature(node.members), findings, mutate,
        z_allowed=False, op_name=op_name,
    )
    if not deltas and not findings:
        findings.append(
            finding("invalid_op", "delete refused (minimum vertex count / point vertex / no change)")
        )
        return [], findings, {"op": op_name}
    summary: OperationSummary = {
        "op": op_name,
        "node": [round(node.x, 9), round(node.y, 9)],
        "touched_features": len(touched),
    }
    return deltas, findings, summary


# ─── op：reshape_shared_edge ───────────────────────────────────────────────

def reshape_shared_edge(
    fc: GeoJSONFeatureCollection,
    index: SharedTopologyIndex,
    op: Dict[str, Any],
) -> Tuple[List[FeatureDelta], List[EditFinding], OperationSummary]:
    """重塑共享边：两端节点之间的直边在所有共享要素上同步替换为新路径。

    op 形状：{op, between: [[lon,lat],[lon,lat]], path: [[lon,lat], ...]}
    语义：between 必须是相邻顶点（直边）；path 是新中间顶点（≥1 点）；
    按各要素自身方向（正/反）同步插入；端点保留。
    """
    op_name = "reshape_shared_edge"
    findings: List[EditFinding] = []
    between = op.get("between")
    path = op.get("path")
    if not (isinstance(between, list) and len(between) == 2) or not isinstance(path, list) or not path:
        findings.append(finding("invalid_op", "reshape_shared_edge requires between: [a, b] and non-empty path"))
        return [], findings, {"op": op_name}
    if len(path) > _MAX_RESHAPE_PATH_POINTS:
        findings.append(finding("invalid_op", "reshape path too long", limit=_MAX_RESHAPE_PATH_POINTS))
        return [], findings, {"op": op_name}
    pa = _coord_of(between[0])
    pb = _coord_of(between[1])
    if pa is None or pb is None:
        findings.append(finding("invalid_op", "between endpoints must be [lon, lat] pairs"))
        return [], findings, {"op": op_name}
    new_pts: List[Tuple[float, float]] = []
    for p in path[:_MAX_RESHAPE_PATH_POINTS]:
        c = _coord_of(p)
        if c is None:
            findings.append(finding("invalid_op", "path points must be [lon, lat] pairs"))
            return [], findings, {"op": op_name}
        new_pts.append(c)

    node_a, amb_a = index.node_at(pa[0], pa[1])
    node_b, amb_b = index.node_at(pb[0], pb[1])
    if amb_a or amb_b:
        findings.append(
            finding(
                "shared_node_ambiguous",
                "reshape endpoints match multiple nodes; use exact coordinates",
                candidates=len(amb_a or amb_b),
            )
        )
        return [], findings, {"op": op_name}
    if node_a is None or node_b is None:
        findings.append(finding("shared_node_not_found", "reshape endpoints must be existing vertices of the dataset"))
        return [], findings, {"op": op_name}
    ekey = (min(node_a.nid, node_b.nid), max(node_a.nid, node_b.nid))
    edge = index.edges.get(ekey)
    if edge is None:
        findings.append(finding("shared_node_not_found", "the two endpoints are not adjacent vertices (no direct edge)"))
        return [], findings, {"op": op_name}

    touched = {m[0] for m in edge.members}
    budget = _touched_budget_ok(touched)
    if budget is not None:
        findings.append(budget)
        return [], findings, {"op": op_name}

    def mutate(coords: List[List[Any]], kind: str, ref: Tuple[int, int]) -> bool:
        k = ref[1]
        if k + 1 >= len(coords):
            return False  # 收尾边闭环位（b 在 0 位）：插入语义不同，本 op 拒绝（all-or-nothing 披露）
        forward = _same_coord(coords[k], (node_a.x, node_a.y))
        pts = new_pts if forward else list(reversed(new_pts))
        coords[k + 1 : k + 1] = [[x, y] for (x, y) in pts]
        return True

    deltas = _apply_vertex_edit(
        fc, index, _members_by_feature(edge.members), findings, mutate,
        z_allowed=False, op_name=op_name,
    )
    if not deltas and not findings:
        findings.append(finding("invalid_op", "reshape produced no geometry change"))
        return [], findings, {"op": op_name}
    summary: OperationSummary = {
        "op": op_name,
        "between": [[round(pa[0], 9), round(pa[1], 9)], [round(pb[0], 9), round(pb[1], 9)]],
        "path_points": len(new_pts),
        "touched_features": len(touched),
    }
    return deltas, findings, summary


# ─── op：connect_endpoint ──────────────────────────────────────────────────

def connect_endpoint(
    fc: GeoJSONFeatureCollection,
    index: SharedTopologyIndex,
    op: Dict[str, Any],
) -> Tuple[List[FeatureDelta], List[EditFinding], OperationSummary]:
    """受控端点连接：把线要素指定端点移动到目标节点/坐标（容差 fail-closed）。

    op 形状：{op, endpoint: {wfid, end: "start"|"end"} | {wfid, vertex_index},
             to: [lon,lat] | endpoint-ref, tolerance_m, level_field?}
    测地距离 > tolerance_m → no_snap_candidate；level_field 守卫拒绝桥/隧误连。
    """
    op_name = "connect_endpoint"
    findings: List[EditFinding] = []
    tol = op.get("tolerance_m")
    if (
        not isinstance(tol, (int, float))
        or isinstance(tol, bool)
        or tol <= 0
        or tol > MAX_CONNECT_TOLERANCE_M
    ):
        findings.append(
            finding(
                "unit_required",
                "connect_endpoint requires positive tolerance_m within the bound",
                limit=MAX_CONNECT_TOLERANCE_M,
            )
        )
        return [], findings, {"op": op_name}
    tol_f = float(tol)

    end_loc, end_findings = _locate_line_endpoint(index, op.get("endpoint"))
    findings.extend(end_findings)
    if end_findings or end_loc is None:
        return [], findings, {"op": op_name}
    end_wfid, end_path_i, end_k, end_x, end_y = end_loc

    target = op.get("to")
    target_xy: Optional[Tuple[float, float]] = None
    target_wfid: Optional[str] = None
    if isinstance(target, dict):
        tgt_loc, tgt_findings = _locate_line_endpoint(index, target)
        findings.extend(tgt_findings)
        if tgt_findings or tgt_loc is None:
            return [], findings, {"op": op_name}
        target_wfid = tgt_loc[0]
        target_xy = (tgt_loc[3], tgt_loc[4])
    else:
        c = _coord_of(target)
        if c is None:
            findings.append(finding("invalid_op", "connect target must be [lon,lat] or an endpoint ref"))
            return [], findings, {"op": op_name}
        target_xy = c

    dist = geodesic_distance_m(end_x, end_y, target_xy[0], target_xy[1])
    if dist > tol_f:
        findings.append(
            finding(
                "no_snap_candidate",
                "endpoint is beyond tolerance of the connect target",
                distance_m=round(dist, 3),
                tolerance_m=tol_f,
            )
        )
        return [], findings, {"op": op_name}

    level_field = op.get("level_field")
    if isinstance(level_field, str) and level_field and target_wfid is not None:
        lv_end = _feature_level_value(fc, end_wfid, level_field)
        lv_tgt = _feature_level_value(fc, target_wfid, level_field)
        if lv_end != lv_tgt:
            findings.append(
                finding(
                    "connect_level_conflict",
                    "connect refused: endpoint features declare different level values",
                    level_field=level_field,
                    endpoint_level=None if lv_end is None else str(lv_end)[:32],
                    target_level=None if lv_tgt is None else str(lv_tgt)[:32],
                )
            )
            return [], findings, {"op": op_name}

    geometry = _feature_geometry(fc, end_wfid)
    if geometry is None:
        findings.append(finding("feature_not_found", "endpoint feature missing", wfid=end_wfid))
        return [], findings, {"op": op_name}
    import copy as _copy

    new_geometry = _copy.deepcopy(geometry)
    moved = False
    paths_meta = index.paths.get(end_wfid) or []
    # 只动解析到的那个 part（R1-F4：MultiLineString 逐 part 瞬移会以零
    # findings 静默破坏其它 part —— 距离检查只对解析端点做过）。
    if end_path_i < len(paths_meta):
        _pi, old_coords, addr, kind = paths_meta[end_path_i]
        if kind == "line" and old_coords:
            target_coords = _coords_at(new_geometry, addr)
            if target_coords is not None and end_k < len(target_coords):
                if _has_z(target_coords):
                    findings.append(finding("invalid_op", "connect on Z-coordinate path is not supported", wfid=end_wfid))
                    return [], findings, {"op": op_name}
                target_coords[end_k] = [target_xy[0], target_xy[1]]
                moved = True
    if not moved:
        findings.append(finding("invalid_op", "no line endpoint was moved (endpoint spec mismatch)"))
        return [], findings, {"op": op_name}
    geom_after = _validated_shape(new_geometry, findings, end_wfid)
    if geom_after is None:
        return [], findings, {"op": op_name}
    deltas: List[FeatureDelta] = [
        {"wfid": end_wfid, "geometry_before": geometry, "geometry_after": new_geometry}
    ]
    if _coords_equal(geometry, new_geometry):
        findings.append(finding("invalid_op", "connect produces no coordinate change"))
        return [], findings, {"op": op_name}
    summary: OperationSummary = {
        "op": op_name,
        "wfid": end_wfid,
        "from": [round(end_x, 9), round(end_y, 9)],
        "to": [round(target_xy[0], 9), round(target_xy[1], 9)],
        "distance_m": round(dist, 3),
        "tolerance_m": tol_f,
    }
    return deltas, findings, summary


# ─── 结构检查摘要（inspect 工具用） ────────────────────────────────────────

def inspect_shared_structure(
    index: SharedTopologyIndex,
    *,
    neighbor_wfids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """共享结构的有界摘要（匹配模式/共享边数/节点度分布/悬挂端点/邻接）。"""
    degree_hist: Dict[int, int] = {}
    shared_edges = 0
    for edge in index.edges.values():
        if len({m[0] for m in edge.members}) >= 2:
            shared_edges += 1
    for node in index.nodes.values():
        d = len({m[0] for m in node.members})
        if d >= 2:
            degree_hist[d] = degree_hist.get(d, 0) + 1
    out: Dict[str, Any] = {
        "match_mode": "tolerance" if index.tolerance_m is not None else "exact",
        "tolerance_m": index.tolerance_m,
        "level_field": index.level_field,
        "indexed_features": len(index.paths),
        "excluded_features": dict(sorted(index.excluded.items())[:8]),
        "vertex_count": index.vertex_count,
        "shared_edges": shared_edges,
        "junction_nodes_by_feature_count": dict(sorted(degree_hist.items())[:8]),
        "dangling_endpoints": index.dangling_endpoints(limit=32),
    }
    if neighbor_wfids:
        out["neighbors"] = index.neighbors_of(set(neighbor_wfids))
    return out


def vertex_delta_summary(deltas: List[FeatureDelta]) -> Dict[str, Any]:
    """delta 的测地面积/长度变化摘要（impact 投影复用）。"""
    from pyproj import Geod as _G

    geod = _G(ellps="WGS84")
    per_feature: List[Dict[str, Any]] = []
    area_delta = 0.0
    length_delta = 0.0
    truncated = len(deltas) > MAX_SHARED_TOUCHED_FEATURES
    for d in deltas[:MAX_SHARED_TOUCHED_FEATURES]:
        rec: Dict[str, Any] = {"wfid": d.get("wfid")}
        gb, ga = d.get("geometry_before"), d.get("geometry_after")
        try:
            if isinstance(gb, dict) and isinstance(ga, dict):
                g_before = shape(cast(Dict[str, Any], gb))
                g_after = shape(cast(Dict[str, Any], ga))
                if g_before.geom_type in ("Polygon", "MultiPolygon"):
                    a_before, _p = geod.geometry_area_perimeter(g_before)
                    a_after, _p = geod.geometry_area_perimeter(g_after)
                    area_delta += abs(float(a_after)) - abs(float(a_before))
                    rec["area_before_m2"] = round(abs(float(a_before)), 3)
                    rec["area_after_m2"] = round(abs(float(a_after)), 3)
                elif g_before.geom_type in ("LineString", "MultiLineString"):
                    l_before = float(geod.geometry_length(g_before))
                    l_after = float(geod.geometry_length(g_after))
                    length_delta += l_after - l_before
                    rec["length_before_m"] = round(l_before, 3)
                    rec["length_after_m"] = round(l_after, 3)
        except Exception:
            rec["measure"] = "unavailable"
        per_feature.append(rec)
    summary: Dict[str, Any] = {"touched": per_feature}
    if truncated:
        summary["truncated"] = True  # 度量投影截断披露（R1-F9；delta 本身不截断）
    if area_delta:
        summary["area_delta_m2"] = round(area_delta, 3)
    if length_delta:
        summary["length_delta_m"] = round(length_delta, 3)
    return summary


# ─── 内部定位小件 ──────────────────────────────────────────────────────────

def _locate_node(index: SharedTopologyIndex, op: Dict[str, Any]) -> Tuple[Optional[_Node], List[EditFinding]]:
    """op.at：坐标（容差内唯一）或 {wfid, path_index, vertex_index} 精确引用。"""
    at = op.get("at")
    tol = op.get("tolerance_m")
    tol_f: Optional[float] = None
    if tol is not None:
        if (
            not isinstance(tol, (int, float))
            or isinstance(tol, bool)
            or tol <= 0
            or tol > MAX_SHARED_VERTEX_TOLERANCE_M
        ):
            return None, [finding("unit_required", "tolerance_m must be positive meters within the bound")]
        tol_f = float(tol)
    if isinstance(at, dict):
        wfid = _resolve_wfid(index, at.get("wfid"))
        if wfid is None:
            return None, [finding("shared_node_not_found", "at.wfid is not an indexed feature", wfid=str(at.get("wfid"))[:64])]
        path_i = at.get("path_index")
        k = at.get("vertex_index")
        if (
            not isinstance(path_i, int)
            or not isinstance(k, int)
            or isinstance(path_i, bool)
            or isinstance(k, bool)
        ):
            return None, [finding("invalid_op", "at requires path_index and vertex_index integers")]
        paths = index.paths.get(wfid) or []
        if path_i >= len(paths) or k >= len(paths[path_i][1]):
            return None, [finding("shared_node_not_found", "path/vertex index out of range", wfid=wfid)]
        # 成员身份精确定址（同坐标多层级节点经坐标查询不可区分）。
        nid = index.member_nid.get((wfid, path_i, k))
        node = index.nodes.get(nid) if nid is not None else None
        if node is None:
            return None, [finding("shared_node_not_found", "vertex not found in index", wfid=wfid)]
        return node, []
    c = _coord_of(at)
    if c is None:
        return None, [finding("invalid_op", "at must be [lon, lat] or {wfid, path_index, vertex_index}")]
    node, amb = index.node_at(c[0], c[1], tol_f)
    if amb:
        return None, [
            finding(
                "shared_node_ambiguous",
                "multiple nodes within tolerance; specify exact coordinates or a vertex ref",
                candidates=[
                    {"coordinate": [round(n.x, 9), round(n.y, 9)], "members": len(n.members)}
                    for n in amb[:8]
                ],
            )
        ]
    if node is None:
        return None, [finding("shared_node_not_found", "no vertex at the given location (exact/tolerance)")]
    return node, []


def _locate_edge(
    index: SharedTopologyIndex, op: Dict[str, Any]
) -> Tuple[Optional[List[Tuple[str, int, int, int]]], List[EditFinding]]:
    """op.edge：{between: [coordA, coordB]} 精确边 或 {near: coord, tolerance_m} 最近边。"""
    edge_spec = op.get("edge")
    if not isinstance(edge_spec, dict):
        return None, [finding("invalid_op", "insert_shared_vertex requires edge: {between} or {near, tolerance_m}")]
    between = edge_spec.get("between")
    if isinstance(between, list) and len(between) == 2:
        ca = _coord_of(between[0])
        cb = _coord_of(between[1])
        if ca is None or cb is None:
            return None, [finding("invalid_op", "edge.between endpoints must be [lon, lat] pairs")]
        na, amb_a = index.node_at(ca[0], ca[1])
        nb, amb_b = index.node_at(cb[0], cb[1])
        if amb_a or amb_b:
            return None, [finding("shared_node_ambiguous", "edge endpoints match multiple nodes", candidates=len(amb_a or amb_b))]
        if na is None or nb is None:
            return None, [finding("shared_node_not_found", "edge endpoints must be existing vertices")]
        ekey = (min(na.nid, nb.nid), max(na.nid, nb.nid))
        edge = index.edges.get(ekey)
        if edge is None:
            return None, [finding("shared_node_not_found", "the endpoints are not adjacent vertices (no edge)")]
        return list(edge.members), []
    near = edge_spec.get("near")
    if near is None:
        return None, [finding("invalid_op", "edge requires between or near")]
    c = _coord_of(near)
    if c is None:
        return None, [finding("invalid_op", "edge.near must be [lon, lat]")]
    tol = edge_spec.get("tolerance_m")
    if (
        not isinstance(tol, (int, float))
        or isinstance(tol, bool)
        or tol <= 0
        or tol > MAX_SHARED_VERTEX_TOLERANCE_M
    ):
        return None, [finding("unit_required", "edge.near requires positive tolerance_m within the bound")]
    tol_f = float(tol)
    raw = _ShapelyPoint(c[0], c[1])
    best: Optional[Tuple[float, Tuple[int, int]]] = None
    for ekey, edge in index.edges.items():
        coords_pair = _edge_segment_coords(index, edge.members)
        if len(coords_pair) < 2:
            continue
        seg = _LS(coords_pair)
        d_deg = seg.distance(raw)
        # 度距离粗过滤后再测地精算（中点近似；容差 ≤100m 时误差可忽略并披露近似）
        if d_deg > (tol_f / 100_000.0):
            continue
        mid = seg.interpolate(0.5, normalized=True)
        d_m = geodesic_distance_m(c[0], c[1], float(mid.x), float(mid.y))
        if d_m <= tol_f and (best is None or d_m < best[0]):
            best = (d_m, ekey)
    if best is None:
        return None, [finding("shared_node_not_found", "no edge within tolerance of edge.near")]
    return list(index.edges[best[1]].members), []


def _edge_segment_coords(
    index: SharedTopologyIndex, members: List[Tuple[str, int, int, int]]
) -> List[Tuple[float, float]]:
    if not members:
        return []
    (wfid, path_i, _al, k) = members[0]
    paths = index.paths.get(wfid) or []
    if path_i >= len(paths):
        return []
    coords = paths[path_i][1]
    if k + 1 < len(coords):
        a, b = coords[k], coords[k + 1]
        return [(float(a[0]), float(a[1])), (float(b[0]), float(b[1]))]
    a = coords[k]
    b = coords[0]  # 收尾边
    return [(float(a[0]), float(a[1])), (float(b[0]), float(b[1]))]


def _locate_line_endpoint(
    index: SharedTopologyIndex, spec: Any
) -> Tuple[Optional[Tuple[str, int, int, float, float]], List[EditFinding]]:
    """endpoint spec → (wfid, path_i, k, x, y)。

    {wfid, end: start|end} 解析到**第一条线 path** 的首/尾顶点；{wfid,
    vertex_index} 解析到首个含该下标的线 path。path 级精确解析（R1-F4：
    MultiLineString 的连接只动解析到的那个 part，绝不逐 part 瞬移）。
    """
    if not isinstance(spec, dict):
        return None, [finding("invalid_op", "endpoint must be {wfid, end|vertex_index}")]
    wfid = _resolve_wfid(index, spec.get("wfid"))
    if wfid is None:
        return None, [finding("shared_node_not_found", "endpoint.wfid is not an indexed feature", wfid=str(spec.get("wfid"))[:64])]
    paths = index.paths.get(wfid) or []
    line_paths = [(pi, pm) for pi, pm in enumerate(paths) if pm[3] == "line" and pm[1]]
    if not line_paths:
        return None, [finding("geometry_type_mismatch", "endpoint feature has no line paths", wfid=wfid)]
    end = spec.get("end")
    k = spec.get("vertex_index")
    if isinstance(k, int) and not isinstance(k, bool):
        found: Optional[Tuple[int, int]] = None
        for pi, pm in line_paths:
            if 0 <= k < len(pm[1]):
                found = (pi, k)
                break
        if found is None:
            return None, [finding("shared_node_not_found", "vertex_index out of range", wfid=wfid)]
        target_path, target_k = found
    elif end in ("start", "end"):
        pi, pm = line_paths[0]
        target_path = pi
        target_k = 0 if end == "start" else len(pm[1]) - 1
    else:
        return None, [finding("invalid_op", "endpoint requires end: start|end or vertex_index")]
    coords = paths[target_path][1]
    p = coords[target_k]
    return (wfid, target_path, target_k, float(p[0]), float(p[1])), []


def _endpoint_at_last(spec: Any) -> bool:
    if isinstance(spec, dict):
        k = spec.get("vertex_index")
        if isinstance(k, int) and not isinstance(k, bool):
            return k < 0
        if spec.get("end") == "end":
            return True
    return False


def _feature_level_value(fc: GeoJSONFeatureCollection, wfid: str, field: str) -> Any:
    for feature in fc.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        if isinstance(props, dict) and props.get(WFID_PROP) == wfid:
            return props.get(field)
    return None
