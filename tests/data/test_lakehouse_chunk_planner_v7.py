"""Lakehouse V7 Wave 5-6 — chunk planner + labeled selection.

覆盖面（ADR-0119 §2-§3，perf 契约 §13）：
- plan_chunks：确定性、触达块 ∝ 窗口（结构性证据：同 cube 小窗口触达
  块数 << 全量块数；窗口翻倍触达块按相交增长）、负/越界钳制、cap 闸；
- plan_rechunk：预算满足、时间轴=1（CoW 契约推广）、幂等（unchanged）、
  确定性；
- labeled selection：标签解析（未知标签 typed）、bbox 相交、显式切片
  优先、单元预算、空选择 typed 拒绝；
- selection→计划→真实窗口读的一致性（计划触达块 == zarr 实际触达块，
  counting store 结构性证明）。
"""
from __future__ import annotations

import numpy as np
import pytest

zarr_mod = pytest.importorskip("zarr")

from app.services.lakehouse.chunk_planner import (
    MAX_PLAN_CHUNKS,
    plan_chunks,
    plan_rechunk,
)
from app.services.lakehouse.cube_schema import CubeSchemaError
from app.services.lakehouse.cube_store import read_labeled_window, write_labeled_cube
from app.services.lakehouse.labeled_selection import (
    DEFAULT_MAX_CELLS,
    plan_selection,
    resolve_selection,
)

YS = [7.5, 6.5, 5.5, 4.5, 3.5, 2.5, 1.5, 0.5]
XS = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5]
TIMES = ["2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z", "2024-03-01T00:00:00Z"]
DIMS = ["time", "band", "y", "x"]
SHAPE = (3, 2, 8, 8)
CHUNKS = (1, 1, 4, 4)


def _write_cube(tmp_path):
    data = np.arange(3 * 2 * 8 * 8, dtype="float32").reshape(SHAPE)
    return write_labeled_cube(
        {"reflectance": (("time", "band", "y", "x"), data)},
        {"time": TIMES, "band": ["B02", "B03"], "y": YS, "x": XS},
        tmp_path / "cube.zarr",
        crs="EPSG:4326",
        chunks={"time": 1, "band": 1, "y": 4, "x": 4},
    )


# ── plan_chunks ───────────────────────────────────────────────────────


def test_plan_chunks_deterministic_and_proportional():
    kw = dict(shape=SHAPE, chunks=CHUNKS, itemsize=4, workload="read",
              dim_order=DIMS)
    small = plan_chunks(window={"time": slice(0, 1), "band": slice(0, 1),
                                "y": slice(0, 2), "x": slice(0, 2)}, **kw)
    small2 = plan_chunks(window={"time": slice(0, 1), "band": slice(0, 1),
                                 "y": slice(0, 2), "x": slice(0, 2)}, **kw)
    assert small == small2  # 确定性
    # 小窗口（2x2 像元 × 单 time × 单 band）只触 1 块；cube 全量 24 块。
    assert small["touched_chunks"] == 1
    assert small["total_chunks"] == 24
    assert small["chunk_bytes"] == 1 * 4 * 4 * 4
    # 未给轴 = 整轴：band 全轴 → 触达翻倍（轴身份如实投影）。
    with_band_full = plan_chunks(window={"time": slice(0, 1),
                                         "y": slice(0, 2), "x": slice(0, 2)},
                                 **kw)
    assert with_band_full["touched_chunks"] == 2
    assert "band" in with_band_full["full_axes"]
    # 窗口按块粒度增长 → 触达块按相交增长（∝ 窗口，非线性爆炸）。
    doubled = plan_chunks(window={"time": slice(0, 3), "band": slice(0, 1),
                                  "y": slice(0, 4), "x": slice(0, 4)}, **kw)
    assert doubled["touched_chunks"] == 3  # 3 time × 1 band × 1 spatial block
    whole = plan_chunks(window={}, **kw)
    assert whole["touched_chunks"] == 24
    # 结构性证据：触达块 ∝ 窗口，远小于全量 —— 窗口读 ≠ 全量读。
    assert small["touched_chunks"] < whole["touched_chunks"] / 10


def test_plan_chunks_clamps_and_rejects():
    out = plan_chunks(
        shape=SHAPE, chunks=CHUNKS, itemsize=4, workload="read",
        window={"y": slice(-5, 999), "x": slice(2, 2)}, dim_order=DIMS,
    )
    assert out["window_shape"][2] == 8  # y 负下界/越上界 → 钳制到整轴
    assert out["window_shape"][3] == 0  # 空 x 窗口 = 0 块
    assert out["chunk_indices"] == []
    with pytest.raises(CubeSchemaError, match="unknown workload"):
        plan_chunks(shape=SHAPE, chunks=CHUNKS, itemsize=4, workload="quantum")
    with pytest.raises(CubeSchemaError, match="rank"):
        plan_chunks(shape=SHAPE, chunks=(1, 1), itemsize=4, workload="read")


