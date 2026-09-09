"""TilePlanner —— 推理专属分区权威（ADR-0119 §3.4；R1-C3/M7 修订）。

边界（架构挑战 R1-M7 冻结）：

- 本 planner 是**推理专属**分区（唯一允许 stride<chip 的迭代），与
  ``chunk.iter_chunk_descriptors`` / 经典 windowed 路径互不回馈；
- 纯像素空间：窗口 = (row, col, h, w) 整数框；georef 只经
  ``RasterReader.window_transform``（engine 层）；
- 确定性：row-major 序 + 纯整数算术；同 (shape, chip, stride) 必同计划；
- **metadata-only**：planner 不读像素（virtual huge raster 的 lazy 证明）；
- read window 永远 clamp 在栅格内（RasterReader 硬拒越界，R1-C3）；
  pad 由引擎在 numpy 层施加。

TileSpec 语义（R1-C3）::

    core_window   = 参与输出融合的窗口（四邻 chip 不重叠或按 stride 重叠）
    read_window   = clamp 后的读取窗口（core ∪ context，∩栅格范围）
    pad_l/t/r/b   = read 相对 core-context 请求框的缺失边（np.pad 用）
    fill_value    = pad 填充值（preprocess 参数，进 fingerprint）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Sequence, Tuple

from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import PlanningError

Window = Tuple[int, int, int, int]  # (row, col, height, width)


@dataclass(frozen=True)
class TileSpec:
    """一个推理 chip 的完整定位（core/read 分离）。"""

    index: int
    core_window: Window
    read_window: Window
    pad: Tuple[int, int, int, int]      # (left, top, right, bottom) — np.pad 语义
    chip_hw: Tuple[int, int]            # 读取后的 chip 形状（含 pad，=context 大小）

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "core": list(self.core_window),
            "read": list(self.read_window),
            "pad": list(self.pad),
            "chip_hw": list(self.chip_hw),
        }


@dataclass(frozen=True)
class TilePlan:
    """确定性 tile 计划（进 InferenceFingerprint）。"""

    raster_height: int
    raster_width: int
    chip_h: int
    chip_w: int
    stride_y: int
    stride_x: int
    context_h: int
    context_w: int
    tiles: Tuple[TileSpec, ...]

    def as_dict(self) -> dict:
        return {
            "raster": [self.raster_height, self.raster_width],
            "chip": [self.chip_h, self.chip_w],
            "stride": [self.stride_y, self.stride_x],
            "context": [self.context_h, self.context_w],
            "tile_count": len(self.tiles),
            # tile 明细只在诊断需要时展开（manifest 默认带计划摘要 + 计划
            # 确定性由参数保证）。
        }

    def fingerprint_payload(self) -> dict:
        return {
            "raster": [self.raster_height, self.raster_width],
            "chip": [self.chip_h, self.chip_w],
            "stride": [self.stride_y, self.stride_x],
            "context": [self.context_h, self.context_w],
            "tile_count": len(self.tiles),
            "core_origins": [[t.core_window[0], t.core_window[1]] for t in self.tiles],
        }

    def __len__(self) -> int:
        return len(self.tiles)


def plan_tiles(
    descriptor: GeoModelDescriptor,
    *,
    raster_height: int,
    raster_width: int,
) -> TilePlan:
    """从 descriptor 空间参数构造确定性 tile 计划（纯函数）。

    chip = core 输出块；context ≥ chip，读 context（halo 语义，与
    raster_windowed 对齐）；stride 缺省 = chip（无缝）；overlap 由
    stride<chip 表达。
    """
    chip_w, chip_h = descriptor.spatial.chip_size
    ctx_w, ctx_h = descriptor.spatial.context_size
    if raster_height < 1 or raster_width < 1:
        raise PlanningError(f"raster too small: {raster_width}x{raster_height}")
    stride_y, stride_x = descriptor.spatial.stride or (chip_h, chip_w)
    if stride_y < 1 or stride_x < 1:
        raise PlanningError(f"stride must be positive (got {stride_y}x{stride_x})")
    if stride_y > chip_h or stride_x > chip_w:
        raise PlanningError(
            f"stride {stride_y}x{stride_x} exceeds chip {chip_h}x{chip_w} (gap tiles forbidden)"
        )
    if ctx_h < chip_h or ctx_w < chip_w:
        raise PlanningError("context_size must be >= chip_size")

    tiles: List[TileSpec] = []
    index = 0
    # core 网格：行优先；最后一列/行对齐右/下边界（确定性，等分摊余数）。
    core_cols = _grid_axis(raster_width, chip_w, stride_x)
    core_rows = _grid_axis(raster_height, chip_h, stride_y)
    half_ctx_y = (ctx_h - chip_h) // 2
    half_ctx_x = (ctx_w - chip_w) // 2
    for row in core_rows:
        for col in core_cols:
            core_h = min(chip_h, raster_height - row)
            core_w = min(chip_w, raster_width - col)
            core: Window = (row, col, core_h, core_w)
            # 请求读框（context 扩展），随后 clamp。
            req_row = max(0, row - half_ctx_y)
            req_col = max(0, col - half_ctx_x)
            req_h = min(ctx_h, raster_height - req_row)
            req_w = min(ctx_w, raster_width - req_col)
            read: Window = (req_row, req_col, req_h, req_w)
            # pad = 请求 context 相对 read 的缺失（上下边）。
            pad_top = row - half_ctx_y - req_row
            pad_left = col - half_ctx_x - req_col
            chip_total_h = pad_top + read[2]
            chip_total_w = pad_left + read[3]
            pad_bottom = max(0, ctx_h - chip_total_h)
            pad_right = max(0, ctx_w - chip_total_w)
            tiles.append(
                TileSpec(
                    index=index,
                    core_window=core,
                    read_window=read,
                    pad=(max(0, pad_left), max(0, pad_top), pad_right, pad_bottom),
                    chip_hw=(pad_top + read[2] + pad_bottom,
                             pad_left + read[3] + pad_right),
                )
            )
            index += 1
    return TilePlan(
        raster_height=raster_height,
        raster_width=raster_width,
        chip_h=chip_h,
        chip_w=chip_w,
        stride_y=stride_y,
        stride_x=stride_x,
        context_h=ctx_h,
        context_w=ctx_w,
        tiles=tuple(tiles),
    )


def _grid_axis(total: int, size: int, stride: int) -> List[int]:
    """1D core 起点（行优先确定性；末端对齐边界，不产生 0 宽 tile）。"""
    if total <= size:
        return [0]
    starts = list(range(0, total - size + 1, stride))
    last = starts[-1] if starts else 0
    if last + size < total:
        starts.append(total - size)
    elif last + size > total and (not starts or starts[-1] != total - size):
        starts[-1] = total - size
    return starts


def core_coverage(plan: TilePlan) -> int:
    """core 像素覆盖计数（planner 自检：stride<chip 时允许重叠）。"""
    return sum(t.core_window[2] * t.core_window[3] for t in plan.tiles)


def tile_iterator(plan: TilePlan) -> Iterator[TileSpec]:
    yield from plan.tiles
