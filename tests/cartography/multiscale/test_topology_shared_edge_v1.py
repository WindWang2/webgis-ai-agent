"""topology 引擎测试 —— 共享边一致性 oracle / 无缝几何审计 / 回退传播。

核心 oracle（独立几何审计，非被测函数自证）：
- 共享边两侧输出坐标逐字节一致（不可能缝隙的引擎级保证）；
- 派生集合逐对 intersection 面积 ≈ 0（无重叠）且 union 覆盖差 ≤ 容差级
  （shapely 独立复核，与引擎内部不变量无关）；
- 回退传播后邻居同步回原链（第二轮重建语义）。
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shapely.geometry import shape
from shapely.ops import unary_union

from app.lib.cartography.multiscale.topology import simplify_polygon_topology

# 坐标（度，北京纬度附近）；容差 30km：抖动中点（≈2-3km 偏移）全塌、
# 角点/方块的 1° 边（≈86-111km）与洞（≈44km 偏移）恒存。
TOL_M = 30_000.0

#: 共享边抖动（y 位置, 向外偏移 dx 度）。dx≠0 的塌缩产生真实面积变化。
_JITTER_AB: Sequence[Tuple[float, float]] = ((0.25, 0.02), (0.5, -0.02),
                                             (0.75, 0.02))


def _square(x0: float, y0: float, x1: float, y1: float, *, fid: str,
            left_edge: Optional[Sequence[Tuple[float, float]]] = None,
            right_edge: Optional[Sequence[Tuple[float, float]]] = None) -> Dict[str, Any]:
    """轴对方块；left/right_edge = 沿该边 (y, x 偏移) 的抖动中点。

    偏移语义 = **共享物理边界点**：左邻 right_edge 的 (y, b) 与右邻
    left_edge 的 (y, b) 是同一坐标 (x_edge + b, y) —— 两侧配对才构成
    共享链。环恒闭合、逆时针（左点按 y 降序插在顶左与底左之间）。
    """
    right_pts: List[List[float]] = []
    if right_edge:
        right_pts = [[x1 + b, y] for y, b in sorted(right_edge)]
    left_pts: List[List[float]] = []
    if left_edge:
        left_pts = [[x0 + b, y] for y, b in sorted(left_edge, reverse=True)]
    ring: List[List[float]] = [[x0, y0], [x1, y0]]
    ring.extend(right_pts)
    ring.append([x1, y1])
    ring.append([x0, y1])
    ring.extend(left_pts)
    ring.append([x0, y0])
    return {
        "type": "Feature", "id": fid,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"name": fid},
    }


def _edge_pts_of(geom: Dict[str, Any], x: float) -> List[List[float]]:
    """几何 → 共享边附近（|x'−x|<0.05）的顶点序列（环序，去闭合重复）。"""
    ring = geom["coordinates"][0]
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    return [c for c in ring if abs(c[0] - x) < 0.05]


def _norm_edge(pts: List[List[float]]) -> List[List[float]]:
    """边顶点按 y 升序归一（两侧环序方向相反；归一后可比）。"""
    return sorted(pts, key=lambda c: c[1])


def _pairwise_sliver_audit(features: List[Dict[str, Any]]) -> Dict[str, float]:
    """独立几何审计：派生集合逐对 intersection / union 覆盖差（米²）。"""
    geoms = [shape(f["geometry"]) for f in features
             if f.get("geometry", {}).get("type") in ("Polygon", "MultiPolygon")]
    m_per_deg_lat = 110_574.0
    kx = 111_320.0 * math.cos(math.radians(39.0))
    max_overlap = 0.0
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            inter = geoms[i].intersection(geoms[j]).area
            max_overlap = max(max_overlap, inter * kx * m_per_deg_lat)
    union = unary_union(geoms).area if geoms else 0.0
    total = sum(g.area for g in geoms)
    cover_gap = (total - union) * kx * m_per_deg_lat
    return {
        "max_pairwise_overlap_m2": max_overlap,
        "coverage_gap_m2": max(0.0, cover_gap),
    }


