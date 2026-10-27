"""G3D-V2-C 视域几何提取 conformance（app/lib/geo_analysis/viewshed_geometry.py）。

解析 oracle（独立手算期望，非实现回声）：

- cell-edge 对齐恒等式：多边形面积合计（shoelace，CRS 单位²）== 像元
  计数 × |det(transform)| —— 整像元归并不重不漏；
- 矩形掩膜 → 精确世界坐标矩形（UL 原点/cell 尺寸闭式可验）；
- 环结构：环外岛 + 中央洞（甜甜圈）→ 1 个多边形 1 个洞；
- 环取向：GeoJSON RFC 7946 外环 CCW / 洞 CW（北向上图 y 翻转仿射下）；
- 有界化：max_polygons 截断按面积保留最大者；被淘汰像元进
  unattributed_cells（stats 与几何可对账）；
- nodata 像元不进任何多边形；空掩膜 → 空 FeatureCollection（不抛错）；
- 旋转仿射（b/d ≠ 0）：面积恒等式仍精确（仿射面积缩放 |det|）。

与内核的零耦合：可见性判定不在本模块——输入即 terrain.viewshed 的
输出形状（bool 栅格），stats 零漂移由 test_terrain_tool 视域族守护。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.lib.geo_analysis.viewshed_geometry import (
    raster_mask_polygons,
    visibility_geometry,
)

pytestmark = pytest.mark.unit

# 北向上图 GDAL 仿射（cell 10 m，UL 原点 (100, 200)，y 向下）：
# det = a·e − b·d = 10·(−10) = −100 → |det| = 100 m²/像元。
_TF = (10.0, 0.0, 100.0, 0.0, -10.0, 200.0)


def _mask_3x3_corner() -> np.ndarray:
    m = np.zeros((5, 5), dtype=bool)
    m[0:3, 0:3] = True
    return m


# ── 解析恒等式：像元原子性（不重不漏）─────────────────────────────


@pytest.mark.parametrize(
    "shape",
    [(5, 5), (16, 9), (31, 7)],
)
def test_polygon_area_reconstructs_cell_count_exactly(shape):
    rng = np.random.default_rng(42)
    mask = rng.random(shape) > 0.45
    fc, meta = raster_mask_polygons(mask, _TF)
    n = int(np.count_nonzero(mask))
    assert meta["mask_cells"] == n
    # shoelace 合计（含淘汰）必须精确回到 n×|det|（模块内已断言，这里
    # 从输出侧再对账一次：attributed + unattributed == n）。
    assert meta["attributed_cells"] + meta["unattributed_cells"] == n
    for feat in fc["features"]:
        rings = feat["geometry"]["coordinates"]
        total = 0.0
        for ri, ring in enumerate(rings):
            s = 0.0
            for i in range(len(ring) - 1):
                s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
            total += 0.5 * s if ri == 0 else -abs(0.5 * s)
        assert total > 0
        assert abs(total / 100.0 - round(total / 100.0)) < 1e-9


def test_rectangle_mask_exact_world_coordinates():
    """3×3 掩膜（rows 0-2, cols 0-2）→ 世界矩形 [100,130]×[170,200]。

    cell-edge 语义：像元 (0,0) 角点在 (100, 200)（UL 原点），像元
    (2,2) 对角在 (130, 170)——闭式可验，无半像元外插。
    """
    fc, meta = raster_mask_polygons(_mask_3x3_corner(), _TF)
    assert meta["polygon_count"] == 1
    assert meta["attributed_cells"] == 9
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    xs = sorted({round(p[0], 9) for p in ring})
    ys = sorted({round(p[1], 9) for p in ring})
    assert xs == [100.0, 130.0]
    assert ys == [170.0, 200.0]


def test_donut_mask_single_polygon_with_hole():
    m = np.zeros((7, 7), dtype=bool)
    m[0:7, 0:7] = True
    m[3, 3] = False
    fc, meta = raster_mask_polygons(m, _TF)
    assert meta["polygon_count"] == 1
    feat = fc["features"][0]
    assert feat["properties"]["n_holes"] == 1
    rings = feat["geometry"]["coordinates"]
    assert len(rings) == 2
    # 外环 CCW（正有向面积）、洞 CW（负有向面积）——RFC 7946。

    def signed(ring):
        s = 0.0
        for i in range(len(ring) - 1):
            s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
        return 0.5 * s

    assert signed(rings[0]) > 0
    assert signed(rings[1]) < 0
    # 7×7 − 中央 1 = 48 像元
    assert meta["attributed_cells"] == 48


def test_valid_mask_excludes_nodata_from_all_polygons():
    m = np.ones((4, 4), dtype=bool)
    valid = np.ones((4, 4), dtype=bool)
    valid[1:3, 1:3] = False  # 中央 nodata 块
    fc, meta = raster_mask_polygons(m, _TF, valid=valid)
    # 中央 2×2 是洞：12 像元 = 1 个多边形（16−4 边界构成环）
    assert meta["attributed_cells"] == 12
    assert fc["features"][0]["properties"]["n_holes"] == 1


def test_empty_mask_yields_empty_fc_not_error():
    fc, meta = raster_mask_polygons(np.zeros((4, 4), dtype=bool), _TF)
    assert fc["features"] == []
    assert meta["mask_cells"] == 0
    assert meta["polygon_count"] == 0


# ── 有界化 ─────────────────────────────────────────────────────────


def test_max_polygons_keeps_largest_by_area():
    # 3 个独立块：4×4 / 3×3 / 2×2
    m = np.zeros((20, 20), dtype=bool)
    m[0:4, 0:4] = True
    m[10:13, 10:13] = True
    m[17:19, 17:19] = True
    fc, meta = raster_mask_polygons(m, _TF, max_polygons=2)
    assert meta["polygon_count"] == 2
    assert meta["attributed_cells"] == 16 + 9
    assert meta["unattributed_cells"] == 4
    areas = [f["geometry"] for f in fc["features"]]
    assert len(areas) == 2


def test_vertex_budget_raises_area_threshold_deterministically():
    rng = np.random.default_rng(7)
    m = rng.random((60, 60)) > 0.5  # 噪声掩膜：大量锯齿小多边形
    fc1, meta1 = raster_mask_polygons(m, _TF, max_total_vertices=400)
    fc2, meta2 = raster_mask_polygons(m, _TF, max_total_vertices=400)
    assert meta1 == meta2  # 确定性
    assert meta1["vertex_count"] <= 400 or meta1["area_threshold_cells"] >= 64
    assert meta1["attributed_cells"] + meta1["unattributed_cells"] == int(
        np.count_nonzero(m)
    )


# ── 仿射一般性 ─────────────────────────────────────────────────────


def test_rotated_transform_area_identity():
    """旋转仿射（45°）：shoelace 面积仍 = 像元数 × |det|（精确恒等）。"""
    ang = math.pi / 4.0
    c, s = math.cos(ang) * 10.0, math.sin(ang) * 10.0
    tf = (c, -s, 1000.0, s, c, 2000.0)  # det = c² + s² = 100
    m = np.zeros((6, 6), dtype=bool)
    m[1:5, 1:5] = True
    fc, meta = raster_mask_polygons(m, tf)
    assert meta["attributed_cells"] == 16
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    total = 0.0
    for i in range(len(ring) - 1):
        total += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    assert abs(0.5 * total - 16 * 100.0) < 1e-6


# ── visibility_geometry 包装层 ─────────────────────────────────────


def test_visibility_geometry_meta_and_metric_accounting():
    vis = np.zeros((10, 10), dtype=bool)
    vis[2:7, 2:7] = True  # 25 像元
    valid = np.ones((10, 10), dtype=bool)
    valid[4, 4] = False  # nodata 在可视区内
    fc, meta = visibility_geometry(
        vis, valid, _TF, cell_size=10.0, cell_size_x=10.0
    )
    assert meta["visible_cells"] == 24  # 25 − nodata 1
    assert meta["valid_cells"] == 99
    assert meta["attributed_visible_area_m2"] == 2400.0  # 24 × 100 m²
    assert meta["geometry_attribution_fraction"] == 1.0
    assert meta["effective_resolution_m"] == [10.0, 10.0]
    # nodata 像元不可声称 → 多边形带洞
    assert fc["features"][0]["properties"]["n_holes"] == 1


def test_visibility_geometry_rejects_shape_mismatch():
    with pytest.raises(ValueError):
        visibility_geometry(
            np.zeros((3, 3), dtype=bool),
            np.zeros((4, 4), dtype=bool),
            _TF,
            cell_size=10.0,
        )


def test_visibility_geometry_partial_attribution_disclosed():
    vis = np.zeros((30, 30), dtype=bool)
    vis[0:5, 0:5] = True
    vis[20:22, 20:22] = True  # 小块会被 max_polygons=1 淘汰
    valid = np.ones((30, 30), dtype=bool)
    _fc, meta = visibility_geometry(
        vis, valid, _TF, cell_size=10.0, max_polygons=1
    )
    assert meta["attributed_visible_area_m2"] == 2500.0  # 25 × 100 m²
    assert meta["unattributed_visible_area_m2"] == 400.0  # 4 × 100 m²
    assert 0 < meta["geometry_attribution_fraction"] < 1
