"""Raster Grid Contract & Alignment 决策测试（Runtime V3 P1/P6，ADR-0089）。

覆盖：
- 网格身份严格规则：仅 shape 相等绝不构成 aligned（G4 的判定基础）；
- 决策对象：aligned / needs_resample / needs_reproject / incompatible；
- 对齐策略：连续量 bilinear、分类量 nearest（分类图禁 bilinear）；
- aligned_reader（WarpedVRT）：窗口读即对齐读，B 值按 A 网格采样；
- 窗口边长推导：内存预算 → 边长（无拍脑袋 magic number），护栏生效。
"""
import os
import uuid

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds, from_origin

from app.lib.geo_analysis.raster_grid import (
    RasterGridProfile,
    aligned_reader,
    decide_alignment,
    grids_align,
    iter_bounded_windows,
    window_side_from_budget,
    _MAX_WINDOW_SIDE,
    _MIN_WINDOW_SIDE,
)

TD = "data/tmp_grid_tests"


def _write(path, data, *, crs="EPSG:4326", transform=None, nodata=None, res=None):
    h, w = data.shape
    tr = transform or from_origin(0, h, *(res or (1.0, 1.0)))
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype=data.dtype, crs=crs, transform=tr, nodata=nodata,
        tiled=True, blockxsize=32, blockysize=32,
    ) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture(scope="module")
def _td():
    os.makedirs(TD, exist_ok=True)
    return TD


# ── 网格身份（§8）────────────────────────────────────────────────────

def test_grids_align_requires_full_grid_identity(_td):
    """shape 各自相等但 transform 不同 → NOT aligned（§8 明文规则）。"""
    p = os.path.join(_td, f"g_{uuid.uuid4().hex[:6]}.tif")
    _write(p, np.ones((4, 4), dtype="float32"), transform=from_origin(0, 4, 1, 1))
    with rasterio.open(p) as src:
        g1 = RasterGridProfile.from_dataset(src)
    g2 = RasterGridProfile(
        width=4, height=4, crs="EPSG:4326",
        transform=(1.0, 0.0, 0.5, 0.0, -1.0, 4.0),  # 平移半像元的网格
    )
    aligned, reason = grids_align(g1, g2)
    assert not aligned
    assert "transform mismatch" in reason
    os.remove(p)


def test_grids_align_crs_mismatch(_td):
    g1 = RasterGridProfile(width=4, height=4, crs="EPSG:4326", transform=(1, 0, 0, 0, -1, 4))
    g2 = RasterGridProfile(width=4, height=4, crs="EPSG:3857", transform=(1, 0, 0, 0, -1, 4))
    aligned, reason = grids_align(g1, g2)
    assert not aligned and "crs" in reason


def test_grids_align_identical(_td):
    g = RasterGridProfile(width=4, height=4, crs="EPSG:4326", transform=(1, 0, 0, 0, -1, 4))
    aligned, _ = grids_align(g, g)
    assert aligned


# ── 对齐决策（§9/§10）───────────────────────────────────────────────

def test_decision_aligned(_td):
    g = RasterGridProfile(width=4, height=4, crs="EPSG:4326", transform=(1, 0, 0, 0, -1, 4))
    d = decide_alignment(g, g)
    assert d.aligned and not d.resampled and not d.reprojected


def test_decision_needs_resample_g4(_td):
    """G4：A 10m、B 20m → needs_resample，目标网格 = A。"""
    g_a = RasterGridProfile(width=8, height=8, crs="EPSG:32650",
                           transform=(10.0, 0.0, 0.0, 0.0, -10.0, 80.0),
                           bounds=(0.0, 0.0, 80.0, 80.0))
    g_b = RasterGridProfile(width=4, height=4, crs="EPSG:32650",
                            transform=(20.0, 0.0, 0.0, 0.0, -20.0, 80.0),
                            bounds=(0.0, 0.0, 80.0, 80.0))
    d = decide_alignment(g_a, g_b)
    assert d.status == "needs_resample"
    assert d.resampled and not d.reprojected
    assert (d.target_width, d.target_height) == (8, 8)
    assert d.target_transform == g_a.transform
    assert d.resampling == "bilinear"          # 连续量默认
    assert d.to_dict()["target_crs"] == "EPSG:32650"


