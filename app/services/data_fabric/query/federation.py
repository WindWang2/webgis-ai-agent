"""受控两源联邦查询执行器（ADR-0094 §7）。

目标：GIS agent 所需的 bounded federation——不是 Trino/Spark。

支持的计划形态（第一阶段）：
1. **attribute join**：A.join_field == B.join_field（等值连接）
2. **spatial join**：points-within-polygons / intersects（shapely STRtree，
   禁止 O(N·M) 双循环）
3. **aggregate + join**：join 后按 B 分组聚合（count/sum/avg/min/max）

安全预算（硬限制，超限 typed QUERY_BUDGET_EXCEEDED + 缩减建议）：
``max_source_rows / max_local_rows / max_bytes / max_vertices /
max_execution_s / max_join_candidates``。

规划决策：
- 同源（同 PostGIS profile）优先 server-side join（adapter.server_spatial_join）。
- 跨源：先执行"小结果侧"（聚合侧/多边形侧），把 join 键/几何载入本地索引，
  另一侧流式分页扫描 + 逐页 join，行数计入预算。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.schemas.data_fabric_schema import QuerySpec
from app.services.data_fabric.errors import (
    DataFabricError,
    QueryBudgetExceededError,
)
from app.services.data_fabric.query.execution import StreamingBudget
from app.services.data_fabric.query.models import ExecutionBudget

logger = logging.getLogger(__name__)

# 联邦默认预算（比单源更紧）
FEDERATION_BUDGET = ExecutionBudget(
    deadline_s=60.0,
    max_rows=200_000,
    max_bytes=128 * 1024 * 1024,
    max_vertices=20_000_000,
    max_pages=50,
)
MAX_JOIN_CANDIDATES = 100_000
JOIN_PAGE_SIZE = 2_000


class FederatedQueryError(DataFabricError):
    """联邦查询构造错误（非预算）。"""


@dataclass
class FederatedPlan:
    """两源联邦计划（可序列化描述）。"""

    kind: str  # attribute_join | spatial_join | aggregate_join
    left: Dict[str, Any] = field(
        default_factory=dict
    )  # {source_id, dataset_id, spec extras}
    right: Dict[str, Any] = field(default_factory=dict)
    join_field_left: Optional[str] = None
    join_field_right: Optional[str] = None
    spatial_op: Optional[str] = None  # within | intersects
    group_by_right: Optional[List[str]] = None
    aggregates: Optional[List[Dict[str, Any]]] = None
    strategy: str = "local_hash_or_strtree"  # server_side | local_hash | local_strtree
    estimated_left_rows: Optional[int] = None
    estimated_right_rows: Optional[int] = None
    warnings: List[str] = field(default_factory=list)
    # ---- V4 additive（ADR-0101 D7）：被拒绝的替代序（EXPLAIN 诚实披露）----
    rejected_orders: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "kind": self.kind,
            "left": self.left,
            "right": self.right,
            "join_field_left": self.join_field_left,
            "join_field_right": self.join_field_right,
            "spatial_op": self.spatial_op,
            "group_by_right": self.group_by_right,
            "aggregates": self.aggregates,
            "strategy": self.strategy,
            "estimated_left_rows": self.estimated_left_rows,
            "estimated_right_rows": self.estimated_right_rows,
            "warnings": self.warnings,
        }
        if self.rejected_orders:
            out["rejected_orders"] = self.rejected_orders
        return out


@dataclass
class FederatedQueryRequest:
    """联邦查询输入（tool/route 归一化产物）。"""

    left_source_id: str
    left_dataset_id: str
    right_source_id: str
    right_dataset_id: str
    # attribute join
    join_field_left: Optional[str] = None
    join_field_right: Optional[str] = None
    # spatial join
    spatial_op: Optional[str] = None  # within | intersects
    # aggregate+join
    group_by_right: Optional[List[str]] = None
    aggregates: Optional[List[Dict[str, Any]]] = None  # [{func, field}]
    # 共同谓词（分别应用到两侧源查询）
    left_where: Optional[Any] = None
    right_where: Optional[Any] = None
    bbox: Optional[List[float]] = None
    limit: int = 10_000
    budget: ExecutionBudget = field(default_factory=lambda: FEDERATION_BUDGET)


def plan_federated(req: FederatedQueryRequest) -> FederatedPlan:
    """构造并校验联邦计划（纯函数；执行器决定 server-side 或本地）。"""
    if req.spatial_op and req.join_field_left:
        raise FederatedQueryError("cannot mix spatial join and attribute join")
    if not req.spatial_op and not (req.join_field_left and req.join_field_right):
        raise FederatedQueryError("attribute join requires join_field on both sides")
    if req.spatial_op and req.spatial_op not in ("within", "intersects"):
        raise FederatedQueryError(
            f"unsupported spatial_op {req.spatial_op!r} (within|intersects)"
        )
    if req.aggregates and not req.group_by_right:
        raise FederatedQueryError(
            "aggregate join requires group_by fields from the right side"
        )

    kind = (
        "aggregate_join"
        if req.aggregates
        else ("spatial_join" if req.spatial_op else "attribute_join")
    )
    plan = FederatedPlan(
        kind=kind,
        left={
            "source_id": req.left_source_id,
            "dataset_id": req.left_dataset_id,
        },
        right={
            "source_id": req.right_source_id,
            "dataset_id": req.right_dataset_id,
        },
        join_field_left=req.join_field_left,
        join_field_right=req.join_field_right,
        spatial_op=req.spatial_op,
        group_by_right=req.group_by_right,
        aggregates=req.aggregates,
    )
    if req.limit > req.budget.max_rows:
        raise QueryBudgetExceededError(
            f"federated limit {req.limit} exceeds budget {req.budget.max_rows}",
            details={
                "hint": "reduce limit, add bbox/filters, or aggregate on the source"
            },
        )
    return plan


# ── 本地执行原语 ────────────────────────────────────────────────────────────


class _LocalSpatialIndex:
    """shapely STRtree 包装（不可用时退化为线性扫描并记录 warning）。"""

    def __init__(self, geoms: List[Any]):
        self._geoms = geoms
        self._tree = None
        try:
            from shapely.strtree import STRtree

            if geoms:
                self._tree = STRtree(geoms)
        except Exception:
            self._tree = None

    def candidates(self, geom: Any) -> List[int]:
        """返回可能相交的索引（候选集；调用方做精确判定）。"""
        if self._tree is None or not self._geoms:
            return list(range(len(self._geoms)))
        try:
            hits = self._tree.query(geom)
            return [int(i) for i in hits]
        except Exception:
            return list(range(len(self._geoms)))


def _shapely_from_geojson(geom: Optional[Dict[str, Any]]):
    if not isinstance(geom, dict):
        return None
    try:
        from shapely.geometry import shape

        return shape(geom)
    except Exception:
        return None


def spatial_join_local(
    points: Sequence[Dict[str, Any]],
    polygons: Sequence[Dict[str, Any]],
    *,
    spatial_op: str = "within",
    join_field_right: Optional[str] = None,
    budget: Optional[StreamingBudget] = None,
    max_output: Optional[int] = None,
    spatial_index: Optional["_LocalSpatialIndex"] = None,
    carry_left_geometry: bool = False,
) -> List[Dict[str, Any]]:
    """点面本地 join（STRtree 候选 + 精确判定；绝不 O(N·M) 双循环）。

    返回 join 行：``{point_properties..., "__right__": right_properties}``。

    ``carry_left_geometry``（V3 链式联邦专用，缺省 False）：True 时输出行
    额外携带 ``__left_geometry__``（左点原始几何），供链上后续空间跳重建
    左要素再入 STRtree。缺省 False 时两源路径输出逐字节不变。
    """
    shp_polys = []
    for p in polygons:
        g = _shapely_from_geojson(p.get("geometry"))
        if g is not None and not g.is_empty:
            shp_polys.append((g, p))
    # R4-M4：调用方可注入复用的空间索引（跨左页扫描只构建一次 STRtree）
    index = spatial_index or _LocalSpatialIndex([g for g, _ in shp_polys])
    if (
        index._tree is None
        and len(points) * max(1, len(shp_polys)) > MAX_JOIN_CANDIDATES * 10
    ):
        # 仅线性回退时产品积守卫才有意义；STRtree 的复杂度是 O(N·candidates)
        raise QueryBudgetExceededError(
            f"join candidate space {len(points)}x{len(shp_polys)} too large "
            "(no STRtree available)",
            details={
                "hint": "install shapely, or apply bbox/filters to reduce both sides"
            },
        )

    out: List[Dict[str, Any]] = []
    for pt in points:
        g = _shapely_from_geojson(pt.get("geometry"))
        if g is None:
            continue
        if budget is not None:
            budget.add_feature(pt)
        hit: Optional[Dict[str, Any]] = None
        for cand_idx in index.candidates(g):
            pg, props = shp_polys[cand_idx]
            try:
                ok = (
                    pg.contains(g) or pg.equals(g)
                    if spatial_op == "within"
                    else pg.intersects(g)
                )
            except Exception:
                continue
            if ok:
                hit = props
                break
        if hit is not None:
            # R4-C2：输出行计入预算（join 扇出可能远超输入行数）
            out_row = dict(pt.get("properties") or {})
            out_row["__right__"] = hit.get("properties") or {}
            out_row["__right_geometry__"] = hit.get("geometry")
            if carry_left_geometry:
                out_row["__left_geometry__"] = pt.get("geometry")
            if budget is not None:
                budget.add_feature(out_row)
            out.append(out_row)
            if max_output is not None and len(out) >= max_output:
                break
    return out


def attribute_join_local(
    left_rows: Sequence[Dict[str, Any]],
    right_rows: Sequence[Dict[str, Any]],
    *,
    join_field_left: str,
    join_field_right: str,
    budget: Optional[StreamingBudget] = None,
    max_output: Optional[int] = None,
    left_key_resolver: Optional[Callable[[Dict[str, Any], str], Any]] = None,
    right_index: Optional[Dict[Any, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """等值连接（右侧哈希索引；左行流式探测）。

    ``left_key_resolver``（V3 链式联邦专用，缺省 None）：给定 ``(左行, 字段)``
    返回连接键。链式累积行是扁平属性 dict——上一跳的右属性嵌在 ``__right__``
    下，需用 ``_chain_row_key`` 穿透取键；缺省 None 时保持两源路径的顶层取键
    语义（逐字节不变）。

    ``right_index``（V6 W6 additive）：调用方预建的右侧哈希索引（跨页复用，
    形状与内部索引一致）；缺省 None 时本函数自建（历史路径逐位不变）。
    """
    index: Dict[Any, List[Dict[str, Any]]] = (
        right_index
        if right_index is not None
        else build_attribute_index(right_rows, join_field_right)
    )
    out: List[Dict[str, Any]] = []
    for lrow in left_rows:
        if budget is not None:
            budget.add_feature(
                lrow
                if isinstance(lrow, dict) and "properties" in lrow
                else {"properties": lrow}
            )
        if left_key_resolver is not None:
            key = left_key_resolver(lrow, join_field_left)
        else:
            key = (lrow.get("properties") or lrow).get(join_field_left)
        if key is None:
            continue
        for rrow in index.get(_hashable_key(key), ()):
            row = dict(lrow.get("properties") or lrow)
            row["__right__"] = rrow.get("properties") or rrow
            if budget is not None:
                budget.add_feature(row)
            out.append(row)
            if max_output is not None and len(out) >= max_output:
                return out
    return out


def build_attribute_index(
    right_rows: Sequence[Dict[str, Any]], join_field_right: str
) -> Dict[Any, List[Dict[str, Any]]]:
    """右侧哈希索引（``attribute_join_local`` 内部形状；V6 跨页复用入口）。"""
    index: Dict[Any, List[Dict[str, Any]]] = {}
    for r in right_rows:
        key = (r.get("properties") or r).get(join_field_right)
        if key is not None:
            index.setdefault(_hashable_key(key), []).append(r)
    return index


def _hashable_key(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return str(v)
    if isinstance(v, float) and v == int(v):
        return int(v)  # 1 与 1.0 join 语义一致
    return v


def aggregate_join_rows(
    joined_rows: Sequence[Dict[str, Any]],
    aggregates: Sequence[Dict[str, Any]],
    group_by: Sequence[str],
):
    """join 行 → 分组聚合（复用 V2 本地聚合器语义）。"""
    from app.services.data_fabric.query.models import AggSpec

    aggs = [AggSpec(**a) for a in aggregates]
    return _aggregate_with_right(joined_rows, aggs, group_by)


def _aggregate_with_right(rows, aggs, group_by):
    """分组聚合（R4-M5：标量累加器，不再复制全部成员行——峰值内存 O(组数)）。

    stddev 为样本口径（与 Postgres STDDEV 一致）：在线 Welford。
    V6（ADR-0118 W6）：增量内核提取为 ``_AggregateState``（单一语义真相），
    本函数与 V6 流式执行器共用同一实现 —— 逐位行为不变。
    """
    state = _AggregateState(aggs, group_by)
    for row in rows:
        state.update(row)
    return state.finalize()


class _AggregateState:
    """join 行分组聚合的增量内核（V6 W6 提取；``_aggregate_with_right``
    与 V6 物理执行器共用 —— 语义单一真相，流式喂行，O(组数) 内存）。"""

    def __init__(self, aggs, group_by):
        self.aggs = list(aggs)
        self.group_by = list(group_by)
        self.groups: Dict[Tuple, Dict[str, Any]] = {}

    def update(self, row: Dict[str, Any]) -> None:
        right = row.get("__right__") or {}
        key = tuple(right.get(g) for g in self.group_by)
        acc = self.groups.get(key)
        if acc is None:
            # 每个 agg 一个累加器槽：count / sum / sumsq / min / max / distinct-set
            acc = {"n": 0, "cells": {}}
            for a in self.aggs:
                name = a.func if a.field is None else f"{a.func}_{a.field}"
                acc["cells"][name] = {
                    "count": 0,
                    "sum": 0.0,
                    "sumsq": 0.0,
                    "min": None,
                    "max": None,
                    "distinct": set(),
                }
            self.groups[key] = acc
        acc["n"] += 1
        for a in self.aggs:
            name = a.func if a.field is None else f"{a.func}_{a.field}"
            cell = acc["cells"][name]
            if a.func == "count" and a.field is None:
                cell["count"] += 1
                continue
            v = None
            if a.field is not None:
                left_props = {
                    k: v2_ for k, v2_ in row.items() if not k.startswith("__")
                }
                right_props = row.get("__right__") or {}
                if a.field in left_props:
                    v = left_props[a.field]  # 左（事实表）优先
                elif a.field in right_props:
                    v = right_props[a.field]  # 右回退（R2-C2：右字段不再静默读左）
            if v is None:
                continue
            cell["count"] += 1
            cell["distinct"].add(str(v))
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                fv = float(v)
                cell["sum"] += fv
                cell["sumsq"] += fv * fv
                cell["min"] = fv if cell["min"] is None else min(cell["min"], fv)
                cell["max"] = fv if cell["max"] is None else max(cell["max"], fv)
            else:
                cell["min"] = v if cell["min"] is None else min(cell["min"], v, key=str)
                cell["max"] = v if cell["max"] is None else max(cell["max"], v, key=str)

    def finalize(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for key, acc in self.groups.items():
            result: Dict[str, Any] = {}
            for g, v in zip(self.group_by, key):
                result[g] = v
            for a in self.aggs:
                name = a.func if a.field is None else f"{a.func}_{a.field}"
                cell = acc["cells"][name]
                import math as _math

                if a.func == "count":
                    result[name] = cell["count"]
                elif a.func == "distinct_count":
                    result[name] = len(cell["distinct"])
                elif a.func == "sum":
                    result[name] = cell["sum"] if cell["count"] else None
                elif a.func == "avg":
                    result[name] = (
                        (cell["sum"] / cell["count"]) if cell["count"] else None
                    )
                elif a.func == "min":
                    result[name] = cell["min"]
                elif a.func == "max":
                    result[name] = cell["max"]
                elif a.func == "stddev":
                    n = cell["count"]
                    if n < 2:
                        result[name] = None
                    else:
                        mean = cell["sum"] / n
                        var = max(0.0, (cell["sumsq"] - n * mean * mean) / (n - 1))
                        result[name] = _math.sqrt(var)
            out.append(result)
        return out


# ── 执行器 ──────────────────────────────────────────────────────────────────


class FederatedExecutor:
    """两源联邦执行器（依赖注入 adapter 工厂，便于测试与多租户）。"""

    def __init__(self, adapter_factory):
        """``adapter_factory(source_id) -> adapter | None``。"""
        self._adapter_factory = adapter_factory

    def execute(self, req: FederatedQueryRequest) -> Dict[str, Any]:
        plan = plan_federated(req)
        started = time.monotonic()

        left_adapter = self._adapter_factory(req.left_source_id)
        right_adapter = self._adapter_factory(req.right_source_id)
        if left_adapter is None or right_adapter is None:
            raise FederatedQueryError(
                "one or both federated sources are not connected",
                details={
                    "left": req.left_source_id,
                    "right": req.right_source_id,
                },
            )

        # 同源 server-side 优先（PostGIS 快路径）
        if (
            req.left_source_id == req.right_source_id
            and plan.kind in ("spatial_join", "aggregate_join")
            and hasattr(left_adapter, "server_spatial_join")
        ):
            try:
                rows = left_adapter.server_spatial_join(
                    req.left_dataset_id,
                    req.right_dataset_id,
                    join_op=req.spatial_op or "within",
                    group_by_polygon_field=(req.group_by_right or [None])[0],
                    limit=req.limit,
                )
                return self._result(
                    plan, rows, started, strategy="server_side", rows_fetched=len(rows)
                )
            except DataFabricError:
                raise
            except Exception as e:  # server join 不可用 → 本地回退（记录）
                logger.info(
                    "[Federation] server-side join unavailable, falling back: %s", e
                )
                plan.warnings.append(
                    f"server-side join failed ({e}); local execution used"
                )

        return self._execute_local(req, plan, left_adapter, right_adapter, started)

    # ── 内部 ─────────────────────────────────────────────────────────

    def execute_chain(self, req) -> Dict[str, Any]:
        """N 源链执行（V3 additive；V6：engine 分派）。"""
        if getattr(req, "engine", "v5") == "v6":
            return execute_chain_v6(self, req)
        return execute_federated_chain(self, req)

    def _source_query(
        self,
        adapter,
        dataset_id: str,
        req: FederatedQueryRequest,
        side_where: Optional[Any],
        *,
        fields: Optional[List[str]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        extras: Dict[str, Any] = {}
        if fields:
            extras["fields"] = fields
        if side_where is not None:
            extras["where"] = side_where
        if req.bbox:
            extras["bbox"] = req.bbox
        extras["limit"] = limit
        extras["deadline_s"] = req.budget.deadline_s
        extras["max_rows"] = req.budget.max_rows
        spec = QuerySpec(**extras)
        result = adapter.query(dataset_id, spec)
        feats = result.features or []
        if len(feats) > req.budget.max_rows:
            raise QueryBudgetExceededError(
                f"source returned {len(feats)} rows (budget {req.budget.max_rows})",
                details={"hint": "narrow bbox or add filters on both sides"},
            )
        return feats

    def _execute_local(self, req, plan, left_adapter, right_adapter, started):
        budget = StreamingBudget(
            max_rows=req.budget.max_rows,
            max_bytes=req.budget.max_bytes,
            max_vertices=req.budget.max_vertices,
        )
        # 右侧（多边形/维表侧）先行 —— 通常是小结果侧，物化成本最低
        right_fields = None
        if req.join_field_right:
            right_fields = [req.join_field_right] + (req.group_by_right or [])
        right_rows = self._source_query(
            right_adapter,
            req.right_dataset_id,
            req,
            req.right_where,
            fields=right_fields,
            limit=min(req.budget.max_rows, MAX_JOIN_CANDIDATES),
        )
        if len(right_rows) > MAX_JOIN_CANDIDATES:
            raise QueryBudgetExceededError(
                f"right side has {len(right_rows)} rows (> {MAX_JOIN_CANDIDATES} join candidates)",
                details={
                    "hint": "filter the right (dimension/polygon) side before joining"
                },
            )

        # 左侧流式分页扫描 + 逐页 join（页大小 JOIN_PAGE_SIZE，页数受预算约束）
        left_fields = [req.join_field_left] if req.join_field_left else None
        joined: List[Dict[str, Any]] = []
        rows_fetched = 0
        offset = 0
        # V5（Wave 9 审计步骤 5）：链式半连接约减回迁两源路径 —— 等值 join
        # 的右行只在键出现于本页左侧键集时才进哈希索引（内连接语义不变；
        # 键集超上限诚实放弃）。spatial join 无键可约减，不适用。
        semi_join_stats: List[Dict[str, Any]] = []
        deadline = started + req.budget.deadline_s
        # R4-M4：右侧空间索引只构建一次（跨左页复用）
        spatial_index = None
        if plan.kind in ("spatial_join", "aggregate_join") and req.spatial_op:
            shp_polys = [
                g
                for g in (_shapely_from_geojson(p.get("geometry")) for p in right_rows)
                if g is not None and not g.is_empty
            ]
            spatial_index = _LocalSpatialIndex(shp_polys)
        while len(joined) < req.limit:
            if time.monotonic() > deadline:
                raise QueryBudgetExceededError(
                    f"federated join exceeded {req.budget.deadline_s}s deadline",
                    details={
                        "hint": "reduce scope (bbox/filters) or aggregate on sources"
                    },
                )
            fetch_size = min(JOIN_PAGE_SIZE, req.limit - len(joined) + 1)
            page = (
                self._source_query(
                    left_adapter,
                    req.left_dataset_id,
                    req,
                    req.left_where,
                    fields=left_fields,
                    limit=fetch_size,
                )
                if offset == 0
                else self._source_query_page(
                    left_adapter,
                    req.left_dataset_id,
                    req,
                    req.left_where,
                    fields=left_fields,
                    limit=fetch_size,
                    offset=offset,
                )
            )
            rows_fetched += len(page)
            if rows_fetched > req.budget.max_rows:
                raise QueryBudgetExceededError(
                    f"left side scanned {rows_fetched} rows (budget {req.budget.max_rows})",
                    details={
                        "hint": "narrow bbox or add filters; or aggregate on the source"
                    },
                )
            if not page:
                break
            remaining = req.limit - len(joined)
            if (plan.kind == "spatial_join") or (
                plan.kind == "aggregate_join" and req.spatial_op
            ):
                batch = spatial_join_local(
                    page,
                    right_rows,
                    spatial_op=req.spatial_op or "within",
                    join_field_right=req.join_field_right,
                    budget=budget,
                    max_output=remaining + 1,  # +1 探测是否还有更多（R4-C2）
                    spatial_index=spatial_index,
                )
            else:
                # V5：本页键集对右侧行做半连接约减（等值内连接语义不变；
                # 键集过大/全 None 时诚实放弃原样返回）。
                reduced, original = _semi_join_reduce_right(page, right_rows, plan)
                if reduced is not right_rows:
                    semi_join_stats.append(
                        {
                            "left_row_offset": offset,
                            "right_rows_before": original,
                            "right_rows_after": len(reduced),
                        }
                    )
                    right_effective = reduced
                else:
                    right_effective = right_rows
                batch = attribute_join_local(
                    page,
                    right_effective,
                    join_field_left=req.join_field_left or "",
                    join_field_right=req.join_field_right or "",
                    budget=budget,
                    max_output=remaining + 1,
                )
            joined.extend(batch)
            if len(page) < fetch_size:
                break  # 真实末页（按本次 fetch_size 判定，R2-C3）
            offset += len(page)

        if plan.kind == "aggregate_join" and req.aggregates:
            rows = aggregate_join_rows(joined, req.aggregates, req.group_by_right or [])
            return self._result(
                plan,
                rows,
                started,
                strategy=plan.strategy,
                rows_fetched=rows_fetched,
                joined_rows=len(joined),
                semi_join_stats=semi_join_stats,
            )
        # plain join：剥除内部键后返回
        for row in joined:
            row.pop("__right_geometry__", None)
        return self._result(
            plan,
            joined[: req.limit],
            started,
            strategy=plan.strategy,
            rows_fetched=rows_fetched,
            semi_join_stats=semi_join_stats,
        )

    def _source_query_page(
        self, adapter, dataset_id, req, side_where, *, fields, limit, offset
    ):
        extras: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "deadline_s": req.budget.deadline_s,
            "max_rows": req.budget.max_rows,
        }
        if fields:
            extras["fields"] = fields
        if side_where is not None:
            extras["where"] = side_where
        if req.bbox:
            extras["bbox"] = req.bbox
        result = adapter.query(dataset_id, QuerySpec(**extras))
        return result.features or []

    def _result(
        self,
        plan,
        rows,
        started,
        *,
        strategy,
        rows_fetched,
        joined_rows=None,
        semi_join_stats=None,
    ):
        out = {
            "status": "success",
            "plan": plan.to_dict(),
            "strategy": strategy,
            "rows": rows,
            "row_count": len(rows),
            "joined_row_count": joined_rows,
            "rows_fetched": rows_fetched,
            "pushdown_ratio": (
                round(len(rows) / rows_fetched, 6) if rows_fetched else None
            ),
            "execution_duration_s": round(time.monotonic() - started, 4),
            "warnings": plan.warnings,
        }
        if semi_join_stats:
            # V5 additive：两源半连接约减披露（与链式结果同一字段名）。
            out["semi_join_reduction"] = semi_join_stats
        return out


# ── N 源有界链式联邦（V3 additive，ADR-0096 D3；两源 API 保持原样）──────────

#: 链式联邦的源数硬上限（左深链，绝不组合枚举）。
MAX_FEDERATED_SOURCES = 4


@dataclass
class ChainSource:
    """链中的一个源。``estimated_rows`` 是成本排序提示（诚实可未知）。

    ``srs``（可选）声明该源几何的 CRS（如 ``"EPSG:4326"``）。V3 链不做
    在线坐标变换：≥2 个互不相同的非空 srs 在**计划期**即 typed fail-fast。
    """

    source_id: str
    dataset_id: str
    where: Optional[Any] = None
    fields: Optional[List[str]] = None
    estimated_rows: Optional[int] = None
    srs: Optional[str] = None
    # ── V7（ADR-0119 additive）──
    #: 源类型（如 "postgis"）：驱动静态 capability 注入（聚合下推证明、
    #: 下推边界披露）。可选；缺省 = 能力未知（保守，不下推）。
    source_type: Optional[str] = None


@dataclass
class ChainSourceStats:
    """源的统计提示（ADR-0101 D7）：驱动有界序枚举与 join 基数估计。

    全部可缺省 —— 没有统计时行为与 V3 排序逐位一致。
    """

    estimated_rows: Optional[int] = None
    column_ndv: Optional[Dict[str, int]] = None  # 列名 → 近似基数
    # ── V7（ADR-0119 W9）：measured 级唯一键声明（启用安全聚合下推证明；
    # 调用方对声明真实性负责 —— 估计 NDV 不作数）。──
    unique_keys: Optional[List[str]] = None
    # ── V8（ADR-0130 additive）：探测后能力覆盖（AdapterCapabilitiesV2）。
    # 由 FabricRuntime 在规划前填充（IO 收敛在 runtime，planner 保持纯函数）；
    # None = 静态默认矩阵。──
    caps: Optional[Any] = None

    def ndv(self, column: Optional[str]) -> Optional[int]:
        if column is None or not self.column_ndv:
            return None
        v = self.column_ndv.get(column)
        return v if isinstance(v, int) and v >= 1 else None


@dataclass
class ChainJoin:
    """累积左侧行与下一个源的连接。语义与两源 plan 一致。

    ``aggregate_join``（F2 修正语义）：链上聚合**先**按 ``join_field_left``
    /``join_field_right`` 将累积行与右源等值连接（右源行进入 ``__right__``），
    **再**按 ``group_by_right``（右源字段）分组聚合 —— 与两源
    ``_execute_local`` 的 aggregate_join 流程一致；跳过连接会让 ``right_rows``
    被静默丢弃、分组键全部落空（单一全 None 组）。

    V4（ADR-0101 D7）：``left_source_id``/``right_source_id`` 可选显式寻址
    —— 全部 join 都给出 id 对时，连接随成本序**一起重排**（序枚举才安全）；
    缺省保持 V3 位置寻址语义逐位不变。
    """

    kind: str  # attribute_join | spatial_join | aggregate_join
    join_field_left: Optional[str] = None  # 累积行的键（顶层，或 "__right__.x"）
    join_field_right: Optional[str] = None
    spatial_op: Optional[str] = None  # within | intersects
    group_by_right: Optional[List[str]] = None
    aggregates: Optional[List[Dict[str, Any]]] = None
    left_source_id: Optional[str] = None
    right_source_id: Optional[str] = None


@dataclass
class FederatedChainRequest:
    """N 源（2..MAX_FEDERATED_SOURCES）左深链式联邦请求。"""

    sources: List[ChainSource] = field(default_factory=list)
    joins: List[ChainJoin] = field(default_factory=list)
    bbox: Optional[List[float]] = None
    limit: int = 10_000
    order_strategy: str = "cost"  # cost | given | cost_stats
    budget: ExecutionBudget = field(
        default_factory=lambda: ExecutionBudget(**FEDERATION_BUDGET.model_dump())
    )
    warnings: List[str] = field(default_factory=list)
    # ---- V4 additive（ADR-0101 D7）----
    #: source_id → 统计提示；``order_strategy="cost_stats"`` 时驱动有界
    #: 序枚举（≤24 候选，绝不指数），无提示时自动回落 V3 排序。
    stats_hints: Optional[Dict[str, ChainSourceStats]] = None
    #: 为各源派生最小投影字段。V5（Wave 9）起默认开启（审计 06 §6.2 步骤 4；
    #: 奇偶校验由既有链测试锁定，几何不变量守卫防止空间跳端点被裁剪成
    #: 静默空结果）；``derive_projection=False`` 显式退出（输出形状复原）。
    derive_projection: bool = True
    #: 执行引擎（V6 additive，ADR-0118）：``"v5"``（默认，位级不变）或
    #: ``"v6"``（cost-based 枚举 + 流式批执行；非 typed 异常回退 v5）。
    engine: str = "v5"
    # ── V7（ADR-0119 W12 additive）──
    #: 会话 owner（结果缓存作用域；None = 全局域 → 缓存禁入，R-M2）。
    session_owner: Optional[str] = None
    #: 结果缓存开关（默认开：命中必披露 + fingerprint 失效 + TTL 有界）。
    use_cache: bool = True
    # ── V8（ADR-0130 additive）：治理面富集披露（source_id → basis dict）。
    # FabricRuntime 填充：rows_basis（request_hint | source_facts:<basis>
    # [×feedback:<factor>(samples=N)]）、caps_basis（probed|default）、
    # governed。EXPLAIN 如实渲染；None = 无富集（行为与 V7 逐位一致）。
    estimate_basis: Optional[Dict[str, Dict[str, Any]]] = None


def _chain_budget(req: FederatedChainRequest) -> ExecutionBudget:
    """显式 ``budget=None`` 会击穿 dataclass ``default_factory`` —— 回落联邦默认。"""
    if req.budget is None:
        return ExecutionBudget(**FEDERATION_BUDGET.model_dump())
    return req.budget


def _chain_order_indices(req: FederatedChainRequest) -> List[int]:
    """左深链成本排序（``estimated_rows`` 提示升序，None 视为最大；稳定）。

    ``order_strategy="given"`` 时返回恒等序。plan 与 execute 共用，杜绝两份
    排序实现漂移。
    """
    order = list(range(len(req.sources)))
    if req.order_strategy in ("cost", "cost_stats"):
        order.sort(
            key=lambda i: (
                req.sources[i].estimated_rows is None,
                req.sources[i].estimated_rows or 0,
                i,
            )
        )
    return order


#: 序枚举候选硬上限（N ≤ MAX_FEDERATED_SOURCES=4 → ≤24；结构性有界）。
MAX_ORDER_CANDIDATES = 24

#: 无统计时的行数占位（保持与 V3「None 视为最大」同一方向）。
_UNESTIMATED_ROWS = 1_000_000


def _chain_join_cardinality(
    left_est: Optional[int],
    right_est: Optional[int],
    ndv_left: Optional[int],
    ndv_right: Optional[int],
) -> int:
    """join 基数估计：|A⋈B| ≈ |A|·|B| / max(ndv_A, ndv_B, 1)。

    两侧 ndv 都未知 → 乘积（与「无信息 = 最坏方向」一致）；有任一侧
    ndv → 标准除数模型。全部取整上界，绝不输出负数/零。
    """
    a = left_est if left_est is not None else _UNESTIMATED_ROWS
    b = right_est if right_est is not None else _UNESTIMATED_ROWS
    ndv = max(ndv_left or 1, ndv_right or 1)
    return max(1, (a * b) // ndv)


def _chain_order_cost(
    order: List[int], req: FederatedChainRequest, id_joins: Dict[str, int]
) -> int:
    """左深序的相对成本：Σ 源传输行 + Σ 跳基数（stats 提示可用时）。

    ``id_joins`` 把 ``左source_id→右source_id`` 映射到 join 下标（id 寻址
    时成本才能跟随源对）；位置寻址的请求只能用传输行 + 行数提示近似。
    """
    sources = req.sources
    stats = req.stats_hints or {}
    total = sum(
        (
            sources[i].estimated_rows
            if sources[i].estimated_rows is not None
            else _UNESTIMATED_ROWS
        )
        for i in order
    )
    acc = (
        sources[order[0]].estimated_rows
        if sources[order[0]].estimated_rows is not None
        else _UNESTIMATED_ROWS
    )
    for pos in range(len(order) - 1):
        left_id, right_id = (
            sources[order[pos]].source_id,
            sources[order[pos + 1]].source_id,
        )
        j = id_joins.get(f"{left_id}>{right_id}")
        if j is None:
            ndv_l = ndv_r = None
        else:
            join = req.joins[j]
            lst, rst = stats.get(left_id), stats.get(right_id)
            ndv_l = lst.ndv(join.join_field_left) if lst else None
            ndv_r = rst.ndv(join.join_field_right) if rst else None
        right_est = sources[order[pos + 1]].estimated_rows
        card = _chain_join_cardinality(acc, right_est, ndv_l, ndv_r)
        total += card
        acc = card
    return total


def _chain_plan_order(req: FederatedChainRequest) -> tuple:
    """计划序：``cost_stats`` 时做有界枚举（≤24 候选），否则 V3 排序。

    返回 ``(order, rejected_orders, order_warning)``；``rejected_orders``
    是按成本升序的落选替代（EXPLAIN 证据，≤3 条）。无统计提示时枚举
    自动回落 V3 排序 —— 行为与 V3 逐位一致。
    """
    id_joins = {
        f"{j.left_source_id}>{j.right_source_id}": i
        for i, j in enumerate(req.joins)
        if j.left_source_id and j.right_source_id
    }
    if req.order_strategy != "cost_stats" or not req.stats_hints:
        return _chain_order_indices(req), [], None
    import itertools

    n = len(req.sources)
    # islice 结构性封顶（评审 MINOR：n 上限将来放宽也不会失去界限）。
    candidates = list(
        itertools.islice(itertools.permutations(range(n)), MAX_ORDER_CANDIDATES)
    )
    if not id_joins:
        # 位置寻址的 join 无法安全跟随重排 —— 诚实回落 V3 排序并披露。
        warning = (
            "cost_stats requested but joins are positional; falling back to "
            "estimated_rows ordering (declare left/right_source_id on joins "
            "to enable bounded enumeration)"
        )
        return _chain_order_indices(req), [], warning

    # 评审 MAJOR：成本枚举只在**可成链**的排列上选优 —— 否则统计变化会
    # 让原本可执行的请求突然 typed 失败（最便宜序未必 join-连通）。
    def _connected(p) -> bool:
        return all(
            f"{req.sources[p[i]].source_id}>{req.sources[p[i + 1]].source_id}"
            in id_joins
            for i in range(n - 1)
        )

    connected = [p for p in candidates if _connected(p)]
    if not connected:
        # 无任何连通排列 → 保持 V3 排序，由 _map_chain_joins_to_order 给出
        # typed 失败（诚实、可行动）。
        return _chain_order_indices(req), [], None
    scored = sorted(
        ((_chain_order_cost(list(p), req, id_joins), p) for p in connected),
        key=lambda t: (t[0], t[1]),
    )
    best_cost, best = scored[0]
    rejected = [
        {"order": [req.sources[i].source_id for i in p], "cost": c}
        for c, p in scored[1:4]
    ]
    warning = (
        f"join order chosen by bounded enumeration ({len(scored)} candidates, "
        f"cost={best_cost}) using stats hints"
    )
    return list(best), rejected, warning


def _map_chain_joins_to_order(
    req: FederatedChainRequest, order: List[int], ordered_sources: List[ChainSource]
) -> List[ChainJoin]:
    """把 joins 映射到有序链：id 寻址跟随源对重排；位置寻址保持 V3 语义。"""
    id_joins = {
        f"{j.left_source_id}>{j.right_source_id}": j
        for j in req.joins
        if j.left_source_id and j.right_source_id
    }
    if not id_joins:
        return list(req.joins)  # V3 位置语义（逐位不变）
    mapped: List[ChainJoin] = []
    for pos in range(len(ordered_sources) - 1):
        key = f"{ordered_sources[pos].source_id}>{ordered_sources[pos + 1].source_id}"
        join = id_joins.get(key)
        if join is None:
            raise FederatedQueryError(
                f"id-addressed joins do not form a chain over the planned order: "
                f"missing join {key} at hop {pos}; use order_strategy='given' or "
                "fix the declared source id pairs",
                details={"hop": pos, "expected": key},
            )
        mapped.append(join)
    return mapped


def derive_chain_fields(
    req: FederatedChainRequest,
    ordered_sources: List[ChainSource],
    ordered_joins: List[ChainJoin],
) -> Dict[str, List[str]]:
    """为各源派生最小必要字段（ADR-0101 D7，opt-in ``derive_projection``）。

    只包含可**证明**需要的字段：两侧连接键、聚合分组/聚合字段、源 where
    过滤引用的字段（M1：过滤器在投影裁剪后仍须可求值 —— 过滤字段缺了
    会静默改变查询语义）、以及下一跳左键（经 F1 提升必须存在于上一跳
    右属性里）。首源额外保留下一跳左键。空间跳不能裁剪（几何承载不变
    量 F3），诚实返回空投影（= 不投影）。派生是输出形状变更 —— 由调用
    方显式 opt-in。
    """
    from app.services.data_fabric.query.predicates import (
        iter_fields,
        predicate_from_dict,
    )

    required: Dict[str, set] = {s.source_id: set() for s in ordered_sources}
    # M1（审计 round1）：源的本地 where 过滤字段是可证明必要的 —— 投影
    # 裁掉过滤字段会让远端/本地过滤静默失真。dict 形式经 predicate AST
    # 解析后提取；字符串形式无法可靠解析 → 不猜测（宁可多取）；AST 解析
    # 失败的源整体退出派生（绝不带着未知过滤字段做裁剪）。
    unprovable: set = set()
    for s in ordered_sources:
        w = s.where
        if w is None:
            continue
        if isinstance(w, dict):
            try:
                required[s.source_id].update(iter_fields(predicate_from_dict(w)))
            except Exception:
                unprovable.add(s.source_id)
        elif not isinstance(w, str) and getattr(w, "op", None):
            required[s.source_id].update(iter_fields(w))
        else:
            # 字符串/未知形状：无法可靠解析 → 该源整体退出派生（宁可多取，
            # 绝不带着未知过滤字段做裁剪）。
            unprovable.add(s.source_id)
    # 评审 CRITICAL：参与空间跳的源**绝不投影** —— 空间连接的右侧行必须
    # 带几何（spatial_join_local 对无几何右行静默跳过）；仅按属性需求
    # 推导会在「属性跳后接空间跳」的链里把几何裁没（成功 0 行的静默
    # 错答）。空间跳两端源整段排除在派生之外（宁可多取，绝不缺几何）。
    spatial_sources: set = set()
    for pos, join in enumerate(ordered_joins):
        if join.kind == "spatial_join":
            spatial_sources.add(ordered_sources[pos].source_id)
            spatial_sources.add(ordered_sources[pos + 1].source_id)
    for pos, join in enumerate(ordered_joins):
        left_id = ordered_sources[pos].source_id
        right_id = ordered_sources[pos + 1].source_id
        if join.kind == "attribute_join":
            required[left_id].add(join.join_field_left)
            required[right_id].add(join.join_field_right)
            # 下一跳左键必须能从本跳右侧属性提升（F1）。
            if pos + 1 < len(ordered_joins):
                nxt = ordered_joins[pos + 1]
                if (
                    nxt.kind in ("attribute_join", "aggregate_join")
                    and nxt.join_field_left
                ):
                    required[right_id].add(nxt.join_field_left)
        elif join.kind == "aggregate_join":
            required[left_id].add(join.join_field_left)
            required[right_id].add(join.join_field_right)
            required[right_id].update(join.group_by_right or [])
            for agg in join.aggregates or []:
                field = agg.get("field")
                if field:
                    required[right_id].add(field)
            if pos + 1 < len(ordered_joins):
                nxt = ordered_joins[pos + 1]
                if (
                    nxt.kind in ("attribute_join", "aggregate_join")
                    and nxt.join_field_left
                ):
                    required[right_id].add(nxt.join_field_left)
        else:  # spatial_join：几何承载不变量，不裁剪
            required[left_id] = set()
            required[right_id] = set()
    out: Dict[str, List[str]] = {}
    for s in ordered_sources:
        if s.source_id in spatial_sources or s.source_id in unprovable:
            continue  # 空间跳端点/过滤不可解析：不派生投影（安全优先）
        fields = sorted(f for f in required.get(s.source_id, set()) if f)
        if fields and s.fields is None:
            out[s.source_id] = fields
    return out


def validate_chain_shape(
    req: FederatedChainRequest, *, check_crs_mix: bool = True
) -> None:
    """链请求结构校验（V5/V6 单一真相；M1，评审 R1）。

    V6 的 ``execute_chain_v6`` 以 ``check_crs_mix=False`` 调用 —— 混 CRS 在
    V6 是可执行的（计划内变换，ADR-0118），其余契约（limit/join 数/join
    字段/spatial_op/group_by）两引擎逐字一致。
    """
    if len(req.sources) < 2:
        raise FederatedQueryError("chain federation requires at least 2 sources")
    if len(req.sources) > MAX_FEDERATED_SOURCES:
        raise FederatedQueryError(
            f"chain federation supports at most {MAX_FEDERATED_SOURCES} sources "
            f"(got {len(req.sources)}); bounded planning is a V3 red line"
        )
    if len(req.joins) != len(req.sources) - 1:
        raise FederatedQueryError(
            f"chain requires exactly len(sources)-1 joins "
            f"({len(req.joins)} given for {len(req.sources)} sources)"
        )
    if check_crs_mix:
        # F4：链内 CRS 一致性 —— V3 链不做在线坐标变换，混用即计划期 typed 失败
        srs_values = {src.srs for src in req.sources if src.srs}
        if len(srs_values) > 1:
            raise FederatedQueryError(
                f"chain sources mix CRS: {sorted(srs_values)}; reproject the sources "
                "upstream or declare a single srs (no on-the-fly transform in V3 chains)"
            )
    for i, join in enumerate(req.joins):
        if join.kind not in ("attribute_join", "spatial_join", "aggregate_join"):
            raise FederatedQueryError(f"joins[{i}].kind {join.kind!r} unsupported")
        if join.kind == "attribute_join" and not (
            join.join_field_left and join.join_field_right
        ):
            raise FederatedQueryError(
                f"joins[{i}] attribute join needs both join fields"
            )
        if join.kind == "spatial_join" and join.spatial_op not in (
            "within",
            "intersects",
        ):
            raise FederatedQueryError(
                f"joins[{i}] spatial join needs within|intersects"
            )
        if join.kind == "aggregate_join":
            if not join.group_by_right:
                raise FederatedQueryError(
                    f"joins[{i}] aggregate join needs group_by_right"
                )
            # F2：链上聚合先连接后聚合 —— 必须给出连接字段，否则右源无法并入
            if not (join.join_field_left and join.join_field_right):
                raise FederatedQueryError(
                    f"joins[{i}] aggregate join needs both join fields "
                    "(chain aggregation groups over the joined right rows)"
                )
    budget = _chain_budget(req)
    if req.limit > budget.max_rows:
        raise QueryBudgetExceededError(
            f"chain limit {req.limit} exceeds budget {budget.max_rows}",
            details={
                "hint": "reduce limit, add bbox/filters, or aggregate per source"
            },
        )


def plan_federated_chain(req: FederatedChainRequest) -> List[FederatedPlan]:
    """校验并产出左深链计划（成本排序；纯函数，无 IO，不改写请求）。

    排序按 ``estimated_rows`` 提示升序（小表建侧/先物化，None 视为最大），
    稳定排序保证同序输入的确定性。fail-fast：limit/CRS 混用/join 数等预算
    与结构先检。planner 产生的 warnings 附在 ``plans[0].warnings`` 返回
    （不追加到 ``req.warnings``——本函数文档约定为纯函数）。
    """
    validate_chain_shape(req, check_crs_mix=True)

    order, rejected_orders, order_warning = _chain_plan_order(req)
    chain_warnings: List[str] = []
    if order_warning:
        chain_warnings.append(order_warning)
    elif req.order_strategy == "cost_stats":
        pass  # 枚举路径的证据已含在 enumeration warning 里，不重复追加
    if req.order_strategy in ("cost", "cost_stats"):
        if req.sources[order[0]].estimated_rows is None:
            chain_warnings.append(
                "join order uses given order (no estimated_rows hints available); "
                "estimates are assumptions"
            )
        else:
            chain_warnings.append(
                "join ordered by estimated_rows hints (cost-based, left-deep)"
            )
    elif req.order_strategy == "given":
        chain_warnings.append(
            "join order uses given order (cost hints ignored); "
            "ordering is an assumption"
        )

    # 按排序重排 sources/joins，join[i] 连接累积行与 sources[i+1]
    ordered_sources = [req.sources[i] for i in order]
    ordered_joins = _map_chain_joins_to_order(req, order, ordered_sources)
    derived_fields = (
        derive_chain_fields(req, ordered_sources, ordered_joins)
        if req.derive_projection
        else {}
    )
    if derived_fields:
        chain_warnings.append(
            "minimal projection derived per source (derive_projection=true): "
            + json.dumps(derived_fields, ensure_ascii=False, sort_keys=True)
        )
    plans: List[FederatedPlan] = []
    for i, join in enumerate(ordered_joins):
        plans.append(
            FederatedPlan(
                kind=join.kind,
                left={
                    "source_id": ordered_sources[i].source_id,
                    "dataset_id": ordered_sources[i].dataset_id,
                    "chain_position": i,
                    **(
                        {"fields": derived_fields[ordered_sources[i].source_id]}
                        if ordered_sources[i].source_id in derived_fields
                        else {}
                    ),
                },
                right={
                    "source_id": ordered_sources[i + 1].source_id,
                    "dataset_id": ordered_sources[i + 1].dataset_id,
                    "chain_position": i + 1,
                    **(
                        {"fields": derived_fields[ordered_sources[i + 1].source_id]}
                        if ordered_sources[i + 1].source_id in derived_fields
                        else {}
                    ),
                },
                join_field_left=join.join_field_left,
                join_field_right=join.join_field_right,
                spatial_op=join.spatial_op,
                group_by_right=join.group_by_right,
                aggregates=join.aggregates,
                estimated_left_rows=ordered_sources[i].estimated_rows,
                estimated_right_rows=ordered_sources[i + 1].estimated_rows,
                warnings=chain_warnings if i == 0 else [],
                rejected_orders=rejected_orders if i == 0 else [],
            )
        )
    return plans


def _chain_row_key(row: Dict[str, Any], field: str) -> Any:
    """链行取键（形状感知）。

    - 首跳累积行是源特性：属性在 ``properties`` 下，优先取键；
    - 后续跳累积行是扁平属性 dict：顶层优先，回退 ``__right__``
      携带的上一跳右侧属性（F1）。
    """
    props = row.get("properties")
    if isinstance(props, dict) and field in props:
        return props.get(field)
    if field in row:
        return row.get(field)
    right = row.get("__right__")
    if isinstance(right, dict):
        return right.get(field)
    return None


def _chain_lift_next_join_key(
    rows: List[Dict[str, Any]], next_join: "ChainJoin"
) -> None:
    """F1：把下一跳需要的连接键提升到累积行顶层（仅当顶层缺失；左字段优先）。

    上一跳的右属性嵌在 ``__right__`` 下；下一跳 attribute/aggregate 连接的
    ``join_field_left`` 若只存在于 ``__right__``，提升到顶层后连接才能命中，
    且对后续投影/过滤顶层可寻址。原地修改——行是本跳 join 新建的 dict。
    """
    if next_join is None or next_join.kind not in ("attribute_join", "aggregate_join"):
        return
    field = next_join.join_field_left
    if not field:
        return
    for row in rows:
        if field in row:
            continue
        right = row.get("__right__")
        if isinstance(right, dict) and field in right:
            row[field] = right[field]


def _chain_left_features(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把累积行归一为 join 原语的左要素形状。

    首跳的累积行就是源特性（含 ``properties``/``geometry``），原样透传；
    后续跳的累积行是扁平属性 dict，几何（如有）由上一空间跳存于
    ``__left_geometry__``（F3），据此重建可入 STRtree 的左要素。
    """
    feats: List[Dict[str, Any]] = []
    for row in rows:
        props = row.get("properties")
        if isinstance(props, dict):
            feats.append(row)
        else:
            feats.append({"properties": row, "geometry": row.get("__left_geometry__")})
    return feats


