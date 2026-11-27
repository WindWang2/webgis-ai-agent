"""G47 纯计算管线（无 IO）：冻结要素 → 布局 artifact + 独立质量报告。

顺序即裁决顺序，任何一步失败都不留下"半个成果"：

    scan_quantities（字段/单位/零/缺失/非法）
      → clamp_plan_budget（要素上界；先于最重的投影+邻接）
      → prepare_geography（身份/几何/CRS/anchor/邻接）
      → clamp_plan_budget（邻接边上界）
      → build_area_scale（共同面积系数）
      → solve_dorling（有界位移）
      → plan_circles（等比归一）
      → evaluate_layout（独立 oracle）
      → assemble_layout（artifact + 指纹）

不收敛/质量门未过**不抛异常**：artifact 如实记录 ``status`` 与
``publication_ready=False``（D20/D21 要求保存真实失败而非伪成功）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.cartogram_authoring.contract import (
    AuthoringPlan,
    clamp_plan_budget,
)
from app.services.cartogram_authoring.errors import AuthoringError
from app.services.cartogram_authoring.geography import prepare_geography
from app.services.cartogram_authoring.layout import (
    LayoutBuild,
    assemble_layout,
    plan_circles,
)
from app.services.cartogram_authoring.quality import evaluate_layout, gate_from_plan
from app.services.cartogram_authoring.quantities import (
    build_area_scale,
    scan_quantities,
)
from app.services.cartogram_authoring.snapshot import FrozenSource
from app.services.cartogram_authoring.solver import SolveParams, solve_dorling


@dataclass(frozen=True)
class _AnchorFrame:
    """投影坐标（米）→ 初始化空间 的仿射；与 anchor 归一化用**同一个** factor/offset。

    存在的意义：参考轮廓的几何仍是投影米坐标，必须经它才能与圆同框。
    """

    factor: float
    cx: float
    cy: float
    offset: float

    def x(self, value: float) -> float:
        return (value - self.cx) * self.factor + self.offset

    def y(self, value: float) -> float:
        return (value - self.cy) * self.factor + self.offset


def _normalize_anchors(
    anchors: List[Tuple[float, float]], extent: float,
) -> Tuple[List[Tuple[float, float]], Optional[_AnchorFrame]]:
    """把投影 anchor 等比归一进 extent 画布（保持相对位置；只作初始化参考）。

    同时返回该归一化的仿射本身 —— 参考轮廓要用它才能落到同一张图上。
    """
    if not anchors:
        return [], None
    xs = [a[0] for a in anchors]
    ys = [a[1] for a in anchors]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    span = max(maxx - minx, maxy - miny)
    if span <= 1e-9:
        # 全重合锚点没有可表达的空间跨度：旧实现用 1e-9 兜底会把 factor
        # 放大到 1e11 级，几何经该仿射展开后把圆半径归一成 0 —— 与其产出
        # 不可读的布局，不如 typed 拒绝。
        raise AuthoringError(
            "EXTREME_VALUE_RANGE",
            "所有统计单元的锚点重合（跨度≈0）—— 无法建立可读的示意布局",
            field="scope",
            correction_hint="检查几何/投影是否有效（零面积或重复几何）；"
                            "排除退化单元后重试。")
    factor = (extent * 0.86) / span
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    offset = extent / 2.0
    frame = _AnchorFrame(factor=factor, cx=cx, cy=cy, offset=offset)
    return ([(frame.x(x), frame.y(y)) for x, y in anchors], frame)


def scoped_features(
    features: List[Dict[str, Any]], plan: AuthoringPlan,
) -> List[Dict[str, Any]]:
    """按 scope 先过滤，再统计。

    顺序有意义：``quantity.total`` / 各类计数 / ``build_area_scale`` 的取最大值
    都必须只看**纳入**的单元。否则披露文案说「总量不含被排除项」，而实际总量
    与共同系数 k 都由被排除项决定 —— 既不诚实，也让「排除异常值」这条纠正建议
    无法执行（排除后仍然报同一个错）。
    """
    include = plan.scope.include_ids
    exclude = set(plan.scope.exclude_ids or ())
    if include is None and not exclude:
        return features
    keep: List[Dict[str, Any]] = []
    for feat in features:
        if not isinstance(feat, dict):
            keep.append(feat)
            continue
        raw_id = feat.get("id")
        key = str(raw_id) if raw_id is not None else None
        if include is not None and key not in include:
            continue
        if key is not None and key in exclude:
            continue
        keep.append(feat)
    return keep


def validate_scope(plan: AuthoringPlan, features: List[Dict[str, Any]]) -> None:
    """预检 scope 的 id 与排除理由（与 worker 同一套裁决，预检期就拒绝）。"""
    available = {str(f.get("id")) for f in features
                 if isinstance(f, dict) and f.get("id") is not None}
    if plan.scope.include_ids is not None:
        unknown = [fid for fid in plan.scope.include_ids if fid not in available]
        if unknown:
            raise AuthoringError(
                "SCOPE_IDS_NOT_FOUND",
                f"scope.include_ids 中 {len(unknown)} 个 id 不在源中（如 {unknown[0]!r}）",
                status_code=422, field="scope.include_ids",
                details={"unknown_ids": unknown[:12]},
                correction_hint="先 inspect 取真实 id；不要用行号或名称猜。")
    unknown_excluded = [fid for fid in plan.scope.exclude_ids if fid not in available]
    if unknown_excluded:
        raise AuthoringError(
            "SCOPE_IDS_NOT_FOUND",
            f"scope.exclude_ids 中 {len(unknown_excluded)} 个 id 不在源中"
            f"（如 {unknown_excluded[0]!r}）",
            status_code=422, field="scope.exclude_ids",
            details={"unknown_ids": unknown_excluded[:12]},
            correction_hint="排除项必须是真实存在的区域；否则总量说明会失真。")
    missing_reasons = [fid for fid in plan.scope.exclude_ids
                       if not (plan.scope.exclude_reasons.get(fid) or "").strip()]
    if missing_reasons:
        raise AuthoringError(
            "INVALID_PLAN",
            f"{len(missing_reasons)} 个排除区域没有写理由（如 {missing_reasons[0]!r}）",
            field="scope.exclude_reasons",
            correction_hint="逐项给理由；发布说明要能解释覆盖范围。")


def compute_layout(
    plan: AuthoringPlan,
    frozen: FrozenSource,
    *,
    should_cancel: Optional[Callable[[], bool]] = None,
    cancellation_exception: Optional[Callable[[], BaseException]] = None,
) -> LayoutBuild:
    """确定性计算（同一冻结内容 + 同一计划 → 同一 artifact 指纹）。"""
    scoped = scoped_features(frozen.features, plan)
    scan = scan_quantities(
        scoped,
        field_name=plan.quantity.field, unit=plan.quantity.unit,
        zero_policy=plan.zero_policy, missing_policy=plan.missing_policy)
    # 要素上界必须在投影/邻接之前：超限源不该先付出最重的一步再被拒。
    clamp_plan_budget(plan, features=len(scoped), adjacency_edges=0)
    validate_scope(plan, frozen.features)

    prepared = prepare_geography(
        scoped,
        source_crs=plan.source_crs,
        target_crs=plan.projection.crs,
        snap_tolerance_m=plan.adjacency.snap_tolerance_m,
        min_shared_length_m=plan.adjacency.min_shared_length_m,
    )
    clamp_plan_budget(plan, features=len(prepared.units),
                      adjacency_edges=len(prepared.edges))

    scale = build_area_scale(scan, max_radius=plan.area_scale.max_radius)

    positive_ids = {e.feature_id for e in scan.positives}
    solve_units = [u for u in prepared.units if u.feature_id in positive_ids]
    if not solve_units:
        raise AuthoringError(
            "NO_VALID_QUANTITY",
            "范围内没有任何正值统计单元 —— 无法生成面积 cartogram",
            field="quantity.field")
    solve_ids = [u.feature_id for u in solve_units]
    solve_index = {fid: i for i, fid in enumerate(solve_ids)}
    value_by_id = {e.feature_id: float(e.value) for e in scan.positives}
    # 初始化空间必须与半径同量纲：把 anchor 等比归一进 extent 画布，否则
    # "米" 级的相邻距离与 diagram 单位半径混用会让吸引力把圆甩出画布。
    anchors, anchor_frame = _normalize_anchors(
        [u.anchor for u in solve_units], float(plan.area_scale.extent))
    radii = [scale.radius(value_by_id[u.feature_id]) for u in solve_units]
    longest = max((e.shared_length for e in prepared.edges), default=0.0) or 1.0
    edges: List[Tuple[int, int, float]] = []
    for e in prepared.edges:
        i = solve_index.get(e.a)
        j = solve_index.get(e.b)
        if i is None or j is None:
            continue
        edges.append((i, j, float(e.shared_length) / longest))

    outcome = solve_dorling(
        anchors=anchors, radii=radii, feature_ids=solve_ids, edges=edges,
        params=SolveParams(
            max_iterations=plan.solver.max_iterations,
            repulsion=plan.solver.repulsion,
            attraction=plan.solver.attraction,
            damping=plan.solver.damping,
            max_step_ratio=plan.solver.max_step_ratio,
            tolerance=plan.solver.tolerance,
            overlap_ratio_tolerance=plan.quality_gate.max_pair_overlap_ratio,
            max_seconds=plan.budgets.max_seconds,
        ),
        should_cancel=should_cancel,
        cancellation_exception=cancellation_exception,
    )

    positions: Dict[str, Tuple[float, float]] = {
        fid: outcome.positions[i] for i, fid in enumerate(solve_ids)
    }
    anchor_by_id: Dict[str, Tuple[float, float]] = {
        fid: anchors[i] for i, fid in enumerate(solve_ids)
    }
    norm = plan_circles(
        plan=plan, scan=scan, prepared=prepared, scale=scale,
        positions=positions, anchors=anchor_by_id,
        source_frame=anchor_frame)
    quality = evaluate_layout(
        circles=norm.circles, coefficient=norm.coefficient,
        gates=gate_from_plan(plan), adjacency_edges=norm.adjacency_edges,
        max_radius=norm.max_radius)

    anchor = frozen.anchor()
    built = assemble_layout(
        plan=plan, source_anchor=anchor, plan_digest=plan.plan_digest(),
        candidate_digest=plan.candidate_digest(anchor), scan=scan,
        prepared=prepared, scale=scale, outcome=outcome, norm=norm,
        quality=quality)
    return built


def summarize(built: LayoutBuild) -> Dict[str, Any]:
    """作业/回执用的有界摘要。"""
    layout = built.layout
    metrics = layout.get("metrics") or {}
    return {
        "layout_fingerprint": built.fingerprint,
        "candidate_digest": layout.get("candidate_digest"),
        "plan_digest": layout.get("plan_digest"),
        "status": layout.get("status"),
        "publication_ready": layout.get("publication_ready"),
        "circle_count": len(layout.get("circles") or []),
        "zero_count": len(layout.get("zero_ids") or []),
        "missing_count": len(layout.get("missing_ids") or []),
        "area": (metrics.get("area") or {}),
        "overlap": {
            k: (metrics.get("overlap") or {}).get(k)
            for k in ("max_pair_overlap_ratio", "overlapping_pairs",
                      "pairs_over_threshold", "pairs_over_threshold_ratio",
                      "fraction_pair_threshold", "total_overlap_area_ratio",
                      "pair_cap_violated", "fraction_cap_violated")
        },
        "displacement": {
            k: (metrics.get("displacement") or {}).get(k)
            for k in ("max_ratio", "p95_ratio", "mean_ratio")
        },
        "adjacency": {
            k: (metrics.get("adjacency") or {}).get(k)
            for k in ("original_edges", "retained", "lost", "retained_ratio")
        },
        "solver": {
            k: (metrics.get("solver") or {}).get(k)
            for k in ("iterations", "converged", "stop_reason", "elapsed_s")
        },
    }