class TestSharedEdgeConsistency:
    def test_shared_edge_byte_identical_both_sides(self):
        a = _square(0, 0, 1, 1, fid="A", right_edge=_JITTER_AB)
        b = _square(1, 0, 2, 1, fid="B", left_edge=_JITTER_AB)
        out = simplify_polygon_topology([a, b], tolerance_m=TOL_M,
                                        latitude_hint=39.0)
        assert out.receipt["geometry_simplified"] == 2
        feats = out.feature_collection["features"]
        ga, gb = feats[0]["geometry"], feats[1]["geometry"]
        # 共享边（x=1 竖边）两侧坐标序列必须逐字节一致（引擎核心保证）
        edge_a = _norm_edge(_edge_pts_of(ga, 1.0))
        edge_b = _norm_edge(_edge_pts_of(gb, 1.0))
        assert edge_a, "A 侧必须保留共享边"
        assert edge_a == edge_b
        # 抖动中点两侧都被同一条链简化掉（只剩两端角点）
        assert len(edge_a) == 2

    def test_no_gap_no_overlap_audit(self):
        a = _square(0, 0, 1, 1, fid="A", right_edge=_JITTER_AB)
        b = _square(1, 0, 2, 1, fid="B", left_edge=_JITTER_AB)
        out = simplify_polygon_topology([a, b], tolerance_m=TOL_M,
                                        latitude_hint=39.0)
        audit = _pairwise_sliver_audit(out.feature_collection["features"])
        assert audit["max_pairwise_overlap_m2"] < 1.0        # 无重叠
        assert audit["coverage_gap_m2"] < 1.0                # 无缝隙

    def test_three_chain_neighbors(self):
        jt: Sequence[Tuple[float, float]] = ((0.4, 0.02), (0.7, -0.02))
        feats = [
            _square(0, 0, 1, 1, fid="A", right_edge=jt),
            _square(1, 0, 2, 1, fid="B", left_edge=jt, right_edge=jt),
            _square(2, 0, 3, 1, fid="C", left_edge=jt),
        ]
        out = simplify_polygon_topology(feats, tolerance_m=TOL_M,
                                        latitude_hint=39.0)
        assert out.receipt["chains_shared_multi"] >= 2
        g = [f["geometry"] for f in out.feature_collection["features"]]
        for i in range(2):
            edge_left = _norm_edge(_edge_pts_of(g[i + 1], float(i + 1)))
            edge_right = _norm_edge(_edge_pts_of(g[i], float(i + 1)))
            assert edge_left == edge_right
        audit = _pairwise_sliver_audit(out.feature_collection["features"])
        assert audit["max_pairwise_overlap_m2"] < 1.0
        assert audit["coverage_gap_m2"] < 1.0

    def test_opposite_shared_edges_ring_closure(self):
        # 中间环两侧都与邻居共享（左右共享、上下独占）
        jt: Sequence[Tuple[float, float]] = ((0.5, 0.03),)
        mid = _square(1, 0, 2, 1, fid="M", left_edge=jt, right_edge=jt)
        left = _square(0, 0, 1, 1, fid="L", right_edge=jt)
        right = _square(2, 0, 3, 1, fid="R", left_edge=jt)
        out = simplify_polygon_topology([left, mid, right], tolerance_m=TOL_M,
                                        latitude_hint=39.0)
        feats = out.feature_collection["features"]
        assert out.receipt["geometry_fallback"] == 0
        for f in feats:
            assert shape(f["geometry"]).is_valid


