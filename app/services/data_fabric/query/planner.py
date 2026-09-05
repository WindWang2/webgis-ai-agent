"""Capability-aware Spatial Query Planner（ADR-0094 §5）。

``plan_query`` 是唯一计划入口：normalize 后的 QuerySpecV2 + DatasetDescriptor
+ capability 矩阵 → 确定性 QueryPlan。adapter 不得私自决定全局语义；本函数
输出的计划与 adapter 执行共用同一决策（adapter 调用本函数并把 plan 附到
QueryResult.metadata["query_plan"]）。

决策内容：pushdown 划分、pagination 策略、result mode、估算、fallback、
预算检查（QUERY_BUDGET_EXCEEDED）、CRS 一致性、反子午线、性能警告。
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence

from app.services.data_fabric.errors import (
    CrsInvalidError,
    InvalidQueryError,
    QueryBudgetExceededError,
)
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.models import (
    AdapterCapabilitiesV2,
    CursorPage,
    DatasetVersion,
    ExecutionFragment,
    OffsetPage,
    QueryPlan,
    QuerySpecV2,
    ResultMode,
)
from app.services.data_fabric.query.predicates import (
    bbox_crosses_antimeridian,
    predicate_summary,
)
from app.services.data_fabric.query.selectivity import (
    estimate_group_cardinality,
    estimate_predicate_selectivity,
)
from app.services.data_fabric.query.statistics import DatasetStatistics

# 估算参数（确定性；V2 常数仍在 selectivity.py 作为无统计兜底，逐位一致）
_BBOX_FULL_COVER = 1.0
_BYTE_PER_FEATURE_DEFAULT = 700
_BYTE_PER_FEATURE_GEO = 1_800   # 含 geometry
_AGG_GROUPS_ESTIMATE = 5_000


def parse_epsg(crs: Optional[str]) -> Optional[int]:
    """'EPSG:4326' / 'epsg:4326' / '4326' / CRS84 → SRID int；CRS84→4326。"""
    if not crs:
        return None
    s = str(crs).strip()
    if s.upper().endswith("CRS84") or s.upper() == "OGC:CRS84":
        return 4326
    if s.upper().startswith("EPSG:"):
        body = s[5:]
    else:
        body = s
    if not body.isdigit():
        return None
    v = int(body)
    if not (0 < v <= 99_999_999):
        return None
    return v


def dataset_srid(descriptor: Any) -> Optional[int]:
    crs = getattr(descriptor, "srs", None) or getattr(descriptor, "crs", None)
    srid = parse_epsg(crs)
    if srid is None:
        meta = getattr(descriptor, "metadata", None) or {}
        raw = meta.get("srid") if isinstance(meta, dict) else None
        srid = raw if isinstance(raw, int) else None
    return srid


def _desc_bbox_area(bbox: Optional[Sequence[float]]) -> Optional[float]:
    if not bbox or len(bbox) != 4:
        return None
    minx, miny, maxx, maxy = bbox
    return max(0.0, (maxx - minx)) * max(0.0, (maxy - miny))


def _predicate_selectivity(node: Any, stats: Optional[DatasetStatistics] = None) -> float:
    """确定性选择率估算（供行数估算）。

    V3：委托 selectivity 模块 —— 无统计时与历史常数逐位一致；有统计时
    按列 NDV/null_frac/min-max 估计。保留本包装以兼容既有调用方。
    """
    return estimate_predicate_selectivity(node, stats).value


def _estimate_rows(
    spec: QuerySpecV2,
    descriptor: Any,
    bbox_ratio: float,
    stats: Optional[DatasetStatistics] = None,
) -> Optional[int]:
    total = getattr(descriptor, "feature_count", None)
    if not isinstance(total, (int, float)) or total <= 0:
        return None
    sel = (
        bbox_ratio
        * estimate_predicate_selectivity(spec.filter, stats).value
        * estimate_predicate_selectivity(spec.temporal, stats).value
    )
    est = max(1.0, float(total) * max(sel, 1e-9))
    return int(min(est, float(total)))


def plan_query(
    spec: QuerySpecV2,
    descriptor: Any,
    caps: Optional[AdapterCapabilitiesV2] = None,
    *,
    source_id: Optional[str] = None,
    dataset_fingerprint: Optional[str] = None,
    query_fp: Optional[str] = None,
    stats: Optional[DatasetStatistics] = None,
) -> QueryPlan:
    """产出确定性 QueryPlan；不可执行的查询抛 typed error。

    V3（ADR-0096 D3）：可选 ``stats``（DatasetStatistics）驱动选择性
    估算；无统计路径与 V2 逐位一致。产出的计划附加相对成本分解与
    有界备选（EXPLAIN 投影）—— 选中决策仍由本函数单一确定，备选
    不构成第二执行真相。"""
    from app.services.data_fabric.query.models import query_fingerprint

    if caps is None:
        caps = get_capabilities(getattr(descriptor, "source_type", "generic"))
    dataset_id = getattr(descriptor, "id", "?")
    if query_fp is None:
        query_fp = query_fingerprint(spec, dataset_fingerprint)

    warnings: List[str] = []
    steps: List[ExecutionFragment] = []

    # ---- CRS 一致性 ----
    ds_srid = dataset_srid(descriptor)
    query_spatial = spec.spatial
    spatial_crs_srid = None
    if query_spatial is not None:
        spatial_crs_srid = parse_epsg(getattr(query_spatial, "crs", "EPSG:4326"))
        if spatial_crs_srid is None:
            raise CrsInvalidError(
                f"invalid query CRS: {getattr(query_spatial, 'crs', None)!r}",
                details={"hint": "use a valid EPSG code or CRS84"},
            )
    out_srid = parse_epsg(spec.output.crs)
    if spec.output.crs and out_srid is None:
        raise CrsInvalidError(f"invalid output CRS: {spec.output.crs!r}")

    # 反子午线检测：非 4326 CRS 的 minx>maxx 在谓词校验已拒绝；4326 的交给
    # 编译器显式 split，但 plan 需标注。
    if query_spatial is not None and query_spatial.op == "bbox":
        if bbox_crosses_antimeridian(query_spatial.bbox) and spatial_crs_srid != 4326:
            raise InvalidQueryError("antimeridian-crossing bbox only supported in EPSG:4326")
        if bbox_crosses_antimeridian(query_spatial.bbox):
            warnings.append("bbox crosses antimeridian; compiled as split envelope OR")
        if spatial_crs_srid != 4326 and ds_srid is not None and spatial_crs_srid != ds_srid and not caps.server_reprojection:
            raise CrsInvalidError(
                f"query CRS EPSG:{spatial_crs_srid} ≠ dataset CRS EPSG:{ds_srid} "
                "and source cannot reproject",
                details={"hint": "query in EPSG:4326 or the dataset's native CRS"},
            )

    # CRS 输出变换：dataset → output
    if out_srid is not None and ds_srid is not None and out_srid != ds_srid:
        if caps.server_reprojection:
            steps.append(ExecutionFragment(
                step="server_reprojection",
                description=f"ST_Transform / outSR EPSG:{ds_srid}→{out_srid}",
                pushed=True,
            ))
        else:
            steps.append(ExecutionFragment(
                step="local_reprojection",
                description=f"local reprojection EPSG:{ds_srid}→{out_srid}",
            ))
            warnings.append(
                f"output CRS EPSG:{out_srid} differs from dataset EPSG:{ds_srid}; "
                "reprojection applied locally"
            )
    elif ds_srid is None and out_srid is not None and out_srid != 4326:
        warnings.append("dataset CRS unknown; output CRS requested but may not be honored")

    # ---- result mode 协商 ----
    result_mode = spec.output.mode
    if result_mode == ResultMode.VECTOR_TILE and not caps.vector_tiles:
        # 回退：大数据走 MATERIALIZE + 客户端 tile 引擎（现有 MVT 管线）
        result_mode = ResultMode.MATERIALIZE
        warnings.append("source lacks server vector tiles; falling back to bounded materialization + client tiles")

    # ---- pushdown 划分 ----
    pushed_filters: List[str] = []
    local_filters: List[str] = []

    filter_ok = spec.filter is not None and caps.filter_pushdown
    if spec.filter is not None:
        if filter_ok:
            pushed_filters.append(predicate_summary(spec.filter))
        else:
            local_filters.append(predicate_summary(spec.filter))

    pushed_spatial = False
    if spec.spatial is not None:
        op = spec.spatial.op
        if op == "bbox":
            pushed_spatial = caps.bbox_pushdown
        else:
            pushed_spatial = caps.supports_spatial_op(op)
        if not pushed_spatial:
            local_filters.append(f"spatial:{op}")

    pushed_temporal = spec.temporal is not None and caps.temporal_filter
    if spec.temporal is not None and not pushed_temporal:
        local_filters.append(f"temporal:{spec.temporal.op}")

    pushed_projection = spec.select is not None and caps.projection_pushdown

    aggregate_requested = bool(spec.aggregate)
    pushed_aggregation = aggregate_requested and caps.aggregation
    if aggregate_requested and not caps.aggregation:
        # 本地聚合必须拉特征（危险）→ 除非预算允许且行数有界
        warnings.append("source lacks aggregation pushdown; aggregation executes locally over bounded rows")

    pushed_sort = bool(spec.order_by) and caps.sort_pushdown
    if spec.order_by and not pushed_sort:
        local_filters.append("sort(local)")

    # ---- pagination 策略 ----
    page = spec.page
    pagination_strategy = "none"
    pagination_note: Optional[str] = None
    if isinstance(page, CursorPage):
        if caps.cursor_pagination:
            pagination_strategy = "cursor"
        elif caps.offset_pagination:
            pagination_strategy = "offset"
            pagination_note = "cursor requested but source lacks keyset support; decoded to offset"
        else:
            pagination_strategy = "single_page"
    elif isinstance(page, OffsetPage):
        # R1-M8/R2-m7：不再虚构 "offset→cursor 升级"（没有任何 adapter 实现
        # OffsetPage 的 keyset 改写——plan 必须与执行一致）。
        if caps.offset_pagination:
            pagination_strategy = "offset"
        else:
            pagination_strategy = "single_page"
    if pagination_strategy == "offset" and isinstance(page, OffsetPage) and page.offset > 100_000:
        warnings.append("deep OFFSET pagination is O(offset); prefer cursor or narrower filters")

    # 排序确定性：offset/cursor 分页必须有稳定排序
    if pagination_strategy in ("offset", "cursor") and not spec.order_by:
        if pushed_sort or caps.sort_pushdown:
            pagination_note = (pagination_note or "") + " stable order key appended"
        else:
            warnings.append("source lacks sort pushdown; paginated results may not be stable across pages")

    # ---- 估算 ----
    bbox_ratio = 1.0
    if spec.spatial is not None and spec.spatial.op == "bbox":
        q_area = _desc_bbox_area(spec.spatial.bbox)
        d_area = _desc_bbox_area(getattr(descriptor, "bbox", None))
        if q_area is not None and d_area and d_area > 0:
            bbox_ratio = max(0.000001, min(1.0, q_area / d_area))
    estimated_rows = _estimate_rows(spec, descriptor, bbox_ratio, stats)
    if aggregate_requested and estimated_rows is not None:
        if spec.group_by:
            groups_est = estimate_group_cardinality(spec.group_by, stats, estimated_rows)
            estimated_rows = min(estimated_rows, groups_est or _AGG_GROUPS_ESTIMATE)
        else:
            estimated_rows = max(1, len(spec.aggregate or [1]))
    estimated_bytes: Optional[int] = None
    # 页窗口（本查询实际会传输的行上界）：字节估算与预算检查都以此为准——
    # LIMIT 100 的页查询不应因数据集总量巨大而被拒（只看 fetch 窗口）。
    if isinstance(page, OffsetPage):
        page_window = page.offset + page.limit
    else:
        page_window = page.limit
    if estimated_rows is not None and result_mode in (ResultMode.FEATURES, ResultMode.MATERIALIZE, ResultMode.SAMPLE):
        per_feat = _BYTE_PER_FEATURE_GEO if not spec.select else _BYTE_PER_FEATURE_DEFAULT
        fetch_rows = min(estimated_rows, page_window)
        estimated_bytes = int(fetch_rows * per_feat)

    # ---- 预算检查（planning-time；执行器仍有 runtime 检查）----
    budget = spec.execution
    if result_mode in (ResultMode.FEATURES, ResultMode.MATERIALIZE):
        cap_rows = spec.output.max_features or budget.max_rows
        if page_window > cap_rows:
            raise QueryBudgetExceededError(
                f"page window ({page_window}) exceeds row budget ({cap_rows})",
                details={
                    "hint": "reduce limit/offset, add bbox or filters, use aggregation, "
                            "or request a sample",
                },
            )
        if estimated_bytes is not None and estimated_bytes > budget.max_bytes:
            raise QueryBudgetExceededError(
                f"estimated result ~{estimated_bytes} bytes exceeds budget {budget.max_bytes}",
                details={
                    "hint": "narrow bbox, add filters, project fewer fields, or use "
                            "aggregation/sample result mode",
                    "estimated_bytes": estimated_bytes,
                },
            )

    # ---- mode 降级建议（不是错误）：大数据 + FEATURES → 采样提示 ----
    if (
        result_mode == ResultMode.FEATURES
        and estimated_rows is not None
        and estimated_rows > budget.max_rows
    ):
        warnings.append(
            f"estimated rows ({estimated_rows}) exceed feature budget ({budget.max_rows}); "
            "consider SAMPLE mode, aggregation, or VECTOR_TILE"
        )

    # ---- 性能警告：geometry 无索引 ----
    meta = getattr(descriptor, "metadata", None) or {}
    if isinstance(meta, dict):
        if meta.get("has_geometry_index") is False and spec.spatial is not None:
            warnings.append(
                "geometry column has no spatial index; spatial pushdown will full-scan "
                "(admin may create a GiST index)"
            )
        if meta.get("revision_strength") == "weak":
            warnings.append("dataset revision is weak; cached results may be stale")

    # ---- 组装 ----
    execution_mode = "pushdown"
    if local_filters:
        execution_mode = "hybrid" if (pushed_filters or pushed_spatial or pushed_aggregation) else "local_fallback"

    steps.insert(0, ExecutionFragment(
        step="normalize",
        description=f"QuerySpecV2 normalized (fingerprint {query_fp})",
    ))
    if pushed_spatial or spec.spatial is not None:
        steps.append(ExecutionFragment(
            step="spatial",
            description=(
                f"{spec.spatial.op} pushed to source" if pushed_spatial
                else f"{spec.spatial.op} evaluated locally"
            ),
            pushed=pushed_spatial,
        ))
    if pushed_filters:
        steps.append(ExecutionFragment(step="filter_pushdown", description=" AND ".join(pushed_filters), pushed=True))
    if pushed_projection:
        steps.append(ExecutionFragment(step="projection", description=f"{len(spec.select or [])} fields", pushed=True))
    if pushed_aggregation:
        steps.append(ExecutionFragment(
            step="aggregation",
            description=", ".join(a.func + (f"({a.field})" if a.field else "()") for a in spec.aggregate or [])
            + (f" GROUP BY {', '.join(spec.group_by)}" if spec.group_by else ""),
            pushed=True,
        ))
    for lf in local_filters:
        steps.append(ExecutionFragment(step="local_op", description=lf))

    plan = QueryPlan(
        source_type=caps.source_type,
        source_id=source_id,
        dataset_id=dataset_id,
        dataset_fingerprint=dataset_fingerprint,
        query_fingerprint=query_fp,
        normalized_query=spec.canonical_dict(),
        pushed_filters=pushed_filters,
        local_filters=local_filters,
        pushed_projection=pushed_projection,
        pushed_spatial=pushed_spatial,
        pushed_aggregation=pushed_aggregation,
        pushed_sort=pushed_sort,
        pagination_strategy=pagination_strategy,  # type: ignore[arg-type]
        pagination_note=pagination_note.strip() if pagination_note else None,
        estimated_rows=estimated_rows,
        estimated_bytes=estimated_bytes,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        result_mode=result_mode,
        fallback_reason=(
            "capability gaps forced local execution" if execution_mode == "local_fallback" else None
        ),
        warnings=warnings,
        steps=steps,
    )

    # ---- V3：成本分解 + 有界备选 + 假设标注（additive；不改选中决策）----
    from app.services.data_fabric.query import optimizer

    pushed_any = bool(pushed_filters or pushed_spatial or pushed_aggregation or pushed_sort)
    local_rows = estimated_rows if local_filters else 0
    plan.cost = optimizer.cost_of_chosen(
        estimated_rows=estimated_rows,
        estimated_bytes=estimated_bytes,
        pushed_any=pushed_any,
        local_rows=local_rows,
    ).model_dump()
    plan.alternatives = [
        alt.model_dump()
        for alt in optimizer.generate_alternatives(
            source_type=caps.source_type,
            estimated_rows=estimated_rows,
            page_window=page_window,
            budget_max_rows=spec.output.max_features or budget.max_rows,
            budget_max_bytes=budget.max_bytes,
            filter_pushed=filter_ok or spec.filter is None,
            spatial_pushed=pushed_spatial or spec.spatial is None,
            aggregation_pushed=pushed_aggregation or not aggregate_requested,
            aggregate_requested=aggregate_requested,
            projection_pushed=pushed_projection,
            has_select=spec.select is None,
            order_by=bool(spec.order_by),
            sort_pushed=pushed_sort,
            vector_tiles=caps.vector_tiles,
            result_mode=result_mode.value if hasattr(result_mode, "value") else str(result_mode),
        )
    ]
    filter_est = estimate_predicate_selectivity(spec.filter, stats)
    if stats is None or filter_est.is_default:
        plan.assumptions.append(
            "selectivity uses built-in default constants (no column statistics "
            "available); estimates are assumptions, not measurements"
        )
    plan.statistics_confidence = stats.confidence if stats is not None else None
    return plan


def dataset_version_from_descriptor(
    descriptor: Any, fingerprint: Optional[str]
) -> DatasetVersion:
    """从 descriptor 提取 DatasetVersion（诚实标注 revision_strength）。"""
    meta = getattr(descriptor, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    content_hint = meta.get("etag") or meta.get("last_modified") or meta.get("content_hint")
    observed = meta.get("observed_at")
    strength = "strong" if (content_hint or fingerprint) else "weak"
    return DatasetVersion(
        descriptor_fingerprint=fingerprint,
        schema_fingerprint=meta.get("schema_fingerprint"),
        content_hint=str(content_hint) if content_hint else None,
        source_revision=meta.get("source_revision"),
        observed_at=str(observed) if observed else None,
        revision_strength=strength,  # type: ignore[arg-type]
    )


__all__ = ["plan_query", "parse_epsg", "dataset_srid", "dataset_version_from_descriptor"]
