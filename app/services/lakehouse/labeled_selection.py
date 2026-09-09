"""Labeled selection — Spatial Lakehouse V7 (ADR-0119, Scope A/B).

标签级选择 → **有界索引计划**：时间/带/极化/垂直标签与空间窗口
（CRS 坐标 bbox）→ 各轴索引切片 + chunk 触达计划。本模块是标签语义
与 zarr chunk 粒度之间的唯一翻译层（翻译规则确定、可审计）。

契约：

- 标签解析：未知标签 typed 拒绝（绝不静默丢弃/钳制到最近邻）；
- bbox：CRS 坐标闭区间 → 相交 y/x 索引区间（网格中心坐标；半开
  索引区间 = [start, stop)；bbox 与网格 **部分相交** 也入选 ——
  与"窗口读 = 相交块读"一致）；
- 有界：解析后的单元预算（cells）与块预算（plan_chunks cap）双重
  闸；空结果（零选择/零相交）typed 拒绝 —— 调用方显式处理空集；
- **绝不重采样**：bbox 不落格点边界时选择的是**相交像元**（不插值、
  不对齐到块边界之外 —— 块边界只影响 IO，不影响返回值形状）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from app.services.lakehouse.cube_schema import CubeSchemaError

#: 单次选择的单元（cells = 所选变量单元总数）默认预算（与 V6 窗口读
#: 8M cells 同量级 —— 有界读契约不变）。
DEFAULT_MAX_CELLS = 8_000_000

_LABEL_DIMS = ("time", "band", "polarization", "vertical")


def _axis_index(values: np.ndarray, dim: str) -> Dict[str, int]:
    return {str(v): i for i, v in enumerate(values.tolist())}


def _resolve_labels(
    coord: np.ndarray,
    dim: str,
    wanted: Optional[Sequence[Any]],
) -> slice:
    """标签列表/切片 → 索引切片（连续区间；非连续 = 显式多段是调用方
    拆多次选择 —— 本层保持单一连续切片，zarr 窗口读的最优形态）。"""
    if wanted is None:
        return slice(None)
    index = _axis_index(coord, dim)
    if isinstance(wanted, (str, bytes)):
        wanted = [wanted]
    positions: List[int] = []
    for w in wanted:
        key = str(w)
        if key not in index:
            raise CubeSchemaError(
                f"unknown {dim} label {key[:64]!r} — labels must match the "
                "coordinate axis exactly (no nearest-neighbour guessing)"
            )
        positions.append(index[key])
    lo, hi = min(positions), max(positions)
    return slice(lo, hi + 1)


def _resolve_bbox(
    y: np.ndarray, x: np.ndarray, bbox: Sequence[float]
) -> Tuple[slice, slice]:
    """bbox [minx, miny, maxx, maxy] → (y_slice, x_slice)（相交像元）。"""
    if len(bbox) != 4:
        raise CubeSchemaError("bbox must be [minx, miny, maxx, maxy]")
    minx, miny, maxx, maxy = (float(v) for v in bbox)
    if not minx < maxx or not miny < maxy:
        raise CubeSchemaError(
            f"degenerate bbox {bbox!r} — min < max required per axis"
        )
    ys = y.tolist()
    xs = x.tolist()
    y_pos = [i for i, v in enumerate(ys) if miny <= float(v) <= maxy]
    x_pos = [i for i, v in enumerate(xs) if minx <= float(v) <= maxx]
    if not y_pos or not x_pos:
        raise CubeSchemaError(
            f"bbox {bbox!r} does not intersect the cube grid — "
            "empty selections are typed errors (caller decides what empty means)"
        )
    return slice(min(y_pos), max(y_pos) + 1), slice(min(x_pos), max(x_pos) + 1)


def resolve_selection(
    *,
    coordinates: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> Dict[str, slice]:
    """标签/窗口选择 → 索引切片（纯函数；typed 拒绝一切歧义）。

    ``selection`` 键：``time``/``band``/``polarization``/``vertical``
    （标签列表或单标签）、``bbox``（[minx,miny,maxx,maxy]）、
    ``index_slices``（显式索引切片，优先级最高 —— REST 数字切片直通）。
    """
    slices: Dict[str, slice] = {}
    explicit = selection.get("index_slices") or {}
    for dim, sl in explicit.items():
        if isinstance(sl, (list, tuple)) and len(sl) == 2:
            start, stop = (int(v) for v in sl)
            if start < 0 or stop < 0 or stop < start:
                raise CubeSchemaError(
                    f"index slice for {dim!r} must be 0 <= start <= stop"
                )
            slices[str(dim)] = slice(start, stop)
        elif isinstance(sl, slice):
            slices[str(dim)] = sl
        else:
            raise CubeSchemaError(
                f"index_slices[{dim!r}] must be [start, stop]"
            )
    for dim in _LABEL_DIMS:
        if dim in slices:
            continue  # 显式索引切片优先
        wanted = selection.get(dim)
        if wanted is None:
            continue
        if dim not in coordinates:
            raise CubeSchemaError(
                f"selection on dim {dim!r} but the cube has no such axis"
            )
        slices[dim] = _resolve_labels(
            np.asarray(coordinates[dim]), dim, wanted
        )
    bbox = selection.get("bbox")
    if bbox is not None and "y" not in slices and "x" not in slices:
        if "y" not in coordinates or "x" not in coordinates:
            raise CubeSchemaError("bbox selection requires y/x coordinate axes")
        y_sl, x_sl = _resolve_bbox(
            np.asarray(coordinates["y"], dtype="float64"),
            np.asarray(coordinates["x"], dtype="float64"),
            bbox,
        )
        slices["y"], slices["x"] = y_sl, x_sl
    if not slices:
        raise CubeSchemaError(
            "empty selection — at least one label/bbox/index slice is "
            "required (whole-cube reads are refused)"
        )
    return slices


def plan_selection(
    *,
    projection: Mapping[str, Any],
    coordinates: Mapping[str, Any],
    selection: Mapping[str, Any],
    itemsize: Optional[int] = None,
    max_cells: int = DEFAULT_MAX_CELLS,
) -> Dict[str, Any]:
    """选择 → 有界执行计划：索引切片 + chunk 触达 + 单元预算。

    ``projection`` 是 labeled schema 投影（dims/shape/variables/chunks
    —— store readback 或写入器输出同形）。返回计划（``slices``、
    ``touched_chunks``、``cells``、``plan``（chunk planner 输出））。
    """
    dims = [str(d) for d in (projection.get("dims") or [])]
    shape = [int(v) for v in (projection.get("shape") or [])]
    size_of = dict(zip(dims, shape))
    slices = resolve_selection(coordinates=coordinates, selection=selection)
    for dim, sl in slices.items():
        if dim not in size_of:
            raise CubeSchemaError(
                f"selection dim {dim!r} not in cube dims {dims}"
            )
        start = 0 if sl.start is None else max(0, min(int(sl.start), size_of[dim]))
        stop = size_of[dim] if sl.stop is None else max(start, min(int(sl.stop), size_of[dim]))
        slices[dim] = slice(start, stop)

    # 单元预算：所选变量单元总数（每变量 = 其维度切片长度的积）。
    # 变量值兼容 list（dims）与 {"dims": ..., "dtype": ...} 两种投影形态。
    variables = projection.get("variables") or {}
    cells = 0
    per_var: Dict[str, int] = {}
    for name, var_spec in variables.items():
        vd = (
            var_spec.get("dims") if isinstance(var_spec, Mapping) else var_spec
        ) or []
        n = 1
        for d in vd:
            sl = slices.get(d, slice(None))
            start = 0 if sl.start is None else int(sl.start)
            stop = size_of.get(d, 0) if sl.stop is None else int(sl.stop)
            n *= max(0, stop - start)
        per_var[str(name)] = n
        cells += n
    if cells > max_cells:
        raise CubeSchemaError(
            f"selection requests {cells} cells, exceeding the bounded "
            f"budget {max_cells} — narrow the selection"
        )
    if cells == 0:
        raise CubeSchemaError("selection resolves to zero cells")

    if itemsize is None:
        itemsize = np.dtype(str(projection.get("dtype") or "float32")).itemsize
    from app.services.lakehouse.chunk_planner import plan_chunks

    chunks = projection.get("chunks")
    if chunks is None:
        plan = {
            "chunk_indices": [], "touched_chunks": -1, "total_chunks": -1,
            "chunk_bytes": -1, "touched_bytes": -1, "io_calls": -1,
            "window_shape": [], "full_axes": [], "workload": "read",
        }
    else:
        if isinstance(chunks, Mapping):
            chunk_list = [int(chunks[d]) for d in dims]
        else:
            chunk_list = [int(v) for v in chunks]
        plan = plan_chunks(
            shape=shape,
            chunks=chunk_list,
            itemsize=int(itemsize),
            workload="read",
            window={d: slices[d] for d in dims if d in slices},
            dim_order=dims,
        )
    return {
        "slices": {d: [s.start, s.stop] for d, s in slices.items()},
        "cells": cells,
        "cells_per_variable": per_var,
        "plan": plan,
    }
