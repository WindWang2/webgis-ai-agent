"""Workflow Runtime V6 —— Science 适配器（analysis capability → 真实算子）。

Phase H「真实执行」：analysis 节点的 stats 类 capability 直接调用
**data_fabric 查询执行平面**的 ``compute_aggregates``（与 SQL 对齐的真实
聚合实现，NULL 语义/样本标准差同一口径）—— 不是 mock，也不经 LLM。

边界纪律：
- 只接线 data_fabric 已有的真实算子；未接线 capability 返回 None →
  driver 诚实 ``NODE_NOT_EXECUTABLE``（绝不假装执行）；
- 输入 = 上游 session ref 的真实要素载荷（properties 行）；
- 输出 = rows 表 payload 落 session ref（``{"type":"table", ...}``），
  evidence 记 rows_emitted。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ScienceOutcome:
    """science 节点执行结果（与 GeoComputeNodeOutcome 同形；有界）。"""

    __slots__ = ("ok", "output_ref", "error_code", "error_message",
                 "duration_ms", "rows_emitted")

    def __init__(self, *, ok: bool, output_ref: str = "",
                 error_code: str = "", error_message: str = "",
                 duration_ms: int = 0, rows_emitted: int = 0):
        self.ok = ok
        self.output_ref = output_ref
        self.error_code = error_code[:64]
        self.error_message = error_message[:200]
        self.duration_ms = duration_ms
        self.rows_emitted = rows_emitted


def science_executable(capability: str) -> bool:
    """capability 是否有已接线真实执行路径（确定性词表）。"""
    return str(capability or "") in _WIRED


#: capability → (aggs 工厂, group_by 提取)（真实 data_fabric AggSpec 词表）。
_WIRED = ("category_breakdown", "admin_aggregation", "rate_aggregation")


def _group_field(params: Dict[str, Any], default: str) -> str:
    field = str(params.get("field") or params.get("group_by")
                or params.get("admin_field") or default)
    return field[:64]


def _aggs_for(capability: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """capability → AggSpec 参数（真实词表：count/sum/avg/…）。"""
    if capability == "category_breakdown":
        return [{"func": "count"}]
    if capability == "admin_aggregation":
        aggs: List[Dict[str, Any]] = [{"func": "count"}]
        value_field = str(params.get("value_field") or "")
        if value_field:
            aggs.append({"func": "sum", "field": value_field[:64]})
        return aggs
    if capability == "rate_aggregation":
        return [{"func": "count"}, {"func": "distinct_count",
                                    "field": _group_field(params, "id")}]
    return [{"func": "count"}]


async def execute_science_node(
    node: Dict[str, Any], *, input_refs: List[str], params: Dict[str, Any],
    session_id: str,
) -> ScienceOutcome:
    """真实执行 stats 类 analysis 节点（data_fabric compute_aggregates）。"""
    import time as _time

    from app.services.session_data import session_data_manager

    start = _time.monotonic()
    capability = str(node.get("capability") or node.get("algorithm_id") or "")
    try:
        # 输入：上游 ref 的真实要素 properties 行（多输入取首个有要素者）
        rows: List[Dict[str, Any]] = []
        for ref in input_refs[:4]:
            payload = await session_data_manager.get(session_id, ref)
            if isinstance(payload, list):
                # MATERIALIZE 产物形态：裸要素数组（非 FeatureCollection）
                payload = {"features": payload}
            if isinstance(payload, dict) and isinstance(
                    payload.get("features"), list):
                rows = [f.get("properties") or {}
                        for f in payload["features"]]
                break
            if isinstance(payload, dict) and isinstance(
                    payload.get("rows"), list):
                rows = payload["rows"]
                break
        if not rows:
            return ScienceOutcome(
                ok=False, error_code="SCIENCE_INPUT_EMPTY",
                error_message="no rows/features resolvable from inputs",
                duration_ms=_elapsed(start))

        from app.services.data_fabric.query.execution import (
            compute_aggregates,
        )
        from app.services.data_fabric.query.models import AggSpec

        group_by = _group_field(params, "category")
        aggs = [AggSpec(**a) for a in _aggs_for(capability, params)]
        out_rows = compute_aggregates(rows, aggs, [group_by])
        if not out_rows:
            return ScienceOutcome(
                ok=False, error_code="SCIENCE_EMPTY_RESULT",
                error_message="aggregation produced no groups",
                duration_ms=_elapsed(start))
        table_payload = {
            "type": "table",
            "capability": capability[:64],
            "group_by": group_by,
            "columns": list(out_rows[0].keys()),
            "rows": out_rows[:10000],
            "row_count": len(out_rows),
        }
        ref = await session_data_manager.store(
            session_id, table_payload,
            prefix=f"wfv6sci-{capability[:24]}".replace(":", "-"))
        return ScienceOutcome(
            ok=True, output_ref=ref, rows_emitted=len(out_rows),
            duration_ms=_elapsed(start))
    except Exception as exc:  # noqa: BLE001 — 分类落证据
        code = getattr(exc, "code", None) or "SCIENCE_EXECUTION_ERROR"
        return ScienceOutcome(
            ok=False, error_code=str(code),
            error_message=str(exc), duration_ms=_elapsed(start))


def _elapsed(start: float) -> int:
    import time as _time

    return int((_time.monotonic() - start) * 1000)
