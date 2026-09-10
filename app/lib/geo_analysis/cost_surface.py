"""Least-cost surface science（Goal 07 Phase E —— terrain 域 cost 映射实现层）。

累积成本面（cost distance）与最小成本路径（least cost path）：
8 邻接 Dijkstra（heapq，O(N log N)）+ 累积面回溯排水。

职责边界（CONTRACT_BACKBONE §1，与 terrain.py 同门）：纯 NumPy/标量数学
——不读文件、不写 artifact、不挂证据块（工具层职责）。

- 接受 2D ``cost`` 摩擦面（正值）+ 源像元 + 像元尺寸 + 可选 ``nodata``；
- nodata 感知：``cost == nodata`` 与 NaN/±Inf 视为**不可通行**像元
  （非零通行成本）——与 terrain 域 nodata 口径一致；
- 返回 (数组或路径, meta dict)，meta 为确定性纯文本/数值事实（无时间戳）。

语义约定（GRASS r.cost / ESRI cost distance 同族）：

- 移动通过像元 i→j 的边成本 = (cost_i + cost_j)/2 × dist(i,j)
  （平均摩擦 × 米距离；对角边 dist = √2·像元尺寸）；
- 源像元累积成本 = 0；不可达像元 = NaN（显式披露 unreachable 计数）；
- least_cost_path = 从目标沿累积面**严格下降**回溯到任一源
  （并列按行主序确定性裁决；平局/停滞 = 目标不可达，类型化报错）。
"""
from __future__ import annotations

import heapq
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.lib.cancellation import checkpoint
from app.lib.gis.scientific_errors import (
    DegenerateData,
    NoValidObservations,
    ResourceScaleMismatch,
)

__all__ = [
    "COST_EDGE_POLICY",
    "cost_distance",
    "least_cost_path",
]

#: 边界/无效像元策略（与 terrain.EDGE_POLICY 同族的成本面版本）。
COST_EDGE_POLICY = "invalid cells are impassable; unreachable cells are NaN"

#: Dijkstra 网格像元硬顶（先拒绝后分配；与 terrain 域护栏同级）。
_MAX_CELLS = 50_000_000

_D8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1),
       (0, 1), (1, -1), (1, 0), (1, 1))


def _validate_inputs(
    cost: np.ndarray,
    cell_size: float,
    cell_size_x: Optional[float],
) -> Tuple[np.ndarray, float, float]:
    c_raw = np.asarray(cost)
    if getattr(c_raw, "ndim", 0) != 2:
        raise NoValidObservations(
            f"cost surface must be a 2D array (got ndim {getattr(c_raw, 'ndim', 0)})")
    if c_raw.size == 0:
        raise NoValidObservations("cost surface is empty")
    if c_raw.size > _MAX_CELLS:
        raise ResourceScaleMismatch(
            f"cost surface has {c_raw.size:,} cells (limit {_MAX_CELLS:,}); "
            "split the extent or coarsen the grid before running cost distance")
    if not np.isfinite(cell_size) or cell_size <= 0:
        raise ValueError(f"cell_size must be a positive finite number (got {cell_size!r})")
    cs_x = float(cell_size_x) if cell_size_x is not None else float(cell_size)
    if not np.isfinite(cs_x) or cs_x <= 0:
        raise ValueError(f"cell_size_x must be a positive finite number (got {cs_x!r})")
    return c_raw, float(cell_size), cs_x


def _prepare_cost(
    cost: np.ndarray,
    nodata: Optional[float],
) -> Tuple[np.ndarray, np.ndarray]:
    """有效像元的正摩擦值；无效（nodata/非有限）→ NaN 并返回 valid 掩膜。"""
    c = cost.astype(np.float64, copy=True)
    invalid = ~np.isfinite(c)
    if nodata is not None:
        invalid |= (c == float(nodata))
    c[invalid] = np.nan
    valid = ~invalid
    if not valid.any():
        raise NoValidObservations(
            "cost surface has no valid (finite, non-nodata) cells")
    finite_valid = c[valid]
    if float(finite_valid.min()) <= 0:
        raise DegenerateData(
            "cost surface must be strictly positive on valid cells "
            "(zero/negative friction is not a cost); rescale or mask those cells",
            correction_hint="use a positive friction surface, or set cost<=0 cells to nodata",
        )
    return c, valid