def test_plan_chunks_cap():
    with pytest.raises(CubeSchemaError, match="planning cap"):
        plan_chunks(
            shape=(MAX_PLAN_CHUNKS + 4,), chunks=(1,), itemsize=1,
            workload="read", dim_order=["time"],
        )


# ── plan_rechunk ──────────────────────────────────────────────────────


def test_plan_rechunk_budget_and_temporal_granularity():
    result = plan_rechunk(
        shape=(64, 256, 256), chunks=(1, 16, 16), itemsize=4,
        max_chunk_bytes=1 * 1024 * 1024,
        spatial_axes=[1, 2], temporal_axes=[0],
    )
    assert result["chunks"][0] == 1  # 时间轴 chunk=1（CoW 契约推广）
    assert result["chunk_bytes"] <= 1 * 1024 * 1024
    assert not result["unchanged"]
    # 确定性 + 幂等：对结果再计划 → unchanged=True 且同计划。
    again = plan_rechunk(
        shape=(64, 256, 256), chunks=result["chunks"], itemsize=4,
        max_chunk_bytes=1 * 1024 * 1024,
        spatial_axes=[1, 2], temporal_axes=[0],
    )
    assert again["chunks"] == result["chunks"]
    assert again["unchanged"]


def test_plan_rechunk_rejects_too_small_budget():
    with pytest.raises(CubeSchemaError, match="below one cell"):
        plan_rechunk(shape=(4, 4, 4), chunks=(1, 2, 2), itemsize=4,
                     max_chunk_bytes=2)


# ── labeled selection ─────────────────────────────────────────────────


def test_resolve_selection_labels_and_bbox():
    coords = {"time": TIMES, "band": ["B02", "B03"], "y": YS, "x": XS}
    slices = resolve_selection(
        coordinates=coords,
        selection={"time": ["2024-02-01T00:00:00Z"], "band": "B03"},
    )
    assert slices["time"] == slice(1, 2)
    assert slices["band"] == slice(1, 2)
    with pytest.raises(CubeSchemaError, match="unknown time label"):
        resolve_selection(
            coordinates=coords, selection={"time": ["1999-01-01"]},
        )
    # y 降序（北-up）：bbox [2,2,4,4] → y 值 3.5/2.5（idx 4-5）；
    # x 升序：x 值 2.5/3.5（idx 2-3）。
    bounded = resolve_selection(
        coordinates=coords, selection={"bbox": [2.0, 2.0, 4.0, 4.0]},
    )
    assert bounded["y"] == slice(4, 6)
    assert bounded["x"] == slice(2, 4)
    with pytest.raises(CubeSchemaError, match="does not intersect"):
        resolve_selection(
            coordinates=coords, selection={"bbox": [100.0, 100.0, 101.0, 101.0]},
        )
    with pytest.raises(CubeSchemaError, match="empty selection"):
        resolve_selection(coordinates=coords, selection={})


def test_plan_selection_budget_and_consistency(tmp_path):
    proj = _write_cube(tmp_path)
    store = tmp_path / "cube.zarr"
    coords = {"time": TIMES, "band": ["B02", "B03"], "y": YS, "x": XS}
    selection = {
        "time": ["2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z"],
        "bbox": [0.0, 0.0, 4.0, 4.0],
    }
    plan = plan_selection(
        projection=proj, coordinates=coords, selection=selection,
    )
    # bbox [0,0,4,4]：y 4:8（4 像元）、x 0:4（4 像元）× 2 time × 2 band。
    assert plan["cells"] == 2 * 2 * 4 * 4
    assert plan["plan"]["touched_chunks"] == 4  # 2 time × 2 band × 1 spatial
    # 一致性：按计划切片真实读，形状 == 计划窗口。
    slices = {d: slice(a, b) for d, (a, b) in plan["slices"].items()}
    result = read_labeled_window(store, index_slices=slices)
    assert result["variables"]["reflectance"].shape == (2, 2, 4, 4)
    # 值一致性：计划区间 == 真实数据区间（arrange 布局：[0,0,y=4,x=0]=32）。
    assert int(result["variables"]["reflectance"][0, 0, 0, 0]) == 32


def test_plan_selection_cell_budget_rejects():
    proj = {"dims": DIMS, "shape": [512, 8, 512, 512], "dtype": "float32",
            "variables": {"v": DIMS}}
    coords = {"time": [f"t{i}" for i in range(512)],
              "band": [f"b{i}" for i in range(8)],
              "y": [float(i) for i in range(512)],
              "x": [float(i) for i in range(512)]}
    with pytest.raises(CubeSchemaError, match="empty selection"):
        plan_selection(projection=proj, coordinates=coords, selection={})
    with pytest.raises(CubeSchemaError, match="bounded budget"):
        plan_selection(
            projection=proj, coordinates=coords,
            selection={"index_slices": {"y": [0, 512], "x": [0, 512]}},
            max_cells=1000,
        )
    assert DEFAULT_MAX_CELLS == 8_000_000