def test_decision_crs_mismatch_needs_reproject_g5(_td):
    """G5：不同 CRS → needs_reproject，绝不静默逐像元。

    A（EPSG:4326，116.2°~116.28°E）与 B（EPSG:3857，同区域的墨卡托坐标）
    是同一块地表 —— 跨 CRS 的 bounds 必须先换算再判交集。
    """
    g_a = RasterGridProfile(width=8, height=8, crs="EPSG:4326",
                           transform=(0.01, 0.0, 116.2, 0.0, -0.01, 40.0),
                           bounds=(116.2, 39.92, 116.28, 40.0))
    # EPSG:3857: lon 116.2 → x ≈ 12,935,325；lat 39.92 → y ≈ 4,854,324
    # （transform_bounds 实测值；B 取该区域内一小块）
    g_b = RasterGridProfile(width=8, height=8, crs="EPSG:3857",
                           transform=(100.0, 0.0, 12936000.0, 0.0, -100.0, 4865000.0),
                           bounds=(12936000.0, 4855000.0, 12936800.0, 4865000.0))
    d = decide_alignment(g_a, g_b)
    assert d.status == "needs_reproject"
    assert d.reprojected and d.target_crs == "EPSG:4326"


def test_decision_cross_crs_disjoint_is_incompatible(_td):
    """跨 CRS 且（换算后）确实无交集 → incompatible。"""
    g_a = RasterGridProfile(width=4, height=4, crs="EPSG:4326",
                           transform=(1, 0, 0, 0, -1, 4), bounds=(0, 0, 4, 4))
    # EPSG:3857 x≈10,000,000 → lon ≈ 89.9°（远在 A 的 0..4° 之外）
    g_b = RasterGridProfile(width=4, height=4, crs="EPSG:3857",
                           transform=(10, 0, 10000000, 0, -10, 4600400),
                           bounds=(10000000, 4600000, 1000040, 4600400))
    d = decide_alignment(g_a, g_b)
    assert d.incompatible


def test_decision_categorical_forces_nearest(_td):
    """§10：分类栅格默认 nearest，不能用 bilinear。"""
    g_a = RasterGridProfile(width=8, height=8, crs="EPSG:32650",
                           transform=(10.0, 0.0, 0.0, 0.0, -10.0, 80.0),
                           bounds=(0.0, 0.0, 80.0, 80.0))
    g_b = RasterGridProfile(width=4, height=4, crs="EPSG:32650",
                            transform=(20.0, 0.0, 0.0, 0.0, -20.0, 80.0),
                            bounds=(0.0, 0.0, 80.0, 80.0))
    d = decide_alignment(g_a, g_b, categorical=True)
    assert d.resampling == "nearest"


def test_decision_disjoint_footprints_incompatible(_td):
    """对抗 C：足迹无交集 → incompatible（不产空垃圾栅格）。"""
    g_a = RasterGridProfile(width=4, height=4, crs="EPSG:4326",
                           transform=(1, 0, 0, 0, -1, 4), bounds=(0, 0, 4, 4))
    g_b = RasterGridProfile(width=4, height=4, crs="EPSG:4326",
                            transform=(1, 0, 100, 0, -1, 104), bounds=(100, 100, 104, 104))
    d = decide_alignment(g_a, g_b)
    assert d.incompatible and "overlap" in d.reason


# ── aligned_reader（WarpedVRT 虚拟对齐）─────────────────────────────

def test_aligned_reader_resamples_b_onto_a_grid(_td):
    """20m 恒值 B 对齐到 10m A 网格：窗口读得到的值/形状都在 A 网格上。"""
    pa = os.path.join(_td, f"a_{uuid.uuid4().hex[:6]}.tif")
    pb = os.path.join(_td, f"b_{uuid.uuid4().hex[:6]}.tif")
    _write(pa, np.zeros((8, 8), dtype="float32"),
           crs="EPSG:32650", transform=from_bounds(0, 0, 80, 80, 8, 8))
    _write(pb, np.full((4, 4), 7.0, dtype="float32"),
           crs="EPSG:32650", transform=from_bounds(0, 0, 80, 80, 4, 4))
    try:
        with rasterio.open(pa) as src_a:
            g_a = RasterGridProfile.from_dataset(src_a)
        with rasterio.open(pb) as src_b:
            g_b = RasterGridProfile.from_dataset(src_b)
        decision = decide_alignment(g_a, g_b)
        with aligned_reader(pb, decision) as (reader, eff_nodata):
            from rasterio.windows import Window

            win = Window(0, 0, 8, 8)
            arr = reader.read(1, window=win)
            assert arr.shape == (8, 8)
            # B 全域恒 7：所有完全覆盖像元 ≈ 7（bilinear 边界可能轻微混合，
            # 中心区域必须精确）。
            np.testing.assert_allclose(arr[2:6, 2:6], 7.0, atol=1e-6)
    finally:
        os.remove(pa)
        os.remove(pb)