def _normalize_sources(
    sources: Any,
    shape: Tuple[int, int],
    valid: np.ndarray,
) -> np.ndarray:
    """bool 掩膜或 (row, col) 序列 → 行主序源像元索引数组（确定性升序）。"""
    h, w = shape
    if isinstance(sources, np.ndarray) and sources.dtype == bool:
        if sources.shape != shape:
            raise ValueError(
                f"sources mask shape {sources.shape} != cost shape {shape}")
        mask = sources & valid
    else:
        mask = np.zeros(shape, dtype=bool)
        try:
            pairs = list(sources)
        except TypeError as exc:
            raise ValueError(
                "sources must be a boolean mask or an iterable of (row, col) pairs"
            ) from exc
        if not pairs:
            raise NoValidObservations("no source cells given")
        for item in pairs:
            try:
                r, c = item
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"source cell must be a (row, col) pair, got {item!r}") from exc
            r_i, c_i = int(r), int(c)
            if not (0 <= r_i < h and 0 <= c_i < w):
                raise ValueError(
                    f"source cell ({r_i}, {c_i}) outside grid {shape}")
            mask[r_i, c_i] = True
        mask &= valid
    if not mask.any():
        raise NoValidObservations(
            "no valid source cells (all sources are on invalid/impassable cells)")
    return np.flatnonzero(mask.ravel())


