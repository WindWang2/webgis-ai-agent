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

    kind: str                       # attribute_join | spatial_join | aggregate_join
    left: Dict[str, Any] = field(default_factory=dict)   # {source_id, dataset_id, spec extras}
    right: Dict[str, Any] = field(default_factory=dict)
    join_field_left: Optional[str] = None
    join_field_right: Optional[str] = None
    spatial_op: Optional[str] = None            # within | intersects
    group_by_right: Optional[List[str]] = None
    aggregates: Optional[List[Dict[str, Any]]] = None
    strategy: str = "local_hash_or_strtree"     # server_side | local_hash | local_strtree
    estimated_left_rows: Optional[int] = None
    estimated_right_rows: Optional[int] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
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
    spatial_op: Optional[str] = None            # within | intersects
    # aggregate+join
    group_by_right: Optional[List[str]] = None
    aggregates: Optional[List[Dict[str, Any]]] = None   # [{func, field}]
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
        raise FederatedQueryError(
            "attribute join requires join_field on both sides"
        )
    if req.spatial_op and req.spatial_op not in ("within", "intersects"):
        raise FederatedQueryError(
            f"unsupported spatial_op {req.spatial_op!r} (within|intersects)"
        )
    if req.aggregates and not req.group_by_right:
        raise FederatedQueryError("aggregate join requires group_by fields from the right side")

    kind = (
        "aggregate_join" if req.aggregates else
        ("spatial_join" if req.spatial_op else "attribute_join")
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
            details={"hint": "reduce limit, add bbox/filters, or aggregate on the source"},
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
    if index._tree is None and len(points) * max(1, len(shp_polys)) > MAX_JOIN_CANDIDATES * 10:
        # 仅线性回退时产品积守卫才有意义；STRtree 的复杂度是 O(N·candidates)
        raise QueryBudgetExceededError(
            f"join candidate space {len(points)}x{len(shp_polys)} too large "
            "(no STRtree available)",
            details={"hint": "install shapely, or apply bbox/filters to reduce both sides"},
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
                ok = pg.contains(g) or pg.equals(g) if spatial_op == "within" else pg.intersects(g)
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
) -> List[Dict[str, Any]]:
    """等值连接（右侧哈希索引；左行流式探测）。

    ``left_key_resolver``（V3 链式联邦专用，缺省 None）：给定 ``(左行, 字段)``
    返回连接键。链式累积行是扁平属性 dict——上一跳的右属性嵌在 ``__right__``
    下，需用 ``_chain_row_key`` 穿透取键；缺省 None 时保持两源路径的顶层取键
    语义（逐字节不变）。
    """
    index: Dict[Any, List[Dict[str, Any]]] = {}
    for r in right_rows:
        key = (r.get("properties") or r).get(join_field_right)
        if key is not None:
            index.setdefault(_hashable_key(key), []).append(r)
    out: List[Dict[str, Any]] = []
    for lrow in left_rows:
        if budget is not None:
            budget.add_feature(lrow if isinstance(lrow, dict) and "properties" in lrow else {"properties": lrow})
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
    """
    out: List[Dict[str, Any]] = []
    groups: Dict[Tuple, Dict[str, Any]] = {}
    for row in rows:
        right = row.get("__right__") or {}
        key = tuple(right.get(g) for g in group_by)
        acc = groups.get(key)
        if acc is None:
            # 每个 agg 一个累加器槽：count / sum / sumsq / min / max / distinct-set
            acc = {"n": 0, "cells": {}}
            for a in aggs:
                name = a.func if a.field is None else f"{a.func}_{a.field}"
                acc["cells"][name] = {
                    "count": 0, "sum": 0.0, "sumsq": 0.0,
                    "min": None, "max": None, "distinct": set(),
                }
            groups[key] = acc
        acc["n"] += 1
        for a in aggs:
            name = a.func if a.field is None else f"{a.func}_{a.field}"
            cell = acc["cells"][name]
            if a.func == "count" and a.field is None:
                cell["count"] += 1
                continue
            v = None
            if a.field is not None:
                left_props = {k: v2_ for k, v2_ in row.items() if not k.startswith("__")}
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
    for key, acc in groups.items():
        result: Dict[str, Any] = {}
        for g, v in zip(group_by, key):
            result[g] = v
        for a in aggs:
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
                result[name] = (cell["sum"] / cell["count"]) if cell["count"] else None
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
                return self._result(plan, rows, started, strategy="server_side",
                                    rows_fetched=len(rows))
            except DataFabricError:
                raise
            except Exception as e:  # server join 不可用 → 本地回退（记录）
                logger.info("[Federation] server-side join unavailable, falling back: %s", e)
                plan.warnings.append(f"server-side join failed ({e}); local execution used")

        return self._execute_local(req, plan, left_adapter, right_adapter, started)

    # ── 内部 ─────────────────────────────────────────────────────────

    def execute_chain(self, req) -> Dict[str, Any]:
        """N 源左深链执行（V3 additive）。"""
        return execute_federated_chain(self, req)

    def _source_query(
        self, adapter, dataset_id: str, req: FederatedQueryRequest,
        side_where: Optional[Any], *, fields: Optional[List[str]], limit: int,
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
            right_adapter, req.right_dataset_id, req, req.right_where,
            fields=right_fields, limit=min(req.budget.max_rows, MAX_JOIN_CANDIDATES),
        )
        if len(right_rows) > MAX_JOIN_CANDIDATES:
            raise QueryBudgetExceededError(
                f"right side has {len(right_rows)} rows (> {MAX_JOIN_CANDIDATES} join candidates)",
                details={"hint": "filter the right (dimension/polygon) side before joining"},
            )

        # 左侧流式分页扫描 + 逐页 join（页大小 JOIN_PAGE_SIZE，页数受预算约束）
        left_fields = [req.join_field_left] if req.join_field_left else None
        joined: List[Dict[str, Any]] = []
        rows_fetched = 0
        offset = 0
        deadline = started + req.budget.deadline_s
        # R4-M4：右侧空间索引只构建一次（跨左页复用）
        spatial_index = None
        if plan.kind in ("spatial_join", "aggregate_join") and req.spatial_op:
            shp_polys = [
                g for g in (
                    _shapely_from_geojson(p.get("geometry")) for p in right_rows
                ) if g is not None and not g.is_empty
            ]
            spatial_index = _LocalSpatialIndex(shp_polys)
        while len(joined) < req.limit:
            if time.monotonic() > deadline:
                raise QueryBudgetExceededError(
                    f"federated join exceeded {req.budget.deadline_s}s deadline",
                    details={"hint": "reduce scope (bbox/filters) or aggregate on sources"},
                )
            fetch_size = min(JOIN_PAGE_SIZE, req.limit - len(joined) + 1)
            page = self._source_query(
                left_adapter, req.left_dataset_id, req, req.left_where,
                fields=left_fields, limit=fetch_size,
            ) if offset == 0 else self._source_query_page(
                left_adapter, req.left_dataset_id, req, req.left_where,
                fields=left_fields, limit=fetch_size, offset=offset,
            )
            rows_fetched += len(page)
            if rows_fetched > req.budget.max_rows:
                raise QueryBudgetExceededError(
                    f"left side scanned {rows_fetched} rows (budget {req.budget.max_rows})",
                    details={"hint": "narrow bbox or add filters; or aggregate on the source"},
                )
            if not page:
                break
            remaining = req.limit - len(joined)
            if (plan.kind == "spatial_join") or (plan.kind == "aggregate_join" and req.spatial_op):
                batch = spatial_join_local(
                    page, right_rows, spatial_op=req.spatial_op or "within",
                    join_field_right=req.join_field_right, budget=budget,
                    max_output=remaining + 1,   # +1 探测是否还有更多（R4-C2）
                    spatial_index=spatial_index,
                )
            else:
                batch = attribute_join_local(
                    page, right_rows,
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
            return self._result(plan, rows, started, strategy=plan.strategy,
                                rows_fetched=rows_fetched, joined_rows=len(joined))
        # plain join：剥除内部键后返回
        for row in joined:
            row.pop("__right_geometry__", None)
        return self._result(plan, joined[: req.limit], started, strategy=plan.strategy,
                            rows_fetched=rows_fetched)

    def _source_query_page(self, adapter, dataset_id, req, side_where, *, fields, limit, offset):
        extras: Dict[str, Any] = {"limit": limit, "offset": offset,
                                  "deadline_s": req.budget.deadline_s,
                                  "max_rows": req.budget.max_rows}
        if fields:
            extras["fields"] = fields
        if side_where is not None:
            extras["where"] = side_where
        if req.bbox:
            extras["bbox"] = req.bbox
        result = adapter.query(dataset_id, QuerySpec(**extras))
        return result.features or []

    def _result(self, plan, rows, started, *, strategy, rows_fetched, joined_rows=None):
        return {
            "status": "success",
            "plan": plan.to_dict(),
            "strategy": strategy,
            "rows": rows,
            "row_count": len(rows),
            "joined_row_count": joined_rows,
            "rows_fetched": rows_fetched,
            "pushdown_ratio": (round(len(rows) / rows_fetched, 6) if rows_fetched else None),
            "execution_duration_s": round(time.monotonic() - started, 4),
            "warnings": plan.warnings,
        }


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


@dataclass
class ChainJoin:
    """累积左侧行与下一个源的连接。语义与两源 plan 一致。

    ``aggregate_join``（F2 修正语义）：链上聚合**先**按 ``join_field_left``
    /``join_field_right`` 将累积行与右源等值连接（右源行进入 ``__right__``），
    **再**按 ``group_by_right``（右源字段）分组聚合 —— 与两源
    ``_execute_local`` 的 aggregate_join 流程一致；跳过连接会让 ``right_rows``
    被静默丢弃、分组键全部落空（单一全 None 组）。
    """

    kind: str                                    # attribute_join | spatial_join | aggregate_join
    join_field_left: Optional[str] = None        # 累积行的键（顶层，或 "__right__.x"）
    join_field_right: Optional[str] = None
    spatial_op: Optional[str] = None             # within | intersects
    group_by_right: Optional[List[str]] = None
    aggregates: Optional[List[Dict[str, Any]]] = None


@dataclass
class FederatedChainRequest:
    """N 源（2..MAX_FEDERATED_SOURCES）左深链式联邦请求。"""

    sources: List[ChainSource] = field(default_factory=list)
    joins: List[ChainJoin] = field(default_factory=list)
    bbox: Optional[List[float]] = None
    limit: int = 10_000
    order_strategy: str = "cost"                 # cost | given
    budget: ExecutionBudget = field(
        default_factory=lambda: ExecutionBudget(**FEDERATION_BUDGET.model_dump())
    )
    warnings: List[str] = field(default_factory=list)


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
    if req.order_strategy == "cost":
        order.sort(key=lambda i: (
            req.sources[i].estimated_rows is None,
            req.sources[i].estimated_rows or 0,
            i,
        ))
    return order


def plan_federated_chain(req: FederatedChainRequest) -> List[FederatedPlan]:
    """校验并产出左深链计划（成本排序；纯函数，无 IO，不改写请求）。

    排序按 ``estimated_rows`` 提示升序（小表建侧/先物化，None 视为最大），
    稳定排序保证同序输入的确定性。fail-fast：limit/CRS 混用/join 数等预算
    与结构先检。planner 产生的 warnings 附在 ``plans[0].warnings`` 返回
    （不追加到 ``req.warnings``——本函数文档约定为纯函数）。
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
        if join.kind == "attribute_join" and not (join.join_field_left and join.join_field_right):
            raise FederatedQueryError(f"joins[{i}] attribute join needs both join fields")
        if join.kind == "spatial_join" and join.spatial_op not in ("within", "intersects"):
            raise FederatedQueryError(f"joins[{i}] spatial join needs within|intersects")
        if join.kind == "aggregate_join":
            if not join.group_by_right:
                raise FederatedQueryError(f"joins[{i}] aggregate join needs group_by_right")
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
            details={"hint": "reduce limit, add bbox/filters, or aggregate per source"},
        )

    order = _chain_order_indices(req)
    chain_warnings: List[str] = []
    if req.order_strategy == "cost":
        if req.sources[order[0]].estimated_rows is None:
            chain_warnings.append(
                "join order uses given order (no estimated_rows hints available); "
                "estimates are assumptions"
            )
        else:
            chain_warnings.append(
                "join ordered by estimated_rows hints (cost-based, left-deep)"
            )
    else:
        chain_warnings.append(
            "join order uses given order (cost hints ignored); "
            "ordering is an assumption"
        )

    # 按排序重排 sources/joins，join[i] 连接累积行与 sources[i+1]
    ordered_sources = [req.sources[i] for i in order]
    plans: List[FederatedPlan] = []
    for i, join in enumerate(req.joins):
        plans.append(FederatedPlan(
            kind=join.kind,
            left={"source_id": ordered_sources[i].source_id,
                  "dataset_id": ordered_sources[i].dataset_id,
                  "chain_position": i},
            right={"source_id": ordered_sources[i + 1].source_id,
                   "dataset_id": ordered_sources[i + 1].dataset_id,
                   "chain_position": i + 1},
            join_field_left=join.join_field_left,
            join_field_right=join.join_field_right,
            spatial_op=join.spatial_op,
            group_by_right=join.group_by_right,
            aggregates=join.aggregates,
            estimated_left_rows=ordered_sources[i].estimated_rows,
            estimated_right_rows=ordered_sources[i + 1].estimated_rows,
            warnings=chain_warnings if i == 0 else [],
        ))
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