#: 半连接键集上限（超过 → 放弃键过滤，整段右侧行照常进入 join）。
SEMI_JOIN_MAX_KEYS = 1000


def _semi_join_reduce_right(
    accumulated: List[Dict[str, Any]],
    right_rows: List[Dict[str, Any]],
    join: "ChainJoin",
) -> tuple:
    """半连接约减（ADR-0101 D7）：右侧行只保留键出现在左侧键集的行。

    等值 join 是内连接 —— 丢掉「键不在左侧键集」的右行不改变结果，
    但显著缩小哈希索引与候选数（本地化的 semi-join reduction）。返回
    ``(reduced_right, original_count)``；键集超上限或全 None → 原样
    返回（诚实放弃，绝不冒语义风险）。
    """
    if not accumulated or not right_rows:
        return right_rows, len(right_rows)
    field = join.join_field_right
    if not field:
        return right_rows, len(right_rows)
    keys = set()
    for lrow in accumulated:
        key = _chain_row_key(lrow, join.join_field_left or field)
        if key is not None:
            keys.add(_hashable_key(key))
        if len(keys) > SEMI_JOIN_MAX_KEYS:
            return right_rows, len(right_rows)  # 键集过大 → 诚实放弃
    if not keys:
        return right_rows, len(right_rows)
    reduced = [
        r
        for r in right_rows
        if _hashable_key((r.get("properties") or r).get(field)) in keys
    ]
    return reduced, len(right_rows)