def cost_distance(
    cost: np.ndarray,
    sources: Any,
    cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    nodata: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """8 邻接 Dijkstra 累积成本面（GRASS r.cost 同族语义）。

    边成本 = (cost_i + cost_j)/2 × dist(i,j)（米；对角 ×√2）。源累积=0；
    不可通行（nodata/非有限）像元不可穿越，不可达像元为 NaN。

    meta（确定性）：algorithm / connectivity / n_source_cells / reachable /
    unreachable / max_cost / mean_cost_reachable / cell_size(_x) /
    edge_policy。护栏：网格 ≤ 50M 像元；弹出循环每 65536 次检查取消点。
    """
    c_raw, cy, cx = _validate_inputs(cost, cell_size, cell_size_x)
    c, valid = _prepare_cost(c_raw, nodata)
    h, w = c.shape
    src_flat = _normalize_sources(sources, (h, w), valid)
    del c_raw

    cost_flat = c.ravel()
    accum = np.full(h * w, np.inf, dtype=np.float64)
    visited = np.zeros(h * w, dtype=bool)
    heap: List[Tuple[float, int, int]] = []
    counter = 0
    for s in src_flat.tolist():
        accum[s] = 0.0
        heapq.heappush(heap, (0.0, counter, s))
        counter += 1

    diag = math.hypot(cy, cx)
    pop_count = 0
    while heap:
        d, _, cur = heapq.heappop(heap)
        if visited[cur]:
            continue
        visited[cur] = True
        r, col = divmod(cur, w)
        pop_count += 1
        if pop_count % 65536 == 0:
            checkpoint()
        for dr, dc in _D8:
            nr, nc = r + dr, col + dc
            if nr < 0 or nr >= h or nc < 0 or nc >= w:
                continue
            nxt = nr * w + nc
            if visited[nxt]:
                continue
            c_next = cost_flat[nxt]
            if not np.isfinite(c_next):
                continue  # 不可通行像元
            step = diag if (dr and dc) else (cy if dr else cx)
            nd = d + (cost_flat[cur] + c_next) * 0.5 * step
            if nd < accum[nxt]:
                accum[nxt] = nd
                heapq.heappush(heap, (nd, counter, nxt))
                counter += 1

    surface = accum.reshape(h, w)
    reachable_flat = visited & np.isfinite(accum)
    surface[~np.isfinite(surface)] = np.nan
    meta: Dict[str, Any] = {
        "algorithm": "dijkstra_8n_mean_friction",
        "connectivity": 8,
        "cell_size": cy,
        "cell_size_x": cx,
        "n_source_cells": int(len(src_flat)),
        "reachable": int(reachable_flat.sum()),
        "unreachable": int((valid.ravel() & ~reachable_flat).sum()),
        "invalid_cells": int((~valid.ravel()).sum()),
        "max_cost": (
            round(float(surface[reachable_flat.reshape(h, w)].max()), 6)
            if reachable_flat.any() else None),
        "mean_cost_reachable": (
            round(float(surface[reachable_flat.reshape(h, w)].mean()), 6)
            if reachable_flat.any() else None),
        "edge_policy": COST_EDGE_POLICY,
    }
    return surface, meta


def least_cost_path(
    accumulated: np.ndarray,
    target_row: int,
    target_col: int,
    *,
    cost_tolerance: float = 0.0,
) -> Tuple[List[Tuple[int, int]], Dict[str, Any]]:
    """累积成本面回溯排水（GRASS r.drain 同族语义）。

    从目标像元出发，反复走向 8 邻域中累积成本最小且**严格低于**当前值的
    邻居（并列按行主序确定性裁决），到达累积成本为 0 的源像元为止。
    路径是全局最优成本面的下降轨迹 —— 与 cost_distance 的 Dijkstra 面配对
    时即为最小成本路径。

    ``cost_tolerance > 0`` 允许非严格下降容差（浮点噪声场景）；默认 0。
    返回 (路径 [(row, col), ...] 含目标与源端点, meta)。目标不可达 / 不在
    累积面上 → 类型化报错。
    """
    a = np.asarray(accumulated, dtype=np.float64)
    if getattr(a, "ndim", 0) != 2:
        raise NoValidObservations(
            f"accumulated surface must be 2D (got ndim {getattr(a, 'ndim', 0)})")
    h, w = a.shape
    if not (0 <= int(target_row) < h and 0 <= int(target_col) < w):
        raise ValueError(
            f"target ({target_row}, {target_col}) outside grid {(h, w)}")
    r_i, c_i = int(target_row), int(target_col)
    cur_val = a[r_i, c_i]
    if not np.isfinite(cur_val):
        raise NoValidObservations(
            f"target cell ({r_i}, {c_i}) is unreachable (accumulated cost is NaN)")

    path: List[Tuple[int, int]] = [(r_i, c_i)]
    steps = 0
    max_steps = h * w  # 任一像元至多经过一次 —— 超界即停滞
    while cur_val > 0.0:
        steps += 1
        if steps > max_steps:
            raise NoValidObservations(
                f"least-cost path stalled at ({r_i}, {c_i}) after "
                f"{max_steps} steps; accumulated surface is not a "
                "cost_distance product or has a local minimum")
        best: Optional[Tuple[float, int, int]] = None
        for dr, dc in _D8:
            nr, nc = r_i + dr, c_i + dc
            if nr < 0 or nr >= h or nc < 0 or nc >= w:
                continue
            v = a[nr, nc]
            if not np.isfinite(v):
                continue
            if v < cur_val - cost_tolerance and (
                    best is None or v < best[0]):
                best = (float(v), nr, nc)
        if best is None:
            raise NoValidObservations(
                f"least-cost path stuck at ({r_i}, {c_i}): no strictly "
                "lower neighbour and no source reached — target may lie on "
                "a plateau produced by a non-Dijkstra surface")
        cur_val, r_i, c_i = best
        path.append((r_i, c_i))

    total = float(a[path[0][0], path[0][1]])
    meta: Dict[str, Any] = {
        "algorithm": "steepest_descent_drain",
        "n_cells": len(path),
        "target": [int(target_row), int(target_col)],
        "source": [path[-1][0], path[-1][1]],
        "total_cost": round(total, 6),
        "deterministic_tie_break": "row_major",
    }
    return path, meta
