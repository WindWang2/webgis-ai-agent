"""节点算子注册表（ADR-0096 D2）：NodeCategory → 真实执行体。

原则：
- 算子是**薄适配**，全部委托既有能力（data-fabric 查询运行时、
  geo_processor、geo_analysis、raster runtime、session store、artifact
  registry），绝不重写第二套算法实现；
- 没有接线执行器的类别 → :class:`UnsupportedOperationError`（诚实失败）；
- 大输出必须 MATERIALIZE/ARTIFACT_REGISTER 显式落存，执行器同时有预算
  上界兜底（绝无静默巨型载荷）；
- 远端查询走 Data Fabric 管理器（断路器 + 资源守卫 + 计划/证据附着）。

查询入口是可注入的（``query_catalog_fn`` / ``describe_catalog_fn``），
生产默认走 DB-backed ``DataFabricManager``；测试注入 fake 以保持离线。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.lib.cancellation import CancellationToken, checkpoint
from app.services.geocompute.errors import (
    AuthorizationError,
    BudgetExceededError,
    NodeExecutionError,
    UnsupportedOperationError,
    wrap_unexpected,
)
from app.services.geocompute.plan import ExecutionNode, NodeCategory, ResourceBudget

logger = logging.getLogger(__name__)

#: 单节点内存载荷的行数硬上界（预算之外的最后一道防线；不静默截断，直接报错）。
HARD_NODE_ROW_CAP = 500_000
#: 节点内联参数字节上界（防巨型内联 payload 走 parameters 通道）。
_MAX_PARAMS_BYTES = 8 * 1024 * 1024


@dataclass
class OperatorContext:
    """传给算子的执行上下文（有界、无聊天语义）。

    ``caller``：发起执行的认证身份（auth 依赖返回的 user dict）或 ``None``
    （匿名 / 进程内无身份调用）。目录类算子（QUERY / SOURCE_SCAN）用它做
    目录项准入 —— 与 data_fabric 路由同一租户谓词，fail closed。
    """

    run_id: str
    node_id: str
    session_id: Optional[str] = None
    caller: Optional[dict[str, Any]] = None
    budget: Optional[ResourceBudget] = None
    deadline_ts: Optional[float] = None  # time.monotonic() 绝对时刻
    cancel_token: Optional[CancellationToken] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def remaining_seconds(self) -> Optional[float]:
        if self.deadline_ts is None:
            return None
        return max(0.0, self.deadline_ts - time.monotonic())

    def enforce_deadline(self) -> None:
        remaining = self.remaining_seconds()
        if remaining is not None and remaining <= 0:
            from app.services.geocompute.errors import DeadlineExceededError

            raise DeadlineExceededError(
                f"node '{self.node_id}' exceeded its deadline",
                details={"node_id": self.node_id},
            )

    def checkpoint(self) -> None:
        checkpoint()
        self.enforce_deadline()


#: 节点载荷：features（GeoJSON feature dict 列表）/ rows（属性行）/ ref_id /
#: raster_path / metadata。只承载有界数据；大输出走 ref。
NodePayload = dict[str, Any]

#: 可注入的目录查询入口：(db, item_id, query_spec_dict) -> QueryResult-like
QueryCatalogFn = Callable[[Any, str, dict[str, Any]], Any]


def _default_query_catalog_fn(db: Any, item_id: str, query_spec: dict[str, Any]) -> Any:
    from app.services.data_fabric.manager import DataFabricManager
    from app.schemas.data_fabric_schema import QuerySpec

    return DataFabricManager.query_catalog_item(db, item_id, QuerySpec(**query_spec))


#: 模块级注入点（测试用 monkeypatch 替换；生产保持默认）。
query_catalog_fn: QueryCatalogFn = _default_query_catalog_fn

# ── 目录项准入（SEC 评审 CRITICAL：数据平面内的 catalog authz）──────────────
#
# QUERY / SOURCE_SCAN 之前必须先确认目录项对调用者可见 —— 与
# app/api/routes/data_fabric.py::_authorize_catalog_item（→
# _require_tenant_owned）同一租户谓词。路由层把 deny 映射为 HTTPException
# 404；数据平面无法 import 路由模块（边界契约），因此把谓词提取为
# 「逐行镜像」的本地实现，差异仅在 404/bool 的表达形式。谓词变化必须
# 双向同步（两处都有 docstring 交叉引用）。


#: 与 data_fabric 路由同一语义的匿名哨兵（app/core/auth._ANONYMOUS_USER_IDS
#: 的本地副本；auth 模块不在 data-plane 边界内被禁止，但哨兵集合按值镜像
#: 以保持判定自包含）。
_ANONYMOUS_USER_IDS = frozenset({"anonymous", "anon"})


def _caller_identity(caller: Optional[dict[str, Any]]) -> tuple[Optional[str], Optional[Any]]:
    """auth user dict → (user_id, org_id)；匿名哨兵折叠为 None。

    与 app/core/auth.actor_ids / data_fabric._real_user_id 同一判定：
    接受 user_id / id / sub，"anonymous"/"anon" 视为未认证。
    """
    if not caller:
        return None, None
    uid = caller.get("user_id") or caller.get("id") or caller.get("sub")
    if uid is None or str(uid) in _ANONYMOUS_USER_IDS:
        return None, caller.get("org_id")
    return str(uid), caller.get("org_id")


#: 可注入的目录授权谓词：(db, item, caller) -> bool。
#: ``item`` 是已解析的 CatalogItemModel 行；deny 一律 False（fail closed）。
CatalogAuthorizeFn = Callable[[Any, Any, Optional[dict[str, Any]]], bool]


def _default_catalog_authorize_fn(db: Any, item: Any, caller: Optional[dict[str, Any]]) -> bool:
    """镜像 ``data_fabric._require_tenant_owned`` 的租户谓词（bool 形式）。

    * 数据源行缺失 → False（路由层同一输入会 404）；
    * 匿名 / 无真实 user_id → 仅 org_id 与 owner_id 都为 NULL 的真·全局行
      可见（fail closed：caller=None 时一切有属主的条目一律拒绝）；
    * 有 org claim → org 匹配 或 本人 owner；
    * 无 org claim（当前 JWT 不携带 org_id）→ 本人 owner 或 真·全局行。
    """
    from app.models.data_fabric import DataSourceModel

    src = db.query(DataSourceModel).filter(DataSourceModel.id == item.source_id).first()
    if src is None:
        return False
    user_id, org_id = _caller_identity(caller)
    if user_id is None:
        return src.org_id is None and src.owner_id is None
    if org_id is not None:
        return src.org_id == org_id or src.owner_id == user_id
    return src.owner_id == user_id or (src.org_id is None and src.owner_id is None)


#: 模块级注入点（测试用 monkeypatch 替换；生产保持默认镜像谓词）。
catalog_authorize_fn: CatalogAuthorizeFn = _default_catalog_authorize_fn


def _authorize_catalog_for_ctx(
    ctx: OperatorContext, db: Any, item: Any, dataset_id: str
) -> None:
    """QUERY / SOURCE_SCAN 共用的目录准入：deny → 类型化 AuthorizationError。

    必须在任何适配器/数据传输执行之前调用（authz 先于副作用与出网）。
    """
    if not catalog_authorize_fn(db, item, ctx.caller):
        raise AuthorizationError(
            f"catalog item '{dataset_id}' is not visible to this caller",
            details={"node_id": ctx.node_id, "dataset_id": str(dataset_id)},
        )


def _features_from_input(payloads: dict[str, NodePayload], node: ExecutionNode) -> list[dict]:
    """取第一个含 features 的输入；无输入时允许参数内联 FC。"""
    for src in node.inputs:
        feats = payloads.get(src, {}).get("features")
        if feats:
            return feats
    inline = node.parameters.get("features")
    if isinstance(inline, list):
        return inline
    raise NodeExecutionError(
        f"node '{node.node_id}' requires a features input",
        node_id=node.node_id,
    )


def _rows_from_input(payloads: dict[str, NodePayload], node: ExecutionNode) -> list[dict]:
    """取属性行：优先 rows，否则取 features 的 properties。"""
    for src in node.inputs:
        pl = payloads.get(src, {})
        if pl.get("rows"):
            return pl["rows"]
        if pl.get("features"):
            return [f.get("properties") or {} for f in pl["features"]]
    raise NodeExecutionError(
        f"node '{node.node_id}' requires a rows/features input",
        node_id=node.node_id,
    )


def _payload_from_feature_collection(fc: dict[str, Any], **extra: Any) -> NodePayload:
    feats = fc.get("features", fc if isinstance(fc, list) else [])
    return {"features": list(feats), "metadata": {"feature_count": len(feats), **extra}}


def _check_row_budget(rows: list[Any], ctx: OperatorContext, node: ExecutionNode) -> None:
    n = len(rows)
    if n > HARD_NODE_ROW_CAP:
        raise BudgetExceededError(
            f"node '{node.node_id}' produced {n} rows, exceeding the hard node cap "
            f"{HARD_NODE_ROW_CAP}; materialize in-source or narrow the query",
            suggestions=["push down filters/aggregation to the source",
                         "use MATERIALIZE with source-side bounds",
                         "raise budget explicitly for approved heavy paths"],
            details={"node_id": node.node_id, "rows": n},
        )
    budget = ctx.budget
    if budget is not None and n > budget.max_rows:
        raise BudgetExceededError(
            f"node '{node.node_id}' produced {n} rows, exceeding plan budget "
            f"{budget.max_rows}",
            suggestions=["narrow the query extent", "aggregate before transfer"],
            details={"node_id": node.node_id, "rows": n, "budget_max_rows": budget.max_rows},
        )


# ---------------------------------------------------------------- operators


def _op_query(ctx: OperatorContext, node: OperationNodeAny, payloads: dict[str, NodePayload]) -> NodePayload:
    """QUERY：走 Data Fabric 目录查询（断路器/守卫/计划证据由管理器保证）。

    SEC：目录项准入（与 data_fabric 路由同一租户谓词）先于适配器执行 ——
    任何调用路径（含注入的 query_catalog_fn）都不得绕过 authz。
    """
    dataset_id = node.parameters.get("dataset_id")
    if not dataset_id:
        raise NodeExecutionError("QUERY node requires parameters.dataset_id", node_id=node.node_id)
    query_spec = dict(node.parameters.get("query") or {})
    from app.core.database import SessionLocal
    from app.models.data_fabric import CatalogItemModel

    db = SessionLocal()
    try:
        item = db.query(CatalogItemModel).filter(CatalogItemModel.id == str(dataset_id)).first()
        if item is None:
            raise NodeExecutionError(
                f"catalog item '{dataset_id}' not found", node_id=node.node_id
            )
        _authorize_catalog_for_ctx(ctx, db, item, str(dataset_id))
        result = query_catalog_fn(db, str(dataset_id), query_spec)
    finally:
        db.close()
    features = list(getattr(result, "features", None) or [])
    _check_row_budget(features, ctx, node)
    metadata: dict[str, Any] = {}
    for attr, key in (("query_plan", "query_plan"), ("query_evidence", "query_evidence")):
        val = getattr(result, "metadata", None)
        if isinstance(val, dict) and val.get(attr) is not None:
            metadata[key] = val[attr]
    metadata["feature_count"] = len(features)
    metadata["total_matching"] = getattr(result, "total_matching", None)
    return {"features": features, "metadata": metadata}


def _op_filter(ctx: OperatorContext, node: "ExecutionNode", payloads: dict[str, NodePayload]) -> NodePayload:
    """FILTER：类型化谓词 AST 本地求值（SQL 三值逻辑对齐）。"""
    from app.services.data_fabric.query.predicates import predicate_from_dict, evaluate_predicate

    pred_dict = node.parameters.get("predicate")
    if not pred_dict:
        raise NodeExecutionError("FILTER node requires parameters.predicate", node_id=node.node_id)
    predicate = predicate_from_dict(pred_dict)
    feats = _features_from_input(payloads, node)
    out: list[dict[str, Any]] = []
    for f in feats:
        ctx.checkpoint()
        props = f.get("properties") or {}
        if evaluate_predicate(predicate, props):
            out.append(f)
    _check_row_budget(out, ctx, node)
    return _payload_from_feature_collection(
        {"features": out}, filtered_from=len(feats), predicate=str(pred_dict.get("op", "predicate"))
    )


def _op_aggregate(ctx: OperatorContext, node: "ExecutionNode", payloads: dict[str, NodePayload]) -> NodePayload:
    """AGGREGATE：复用 V2 本地聚合器（NULL 语义与 SQL 对齐）。"""
    from app.services.data_fabric.query.execution import compute_aggregates
    from app.services.data_fabric.query.models import AggSpec

    aggs_raw = node.parameters.get("aggregates") or []
    group_by = list(node.parameters.get("group_by") or [])
    if not aggs_raw:
        raise NodeExecutionError("AGGREGATE node requires parameters.aggregates", node_id=node.node_id)
    aggs = [AggSpec(**a) if isinstance(a, dict) else a for a in aggs_raw]
    rows = _rows_from_input(payloads, node)
    out_rows = compute_aggregates(rows, aggs, group_by or None)
    _check_row_budget(out_rows, ctx, node)
    return {"rows": out_rows, "metadata": {"groups": len(out_rows), "group_by": group_by}}


def _op_spatial_join(ctx: OperatorContext, node: "ExecutionNode", payloads: dict[str, NodePayload]) -> NodePayload:
    """SPATIAL_JOIN：STRtree 候选 + 精确判定的本地 join（绝不全扫）。"""
    from app.services.data_fabric.query.execution import StreamingBudget
    from app.services.data_fabric.query.federation import spatial_join_local

    if len([s for s in node.inputs if payloads.get(s, {}).get("features")]) < 2:
        raise NodeExecutionError(
            "SPATIAL_JOIN node requires two feature inputs (left, right)", node_id=node.node_id
        )
    left = payloads[node.inputs[0]]["features"]
    right = payloads[node.inputs[1]]["features"]
    budget = StreamingBudget(
        max_rows=ctx.budget.max_rows if ctx.budget else HARD_NODE_ROW_CAP,
        max_bytes=ctx.budget.max_bytes if ctx.budget else 256 * 1024 * 1024,
        max_vertices=50_000_000,
    )
    rows = spatial_join_local(
        left, right,
        spatial_op=str(node.parameters.get("spatial_op", "within")),
        join_field_right=node.parameters.get("join_field_right"),
        budget=budget,
        max_output=ctx.budget.max_rows if ctx.budget else None,
    )
    _check_row_budget(rows, ctx, node)
    return {"rows": rows, "metadata": {"join_pairs": len(rows), "left": len(left), "right": len(right)}}


def _op_attribute_join(ctx: OperatorContext, node: "ExecutionNode", payloads: dict[str, NodePayload]) -> NodePayload:
    from app.services.data_fabric.query.execution import StreamingBudget
    from app.services.data_fabric.query.federation import attribute_join_local

    join_field_left = node.parameters.get("join_field_left")
    join_field_right = node.parameters.get("join_field_right") or join_field_left
    if not join_field_left:
        raise NodeExecutionError(
            "ATTRIBUTE_JOIN node requires parameters.join_field_left", node_id=node.node_id
        )
    left_rows = _rows_from_input(payloads, node) if not payloads.get(node.inputs[0], {}).get("features") else None
    if left_rows is None:
        left_rows = [f.get("properties") or {} for f in payloads[node.inputs[0]]["features"]]
    right_in = payloads.get(node.inputs[1], {})
    right_rows = right_in.get("rows") or [f.get("properties") or {} for f in right_in.get("features", [])]
    budget = StreamingBudget(
        max_rows=ctx.budget.max_rows if ctx.budget else HARD_NODE_ROW_CAP,
        max_bytes=ctx.budget.max_bytes if ctx.budget else 256 * 1024 * 1024,
        max_vertices=50_000_000,
    )
    rows = attribute_join_local(
        left_rows, right_rows,
        join_field_left=str(join_field_left),
        join_field_right=str(join_field_right),
        budget=budget,
        max_output=ctx.budget.max_rows if ctx.budget else None,
    )
    _check_row_budget(rows, ctx, node)
    return {"rows": rows, "metadata": {"joined": len(rows)}}


def _op_vector_operation(ctx: OperatorContext, node: "ExecutionNode", payloads: dict[str, NodePayload]) -> NodePayload:
    """VECTOR_OPERATION：委托 geo_processor 几何/叠加能力。"""
    from app.lib.geo_processor.geometry import buffer_smart, clip_smart, dissolve_smart
    from app.lib.geo_processor.overlay import overlay_smart

    op = node.operation or node.parameters.get("op")
    feats = _features_from_input(payloads, node)
    fc = {"type": "FeatureCollection", "features": feats}
    params = node.parameters
    if op == "buffer":
        result = buffer_smart(
            fc, float(params.get("distance", 0.0)),
            unit=str(params.get("unit", "m")),
            dissolve=bool(params.get("dissolve", False)),
        )
    elif op == "clip":
        mask_fc = params.get("mask") or _payloads_fc(payloads, node, 1)
        result = clip_smart(fc, mask_fc)
    elif op == "dissolve":
        result = dissolve_smart(fc, params.get("field"))
    elif op == "overlay":
        other_fc = params.get("other") or _payloads_fc(payloads, node, 1)
        result = overlay_smart(fc, other_fc, how=str(params.get("how", "intersection")))
    else:
        raise UnsupportedOperationError(
            f"vector_operation '{op}' is not wired; wired: buffer|clip|dissolve|overlay",
            details={"node_id": node.node_id, "operation": str(op)},
        )
    if not getattr(result, "success", False):
        raise NodeExecutionError(
            str(getattr(result, "summary", "vector operation failed")), node_id=node.node_id
        )
    data = getattr(result, "data", None)
    out_feats = data.get("features", []) if isinstance(data, dict) else list(data or [])
    _check_row_budget(out_feats, ctx, node)
    return _payload_from_feature_collection({"features": out_feats}, vector_op=str(op))


def _payloads_fc(payloads: dict[str, NodePayload], node: "ExecutionNode", idx: int) -> dict[str, Any]:
    src = node.inputs[idx] if idx < len(node.inputs) else None
    feats = (payloads.get(src or "", {}) or {}).get("features")
    if not feats:
        raise NodeExecutionError(f"node '{node.node_id}' missing secondary feature input", node_id=node.node_id)
    return {"type": "FeatureCollection", "features": feats}


def _op_raster_window_operation(ctx: OperatorContext, node: "ExecutionNode", payloads: dict[str, NodePayload]) -> NodePayload:
    """RASTER_WINDOW_OPERATION：委托 V3/V4 窗口化栅格运行时（有界内存）。"""
    op = node.operation or node.parameters.get("op")
    raster_path = node.parameters.get("raster_path") or _raster_from_inputs(payloads, node)
    if not raster_path:
        raise NodeExecutionError(
            "RASTER_WINDOW_OPERATION requires parameters.raster_path or a raster input",
            node_id=node.node_id,
        )
    params = node.parameters
    if op == "raster_calculator":
        from app.lib.geo_analysis.raster_math import raster_calculator

        expression = params.get("expression")
        if not expression:
            raise NodeExecutionError("raster_calculator requires parameters.expression", node_id=node.node_id)
        second = params.get("raster_path_b") or _raster_from_inputs(payloads, node, index=1)
        result = raster_calculator(
            raster_a=str(raster_path),
            raster_b=str(second) if second else None,
            expression=str(expression),
            constant=params.get("constant"),
        )
        out_path = str(result["output_path"])
    elif op == "resample":
        from app.lib.geo_analysis.raster_math import resample_raster

        result = resample_raster(
            str(raster_path),
            float(params.get("target_resolution", params.get("resolution", 0))),
            target_crs=params.get("target_crs"),
            resampling=str(params.get("resampling", "bilinear")),
        )
        out_path = str(result["output_path"])
    else:
        raise UnsupportedOperationError(
            f"raster_window_operation '{op}' is not wired; wired: raster_calculator|resample",
            details={"node_id": node.node_id, "operation": str(op)},
        )
    import os as _os

    return {"raster_path": out_path, "metadata": {"raster_op": str(op), "source": _os.path.basename(str(raster_path))}}


def _raster_from_inputs(payloads: dict[str, NodePayload], node: "ExecutionNode", index: int = 0) -> Optional[str]:
    found = 0
    for src in node.inputs:
        rp = payloads.get(src, {}).get("raster_path")
        if rp:
            if found == index:
                return rp
            found += 1
    return None


def _op_interpolation(ctx: OperatorContext, node: "ExecutionNode", payloads: dict[str, NodePayload]) -> NodePayload:
    """INTERPOLATION：IDW（H3 网格，资源守卫内置）或普通克里金（含不确定度）。"""
    method = str(node.operation or node.parameters.get("method", "idw")).lower()
    feats = _features_from_input(payloads, node)
    fc = {"type": "FeatureCollection", "features": feats}
    value_field = node.parameters.get("value_field")
    if not value_field:
        raise NodeExecutionError("INTERPOLATION requires parameters.value_field", node_id=node.node_id)
    if method == "idw":
        from app.lib.geo_analysis.interpolation import idw_interpolation

        cells = idw_interpolation(
            fc, str(value_field),
            resolution=int(node.parameters.get("resolution", 8)),
            power=float(node.parameters.get("power", 2.0)),
        )
        _check_row_budget(cells, ctx, node)
        return {"rows": cells, "metadata": {"method": "idw", "cells": len(cells)}}
    if method == "kriging":
        from app.lib.geo_analysis.kriging import kriging_interpolation

        driver = kriging_interpolation(
            fc, str(value_field),
            resolution=int(node.parameters.get("resolution", 8)),
        )
        records = list(driver.get("records") or [])
        _check_row_budget(records, ctx, node)
        return {"rows": records, "metadata": {"method": "kriging", **{
            k: driver.get(k) for k in ("metadata",) if driver.get(k) is not None
        }}}
    raise UnsupportedOperationError(
        f"interpolation method '{method}' is not wired; wired: idw|kriging",
        details={"node_id": node.node_id},
    )


def _op_materialize(ctx: OperatorContext, node: "ExecutionNode", payloads: dict[str, NodePayload]) -> NodePayload:
    """MATERIALIZE：显式落存为 session ref（大载荷离开执行图的唯一正门）。"""
    if not ctx.session_id:
        raise NodeExecutionError(
            "MATERIALIZE requires a session context", node_id=node.node_id
        )
    source = payloads.get(node.inputs[0]) if node.inputs else None
    data = (source or {}).get("features") or (source or {}).get("rows")
    if data is None:
        data = node.parameters.get("data")
    if data is None:
        raise NodeExecutionError("MATERIALIZE has no data input", node_id=node.node_id)
    _check_row_budget(list(data), ctx, node)
    prefix = str(node.parameters.get("prefix", "geocompute"))
    from app.services.session_data import session_data_manager

    from app.services.geocompute._async_bridge import run_coro_sync

    ref_id = run_coro_sync(
        session_data_manager.store(ctx.session_id, data, prefix=prefix)
    )
    return {
        "ref_id": ref_id,
        "metadata": {"materialized_rows": len(list(data)), "prefix": prefix},
    }


def _op_artifact_register(ctx: OperatorContext, node: "ExecutionNode", payloads: dict[str, NodePayload]) -> NodePayload:
    """ARTIFACT_REGISTER：经由既有 ArtifactRegistry（增值记录，绝不阻断）。"""
    source = payloads.get(node.inputs[0]) if node.inputs else None
    ref_id = (source or {}).get("ref_id") or node.parameters.get("ref_id")
    if not ref_id or not ctx.session_id:
        raise NodeExecutionError(
            "ARTIFACT_REGISTER requires an input ref_id and session context",
            node_id=node.node_id,
        )
    from app.services.artifact_registry import register_artifact

    from app.services.geocompute._async_bridge import run_coro_sync

    record = run_coro_sync(
        register_artifact(
            ctx.session_id,
            artifact_id=str(ref_id),
            artifact_type=str(node.parameters.get("artifact_type", "geocompute")),
            producer_capability=str(node.operation or "geocompute.node"),
            producer_tool="geocompute_executor",
            producer_node=node.node_id,
            inputs=[s for s in node.inputs],
            descriptor=node.parameters.get("descriptor"),
        )
    )
    return {
        "ref_id": str(ref_id),
        "metadata": {
            "artifact_state": getattr(record, "state", None) and str(getattr(record, "state")),
            "registered": record is not None,
        },
    }


def _op_source_scan(ctx: OperatorContext, node: "ExecutionNode", payloads: dict[str, NodePayload]) -> NodePayload:
    """SOURCE_SCAN：目录项描述（有界元数据；不做数据传输）。

    SEC：与 QUERY 同一目录项准入 —— 描述本身也是元数据泄漏面。
    """
    dataset_id = node.parameters.get("dataset_id")
    if not dataset_id:
        raise NodeExecutionError("SOURCE_SCAN requires parameters.dataset_id", node_id=node.node_id)
    from app.core.database import SessionLocal
    from app.models.data_fabric import CatalogItemModel

    db = SessionLocal()
    try:
        item = db.query(CatalogItemModel).filter(CatalogItemModel.id == str(dataset_id)).first()
        if item is not None:
            _authorize_catalog_for_ctx(ctx, db, item, str(dataset_id))
    finally:
        db.close()
    if item is None:
        raise NodeExecutionError(f"catalog item '{dataset_id}' not found", node_id=node.node_id)
    descriptor = item.descriptor_json or {}
    summary = {
        "id": descriptor.get("id", getattr(item, "name", None)),
        "source_type": descriptor.get("source_type") or getattr(item, "source_type", None),
        "feature_count": descriptor.get("feature_count"),
        "bbox": descriptor.get("bbox") or getattr(item, "bbox_json", None),
        "crs": descriptor.get("crs") or descriptor.get("srs") or getattr(item, "crs", None),
        "geometry_type": descriptor.get("geometry_type"),
        "fields": [
            {"name": f.get("name"), "type": f.get("type")}
            for f in (descriptor.get("fields") or [])[:64]
            if isinstance(f, dict)
        ],
    }
    return {"rows": [summary], "metadata": {"scan": "descriptor_only"}}


#: 类别 → 执行体。新类别必须连同一个离线测试一起接线（「声明即契约」）。
REGISTRY: dict[NodeCategory, Callable[..., NodePayload]] = {
    NodeCategory.SOURCE_SCAN: _op_source_scan,
    NodeCategory.QUERY: _op_query,
    NodeCategory.FILTER: _op_filter,
    NodeCategory.AGGREGATE: _op_aggregate,
    NodeCategory.SPATIAL_JOIN: _op_spatial_join,
    NodeCategory.ATTRIBUTE_JOIN: _op_attribute_join,
    NodeCategory.VECTOR_OPERATION: _op_vector_operation,
    NodeCategory.RASTER_WINDOW_OPERATION: _op_raster_window_operation,
    NodeCategory.INTERPOLATION: _op_interpolation,
    NodeCategory.MATERIALIZE: _op_materialize,
    NodeCategory.ARTIFACT_REGISTER: _op_artifact_register,
}


def has_operator(category: NodeCategory) -> bool:
    return category in REGISTRY


def wired_categories() -> list[str]:
    return sorted(c.value for c in REGISTRY)


def execute_node(ctx: OperatorContext, node: ExecutionNode, payloads: dict[str, NodePayload]) -> NodePayload:
    """执行单个节点：注册表分发 + 未接线类别的诚实失败。"""
    handler = REGISTRY.get(node.category)
    if handler is None:
        raise UnsupportedOperationError(
            f"node category '{node.category.value}' has no wired executor "
            f"(wired: {', '.join(wired_categories())})",
            details={"node_id": node.node_id, "category": node.category.value},
        )
    # 内联参数字节上界（评审 S-M5）：parameters 不是无限输入通道；
    # 大数据必须走 session ref / MATERIALIZE，而不是内联 JSON。
    import json as _json

    if len(_json.dumps(node.parameters, default=str)) > _MAX_PARAMS_BYTES:
        raise BudgetExceededError(
            f"node '{node.node_id}' inline parameters exceed {_MAX_PARAMS_BYTES} bytes",
            suggestions=["materialize the data as a session ref and pass the ref",
                         "query the source with filters instead of inlining features"],
            details={"node_id": node.node_id},
        )
    try:
        ctx.checkpoint()
        return handler(ctx, node, payloads)
    except Exception as exc:
        raise wrap_unexpected(exc, node_id=node.node_id) from exc


#: 类型别名仅用于注解可读性。
OperationNodeAny = ExecutionNode