def execute_federated_chain(
    executor: "FederatedExecutor", req: FederatedChainRequest
) -> Dict[str, Any]:
    """执行左深链（每源一次有界拉取；逐跳 join 预算 fail-fast）。

    与两源执行器共用 ``_source_query`` 的预算/有界语义。中间结果超过
    预算立即抛 ``QUERY_BUDGET_EXCEEDED``（绝无静默截断）。

    链上不变量（V3 正确性修正）：
    - attribute/aggregate 跳的左键经 ``_chain_row_key`` 穿透 ``__right__``
      取键（F1）；每跳结束后把下一跳需要、但仅存于 ``__right__`` 的连接键
      提升到累积行顶层（左字段优先，不覆盖）。
    - aggregate 跳先与右源按 join 字段等值连接、再按右源字段分组聚合（F2），
      与两源 ``_execute_local`` 流程一致。
    - 空间跳把累积行重建为左要素再入 STRtree，几何取自上一空间跳写入的
      ``__left_geometry__``（F3）；无任何几何可携带时 typed 失败，绝不静默 0 行。

    V4（ADR-0101 D7）：
    - 首跳同源（同 source_id）且 adapter 支持 ``server_spatial_join`` →
      服务端快路径；**typed DataFabricError 原样上抛（不回退）**，仅
      非 typed 异常回退本地并记录 warning —— 与两源路径同一语义。
    - attribute/aggregate 跳先做半连接右侧行约减（键集有界）。
    - derive_projection 时按计划的派生字段投影拉取。
    """
    import app.services.data_fabric.query.federation as _self

    started = time.monotonic()
    if req.budget is None:  # F7：显式 None 击穿 default_factory → 回落联邦默认
        req.budget = _chain_budget(req)
    plans = plan_federated_chain(req)
    # F7：直接消费规划器产出的有序计划（left[0] 起头，right[i] 依次为链上
    # 第 i+1 个源），按 source_id 映射回 ChainSource —— 不再本地重排第二份
    # 排序逻辑（与 plan_federated_chain 共享同一份 _chain_plan_order）。
    order_ids = [plans[0].left["source_id"]] + [p.right["source_id"] for p in plans]
    by_id = {s.source_id: s for s in req.sources}
    ordered_sources = [by_id[sid] for sid in order_ids]
    # 有序 joins 从计划重建（计划携带全部 join 语义字段）。
    ordered_joins = [
        ChainJoin(
            kind=p.kind,
            join_field_left=p.join_field_left,
            join_field_right=p.join_field_right,
            spatial_op=p.spatial_op,
            group_by_right=p.group_by_right,
            aggregates=p.aggregates,
        )
        for p in plans
    ]

    # 派生投影（opt-in）：计划里带 "fields" 的源，拉取时按其投影。
    derived_fields: Dict[str, List[str]] = {}
    for p in plans:
        for side in (p.left, p.right):
            if isinstance(side.get("fields"), list):
                derived_fields[side["source_id"]] = side["fields"]

    def _fetch(src: ChainSource) -> List[Dict[str, Any]]:
        adapter = executor._adapter_factory(src.source_id)
        if adapter is None:
            raise FederatedQueryError(
                f"chain source '{src.source_id}' is not connected",
                details={"source_id": src.source_id},
            )
        fields = (
            src.fields if src.fields is not None else derived_fields.get(src.source_id)
        )
        return executor._source_query(
            adapter,
            src.dataset_id,
            _SideView(req, src),
            src.where,
            fields=fields,
            limit=req.limit,
        )

    streaming = StreamingBudget(
        max_rows=req.budget.max_rows,
        max_bytes=req.budget.max_bytes,
        max_vertices=req.budget.max_vertices,
    )
    per_source_rows: List[int] = []
    adapters: List[Any] = []
    semi_join_stats: List[Dict[str, int]] = []
    left_adapter0 = executor._adapter_factory(ordered_sources[0].source_id)
    right_adapter0 = executor._adapter_factory(ordered_sources[1].source_id)
    first_join = ordered_joins[0]
    server_side_first_hop = False
    accumulated: List[Dict[str, Any]] = []
    if (
        ordered_sources[0].source_id == ordered_sources[1].source_id
        and first_join.kind in ("spatial_join", "aggregate_join")
        and left_adapter0 is not None
        and hasattr(left_adapter0, "server_spatial_join")
        and left_adapter0 is right_adapter0
    ):
        try:
            rows = left_adapter0.server_spatial_join(
                ordered_sources[0].dataset_id,
                ordered_sources[1].dataset_id,
                join_op=first_join.spatial_op or "within",
                group_by_polygon_field=(first_join.group_by_right or [None])[0],
                limit=req.limit,
            )
            accumulated = list(rows)
            per_source_rows = [len(rows), len(rows)]
            server_side_first_hop = True
        except DataFabricError:
            raise
        except Exception as e:  # noqa: BLE001 - server join 不可用 → 本地回退
            logger.info(
                "[Federation] chain server-side first hop unavailable, "
                "falling back to local: %s",
                e,
            )
            plans[0].warnings.append(
                f"server-side first hop failed ({e}); local execution used"
            )
    if not server_side_first_hop:
        for src in ordered_sources:
            feats = _fetch(src)
            per_source_rows.append(len(feats))
            adapters.append(feats)
        accumulated = adapters[0]
    else:
        # 首跳已在服务端完成：占位 [None, None] 保持 adapters[i] 与
        # ordered_sources[i] 的对齐不变量，仅拉取链上剩余源。
        adapters = [None, None]
        for src in ordered_sources[2:]:
            feats = _fetch(src)
            per_source_rows.append(len(feats))
            adapters.append(feats)

    joined_total = len(accumulated) if server_side_first_hop else 0
    hops = ordered_joins[1:] if server_side_first_hop else ordered_joins
    hop_offset = 1 if server_side_first_hop else 0

    for i, join in enumerate(hops):
        src_pos = i + hop_offset
        right_rows = adapters[src_pos + 1]
        if join.kind == "attribute_join":
            reduced, original = _semi_join_reduce_right(accumulated, right_rows, join)
            if reduced is not right_rows:
                semi_join_stats.append(
                    {
                        "hop": src_pos,
                        "right_rows_before": original,
                        "right_rows_after": len(reduced),
                    }
                )
                right_rows = reduced
            # F1：左键经 _chain_row_key 穿透（顶层优先，回退上一跳 __right__）
            accumulated = _self.attribute_join_local(
                accumulated,
                right_rows,
                join_field_left=str(join.join_field_left),
                join_field_right=str(join.join_field_right),
                budget=streaming,
                max_output=req.budget.max_rows,
                left_key_resolver=_chain_row_key,
            )
        elif join.kind == "spatial_join":
            # F3：累积行重建为左要素（几何来自上一空间跳的 __left_geometry__）
            left_features = _chain_left_features(accumulated)
            if src_pos > 0 and not any(
                isinstance(f.get("geometry"), dict) for f in left_features
            ):
                raise FederatedQueryError(
                    f"chain spatial join {src_pos} has no left geometry: the preceding hop "
                    "did not carry one",
                    details={
                        "hint": "make the preceding hop a spatial join (it stores "
                        "__left_geometry__), or reproject/carried geometry "
                        "before the chain"
                    },
                )
            accumulated = _self.spatial_join_local(
                left_features,
                right_rows,
                spatial_op=join.spatial_op or "within",
                budget=streaming,
                max_output=req.budget.max_rows,
                carry_left_geometry=True,
            )
        else:  # aggregate_join
            reduced, original = _semi_join_reduce_right(accumulated, right_rows, join)
            if reduced is not right_rows:
                semi_join_stats.append(
                    {
                        "hop": src_pos,
                        "right_rows_before": original,
                        "right_rows_after": len(reduced),
                    }
                )
                right_rows = reduced
            # F2：先连接（右源行进入 __right__），再按右源字段分组聚合 ——
            # 否则 right_rows 被丢弃、分组键全部落空（单一全 None 组）
            joined = _self.attribute_join_local(
                accumulated,
                right_rows,
                join_field_left=str(join.join_field_left or ""),
                join_field_right=str(join.join_field_right or ""),
                budget=streaming,
                max_output=req.budget.max_rows,
                left_key_resolver=_chain_row_key,
            )
            accumulated = _self.aggregate_join_rows(
                joined,
                join.aggregates or [],
                join.group_by_right or [],
            )
        joined_total = len(accumulated)
        if joined_total > req.budget.max_rows:
            raise QueryBudgetExceededError(
                f"chain join {src_pos} produced {joined_total} rows "
                f"(budget {req.budget.max_rows}); fail-fast stops the chain",
                details={
                    "hint": "filter sources harder, or aggregate before joining",
                    "per_source_rows": per_source_rows,
                },
            )
        # F1：为下一跳提升仅存于 __right__ 的连接键到顶层（原地；左字段优先）
        if i + 1 < len(hops):
            _chain_lift_next_join_key(accumulated, hops[i + 1])

    final_rows = accumulated[: req.limit]
    rows_fetched = sum(per_source_rows)
    result = {
        "status": "success",
        "strategy": "server_side_first_hop"
        if server_side_first_hop
        else "left_deep_chain",
        "order": [s.source_id for s in ordered_sources],
        "rows": final_rows,
        "row_count": len(final_rows),
        "rows_fetched": rows_fetched,
        "joined_row_count": joined_total,
        "per_source_rows": dict(
            zip((s.source_id for s in ordered_sources), per_source_rows)
        ),
        "plans": [p.to_dict() for p in plans],
        "pushdown_ratio": (
            round(len(final_rows) / rows_fetched, 6) if rows_fetched else None
        ),
        "execution_duration_s": round(time.monotonic() - started, 4),
        "warnings": plans[0].warnings,  # F7：planner 收集的 warnings 随计划返回
    }
    if semi_join_stats:
        result["semi_join_reduction"] = semi_join_stats
    return result