def test_aligned_reader_marks_outside_footprint_as_fill(_td):
    """B 足迹只覆盖 A 的一部分：足迹外 → fill 哨兵（#931 语义）。"""
    pa = os.path.join(_td, f"a_{uuid.uuid4().hex[:6]}.tif")
    pb = os.path.join(_td, f"b_{uuid.uuid4().hex[:6]}.tif")
    # A: 10x10 覆盖 [0,100]²；B: 5x5 覆盖 [0,50]²（顶部）
    _write(pa, np.zeros((10, 10), dtype="float32"),
           crs="EPSG:3857", transform=from_bounds(0, 0, 100, 100, 10, 10))
    _write(pb, np.full((5, 5), 5.0, dtype="float32"),
           crs="EPSG:3857", transform=from_bounds(0, 50, 50, 100, 5, 5))
    try:
        with rasterio.open(pa) as src_a:
            g_a = RasterGridProfile.from_dataset(src_a)
        with rasterio.open(pb) as src_b:
            g_b = RasterGridProfile.from_dataset(src_b)
        decision = decide_alignment(g_a, g_b)
        assert decision.status == "needs_resample"
        with aligned_reader(pb, decision) as (reader, eff_nodata):
            assert eff_nodata is not None
            arr = reader.read(1)
            # 顶部（B 覆盖）为 5；底部（B 外）为 fill 哨兵。
            assert arr[0, 0] == pytest.approx(5.0)
            assert np.isnan(arr[-1, -1])  # float 源 → NaN 哨兵
    finally:
        os.remove(pa)
        os.remove(pb)


def test_aligned_reader_aligned_path_is_direct(_td):
    """aligned 决策下 reader 就是原数据集（无 VRT 开销）。"""
    p = os.path.join(_td, f"ab_{uuid.uuid4().hex[:6]}.tif")
    _write(p, np.arange(16, dtype="float32").reshape(4, 4))
    try:
        with rasterio.open(p) as src:
            g = RasterGridProfile.from_dataset(src)
        decision = decide_alignment(g, g)
        with aligned_reader(p, decision) as (reader, eff):
            assert isinstance(reader, rasterio.DatasetReader)
    finally:
        os.remove(p)


# ── 窗口推导（§12/§13）──────────────────────────────────────────────

def test_window_side_from_budget_is_derived():
    side = window_side_from_budget(256)
    cells = 256 * 1024 * 1024 // 64
    assert side == int(np.sqrt(cells)) or side == _MAX_WINDOW_SIDE
    # 预算减半 → 边长 ~×1/√2
    side_small = window_side_from_budget(64)
    assert _MIN_WINDOW_SIDE <= side_small < side
    # 护栏：超小预算不跌破下限（1MB → 16384 cells → 128），超大不破上限
    assert window_side_from_budget(1) >= _MIN_WINDOW_SIDE
    assert window_side_from_budget(1) == int(np.sqrt(1024 * 1024 // 64))
    assert window_side_from_budget(1_000_000) == _MAX_WINDOW_SIDE


def test_iter_bounded_windows_fixed_grid_and_subdivision():
    from rasterio.windows import Window

    wins = list(iter_bounded_windows(100, 130, window_side=64))
    assert len(wins) == 2 * 3  # 2 列 × 3 行
    assert wins[0] == Window(0, 0, 64, 64)
    assert wins[-1].width == 100 - 64 and wins[-1].height == 130 - 128


def test_iter_bounded_windows_prefers_source_blocks(_td, monkeypatch):
    """tiled 源且块 ≤ 预算 → 迭代源自然 block（I/O 对齐）。"""
    p = os.path.join(_td, f"blk_{uuid.uuid4().hex[:6]}.tif")
    data = np.zeros((64, 64), dtype="float32")
    with rasterio.open(
        p, "w", driver="GTiff", height=64, width=64, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(0, 64, 1, 1),
        tiled=True, blockxsize=32, blockysize=32,
    ) as dst:
        dst.write(data, 1)
    try:
        with rasterio.open(p) as src:
            wins = list(iter_bounded_windows(64, 64, window_side=2048, src=src))
        shapes = {(int(w.width), int(w.height)) for w in wins}
        assert shapes == {(32, 32)}  # 2×2 个 32×32 块
    finally:
        os.remove(p)
