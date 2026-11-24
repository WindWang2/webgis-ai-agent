"""WP6 —— 新综合引擎性能/正确性对照（合成数据，确定性；heavy 显式跑）。

正确性族（默认跑）：连通性桥接后无断网、消除后计数守恒、聚合后 Σ 守恒、
拓扑简化后无缝（与 test_topology_shared_edge_v1 的 oracle 互为补充 ——
此处用更大的合成网格）。
性能族（MSP_PERF=1 显式跑）：20k 段路网桥接、500 面消除+拓扑、5k 点聚合
的 wall time（同机对照记 EVIDENCE.md，不凭空宣称门限）。
"""
from __future__ import annotations

import os
import time

import pytest

from app.lib.cartography.multiscale.network_select import (
    select_lines_with_connectivity,
)
from app.lib.cartography.multiscale.point_aggregate import aggregate_points
from app.lib.cartography.multiscale.polygon_eliminate import (
    eliminate_small_polygons,
)
from app.lib.cartography.multiscale.qa_pack import (
    audit_polygon_pairwise_seams,
    audit_sum_conservation,
)
from app.lib.cartography.multiscale.topology import simplify_polygon_topology

pytestmark = pytest.mark.cartography

MSP_PERF = os.environ.get("MSP_PERF") == "1"
LAT = 30.66


def _grid_polygons(nx: int, ny: int, d: float = 0.02) -> list:
    feats = []
    for gx in range(nx):
        for gy in range(ny):
            x0 = 104.0 + gx * d
            y0 = LAT + gy * d
            ring = [[x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d],
                    [x0, y0]]
            feats.append({
                "type": "Feature", "id": f"cell-{gx}-{gy}",
                "properties": {"name": f"cell-{gx}-{gy}"},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            })
    return feats