class _SideView:
    """把 ChainSource 适配到两源 ``_source_query`` 的 req 形状（仅 bbox/budget）。"""

    def __init__(self, req: FederatedChainRequest, src: ChainSource):
        self.bbox = req.bbox
        self.budget = req.budget
        self.limit = req.limit


def chain_explain_lines(result: Dict[str, Any], *, max_hops: int = 3) -> List[str]:
    """链式联邦结果的有界 explain 行（工具/REST 证据投影；确定性顺序）。

    只复述执行结果里已有的量（order/strategy/行数/跳计划/半连接约减/
    警告），绝不引入第二份决策；行数有界（≤ 4 + max_hops×2 + 警告 3）。
    """
    lines: List[str] = [
        f"Strategy: {result.get('strategy')}",
        "Order: " + " -> ".join(result.get("order") or []),
        (
            f"Rows: {result.get('row_count')} (joined={result.get('joined_row_count')}, "
            f"fetched={result.get('rows_fetched')})"
        ),
    ]
    for sid, n in (result.get("per_source_rows") or {}).items():
        lines.append(f"Source {sid}: {n} rows")
    plans = result.get("plans") or []
    for i, p in enumerate(plans[:max_hops]):
        left = (p.get("left") or {}).get("source_id", "?")
        right = (p.get("right") or {}).get("source_id", "?")
        fields = (p.get("right") or {}).get("fields")
        hop = f"Hop {i + 1}: {p.get('kind')} {left} -> {right}"
        if fields:
            hop += f" (projected: {','.join(fields)})"
        lines.append(hop)
    if len(plans) > max_hops:
        lines.append(f"... {len(plans) - max_hops} more hops")
    reduction = result.get("semi_join_reduction") or []
    if reduction:
        saved = sum(
            max(0, r.get("right_rows_before", 0) - r.get("right_rows_after", 0))
            for r in reduction
        )
        lines.append(
            f"Semi-join reduction: {len(reduction)} hop(s), {saved} right rows skipped"
        )
    for w in (result.get("warnings") or [])[:3]:
        lines.append(f"Warning: {w}")
    return lines


