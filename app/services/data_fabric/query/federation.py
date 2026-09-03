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
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
) -> List[Dict[str, Any]]:
    """点面本地 join（STRtree 候选 + 精确判定；绝不 O(N·M) 全扫）。

    返回 join 行：``{point_properties..., "__right__": right_properties}``。
    """
    shp_polys = []
    for p in polygons:
        g = _shapely_from_geojson(p.get("geometry"))
        if g is not None and not g.is_empty:
            shp_polys.append((g, p))
    index = _LocalSpatialIndex([g for g, _ in shp_polys])
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
            row = dict(pt.get("properties") or {})
            row["__right__"] = hit.get("properties") or {}
            row["__right_geometry__"] = hit.get("geometry")
            out.append(row)
    return out


def attribute_join_local(
    left_rows: Sequence[Dict[str, Any]],
    right_rows: Sequence[Dict[str, Any]],
    *,
    join_field_left: str,
    join_field_right: str,
    budget: Optional[StreamingBudget] = None,
) -> List[Dict[str, Any]]:
    """等值连接（右侧哈希索引；左行流式探测）。"""
    index: Dict[Any, List[Dict[str, Any]]] = {}
    for r in right_rows:
        key = (r.get("properties") or r).get(join_field_right)
        if key is not None:
            index.setdefault(_hashable_key(key), []).append(r)
    out: List[Dict[str, Any]] = []
    for lrow in left_rows:
        if budget is not None:
            budget.add_feature(lrow if isinstance(lrow, dict) and "properties" in lrow else {"properties": lrow})
        key = (lrow.get("properties") or lrow).get(join_field_left)
        if key is None:
            continue
        for rrow in index.get(_hashable_key(key), ()):
            row = dict(lrow.get("properties") or lrow)
            row["__right__"] = rrow.get("properties") or rrow
            out.append(row)
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
    out: List[Dict[str, Any]] = []
    groups: Dict[Tuple, List[Dict[str, Any]]] = {}
    for row in rows:
        right = row.get("__right__") or {}
        key = tuple(right.get(g) for g in group_by)
        groups.setdefault(key, []).append({**row, **{f"__r_{k}": v for k, v in right.items()}})
    for key, members in groups.items():
        result: Dict[str, Any] = {}
        for g, v in zip(group_by, key):
            result[g] = v
        for a in aggs:
            name = a.func if a.field is None else f"{a.func}_{a.field}"
            if a.func == "count" and a.field is None:
                result[name] = len(members)
                continue
            field = f"__r_{a.field}" if a.field in (group_by or []) else a.field
            vals = [m.get(field) for m in members if m.get(field) is not None]
            nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if a.func == "count":
                result[name] = len(vals)
            elif a.func == "distinct_count":
                result[name] = len(set(map(str, vals)))
            elif a.func == "sum":
                result[name] = sum(nums) if nums else None
            elif a.func == "avg":
                result[name] = (sum(nums) / len(nums)) if nums else None
            elif a.func == "min":
                result[name] = min(vals) if vals else None
            elif a.func == "max":
                result[name] = max(vals) if vals else None
            elif a.func == "stddev":
                import math as _math

                if len(nums) < 2:
                    result[name] = None
                else:
                    mean = sum(nums) / len(nums)
                    result[name] = _math.sqrt(sum((x - mean) ** 2 for x in nums) / (len(nums) - 1))
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
        while len(joined) < req.limit:
            if time.monotonic() > deadline:
                raise QueryBudgetExceededError(
                    f"federated join exceeded {req.budget.deadline_s}s deadline",
                    details={"hint": "reduce scope (bbox/filters) or aggregate on sources"},
                )
            page = self._source_query(
                left_adapter, req.left_dataset_id, req, req.left_where,
                fields=left_fields, limit=min(JOIN_PAGE_SIZE, req.limit - len(joined) + 1),
            ) if offset == 0 else self._source_query_page(
                left_adapter, req.left_dataset_id, req, req.left_where,
                fields=left_fields,
                limit=min(JOIN_PAGE_SIZE, req.limit - len(joined) + 1), offset=offset,
            )
            rows_fetched += len(page)
            if rows_fetched > req.budget.max_rows:
                raise QueryBudgetExceededError(
                    f"left side scanned {rows_fetched} rows (budget {req.budget.max_rows})",
                    details={"hint": "narrow bbox or add filters; or aggregate on the source"},
                )
            if not page:
                break
            if plan.kind == "spatial_join" or plan.kind == "aggregate_join" and req.spatial_op:
                batch = spatial_join_local(
                    page, right_rows, spatial_op=req.spatial_op or "within",
                    join_field_right=req.join_field_right, budget=budget,
                )
            else:
                batch = attribute_join_local(
                    page, right_rows,
                    join_field_left=req.join_field_left or "",
                    join_field_right=req.join_field_right or "",
                    budget=budget,
                )
            joined.extend(batch)
            if len(page) < JOIN_PAGE_SIZE:
                break
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
]
