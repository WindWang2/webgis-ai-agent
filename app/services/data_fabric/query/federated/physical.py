"""V6 物理执行原语（ADR-0118 W6）：分页流式探针/取消点/Bloom 预滤/CRS 变换。

- **分页探针**：源以页为单位拉取（``iter_scan_pages``），每页是取消与
  预算检查点；probe 侧绝不全量物化（build 侧仍受 V5 红线
  ``MAX_JOIN_CANDIDATES`` 硬界）；
- **取消**：``CancelToken``（deadline + 协作 event），逐页/逐跳检查；
- **Bloom 预滤**：盈利（``bloom.semi_join_plan``）时在 build 侧物化前
  过滤右行 —— 只有假阳性语义，绝不影响 join 结果；
- **CRS 变换**：pyproj 一次性变换 decided 侧几何（进 build 缓存 / 累积行
  缓存），绝不逐行逐次重复变换。server-side placement 需要跨 adapter 的
  output.crs 下推管道（legacy QuerySpec 无该字段）—— 显式 follow-up，
  本层一律本地变换并如实标注。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from app.services.data_fabric.errors import QueryBudgetExceededError

logger = logging.getLogger(__name__)

#: 探针页大小（与 V5 两源路径 JOIN_PAGE_SIZE 同口径）。
DEFAULT_PAGE_SIZE = 2_000


class CancelledError(RuntimeError):
    """协作取消（外部 event 触发；非预算类）。"""


class CancelToken:
    """逐批取消/超时检查点。"""

    __slots__ = ("_deadline", "_event")

    def __init__(
        self,
        deadline_s: Optional[float] = None,
        cancel_event: Optional[threading.Event] = None,
    ):
        started = time.monotonic()
        self._deadline = (started + deadline_s) if deadline_s else None
        self._event = cancel_event

    def check(self) -> None:
        if self._event is not None and self._event.is_set():
            raise CancelledError("federated execution cancelled")
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise QueryBudgetExceededError(
                "federated execution exceeded deadline",
                details={"hint": "narrow bbox/filters, or aggregate on sources"},
            )

    @property
    def cancelled(self) -> bool:
        return bool(self._event is not None and self._event.is_set())


def iter_scan_pages(
    adapter: Any,
    dataset_id: str,
    *,
    where: Optional[Any],
    fields: Optional[List[str]],
    bbox: Optional[List[float]],
    fetch_limit: int,
    budget: Any,
    token: CancelToken,
    page_size: int = DEFAULT_PAGE_SIZE,
    output_crs: Optional[str] = None,
    on_result: Optional[Any] = None,
) -> Iterator[List[Dict[str, Any]]]:
    """分页拉取一个源（每页一个 yield；页间检查取消/超时/预算）。

    与 V5 ``_source_query_page`` 同一 adapter 契约（legacy QuerySpec），
    同一 typed 错误；差别只在：V6 逐页消费 + 逐页检查，probe 侧不再
    一次性物化全源。

    V7（ADR-0119 W6）：adapter 显式提供 Arrow 批通道（``iter_query_arrow_
    batches``）且 pyarrow 可用时，委托 ``fabric.arrow_lane`` 批级扫描 ——
    行形状/谓词语义/预算行为逐位同口径；typed ``VectorCarrierUnavailable``
    诚实回落 dict lane。
    """
    from app.services.data_fabric.fabric.arrow_lane import (
        adapter_supports_arrow_lane,
        iter_scan_pages_arrow,
    )

    if adapter_supports_arrow_lane(adapter):
        fetched_arrow = 0
        for page in iter_scan_pages_arrow(
            adapter, dataset_id,
            where=where, fields=fields, bbox=bbox,
            fetch_limit=fetch_limit, budget=budget, token=token,
            page_size=page_size,
        ):
            fetched_arrow += len(page)
            if fetched_arrow > budget.max_rows:
                raise QueryBudgetExceededError(
                    f"source scan fetched {fetched_arrow} rows (budget {budget.max_rows})",
                    details={"hint": "narrow bbox or add filters on the source",
                             "lane": "arrow"},
                )
            yield page
        return

    from app.schemas.data_fabric_schema import QuerySpec

    fetched = 0
    offset = 0
    while offset < fetch_limit:
        token.check()
        size = min(page_size, fetch_limit - offset)
        extras: Dict[str, Any] = {
            "limit": size,
            "offset": offset,
            "deadline_s": budget.deadline_s,
            "max_rows": budget.max_rows,
        }
        if fields:
            extras["fields"] = fields
        if where is not None:
            extras["where"] = where
        if bbox:
            extras["bbox"] = list(bbox)
        if output_crs:
            # V7（ADR-0119 W8）：server-side 交付 CRS（adapter normalize
            # 映射 v2.output.crs；PostGIS ST_Transform / ArcGIS outSR）。
            extras["output_crs"] = output_crs
        result = adapter.query(dataset_id, QuerySpec(**extras))
        if on_result is not None:
            try:
                on_result(result)
            except Exception:  # noqa: BLE001 - 证据回调绝不阻断扫描
                pass
        rows = result.features or []
        fetched += len(rows)
        if fetched > budget.max_rows:
            raise QueryBudgetExceededError(
                f"source scan fetched {fetched} rows (budget {budget.max_rows})",
                details={"hint": "narrow bbox or add filters on the source"},
            )
        yield rows
        if len(rows) < size:
            return  # 真实末页
        offset += len(rows)


def bloom_prefilter(
    rows: List[Dict[str, Any]],
    bloom: Any,
    field: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Bloom 键预滤（假阳性语义：命中即保留；后续精确 join 兜底）。"""
    from app.services.data_fabric.query.federation import _chain_row_key

    kept: List[Dict[str, Any]] = []
    for r in rows:
        key = _chain_row_key(r, field)
        if key is None or key in bloom:
            kept.append(r)
    return kept, {
        "rows_before": len(rows),
        "rows_after": len(kept),
        "filtered": len(rows) - len(kept),
    }