def execute_chain(req: FederatedChainRequest, *, adapter_factory) -> Dict[str, Any]:
    """模块级便捷入口：executor-free 链式执行。"""
    return execute_federated_chain(FederatedExecutor(adapter_factory), req)


__all__ = [
    "FederatedQueryRequest",
    "FederatedPlan",
    "FederatedExecutor",
    "plan_federated",
    "spatial_join_local",
    "attribute_join_local",
    "aggregate_join_rows",
    "FEDERATION_BUDGET",
    "MAX_JOIN_CANDIDATES",
    "MAX_FEDERATED_SOURCES",
    "ChainSource",
    "ChainJoin",
    "FederatedChainRequest",
    "plan_federated_chain",
    "execute_federated_chain",
    "execute_chain",
    "chain_explain_lines",
]


# ── V6 引擎（ADR-0118 W10）：cost-based 枚举 + 流式批执行 ──────────────────


def _make_bushy_replan_fn(req, original_plan):
    """V7（ADR-0119 W10）：bushy 整树重排回调（观测行数 pinned 后重枚举）。

    - 以观测行数覆盖各源 ``estimated_rows`` → 同一 planner 纯函数重枚举；
    - R1-M7 **同基准比较**：previous_cost = 原序在 **pinned 估计下**的链形
      重估成本（_enumerate_fixed_chain）—— 绝不用未 pinned 的旧成本跨基准
      比较；
    - 新计划必须 hash 不同**且 pinned 成本严格更低**才切换；
    - ``order_strategy="given"`` 上游已禁用 adaptive（双保险）。
    """
    from copy import replace as _dataclass_replace

    from app.services.data_fabric.query.federated.planner import (
        build_enumeration_context,
        plan_federation_v6,
    )
    from app.services.data_fabric.query.federated.enumerator import (
        _enumerate_fixed_chain,
    )

    def _pinned_req(actual_rows):
        pinned = dict(actual_rows or {})
        return _dataclass_replace(
            req,
            sources=[
                _dataclass_replace(
                    s,
                    estimated_rows=(
                        int(pinned[s.source_id])
                        if s.source_id in pinned and pinned[s.source_id]
                        else s.estimated_rows
                    ),
                )
                for s in req.sources
            ],
        )

    def _replan(actual_rows):
        pinned_req = _pinned_req(actual_rows)
        new_plan = plan_federation_v6(pinned_req)
        # 同基准基线：原序（planned order）在 pinned 估计下的成本。
        try:
            ctx_pinned = build_enumeration_context(pinned_req)
            baseline = _enumerate_fixed_chain(
                ctx_pinned, list(original_plan.order), positional=False
            )
            previous_cost = baseline.cost
        except Exception:  # noqa: BLE001 - 基线不可估 → 不切换（保守）
            return None
        try:
            new_plan.previous_cost = previous_cost
        except Exception:  # noqa: BLE001
            pass
        return new_plan

    return _replan


