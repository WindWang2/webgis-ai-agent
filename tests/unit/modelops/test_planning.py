"""TilePlanner 契约测试（R1-C3/M7 验收：确定性、core/read 分离、metadata-only）。"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.modelops.descriptor import SpatialRequirements
from app.lib.modelops.errors import PlanningError
from app.lib.modelops.planning import core_coverage, plan_tiles


def _spatial(**kw) -> SpatialRequirements:
    base = dict(chip_size=(16, 16), context_size=(16, 16))
    base.update(kw)
    return SpatialRequirements(**base)


def _desc_like(spatial):
    class _S:
        pass

    _S.spatial = spatial
    return _S()


def test_exact_division_no_edge_tiles():
    plan = plan_tiles(_desc_like(_spatial()), raster_height=32, raster_width=32)
    assert len(plan.tiles) == 4
    assert all(t.core_window[2] == 16 and t.core_window[3] == 16 for t in plan.tiles)


def test_edge_and_odd_dimensions():
    plan = plan_tiles(_desc_like(_spatial()), raster_height=40, raster_width=37)
    # 覆盖完整 + core 全部落在栅格内
    for t in plan.tiles:
        r, c, h, w = t.core_window
        assert 0 <= r and r + h <= 40
        assert 0 <= c and c + w <= 37
        assert h > 0 and w > 0
    covered = np.zeros((40, 37), dtype=bool)
    for t in plan.tiles:
        r, c, h, w = t.core_window
        covered[r: r + h, c: c + w] = True
    assert covered.all()  # 无缝隙：奇数尺寸被完整覆盖


def test_tiny_raster_single_tile():
    plan = plan_tiles(_desc_like(_spatial(chip_size=(64, 64), context_size=(64, 64))),
                      raster_height=10, raster_width=8)
    assert len(plan.tiles) == 1
    tile = plan.tiles[0]
    assert tile.core_window == (0, 0, 10, 8)


def test_overlap_stride():
    plan = plan_tiles(
        _desc_like(_spatial(chip_size=(16, 16), context_size=(16, 16), stride=(8, 8))),
        raster_height=32, raster_width=32,
    )
    assert len(plan.tiles) == 9  # 3x3 网格（起点 0,8,16）
    assert core_coverage(plan) > 32 * 32  # 重叠 ⇒ 覆盖计数超面积


def test_read_window_always_inside_raster():
    """R1-C3：read 必须 clamp 在栅格内（RasterReader 硬拒越界）。"""
    plan = plan_tiles(
        _desc_like(_spatial(chip_size=(16, 16), context_size=(24, 24))),
        raster_height=20, raster_width=20,
    )
    for t in plan.tiles:
        rr, rc, rh, rw = t.read_window
        assert 0 <= rr and rr + rh <= 20
        assert 0 <= rc and rc + rw <= 20


def test_chip_equals_context_when_padded():
    """pad 后 chip 尺寸 = context 尺寸（确定性张量形状）。"""
    plan = plan_tiles(
        _desc_like(_spatial(chip_size=(16, 16), context_size=(24, 24))),
        raster_height=20, raster_width=20,
    )
    for t in plan.tiles:
        assert t.chip_hw == (24, 24)


def test_core_offset_formula_consistent():
    """merge 偏移公式：core 原点在 chip 数组里的位置 = row-read_row+pad_top。"""
    plan = plan_tiles(
        _desc_like(_spatial(chip_size=(16, 16), context_size=(24, 24))),
        raster_height=50, raster_width=50,
    )
    for t in plan.tiles:
        row, col = t.core_window[0], t.core_window[1]
        off_y = row - t.read_window[0] + t.pad[1]
        off_x = col - t.read_window[1] + t.pad[0]
        assert 0 <= off_y and off_y + t.core_window[2] <= t.chip_hw[0]
        assert 0 <= off_x and off_x + t.core_window[3] <= t.chip_hw[1]


def test_deterministic_planning():
    p1 = plan_tiles(_desc_like(_spatial()), raster_height=45, raster_width=33)
    p2 = plan_tiles(_desc_like(_spatial()), raster_height=45, raster_width=33)
    assert p1.tiles == p2.tiles
    assert [t.index for t in p1.tiles] == list(range(len(p1.tiles)))  # row-major


def test_virtual_huge_raster_metadata_only():
    """R1-M7 lazy 证明：100k x 100k 只依赖 metadata（无任何像素读取）。"""
    plan = plan_tiles(
        _desc_like(_spatial(chip_size=(8192, 8192), context_size=(8192, 8192))),
        raster_height=100_000, raster_width=100_000,
    )
    assert len(plan.tiles) == 13 * 13
    assert all(t.read_window[2] <= 8192 for t in plan.tiles)
    # 栅格 shape 只出现在计划值里，planner 不触发任何 IO。


def test_invalid_stride_rejected():
    with pytest.raises(PlanningError):
        plan_tiles(_desc_like(_spatial(stride=(20, 20))), raster_height=32, raster_width=32)


def test_context_smaller_than_chip_rejected():
    with pytest.raises(PlanningError):
        plan_tiles(
            _desc_like(_spatial(chip_size=(24, 24), context_size=(16, 16))),
            raster_height=32, raster_width=32,
        )
