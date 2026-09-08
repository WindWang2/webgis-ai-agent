"""流式向量执行缝隙（ADR-0101 D8，V4 §17）。

批式（chunk-oriented）处理原语：scan / 属性过滤 / bbox 过滤 / 投影 /
轻量变换 / 聚合 / 物化。设计目标：**绝不必要地整数据集物化** ——
上游以迭代器进、下游以迭代器出，批大小受 governor 压力调节。

- 谓词过滤复用 data_fabric 的类型化谓 AST（SQL 三值逻辑对齐）——
  本模块绝不自造第二套语义；
- 聚合使用统一标量累加器（``query.accumulators``，O(组数) 峰值内存 ——
  与 compute_aggregates / Arrow lane 共用同一语义真相）；
- ``batch_size_for`` 按 ResourceGovernor 用量余弦折半（有下界），
  无 governor 时用常数缺省 —— 压力感知但不虚构精度；
- 协作点：每个批次边界调用可选 ``on_batch``（取消/deadline checkpoint
  由调用方挂接 —— 本模块与具体取消原语解耦）。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from app.services.data_fabric.query.accumulators import AggregateDriver

#: 缺省与下界批大小（压力感知折半的下限）。
DEFAULT_BATCH_SIZE = 1024
MIN_BATCH_SIZE = 64


def batch_checkpoint_hook(token: Any) -> Callable[[int], None]:
    """取消 token → ``on_batch`` 批边界钩子。

    ``token`` 为 ``app.lib.cancellation.CancellationToken``（鸭子类型：仅需
    ``raise_if_cancelled()``）。每个批次边界调用一次；已取消 → 抛
    ``OperationCancelled`` 中止流（不产出部分批次）。本模块保持与具体取消
    原语解耦 —— 只约定这一方法。
    """

    def _on_batch(_count: int) -> None:
        token.raise_if_cancelled()

    return _on_batch


def batch_size_for(governor: Optional[Any] = None,
                   governor_path: Optional[str] = None) -> int:
    """压力感知批大小：bytes 用量逼近限额 → 批大小折半（下界 64）。

    限额读取走公开的 ``governor.limits_for(path)``（Wave 5）；
    仅有私有 ``_find`` 的旧形状（测试 fake / 旧 governor）向后兼容回退。
    """
    if governor is None or governor_path is None:
        return DEFAULT_BATCH_SIZE
    try:
        usage = governor.usage_full(governor_path)
        limits = None
        limits_for = getattr(governor, "limits_for", None)
        if callable(limits_for):
            limits = limits_for(governor_path)
        elif hasattr(governor, "_find"):
            limits = getattr(governor._find(governor_path), "limits", None)
        if usage is None or limits is None or limits.max_bytes is None:
            return DEFAULT_BATCH_SIZE
        ratio = usage.bytes / float(limits.max_bytes)
        if ratio <= 0.5:
            return DEFAULT_BATCH_SIZE
        halvings = min(4, int(math.ceil((ratio - 0.5) / 0.125)))
        return max(MIN_BATCH_SIZE, DEFAULT_BATCH_SIZE >> halvings)
    except Exception:  # noqa: BLE001 - 压力感知失败 → 常数批
        return DEFAULT_BATCH_SIZE


def iter_batches(
    rows: Iterable[Dict[str, Any]],
    batch_size: int = DEFAULT_BATCH_SIZE,
    *,
    on_batch: Optional[Callable[[int], None]] = None,
) -> Iterator[List[Dict[str, Any]]]:
    """有界批次扫描（on_batch 是批边界协作点；抛出即中止流）。"""
    batch: List[Dict[str, Any]] = []
    count = 0
    for row in rows:
        batch.append(row)
        if len(batch) >= max(1, batch_size):
            count += 1
            if on_batch is not None:
                on_batch(count)
            yield batch
            batch = []
    if batch:
        count += 1
        if on_batch is not None:
            on_batch(count)
        yield batch


def stream_filter(
    rows: Iterable[Dict[str, Any]],
    predicate: Optional[Dict[str, Any]],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Optional[Callable[[int], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """属性谓词流式过滤（类型化 AST；三值逻辑：仅 True 通过）。"""
    from app.services.data_fabric.query.predicates import (
        evaluate_predicate,
        predicate_from_dict,
    )

    typed = predicate_from_dict(predicate) if predicate is not None else None
    for batch in iter_batches(rows, batch_size, on_batch=on_batch):
        for row in batch:
            if typed is None:
                yield row
                continue
            props = row.get("properties") or row
            if evaluate_predicate(typed, props) is True:
                yield row


def stream_bbox_filter(
    rows: Iterable[Dict[str, Any]],
    bbox: List[float],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Optional[Callable[[int], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """bbox 预过滤（几何外包框快筛；精确空间谓词由上游复算）。"""
    minx, miny, maxx, maxy = bbox

    def _bbox_ok(geom: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(geom, dict):
            return False
        coords = geom.get("coordinates")
        xs: List[float] = []
        ys: List[float] = []
        stack: List[Any] = [coords]
        while stack:
            cur = stack.pop()
            if isinstance(cur, (list, tuple)) and cur and isinstance(cur[0], (int, float)) \
                    and len(cur) >= 2 and isinstance(cur[1], (int, float)):
                xs.append(float(cur[0]))
                ys.append(float(cur[1]))
            elif isinstance(cur, (list, tuple)):
                stack.extend(cur)
        if not xs:
            return False
        return not (max(xs) < minx or min(xs) > maxx or max(ys) < miny or min(ys) > maxy)

    for batch in iter_batches(rows, batch_size, on_batch=on_batch):
        for row in batch:
            if _bbox_ok(row.get("geometry")):
                yield row


def stream_project(
    rows: Iterable[Dict[str, Any]],
    columns: List[str],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Optional[Callable[[int], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """投影（properties 白名单；几何原样携带）。"""
    wanted = set(columns)
    for batch in iter_batches(rows, batch_size, on_batch=on_batch):
        for row in batch:
            props = row.get("properties")
            if isinstance(props, dict):
                yield {**row, "properties": {k: v for k, v in props.items() if k in wanted}}
            else:
                yield {k: v for k, v in row.items() if k in wanted or k in ("geometry",)}


def stream_transform(
    rows: Iterable[Dict[str, Any]],
    fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Optional[Callable[[int], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """轻量变换（fn 返回 None = 丢弃该行；异常向上传播 = typed 失败）。"""
    for batch in iter_batches(rows, batch_size, on_batch=on_batch):
        for row in batch:
            out = fn(row)
            if out is not None:
                yield out


def stream_aggregate(
    rows: Iterable[Dict[str, Any]],
    aggregates: List[Dict[str, Any]],
    group_by: List[str],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Optional[Callable[[int], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """流式分组聚合（统一标量累加器；峰值内存 O(组数)，非 O(行数)）。

    逐批产出**当前**分组快照会破坏语义 —— 聚合是终结操作：本函数在
    输入耗尽后一次性产出分组行（iter_batches 仅作为批边界协作点）。
    值语义委托 ``query.accumulators.AggregateDriver``（与 compute_aggregates /
    Arrow lane 的唯一真相）；stream 行形状（恒带 ``count``、``func_*`` 命名、
    空输入零行）由此处保持。
    """

    driver = AggregateDriver(aggregates, group_by, emit_empty_global_row=False)
    for batch in iter_batches(rows, batch_size, on_batch=on_batch):
        for row in batch:
            driver.update(row.get("properties") or row)
    yield from driver.finalize_rows(style="stream")


def stream_partition(
    rows: Iterable[Dict[str, Any]],
    partition_fn: Callable[[Dict[str, Any]], str],
    *,
    max_partitions: int = 256,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Optional[Callable[[int], None]] = None,
) -> Iterator[Tuple[str, List[Dict[str, Any]]]]:
    """按键分区（空间连接的 partition 策略入口；分区数有硬上界）。"""
    buffers: Dict[str, List[Dict[str, Any]]] = {}
    for batch in iter_batches(rows, batch_size, on_batch=on_batch):
        for row in batch:
            key = partition_fn(row)
            buf = buffers.get(key)
            if buf is None:
                if len(buffers) >= max_partitions:
                    raise ValueError(
                        f"stream_partition exceeded {max_partitions} partitions; "
                        "coarsen the partition function")
                buf = []
                buffers[key] = buf
            buf.append(row)
    for key in sorted(buffers):
        yield key, buffers[key]