# ── V8（ADR-0130）：治理面 → 规划输入的自适应闭环 ────────────────────────────


def enrich_request_from_runtime(
    req: "FederatedChainRequest",
    resolved_by_sid: Dict[str, Any],
) -> None:
    """把 FabricRuntime 富集结果（探测/事实/反馈）拉平为规划纯数据提示。

    职责（Phase C+D 闭环收口 —— 此前全部为孤儿库）：
    - ``ChainSource.source_type`` ← registry record（激活静态能力注入：
      下推边界披露 + 聚合下推证明资格 —— 此前工具路径恒为 None=保守不下推）；
    - ``ChainSourceStats.caps`` ← 探测覆盖（probed 才注入；default/stale
      不注入 —— 绝不把未验证能力当真）；
    - ``estimated_rows`` 缺省时 ← SourceFacts 行数事实（exact/observed/estimate
      basis 如实标注）× 反馈衰减修正因子（仅 ok 观测、半衰加权、样本≥3）；
      显式提示永不覆盖（调用方明确性优先）；
    - ``ChainSourceStats.column_ndv`` ← 事实 NDV（测量来源；已有提示不覆盖）；
    - ``req.estimate_basis`` 披露段（EXPLAIN 渲染；无富集 = None = V7 行为）。

    全程 fail-open：catalog/runtime/事实任一缺失 → 跳过该源（宁缺毋假）。
    """
    from app.services.data_fabric.fabric.runtime import get_fabric_runtime
    from app.services.data_fabric.fingerprint import dataset_fingerprint_service
    from app.services.data_fabric.query.capabilities import get_capabilities
    from app.services.data_fabric.spatial_catalog import spatial_catalog_service

    runtime = get_fabric_runtime()
    owner = getattr(req, "session_owner", None)
    basis: Dict[str, Dict[str, Any]] = {}
    hints: Dict[str, Any] = dict(req.stats_hints or {})

    for src in req.sources:
        rs = resolved_by_sid.get(src.source_id)
        if rs is None:
            continue
        entry: Dict[str, Any] = {
            "governed": bool(getattr(rs, "governed", False)),
        }
        # 1) source_type（静态能力激活）。
        if not src.source_type and getattr(rs, "source_type", None):
            src.source_type = rs.source_type
        entry["source_type"] = src.source_type
        # 2) 探测能力覆盖（probed 才可信）。
        if getattr(rs, "caps_basis", None) == "probed" and rs.caps_overrides:
            try:
                base_caps = get_capabilities(str(rs.source_type or ""), rs.caps_overrides)
            except Exception:  # noqa: BLE001 - 未知源类型/非法覆盖 → 静态兜底
                base_caps = None
            if base_caps is not None:
                hint = hints.get(src.source_id)
                if hint is None:
                    hint = ChainSourceStats()
                    hints[src.source_id] = hint
                if getattr(hint, "caps", None) is None:
                    hint.caps = base_caps
            entry["caps_basis"] = "probed"
        elif getattr(rs, "caps_basis", None):
            entry["caps_basis"] = rs.caps_basis
        # 3) 事实 + 反馈 → 行数估计（仅缺省时）。
        descriptor = None
        fingerprint = None
        try:
            descriptor = spatial_catalog_service.get_dataset(src.dataset_id, owner=owner)
            if descriptor is not None:
                fingerprint = dataset_fingerprint_service.calculate_descriptor_fingerprint(
                    descriptor
                )
        except Exception:  # noqa: BLE001 - catalog 缺失 → 无事实富集
            descriptor = None
        runtime.enrich(rs, fingerprint=fingerprint, descriptor=descriptor)
        factor = getattr(rs, "feedback_factor", None)
        if src.estimated_rows is None and getattr(rs, "facts_row_count", None):
            base_rows = int(rs.facts_row_count)
            rows_basis = f"source_facts:{rs.facts_row_count_basis}"
            if factor:
                base_rows = int(round(base_rows * float(factor)))
                rows_basis += (
                    f"×feedback:{factor}(samples={rs.feedback_samples})"
                )
            src.estimated_rows = max(1, base_rows)
            entry["rows_basis"] = rows_basis
        elif src.estimated_rows is not None:
            entry["rows_basis"] = "request_hint"
            if factor and factor != 1.0:
                # 显式提示不覆盖；偏差证据如实披露供调用方自查。
                entry["hint_feedback_drift"] = {
                    "factor": factor, "samples": rs.feedback_samples,
                }
        # 4) 事实 NDV（测量来源；不覆盖显式提示）。
        ndv = getattr(rs, "facts_ndv", None)
        if ndv:
            hint = hints.get(src.source_id)
            if hint is None:
                hint = ChainSourceStats()
                hints[src.source_id] = hint
            if not getattr(hint, "column_ndv", None):
                hint.column_ndv = dict(ndv)
        basis[src.source_id] = entry

    if hints:
        req.stats_hints = hints
    if basis:
        req.estimate_basis = basis