def _road_grid(n: int) -> list:
    """横向段链（相邻段端点相接、行覆盖 [104,105]）+ 每行一条纵向连接段
    精确接在行链末端 (105, y)→(105, y+0.02)（行间可桥接）。"""
    feats = []
    for i in range(n):
        col = i % 50
        x = 104.0 + col * 0.02
        y = LAT + (i // 50) * 0.02
        cls = "乡道" if i % 3 else "国道"
        pts = [[x, y], [x + 0.02, y]]
        feats.append({
            "type": "Feature", "id": f"road-{i}",
            "properties": {"道路等级": cls},
            "geometry": {"type": "LineString", "coordinates": pts},
        })
    rows = n // 50
    for r in range(max(0, rows - 1)):   # 最后一行无需下探
        y = LAT + r * 0.02
        feats.append({
            "type": "Feature", "id": f"link-{r}",
            "properties": {"道路等级": "乡道"},
            "geometry": {"type": "LineString",
                         "coordinates": [[105.0, y], [105.0, y + 0.02]]},
        })
    return feats


def _points_grid(n: int) -> list:
    feats = []
    side = max(1, int(n ** 0.5))
    for i in range(n):
        lon = 104.0 + (i % side) * 0.001
        lat = LAT + (i // side) * 0.001
        feats.append({
            "type": "Feature", "id": f"p-{i}",
            "properties": {"school_count": 1},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })
    return feats


class TestCorrectnessOnLargerGrids:
    def test_topology_grid_no_seams(self):
        feats = _grid_polygons(12, 12)
        out = simplify_polygon_topology(feats, tolerance_m=150.0,
                                        latitude_hint=LAT)
        assert out.receipt["geometry_fallback"] == 0
        audit = audit_polygon_pairwise_seams(
            out.feature_collection["features"], latitude_hint=LAT)
        assert audit.get("within_tolerance") is True

    def test_jittered_grid_shared_chains_and_seams(self):
        # 非空洞 oracle：共享边界带抖动中点（DP 有真实可塌顶点、两侧引用
        # 同一边界函数 ⇒ 顶点配对）的 6×6 网格 —— 结点切分后链逐段配对
        # （chains_shared_multi>30），塌缩两侧一致，无缝隙/无重叠。
        feats = []
        d = 0.02

        # 偏移 ~5m：远小于 800m 容差（DP 必塌）且远小于面积预算
        # （4 偏移 × 0.5·边·偏移 / 格面积 ≈ 0.1% << 2%）
        def oxv(gx: int, gy: int) -> float:
            return 5e-5 * ((gx + gy) % 3 - 1)   # 竖边界中点 x 偏移

        def oyh(gx: int, gy: int) -> float:
            return 5e-5 * ((gx + gy + 1) % 3 - 1)   # 横边界中点 y 偏移

        for gx in range(6):
            for gy in range(6):
                x0 = 104.0 + gx * d
                y0 = LAT + gy * d
                ring = [
                    [x0, y0],
                    [x0 + d / 2, y0 + oyh(gx, gy)],                  # 底中
                    [x0 + d, y0],
                    [x0 + d + oxv(gx + 1, gy), y0 + d / 2],          # 右中
                    [x0 + d, y0 + d],
                    [x0 + d / 2, y0 + d + oyh(gx, gy + 1)],          # 顶中
                    [x0, y0 + d],
                    [x0 + oxv(gx, gy), y0 + d / 2],                  # 左中
                    [x0, y0],
                ]
                feats.append({
                    "type": "Feature", "id": f"j-{gx}-{gy}",
                    "properties": {},
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                })
        out = simplify_polygon_topology(feats, tolerance_m=800.0,
                                        latitude_hint=LAT)
        assert out.receipt["chains_shared_multi"] > 30
        assert out.receipt["geometry_fallback"] == 0, \
            out.receipt["reason_counts"]
        audit = audit_polygon_pairwise_seams(
            out.feature_collection["features"], latitude_hint=LAT)
        assert audit.get("within_tolerance") is True, audit

    def test_eliminate_grid_conservation(self):
        feats = _grid_polygons(10, 10)
        # 缩小一半的孤立行混入（无邻居消除不了 → 披露保留）
        out = eliminate_small_polygons(feats, min_area_m2=10 ** 9)
        assert out.receipt["stats_match"] is True
        kept = len(out.feature_collection["features"])
        assert kept + out.receipt["eliminated_count"] == 100

    def test_network_bridge_full_connectivity(self):
        feats = _road_grid(100)
        out = select_lines_with_connectivity(
            feats, importance_field="道路等级", kept_values=["国道"])
        assert out.receipt["fully_connected"] is True
        assert out.receipt["components_final"] <= 1

    def test_point_aggregate_sum_conservation_5k(self):
        feats = _points_grid(2500)
        out = aggregate_points(feats, cell_size_m=2000.0,
                               latitude_hint=LAT,
                               sum_fields=["school_count"])
        assert out.receipt["stats_match"] is True
        sums = audit_sum_conservation(
            feats, out.feature_collection["features"],
            sum_fields=["school_count"])
        assert sums["evaluated"] and sums["conserved"] is True


@pytest.mark.skipif(not MSP_PERF, reason="MSP_PERF=1 显式跑")
class TestPerfHeavy:
    def test_20k_road_network_bridging(self):
        feats = _road_grid(20_000)
        t0 = time.monotonic()
        out = select_lines_with_connectivity(
            feats, importance_field="道路等级", kept_values=["国道"])
        dt = time.monotonic() - t0
        assert out.receipt["fully_connected"] is True
        print(f"PERF network_select 20k: {dt:.2f}s "
              f"bridges={out.receipt['bridges_added']}")

    def test_500_polygon_eliminate_topology(self):
        feats = _grid_polygons(22, 23)   # 506 面
        t0 = time.monotonic()
        elim = eliminate_small_polygons(feats, min_area_m2=1.0)
        topo = simplify_polygon_topology(
            elim.feature_collection["features"], tolerance_m=120.0,
            latitude_hint=LAT)
        dt = time.monotonic() - t0
        assert topo.receipt["geometry_fallback"] == 0
        print(f"PERF eliminate+topology 506: {dt:.2f}s "
              f"chains={topo.receipt['chains_shared_multi']}")

    def test_5k_point_aggregation(self):
        feats = _points_grid(5000)
        t0 = time.monotonic()
        out = aggregate_points(feats, cell_size_m=2000.0,
                               latitude_hint=LAT)
        dt = time.monotonic() - t0
        print(f"PERF point_aggregate 5k: {dt:.2f}s "
              f"reps={out.receipt['representative_count']}")