def _chain_lift_next_join_key(rows: List[Dict[str, Any]], next_join: "ChainJoin") -> None:
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


def execute_federated_chain(executor: "FederatedExecutor", req: FederatedChainRequest) -> Dict[str, Any]:
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
    """
    import app.services.data_fabric.query.federation as _self

    started = time.monotonic()
    if req.budget is None:  # F7：显式 None 击穿 default_factory → 回落联邦默认
        req.budget = _chain_budget(req)
    plans = plan_federated_chain(req)
    # F7：直接消费规划器产出的有序计划（left[0] 起头，right[i] 依次为链上
    # 第 i+1 个源），按 source_id 映射回 ChainSource —— 不再本地重排第二份
    # 排序逻辑（与 plan_federated_chain 共享同一份 _chain_order_indices）。
    order_ids = [plans[0].left["source_id"]] + [p.right["source_id"] for p in plans]
    by_id = {s.source_id: s for s in req.sources}
    ordered_sources = [by_id[sid] for sid in order_ids]

    streaming = StreamingBudget(
        max_rows=req.budget.max_rows,
        max_bytes=req.budget.max_bytes,
        max_vertices=req.budget.max_vertices,
    )
    per_source_rows: List[int] = []
    adapters: List[Any] = []
    for src in ordered_sources:
        adapter = executor._adapter_factory(src.source_id)
        if adapter is None:
            raise FederatedQueryError(
                f"chain source '{src.source_id}' is not connected",
                details={"source_id": src.source_id},
            )
        feats = executor._source_query(
            adapter, src.dataset_id, _SideView(req, src),
            src.where, fields=src.fields, limit=req.limit,
        )
        per_source_rows.append(len(feats))
        adapters.append(feats)

    accumulated: List[Dict[str, Any]] = adapters[0]
    joined_total = 0
    for i, join in enumerate(req.joins):
        right_rows = adapters[i + 1]
        if join.kind == "attribute_join":
            # F1：左键经 _chain_row_key 穿透（顶层优先，回退上一跳 __right__）
            accumulated = _self.attribute_join_local(
                accumulated, right_rows,
                join_field_left=str(join.join_field_left),
                join_field_right=str(join.join_field_right),
                budget=streaming, max_output=req.budget.max_rows,
                left_key_resolver=_chain_row_key,
            )
        elif join.kind == "spatial_join":
            # F3：累积行重建为左要素（几何来自上一空间跳的 __left_geometry__）
            left_features = _chain_left_features(accumulated)
            if i > 0 and not any(isinstance(f.get("geometry"), dict) for f in left_features):
                raise FederatedQueryError(
                    f"chain spatial join {i} has no left geometry: the preceding hop "
                    "did not carry one",
                    details={"hint": "make the preceding hop a spatial join (it stores "
                                     "__left_geometry__), or reproject/carried geometry "
                                     "before the chain"},
                )
            accumulated = _self.spatial_join_local(
                left_features, right_rows,
                spatial_op=join.spatial_op or "within",
                budget=streaming, max_output=req.budget.max_rows,
                carry_left_geometry=True,
            )
        else:  # aggregate_join
            # F2：先连接（右源行进入 __right__），再按右源字段分组聚合 ——
            # 否则 right_rows 被丢弃、分组键全部落空（单一全 None 组）
            joined = _self.attribute_join_local(
                accumulated, right_rows,
                join_field_left=str(join.join_field_left or ""),
                join_field_right=str(join.join_field_right or ""),
                budget=streaming, max_output=req.budget.max_rows,
                left_key_resolver=_chain_row_key,
            )
            accumulated = _self.aggregate_join_rows(
                joined, join.aggregates or [], join.group_by_right or [],
            )
        joined_total = len(accumulated)
        if joined_total > req.budget.max_rows:
            raise QueryBudgetExceededError(
                f"chain join {i} produced {joined_total} rows "
                f"(budget {req.budget.max_rows}); fail-fast stops the chain",
                details={"hint": "filter sources harder, or aggregate before joining",
                         "per_source_rows": per_source_rows},
            )
        # F1：为下一跳提升仅存于 __right__ 的连接键到顶层（原地；左字段优先）
        if i + 1 < len(req.joins):
            _chain_lift_next_join_key(accumulated, req.joins[i + 1])

    final_rows = accumulated[: req.limit]
    rows_fetched = sum(per_source_rows)
    return {
        "status": "success",
        "strategy": "left_deep_chain",
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
        "warnings": plans[0].warnings,   # F7：planner 收集的 warnings 随计划返回
    }


class _SideView:
    """把 ChainSource 适配到两源 ``_source_query`` 的 req 形状（仅 bbox/budget）。"""

    def __init__(self, req: FederatedChainRequest, src: ChainSource):
        self.bbox = req.bbox
        self.budget = req.budget
        self.limit = req.limit


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
]