def execute_chain_v6(
    executor: "FederatedExecutor", req: FederatedChainRequest
) -> Dict[str, Any]:
    """``engine="v6"`` 入口：typed 错误原样上抛（与 V5 同错误契约）；
    V6 内部非 typed 异常 → 一次性回退 V5 执行并在 warnings 如实披露。

    同源 server-side 首跳快路径仍委托 V5 执行器（``server_spatial_join``
    是 V5 执行器能力；V6 树路径不重复实现 —— 无第二真相）。
    """
    first_two = [s.source_id for s in req.sources[:2]]
    first_adapter = executor._adapter_factory(first_two[0]) if first_two else None
    same_source_first_hop = (
        len(first_two) == 2
        and first_two[0] == first_two[1]
        and first_adapter is not None
        # m9（评审 R1）：与 V5 同款 adapter **同一实例**判定 —— factory 每次
        # 返回新实例时不误标 server 路径。
        and first_adapter is executor._adapter_factory(first_two[1])
        and req.joins
        and req.joins[0].kind in ("spatial_join", "aggregate_join")
        and hasattr(first_adapter, "server_spatial_join")
    )
    if same_source_first_hop:
        result = execute_federated_chain(executor, req)
        result["engine"] = "v5_server_first_hop"
        result["warnings"] = list(result.get("warnings") or []) + [
            "engine=v6: same-source server-side first hop delegated to V5 executor"
        ]
        return result
    validate_chain_shape(req, check_crs_mix=False)
    # ── V8（ADR-0130）：进程级引擎回退熔断（R2-Mi-4 收口）──
    # 连续 V6 崩溃后直接走 V5（跳过 V6 规划+执行栈，双执行成本归零）；
    # half-open 单 trial 探测 V6 恢复。开启/试验状态如实披露。
    from app.services.data_fabric.fabric.engine_breaker import get_engine_breaker

    engine_breaker = get_engine_breaker()
    if not engine_breaker.allow_v6():
        result = execute_federated_chain(executor, req)
        result["engine"] = "v5_fallback"
        result["warnings"] = list(result.get("warnings") or []) + [
            "engine=v6 skipped: fallback breaker open (recent V6 crashes); "
            "executed with V5 engine"
        ]
        result["engine_breaker"] = engine_breaker.disclosure()
        return result
    # ── V7（ADR-0119 W12/W13）：结果缓存 + 计数器 + 反馈 ──
    cache_ctx = (
        _v7_cache_context(executor, req)
        if getattr(req, "use_cache", True)
        else None
    )
    cache_enabled = cache_ctx is not None
    if cache_enabled:
        scope_key, cache_key, fingerprints = cache_ctx
        from app.services.data_fabric.fabric.result_cache import get_result_cache

        cache = get_result_cache()
        neg = cache.get_negative(cache_key)
        if neg is not None:
            from app.services.data_fabric.errors import (
                SOURCE_AUTH_FAILED,
                SOURCE_UNREACHABLE,
                SourceAuthFailedError,
                SourceUnreachableError,
            )

            cls = {
                SOURCE_UNREACHABLE: SourceUnreachableError,
                SOURCE_AUTH_FAILED: SourceAuthFailedError,
            }.get(neg, SourceUnreachableError)
            raise cls(
                "recent attempt failed (negative cache)",
                details={"cache": "negative_hit", "key": cache_key[:16]},
            )
        cached = cache.get(cache_key, current_fingerprints=fingerprints)
        if cached is not None:
            cached["fabric_counters"] = {
                "cache_hit": True,
                "cache_key": cache_key[:16],
            }
            return cached
    counters = _v7_new_counters(cache_ctx)

    # ── V8（ADR-0130 Phase F）：cache stampede 保护 ──
    # miss 后的规划+执行+构建收进闭包，经 per-key SingleFlight 执行：并发
    # 同键请求在界内等待首问结果（shared 命中如实披露），不重复打远端。
    def _execute_and_build() -> Dict[str, Any]:
        plan = None  # R2-Mi-1：plan 期 typed 错误路径的反馈回调需要安全判空
        try:
            from app.services.data_fabric.query.federated.executor import (
                PhysicalExecutor,
                extract_hop_estimates,
            )
            from app.services.data_fabric.query.federated.explain import explain_v6_lines
            from app.services.data_fabric.query.federated.planner import (
                plan_federation_v6,
            )

            plan = plan_federation_v6(req)
            # M2（评审 R1）：derive_projection 接线 —— 与 V5 derive_chain_fields
            # 单一真相；仅当 V6 选择的序 == given 序（派生的"下一跳左键"集合
            # 依赖跳序，重排序下宁可多取全列，绝不缺字段静默失真）。
            tree = plan.tree
            given_ids = [s.source_id for s in req.sources]
            if getattr(req, "derive_projection", True) and plan.order == given_ids:
                derived = derive_chain_fields(req, list(req.sources), list(req.joins))
                if derived:
                    # C-1（评审 R2）：在**实际计划树**上写投影（保留
                    # LogicalReproject 等全部节点）—— 重建 given 序链会静默丢弃
                    # 变换节点，混 CRS 链默认配置下静默错答。
                    tree = _apply_scan_fields(plan.tree, derived)
                    plan.warnings.append(
                        "minimal projection derived per source (V6, given-order): "
                        + json.dumps(derived, ensure_ascii=False, sort_keys=True)
                    )
            px = PhysicalExecutor(
                adapter_factory=executor._adapter_factory,
                budget=req.budget,
                limit=req.limit,
                bbox=req.bbox,
                adaptive=True,
                order_strategy=getattr(req, "order_strategy", "cost"),
                replan_fn=_make_bushy_replan_fn(req, plan),
            )
            exec_result = px.execute(
                tree,
                hop_estimates=extract_hop_estimates(plan),
                edge_specs={
                    (j.left_source_id, j.right_source_id): j
                    for j in req.joins
                    if j.left_source_id and j.right_source_id
                },
            )
        except DataFabricError as e:
            # V7 R1-M8：typed 错误契约不变（原样上抛），但错误面反馈与负缓存
            # 在上抛前落账（均 fail-open）。
            if cache_ctx is not None:
                try:
                    from app.services.data_fabric.fabric.result_cache import (
                        get_result_cache,
                    )

                    get_result_cache().put_negative(cache_ctx[1], e.code)
                except Exception:  # noqa: BLE001
                    pass
            if plan is not None:
                try:
                    _v7_record_feedback(
                        req, plan, {"per_source_rows": {}}, "error",
                        scope_key=cache_ctx[0] if cache_ctx else "org:_|owner:_|proj:_",
                        error_code=e.code,
                    )
                except Exception:  # noqa: BLE001
                    pass
            raise  # typed 错误契约与 V5 一致（预算/构造错误绝不静默回退）
        except Exception as e:  # noqa: BLE001 - V6 非 typed 异常 → 诚实回退 V5
            logger.warning("[Federation] V6 engine failed (%s); falling back to V5", e)
            # V8：崩溃记账（fail-open；连续崩溃触发进程级熔断，后续请求跳过 V6）。
            try:
                engine_breaker.record_v6_crash(e)
            except Exception:  # noqa: BLE001
                pass
            result = execute_federated_chain(executor, req)
            result["engine"] = "v5_fallback"
            reason = str(e)[:200]
            result["warnings"] = list(result.get("warnings") or []) + [
                f"engine=v6 failed ({reason}); executed with V5 engine"
            ]
            result["engine_breaker"] = engine_breaker.disclosure()
            return result

        rows = exec_result["rows"]
        per_source = exec_result.get("per_source_rows") or {}
        rows_fetched = sum(per_source.values())
        warnings = list(plan.warnings)
        if any(s.estimated_rows is not None for s in req.sources):
            warnings.append("join order chosen by cost-based enumeration (V6 DP)")
        # ── V7：计数器补齐 + 反馈记录（fail-open）──
        from app.services.data_fabric.fabric.counters import collect_from_exec_result

        collect_from_exec_result(counters, exec_result)
        counters.cache_hit = False
        fb_store = _v7_record_feedback(
            req, plan, exec_result, "ok",
            scope_key=cache_ctx[0] if cache_ctx else "org:_|owner:_|proj:_",
        )
        counters.feedback_durable_failures = fb_store.failure_count
        result_dict = {
            "status": "success",
            "engine": "v6",
            "strategy": "v6_cost_based_tree",
            "order": plan.order,
            "rows": rows,
            "row_count": exec_result["row_count"],
            "joined_row_count": exec_result.get("joined_row_count"),
            "rows_fetched": rows_fetched,
            "per_source_rows": per_source,
            "plans": _v6_plan_dicts(plan),
            "pushdown_ratio": (
                round(exec_result["row_count"] / rows_fetched, 6) if rows_fetched else None
            ),
            "execution_duration_s": exec_result.get("execution_duration_s"),
            "warnings": warnings,
            "explain_v6": explain_v6_lines(
                plan, _v6_explain_ctx(req), exec_result=exec_result
            ),
            "semi_join_reduction": exec_result.get("hop_stats"),
            "bloom_reduction": exec_result.get("bloom_stats"),
            "replans_used": exec_result.get("replans_used", 0),
            # ── V7（ADR-0119 W8/W10）执行证据 additive ──
            "per_source_delivered_srid": exec_result.get(
                "per_source_delivered_srid", {}
            ),
            "crs_fallbacks": exec_result.get("crs_fallbacks", []),
        }
        result_dict["fabric"] = _v7_fabric_section(
            counters, cache_ctx, feedback_store_feedback=True
        )
        # V8：V6 成功 → 熔断归零回 CLOSED（含 half-open trial 成功）。
        engine_breaker.record_v6_success()
        # 缓存写入（fail-open；披露段在下一次命中时附入）
        if cache_enabled:
            try:
                scope_key, cache_key, fingerprints = cache_ctx
                cache.put(
                    cache_key, result_dict,
                    fingerprints=fingerprints, scope_key=scope_key,
                )
            except Exception as exc:  # noqa: BLE001 - 缓存绝不影响查询
                logger.debug("[fabric] result cache put failed: %s", exc)
        return result_dict

    # V8：单飞分发 —— cache 开启时并发同键共享首问结果（shared 命中披露
    # basis=singleflight）；未开启缓存时直接执行（行为与 V7 逐位一致）。
    if cache_enabled:
        result_dict, shared = cache.single_flight().run(
            cache_key, _execute_and_build
        )
        if shared:
            result_dict = dict(result_dict)
            result_dict["result_cache"] = {
                "hit": True,
                "age_s": 0.0,
                "ttl_s": cache._ttl_s,
                "basis": "singleflight",
                "key": cache_key[:16],
            }
            result_dict["fabric_counters"] = {
                "cache_hit": True,
                "cache_key": cache_key[:16],
                "shared_execution": True,
            }
        return result_dict
    return _execute_and_build()


