"""Chunk planner — Spatial Lakehouse V7 (ADR-0119, Scope C).

读/写工作负载的**确定性计划器**（纯函数：同输入恒同计划 —— 可缓存、
可审计、可进 manifest 证据）。单一事实源边界：窗口分区权威仍是
``iter_bounded_windows``（V4 红线不动）；本模块工作在 labeled cube 的
**zarr chunk 坐标空间**（store 元数据 → 触达块集合/字节/IO 次数投影），
不复制窗口运行时。

契约：

- **确定性**：无 wall-clock/随机；块枚举顺序固定（行主序 C 序）；
- **有界**：计划块数超 ``MAX_PLAN_CHUNKS`` typed 拒绝（防 OOM 计划
  本身）；单块字节超 ``max_chunk_bytes`` 的 rechunk 计划是合法输出
  （写入器职责是遵守预算，读计划如实报告触达字节）；
- **成本模型**：``chunk_bytes = Π(chunk_i) × itemsize``、IO 次数 =
  触达块数（zarr 语义：每块一次 get/put）—— 结构性证据供 perf 断言；
- **rechunk 策略**：以 ``max_chunk_bytes`` + 空间/时间配比偏好合成新
  chunk 元组（纯元数据；数据搬运是调用方职责 —— 与"绝不静默重采样"
  同族：rechunk 改变布局，绝不改变坐标/值）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.services.lakehouse.cube_schema import CubeSchemaError

#: 计划块数硬上界（计划本身有界 —— 拒绝即诚实）。
MAX_PLAN_CHUNKS = 50_000

#: 读/写负载类型。
WORKLOADS = ("read", "write", "compute")


def _validate_chunks(shape: Sequence[int], chunks: Sequence[int]) -> Tuple[int, ...]:
    cs = tuple(int(v) for v in chunks)
    if len(cs) != len(shape):
        raise CubeSchemaError(
            f"chunks rank {len(cs)} != shape rank {len(shape)}"
        )
    for dim_size, c in zip(shape, cs):
        if c < 1:
            raise CubeSchemaError(f"chunk size must be >= 1, got {c}")
        if dim_size > 0 and c > max(dim_size, 1):
            # 超轴长 chunk 允许（zarr 语义：尾部块截断），但计划按轴长取 min。
            continue
    return cs


def chunk_grid(shape: Sequence[int], chunks: Sequence[int]) -> Tuple[int, ...]:
    """各轴块数（ceil division）。"""
    return tuple(
        max(1, (int(s) + int(c) - 1) // int(c)) if s > 0 else 1
        for s, c in zip(shape, chunks)
    )


def plan_chunks(
    *,
    shape: Sequence[int],
    chunks: Sequence[int],
    itemsize: int,
    workload: str,
    window: Optional[Mapping[str, slice]] = None,
    dim_order: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """读/写窗口 → 触达块计划（确定性；触达块 ∝ 窗口而非 cube —— 结构性
    性能契约的执行单元）。

    ``window`` 按 ``dim_order``（缺省全轴）给索引切片；未给的轴 = 整轴。
    返回 ``{"chunk_indices": [(i…)…], "touched_chunks", "chunk_bytes",
    "touched_bytes", "io_calls", "window_shape", "full_axes": [...]}``。
    """
    if workload not in WORKLOADS:
        raise CubeSchemaError(
            f"unknown workload {workload!r} — expected one of {WORKLOADS}"
        )
    shape_t = tuple(int(v) for v in shape)
    cs = _validate_chunks(shape_t, chunks)
    grid = chunk_grid(shape_t, cs)
    total_blocks = 1
    for g in grid:
        total_blocks *= g
    if total_blocks > MAX_PLAN_CHUNKS:
        raise CubeSchemaError(
            f"cube has {total_blocks} chunks, exceeding the planning cap "
            f"{MAX_PLAN_CHUNKS} — narrow the workload"
        )
    ndim = len(shape_t)
    order = [str(d) for d in (dim_order or [f"dim{i}" for i in range(ndim)])]
    if len(order) != ndim:
        raise CubeSchemaError("dim_order rank != shape rank")

    # 窗口 → 各轴块索引区间（相交块；负/越界切片在 selection 层已钳制，
    # 这里再钳一次 —— 计划是防御性纯函数）。
    block_spans: List[range] = []
    window_shape: List[int] = []
    full_axes: List[str] = []
    for i in range(ndim):
        sl = (window or {}).get(order[i], slice(None)) if window else slice(None)
        start = 0 if sl.start is None else max(0, min(int(sl.start), shape_t[i]))
        stop = shape_t[i] if sl.stop is None else max(start, min(int(sl.stop), shape_t[i]))
        window_shape.append(stop - start)
        if start == 0 and stop == shape_t[i]:
            full_axes.append(order[i])
        block_spans.append(
            range(start // cs[i], max(start // cs[i], (stop - 1) // cs[i] + 1))
            if stop > start
            else range(0, 0)
        )

    # 行主序枚举相交块（确定性顺序）。
    indices: List[Tuple[int, ...]] = []
    if all(len(s) > 0 for s in block_spans):
        def _emit(prefix: List[int], depth: int) -> None:
            if depth == ndim:
                indices.append(tuple(prefix))
                return
            for b in block_spans[depth]:
                prefix.append(b)
                _emit(prefix, depth + 1)
                prefix.pop()

        _emit([], 0)
    if len(indices) > MAX_PLAN_CHUNKS:
        raise CubeSchemaError(
            f"window touches {len(indices)} chunks, exceeding the planning "
            f"cap {MAX_PLAN_CHUNKS}"
        )
    chunk_bytes = 1
    for c in cs:
        chunk_bytes *= c
    chunk_bytes *= max(1, int(itemsize))
    touched = len(indices)
    touched_bytes = touched * chunk_bytes
    return {
        "chunk_indices": [list(i) for i in indices],
        "touched_chunks": touched,
        "total_chunks": total_blocks,
        "chunk_bytes": chunk_bytes,
        "touched_bytes": touched_bytes,
        "io_calls": touched,  # zarr 语义：每块一次 IO
        "window_shape": window_shape,
        "full_axes": full_axes,
        "workload": workload,
    }


def plan_rechunk(
    *,
    shape: Sequence[int],
    chunks: Sequence[int],
    itemsize: int,
    max_chunk_bytes: int,
    spatial_axes: Optional[Sequence[int]] = None,
    temporal_axes: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """rechunk 计划（纯元数据）：新 chunk 元组满足

    - 每块字节 ≤ ``max_chunk_bytes``（预算优先）；
    - 时间轴 chunk = 1（时序 cube 的窗口读/修订粒度优先 —— V6 CoW 契约
      ``chunks[0]==1`` 的推广）；
    - 剩余预算按 ``spatial_axes`` 优先铺满空间轴（窗口读局部性），
      再均摊到其它轴。

    返回 ``{"chunks": [...], "chunk_bytes", "grid": [...], "blocks"}``；
    布局不变时返回原 chunks（幂等 —— 重复计划零成本）。
    """
    shape_t = tuple(int(v) for v in shape)
    cs = _validate_chunks(shape_t, chunks)
    if max_chunk_bytes < max(1, int(itemsize)):
        raise CubeSchemaError(
            f"max_chunk_bytes {max_chunk_bytes} below one cell "
            f"({itemsize} bytes)"
        )
    ndim = len(shape_t)
    spatial = set(int(i) for i in (spatial_axes if spatial_axes is not None else [ndim - 2, ndim - 1]))
    temporal = set(int(i) for i in (temporal_axes if temporal_axes is not None else []))
    spatial = {i for i in spatial if i < ndim}
    temporal = {i for i in temporal if i < ndim} - spatial

    new = [1] * ndim
    for i in temporal:
        new[i] = 1

    def _bytes_of(sizes: Sequence[int]) -> int:
        b = max(1, int(itemsize))
        for v in sizes:
            b *= max(1, int(v))
        return b

    # 空间轴：成倍增长直到下一倍会超预算。
    for i in sorted(spatial):
        target = 1
        while (
            target < shape_t[i]
            and _bytes_of([target * 2 if j == i else new[j] for j in range(ndim)])
            <= max_chunk_bytes
        ):
            target *= 2
        new[i] = max(1, min(target, max(shape_t[i], 1)))
    # 其它（非时间/非空间）轴：预算剩余内均分增长。
    other = [i for i in range(ndim) if i not in spatial and i not in temporal]
    for i in other:
        target = 1
        while (
            target < shape_t[i]
            and _bytes_of([target * 2 if j == i else new[j] for j in range(ndim)])
            <= max_chunk_bytes
        ):
            target *= 2
        new[i] = max(1, min(target, max(shape_t[i], 1)))
    grid = chunk_grid(shape_t, new)
    blocks = 1
    for g in grid:
        blocks *= g
    if blocks > MAX_PLAN_CHUNKS:
        raise CubeSchemaError(
            f"rechunk plan yields {blocks} blocks, exceeding the cap "
            f"{MAX_PLAN_CHUNKS} — raise max_chunk_bytes"
        )
    return {
        "chunks": new,
        "chunk_bytes": _bytes_of(new),
        "grid": list(grid),
        "blocks": blocks,
        "unchanged": list(new) == list(cs),
    }