def transform_rows_geometry(
    rows: List[Dict[str, Any]], from_srid: int, to_srid: int
) -> List[Dict[str, Any]]:
    """一次性变换行几何（GeoJSON）到目标 CRS；失败 → typed 错误。

    V6 语义：变换在 build/累积缓存构建前做**一次**（绝不逐行逐次重复）。
    """
    if from_srid == to_srid or not rows:
        return rows
    try:
        from pyproj import Transformer
        from shapely.geometry import mapping, shape
        from shapely.ops import transform as shp_transform

        transformer = Transformer.from_crs(from_srid, to_srid, always_xy=True)
    except Exception as e:  # noqa: BLE001 - 变换器不可用 → typed 失败
        from app.services.data_fabric.query.federation import FederatedQueryError

        raise FederatedQueryError(
            f"CRS transform EPSG:{from_srid}→EPSG:{to_srid} unavailable: {e}",
            details={"hint": "reproject the source upstream or pick a common CRS"},
        ) from e
    out: List[Dict[str, Any]] = []
    for row in rows:
        geom = row.get("geometry")
        if isinstance(geom, dict):
            try:
                g = shape(geom)
                if not g.is_empty:
                    geom = mapping(shp_transform(transformer.transform, g))
            except Exception as e:  # noqa: BLE001 - 坏几何 → typed 失败（绝不静默）
                from app.services.data_fabric.query.federation import (
                    FederatedQueryError,
                )

                raise FederatedQueryError(
                    f"CRS transform failed on a feature: {e}"
                ) from e
        new_row = dict(row)
        new_row["geometry"] = geom
        out.append(new_row)
    return out


def estimate_rows_bytes(rows: Sequence[Dict[str, Any]]) -> int:
    """粗粒度传输字节估计（行数 × 兜底每行字节；预算与披露用）。"""
    return len(rows) * 1800


__all__ = [
    "CancelledError",
    "CancelToken",
    "DEFAULT_PAGE_SIZE",
    "bloom_prefilter",
    "estimate_rows_bytes",
    "iter_scan_pages",
    "transform_rows_geometry",
]