def _v6_plan_dicts(plan) -> List[Dict[str, Any]]:
    """把 V6 计划树投影为 V5 ``plans`` 形状的逐跳 dict（工具契约兼容）。"""
    from app.services.data_fabric.query.federated.logical import (
        LogicalJoin,
        LogicalReproject,
        LogicalScan,
    )

    out: List[Dict[str, Any]] = []

    def side_dict(node) -> Dict[str, Any]:
        if isinstance(node, LogicalScan):
            return {
                "source_id": node.source_id,
                "dataset_id": node.dataset_id,
                "fetch_limit": node.fetch_limit,
            }
        if isinstance(node, LogicalReproject):
            return {"reproject": f"{node.from_crs}→{node.to_crs}"}
        if isinstance(node, LogicalJoin):
            return {"subtree": node.join_kind}
        return {"node": getattr(node, "kind", "?")}

    def walk(node, depth: int) -> None:
        if isinstance(node, LogicalJoin):
            walk(node.left, depth + 1)
            out.append(
                {
                    "kind": node.join_kind,
                    "left": side_dict(node.left),
                    "right": side_dict(node.right),
                    "join_field_left": node.join_field_left,
                    "join_field_right": node.join_field_right,
                    "spatial_op": node.spatial_op,
                    "group_by_right": node.group_by_right,
                    "aggregates": node.aggregates,
                    "chain_position": depth,
                }
            )
            if not isinstance(node.right, LogicalScan):
                walk(node.right, depth + 1)
        elif isinstance(node, LogicalReproject):
            walk(node.input, depth)

    walk(plan.tree, 0)
    return out


def _v6_explain_ctx(req: FederatedChainRequest):
    """EXPLAIN 用的轻量 ctx（源事实视图；M5 评审 R1：下推边界按提示如实渲染）。"""
    from app.services.data_fabric.query.federated.planner import (
        build_enumeration_context,
    )

    return build_enumeration_context(req)


def _apply_scan_fields(tree, fields_by_sid):
    """把派生投影写回计划树的 scan 节点（M2；返回新树）。"""
    from app.services.data_fabric.query.federated.logical import (
        LogicalScan,
        logical_from_dict,
    )

    if isinstance(tree, LogicalScan):
        f = fields_by_sid.get(tree.source_id)
        if f and tree.fields is None:
            return tree.model_copy(update={"fields": sorted(f)})
        return tree
    data = tree.model_dump()
    for side in ("input", "left", "right"):
        child = data.get(side)
        if isinstance(child, dict) and "kind" in child:
            data[side] = _apply_scan_fields(
                logical_from_dict(child), fields_by_sid
            ).model_dump()
    return logical_from_dict(data)


# ── V7（ADR-0119 W12/W13）：结果缓存 / 计数器 / 反馈的入口辅助 ────────────────


def _v7_scope_key(req: FederatedChainRequest) -> str:
    """作用域键：owner（session）即作用域；全局域显式标记。"""
    from app.services.data_fabric.fabric.connection_registry import TenantScope

    owner = getattr(req, "session_owner", None)
    return TenantScope(owner=owner).scope_key()


def _v7_cache_context(executor, req):
    """缓存上下文（scope/key/per-source fingerprints）或 None（不可缓存）。

    R-M2：owner=None 全局域禁入结果缓存；descriptor fingerprint 从
    catalog（owner 过滤）解析 —— 本地读，无网络。fail-open：任何解析
    失败 → 禁用缓存（宁可 miss 不可错命中）。
    """
    from app.services.data_fabric.fabric.connection_registry import TenantScope
    from app.services.data_fabric.fabric.result_cache import (
        canonical_request_payload,
        result_cache_key,
    )
    from app.services.data_fabric.fingerprint import dataset_fingerprint_service
    from app.services.data_fabric.spatial_catalog import spatial_catalog_service

    try:
        owner = getattr(req, "session_owner", None)
        scope = TenantScope(owner=owner)
        scope_key = scope.scope_key()
        if scope.is_global:
            return None
        fingerprints: Dict[str, str] = {}
        for s in req.sources:
            desc = spatial_catalog_service.get_dataset(s.dataset_id, owner=owner)
            if desc is None:
                return None  # catalog 不可见 → 缓存键不完整 → 禁用
            fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(desc)
            fingerprints[s.source_id] = fp
        key = result_cache_key(
            scope_key=scope_key,
            fingerprints=fingerprints,
            engine="v6",
            request=canonical_request_payload(
                sources=req.sources,
                joins=req.joins,
                bbox=req.bbox,
                limit=req.limit,
                order_strategy=req.order_strategy,
                derive_projection=getattr(req, "derive_projection", True),
            ),
        )
        return scope_key, key, fingerprints
    except Exception as exc:  # noqa: BLE001 - 缓存解析失败 = miss（绝不影响执行）
        logger.debug("[fabric] cache context unavailable: %s", exc)
        return None


def _v7_new_counters(cache_ctx) -> Any:
    from app.services.data_fabric.fabric.counters import FabricCounters

    # 探测请求代价在 probing 服务侧记账（ProbeCost）；此处是执行期聚合起点。
    return FabricCounters()


def _v7_record_feedback(req, plan, exec_result, outcome, *, scope_key, error_code=None):
    """把执行观测提交反馈存储（fail-open；返回 store 供计数披露）。

    R1-C2：``unfiltered`` 消费执行器 trace 事实（无过滤 ∧ 取回完整未触及
    窗口）—— trace 缺席（错误路径）时保守 False。
    """
    from app.services.data_fabric.fabric.feedback import (
        ExecutionFeedback,
        SourceObservation,
        get_feedback_store,
    )
    from app.services.data_fabric.fingerprint import dataset_fingerprint_service
    from app.services.data_fabric.spatial_catalog import spatial_catalog_service

    store = get_feedback_store()
    owner = getattr(req, "session_owner", None)
    obs: List[SourceObservation] = []
    fingerprints: Dict[str, str] = {}
    for s in req.sources:
        desc = spatial_catalog_service.get_dataset(s.dataset_id, owner=owner)
        fp = (
            dataset_fingerprint_service.calculate_descriptor_fingerprint(desc)
            if desc is not None
            else None
        )
        if fp:
            fingerprints[s.source_id] = fp
        actual = (exec_result.get("per_source_rows") or {}).get(s.source_id)
        # R1-C2：unfiltered 消费执行器 trace 事实（无过滤 ∧ 取回完整未触及
        # 窗口）；trace 缺席（错误路径）→ 保守 False。
        unfiltered = bool(
            (exec_result.get("per_source_scan_complete_unfiltered") or {}).get(
                s.source_id
            )
        )
        obs.append(
            SourceObservation(
                source_id=s.source_id,
                dataset_fingerprint=fp,
                estimated_rows=s.estimated_rows,
                actual_rows=actual,
                error=error_code if outcome == "error" else None,
                unfiltered=unfiltered,
            )
        )
    try:
        store.record(
            ExecutionFeedback(
                plan_hash=plan.tree.plan_hash(),
                scope_key=scope_key,
                engine="v6",
                outcome=outcome,
                per_source=obs,
            )
        )
    except Exception as exc:  # noqa: BLE001 - feedback 绝不阻断
        logger.debug("[fabric] feedback record failed: %s", exc)
    return store


def _v7_fabric_section(counters, cache_ctx, *, feedback_store_feedback: bool = False):
    """explain_v7 的 fabric 段（结构性计数 + 披露）。"""
    section = counters.to_dict()
    if cache_ctx is not None:
        section["cache"] = {
            "enabled": True,
            "scope": cache_ctx[0],
            "key": cache_ctx[1][:16],
            "fingerprinted_sources": sorted(cache_ctx[2].keys()),
        }
    else:
        section["cache"] = {
            "enabled": False,
            "reason": "global-scope connection or fingerprint unavailable",
        }
    return section
