"""Arrow IPC adapter — Versioned Lakehouse V8 (ADR-0130 §6).

向量通道的 **record batch chunk 粒度**列式格式适配（pyarrow 既有依赖，
零新增栈 —— zarr/xarray 同款 probe-gated 纪律，缺席 typed 拒绝）：

- ``publish_arrow_ipc``：pyarrow Table / RecordBatchReader → 临时 IPC
  文件 → ``publish_data_object``（两遍纪律与 CAS 去重全部复用 ——
  本模块不建第二发布通道）；payload 记 schema 投影（列名/dtype/batch
  计数/行数 —— 有界，服务窗口读的预算输入）；
- ``read_arrow_batches``：memory-map 局部读 —— 按批范围（或行窗口 →
  批范围）只触涉及的 record batch，返回 (rows, batches_touched) 证据
  （结构性证据优先 —— V6 lazy 读纪律）。

不做的事：不引入 feather/vendored 依赖、不做 bbox 几何过滤（向量 bbox
窗口扫描归 GeoParquet 通道 —— ``vector_scan``）、不做压缩参数调制
（IPC 默认 LZ4 帧，交换格式不调优）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from app.services.lakehouse.data_object import publish_data_object

logger = logging.getLogger(__name__)

#: payload 投影有界（列名清单截断披露）。
MAX_SCHEMA_COLUMNS = 512


class ArrowAdapterError(ValueError):
    """Arrow IPC 契约违例（含 pyarrow 缺席）。"""

    code = "LAKEHOUSE_ARROW_INVALID"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict:
        return {"success": False, "code": self.code, "message": self.message}


def _require_pyarrow():
    try:
        import pyarrow  # noqa: F401

        return pyarrow
    except ImportError as e:  # pragma: no cover — 环境缺席
        raise ArrowAdapterError(
            "pyarrow is required for the Arrow IPC adapter (optional "
            "dependency — install it to enable this lane)"
        ) from e


def _schema_projection(schema: Any) -> Dict[str, Any]:
    """schema → 有界 payload 投影（列名/类型/行数 —— 确定性排序）。"""
    pa = _require_pyarrow()
    names = list(schema.names)[:MAX_SCHEMA_COLUMNS]
    return {
        "columns": names,
        "columns_truncated": len(schema.names) > MAX_SCHEMA_COLUMNS,
        "types": {name: str(schema.field(name).type) for name in names},
    }


def publish_arrow_ipc(
    table: Any,
    *,
    owner_scope: Mapping[str, str],
    payload: Optional[Mapping[str, Any]] = None,
    producer: Optional[Mapping[str, Any]] = None,
    source_refs: Optional[List[str]] = None,
    input_fingerprint: Optional[str] = None,
    store: Optional[Any] = None,
) -> Any:
    """Table/RecordBatchReader → Arrow IPC DataObject（CAS；幂等）。

    IPC 文件先写临时路径（tmp + 原子收敛），再经 ``publish_data_object``
    的流式两遍通道发布 —— 身份与完整性全部归 DataObject manifest。
    """
    import tempfile

    pa = _require_pyarrow()
    import pyarrow as pa_mod
    import pyarrow.ipc as ipc

    if isinstance(table, pa_mod.RecordBatchReader):
        batches = list(table)
        tab = pa_mod.Table.from_batches(batches, table.schema)
    else:
        tab = table
    if not isinstance(tab, pa_mod.Table):
        raise ArrowAdapterError(
            f"publish_arrow_ipc expects a pyarrow Table/RecordBatchReader, "
            f"got {type(table).__name__}"
        )
    num_rows = int(tab.num_rows)
    proj = _schema_projection(tab.schema)
    with tempfile.TemporaryDirectory(prefix="lh_arrow_") as td:
        path = Path(td) / "data.arrows"
        with pa_mod.OSFile(str(path), "wb") as sink:
            with ipc.new_file(sink, tab.schema) as writer:
                batches_out = tab.to_batches(max_chunksize=65_535)
                for batch in batches_out:
                    writer.write_batch(batch)
        proj.update({
            "num_rows": num_rows,
            "num_batches": len(batches_out),
            "format": "arrow_ipc",
        })
        merged = {**(payload or {}), **proj}
        return publish_data_object(
            {"data.arrows": path},
            kind="arrow_ipc",
            owner_scope=owner_scope,
            payload=merged,
            producer=producer,
            source_refs=source_refs,
            input_fingerprint=input_fingerprint,
            store=store,
        )


def read_arrow_batches(
    data_object_id: str,
    *,
    batch_range: Optional[Tuple[int, int]] = None,
    row_offset: int = 0,
    row_limit: Optional[int] = None,
    columns: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    max_rows: int = 200_000,
) -> Dict[str, Any]:
    """批粒度局部读（memory-map；只触批范围内的字节）。

    ``batch_range`` = [start, stop) 批索引；``row_offset/row_limit`` 是
    行窗口（翻译为批范围后相交行返回）。响应带 ``batches_touched`` 与
    ``rows`` 证据（lazy 读的结构性证据）。
    """
    import tempfile

    import pyarrow as pa_mod
    import pyarrow.ipc as ipc

    td = tempfile.TemporaryDirectory(prefix="lh_arrow_read_")
    try:
        dest_dir = Path(td.name)
        from app.services.lakehouse.data_object import (
            materialize_data_object,
            owner_scope_allows,
            resolve_data_object,
        )

        manifest = resolve_data_object(data_object_id)
        if manifest is None or not owner_scope_allows(
            manifest, session_id=session_id, project_id=project_id
        ):
            raise ArrowAdapterError(
                f"data object not accessible: {str(data_object_id)[:12]}"
            )
        if manifest.get("kind") != "arrow_ipc":
            raise ArrowAdapterError(
                f"object kind is {manifest.get('kind')!r}, not arrow_ipc"
            )
        written = materialize_data_object(
            data_object_id, dest_dir,
            owner_session_id=session_id, owner_project_id=project_id,
        )
        dest = dest_dir / written[0]
        with pa_mod.memory_map(str(dest), "rb") as source:
            reader = ipc.open_file(source)
            total_batches = int(reader.num_record_batches)
            batch_rows = [
                int(reader.get_batch(i).num_rows) for i in range(total_batches)
            ]
            total_rows = sum(batch_rows)
            start, stop = 0, total_batches
            if batch_range is not None:
                if len(batch_range) != 2:
                    raise ArrowAdapterError("batch_range must be [start, stop)")
                start = max(0, int(batch_range[0]))
                stop = min(total_batches, int(batch_range[1]))
                if start >= stop:
                    raise ArrowAdapterError(
                        f"empty batch range [{start}, {stop}) — "
                        f"object has {total_batches} batches"
                    )
            # 行窗口 → 精确行集（IO 触相交批，值按窗口切片 —— 与 V6
            # lazy 读纪律一致：块边界只影响 IO，不影响返回值形状）。
            # max_rows 预算无条件生效（row_offset-only 的读也截到预算
            # —— 结构性资源边界绝不因参数组合而旁路）。
            row_start = int(row_offset or 0)
            row_stop = total_rows
            if row_offset or row_limit is not None:
                if row_offset < 0 or (row_limit is not None and row_limit < 0):
                    raise ArrowAdapterError("row window must be non-negative")
                want = total_rows - row_start
                if row_limit is not None:
                    want = min(int(row_limit), max_rows)
                else:
                    want = min(want, max_rows)
                row_stop = min(total_rows, row_start + want)
                if row_start >= row_stop:
                    raise ArrowAdapterError(
                        f"row window [{row_start}, {row_stop}) is empty — "
                        f"object has {total_rows} rows"
                    )
                acc = 0
                for i, n in enumerate(batch_rows):
                    if acc + n > row_start:
                        start = max(start, i)
                        break
                    acc += n
                acc_end = 0
                for i, n in enumerate(batch_rows):
                    acc_end += n
                    if acc_end >= row_stop:
                        stop = min(stop, i + 1)
                        break
                else:
                    stop = min(stop, total_batches)
            rows_before = sum(batch_rows[:start])
            tables: List[Any] = []
            rows_read = 0
            for i in range(start, stop):
                batch = reader.get_batch(i)
                rows_read += int(batch.num_rows)
                tables.append(pa_mod.Table.from_batches([batch]))
            table = (
                pa_mod.concat_tables(tables) if tables
                else pa_mod.Table.from_arrays([], names=[])
            )
            if row_limit is not None or row_offset:
                lo = row_start - rows_before
                table = table.slice(max(0, lo), row_stop - row_start)
            if columns:
                missing = [c for c in columns if c not in table.column_names]
                if missing:
                    raise ArrowAdapterError(
                        f"unknown columns {sorted(missing)}"
                    )
                table = table.select(columns)
            return {
                "table": table,
                "rows": int(table.num_rows),
                "batches_touched": stop - start,
                "total_batches": total_batches,
                "total_rows": total_rows,
                "batch_range": [start, stop],
                "schema": _schema_projection(table.schema),
            }
    finally:
        td.cleanup()