class TestInvariants:
    def test_holes_preserved(self):
        donut = {
            "type": "Feature", "id": "D",
            "geometry": {"type": "Polygon", "coordinates": [
                [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]],
                [[0.8, 0.8], [1.2, 0.8], [1.2, 1.2], [0.8, 1.2], [0.8, 0.8]],
            ]},
            "properties": {},
        }
        neighbor = _square(2, 0, 3, 2, fid="N")
        out = simplify_polygon_topology([donut, neighbor], tolerance_m=TOL_M,
                                        latitude_hint=39.0)
        assert out.receipt["geometry_fallback"] == 0
        d = shape(out.feature_collection["features"][0]["geometry"])
        assert len(d.interiors) == 1, "洞数必须保持"

    def test_multipolygon_parts_kept(self):
        mp = {
            "type": "Feature", "id": "MP",
            "geometry": {"type": "MultiPolygon", "coordinates": [
                [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                [[[3, 0], [4, 0], [4, 1], [3, 1], [3, 0]]],
            ]},
            "properties": {},
        }
        neighbor = _square(1, 0, 2, 1, fid="N")
        out = simplify_polygon_topology([mp, neighbor], tolerance_m=TOL_M,
                                        latitude_hint=39.0)
        assert out.receipt["geometry_fallback"] == 0
        g = shape(out.feature_collection["features"][0]["geometry"])
        assert g.geom_type == "MultiPolygon"
        assert len(g.geoms) == 2

    def test_non_polygon_passthrough(self):
        pt = {"type": "Feature", "id": "P",
              "geometry": {"type": "Point", "coordinates": [1.0, 0.5]},
              "properties": {}}
        line = {"type": "Feature", "id": "L",
                "geometry": {"type": "LineString",
                             "coordinates": [[0, 0], [1, 1]]},
                "properties": {}}
        poly = _square(0, 0, 1, 1, fid="A")
        out = simplify_polygon_topology([pt, line, poly], tolerance_m=TOL_M,
                                        latitude_hint=39.0)
        feats = out.feature_collection["features"]
        assert feats[0]["geometry"] == pt["geometry"]
        assert feats[1]["geometry"] == line["geometry"]
        assert "non_polygon_passthrough" in out.receipt["reason_counts"]

    def test_area_error_budget_forces_protected_chains(self):
        # 面积预算压到极限 → 第一轮超差 → 链保护 → 第二轮原坐标重建恒成功
        a = _square(0, 0, 1, 1, fid="A", right_edge=_JITTER_AB)
        b = _square(1, 0, 2, 1, fid="B", left_edge=_JITTER_AB)
        out = simplify_polygon_topology([a, b], tolerance_m=TOL_M,
                                        latitude_hint=39.0,
                                        max_area_error_ratio=1e-9)
        assert out.receipt["geometry_fallback"] == 0
        assert out.receipt["chains_protected_original"] >= 1
        # 保护链两侧同用原坐标：共享边与输入抖动中点逐字节一致
        feats = out.feature_collection["features"]
        edge_a = _norm_edge(_edge_pts_of(feats[0]["geometry"], 1.0))
        edge_b = _norm_edge(_edge_pts_of(feats[1]["geometry"], 1.0))
        expected = _norm_edge([[1.0, 0.0]] + [[1.0 + b, y] for y, b in _JITTER_AB]
                              + [[1.0, 1.0]])
        def _rnd(pts: List[List[float]]) -> List[List[float]]:
            return [[round(x, 9), round(y, 9)] for x, y in pts]
        assert _rnd(edge_a) == _rnd(edge_b) == _rnd(expected)

    def test_ring_start_on_shared_edge_reverse_traversal(self):
        # 评审 R1-P0-1 回归：右邻格环起点恰在共享边端点 (1,1) 上、共享边
        # 按 y 降序遍历（与左格升序方向相反）—— 此前首个 run 不做方向
        # 纠正 → 拼接从链错误端开始 → 干净输入恒伪回退。
        jt: Sequence[Tuple[float, float]] = ((0.3, 0.02), (0.7, -0.02))
        lower = _square(0, 0, 1, 1, fid="lower", right_edge=jt)
        # 右格逆时针环：tl(1,1) → 左边中点降序（共享边，反向）→ bl(1,0)
        # → br(2,0) → tr(2,1) → 闭合
        ring = [[1.0, 1.0]]
        ring.extend([1.0 + b, y] for y, b in sorted(jt, reverse=True))
        ring.extend([[1.0, 0.0], [2.0, 0.0], [2.0, 1.0]])
        ring.append([1.0, 1.0])
        upper = {
            "type": "Feature", "id": "upper",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {},
        }
        out = simplify_polygon_topology([lower, upper], tolerance_m=30_000.0,
                                        latitude_hint=39.0)
        assert out.receipt["geometry_fallback"] == 0, \
            out.receipt["reason_counts"]
        feats = out.feature_collection["features"]
        edge_lower = _norm_edge(_edge_pts_of(feats[0]["geometry"], 1.0))
        edge_upper = _norm_edge(_edge_pts_of(feats[1]["geometry"], 1.0))
        assert edge_lower == edge_upper, "两侧共享边必须一致"
        # 塌缩后只剩角点（抖动被同链简化）
        assert len(edge_lower) == 2

    def test_empty_input(self):
        out = simplify_polygon_topology([], tolerance_m=TOL_M)
        assert out.feature_collection["features"] == []
        assert out.receipt["stats_match"] is True


class TestHonestDisclosure:
    def test_partial_vertex_match_disclosed(self):
        # 邻居共享顶点但边顶点未配对（A 有中点、B 无）→ 无配对共享链
        a = _square(0, 0, 1, 1, fid="A", right_edge=((0.5, 0.02),))
        b = _square(1, 0, 2, 1, fid="B")
        out = simplify_polygon_topology([a, b], tolerance_m=TOL_M,
                                        latitude_hint=39.0)
        # A 的右边界段与 B 的直边不构成配对共享段 → partial_vertex_match
        assert out.receipt["partial_vertex_match"] is True
        assert any("QA 审计" in d for d in out.receipt["disclosures"])

    def test_determinism(self):
        jt: Sequence[Tuple[float, float]] = ((0.4, 0.02), (0.7, -0.02))
        feats = [
            _square(0, 0, 1, 1, fid="A", right_edge=jt),
            _square(1, 0, 2, 1, fid="B", left_edge=jt, right_edge=jt),
            _square(2, 0, 3, 1, fid="C", left_edge=jt),
        ]
        out1 = simplify_polygon_topology(feats, tolerance_m=TOL_M,
                                         latitude_hint=39.0)
        out2 = simplify_polygon_topology(feats, tolerance_m=TOL_M,
                                         latitude_hint=39.0)
        assert json.dumps(out1.feature_collection, sort_keys=True) == \
            json.dumps(out2.feature_collection, sort_keys=True)
        assert json.dumps(out1.receipt, sort_keys=True) == \
            json.dumps(out2.receipt, sort_keys=True)

    def test_zero_tolerance_is_noop(self):
        feats = [_square(0, 0, 1, 1, fid="A", right_edge=_JITTER_AB),
                 _square(1, 0, 2, 1, fid="B", left_edge=_JITTER_AB)]
        out = simplify_polygon_topology(feats, tolerance_m=0.0,
                                        latitude_hint=39.0)
        # 容差 0：DP 不丢任何顶点；输出经米空间往返，按 1e-9 度容差比对
        def _rounded(geom):
            return [[[round(x, 9), round(y, 9)] for x, y in ring]
                    for ring in geom["coordinates"]]
        for fin, fout in zip(feats, out.feature_collection["features"]):
            assert len(_rounded(fout["geometry"])[0]) == len(
                _rounded(fin["geometry"])[0])
            for c_out, c_in in zip(_rounded(fout["geometry"])[0],
                                   _rounded(fin["geometry"])[0]):
                assert c_out == c_in
