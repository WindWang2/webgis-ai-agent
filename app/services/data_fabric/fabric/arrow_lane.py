"""Federated Arrow lane（ADR-0119 W6）：批级流式扫描通道。

语义红线：
- **可选通道**：仅当 pyarrow 可用且 adapter 显式提供
  ``iter_query_arrow_batches``（当前 GeoParquet）时启用；否则调用方回落
  dict lane（``physical.iter_scan_pages``）—— 绝不假装；
- **同一行形状**：批经 ``vector_carrier.arrow_to_features`` 解码为标准
  GeoJSON feature dict，与 dict lane 逐位同形（V6 join 内核零改动）；
- **同一谓词语义**：属性谓词用同一 AST ``evaluate_predicate``（Kleene
  三值逻辑）在解码行上求值；
- **逐批检查点**：取消/超时/预算在每个批次边界检查；绝无全量物化。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


def adapter_supports_arrow_lane(adapter: Any) -> bool:
    """诚实探测：pyarrow 可用 ∧ adapter 声明批通道。"""
    from app.services.data_fabric import vector_carrier

    return bool(vector_carrier.arrow_available()) and hasattr(
        adapter, "iter_query_arrow_batches"
    )


def iter_scan_pages_arrow(
    adapter: Any,
    dataset_id: str,
    *,
    where: Optional[Any],
    fields: Optional[List[str]],
    bbox: Optional[List[float]],
    fetch_limit: int,
    budget: Any,
    token: Any,
    page_size: int = 2_000,
    on_page: Optional[Any] = None,
) -> Iterator[List[Dict[str, Any]]]:
    """批级扫描（dict lane ``iter_scan_pages`` 的 Arrow 对偶）。

    - ``where``（Predicate AST）与 ``bbox`` 在解码行上求值（bbox 也在
      adapter 侧行组剪枝 —— 双层，语义一致）；
    - 页 = ``page_size`` 个**匹配行**（与 dict lane 页语义同口径）；
    - ``on_page(rows)`` 回调供反馈/计数器挂钩（bytes 估计等）。
    """
    from app.schemas.data_fabric_schema import QuerySpec
    from app.services.data_fabric import vector_carrier
    from app.services.data_fabric.query.predicates import evaluate_predicate

    extras: Dict[str, Any] = {
        "limit": int(fetch_limit),
        "deadline_s": budget.deadline_s,
        "max_rows": budget.max_rows,
    }
    if fields:
        extras["fields"] = list(fields)
    if bbox:
        extras["bbox"] = list(bbox)

    pending: List[Dict[str, Any]] = []
    emitted = 0
    page: List[Dict[str, Any]] = []
    for batch in adapter.iter_query_arrow_batches(dataset_id, QuerySpec(**extras)):
        token.check()
        table = _to_table(batch)
        rows = vector_carrier.arrow_to_features(table)
        if on_page is not None:
            try:
                on_page(len(rows))
            except Exception:  # noqa: BLE001 - 计数回调绝不阻断扫描
                pass
        for feat in rows:
            if where is not None and not evaluate_predicate(
                where, feat.get("properties") or {}
            ):
                continue
            page.append(feat)
            if len(page) >= page_size:
                yield page
                emitted += len(page)
                page = []
        if emitted >= fetch_limit:
            return
    if page:
        yield page


def _to_table(batch: Any) -> Any:
    """RecordBatch → Table（零拷贝视图）。"""
    import pyarrow as pa

    if hasattr(batch, "to_table"):  # RecordBatch
        return batch.to_table()
    return pa.Table.from_batches([batch])


__all__ = ["adapter_supports_arrow_lane", "iter_scan_pages_arrow"]
