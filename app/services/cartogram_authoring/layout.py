"""G47 布局 artifact（WP07）：示意坐标 + 半径 + 身份 + 指标 + 参考轮廓。

artifact 是**唯一**的成果真源：图面、导出、Pi 回执、原图联动都从它读。它明确
声明 ``coordinate_space="diagram"``、``units="diagram_unit"``，并且**没有**任何
伪地理字段（没有 EPSG、没有经纬度圆心、没有假 GeoJSON 面）。下游若想拿它做
距离/面积/路由计算，会先撞上这个声明。

大明细有界：最多 ``MAX_FEATURES_HARD`` 个圆、``MAX_REFERENCE_POINTS`` 个参考轮廓
点；超出时如实 ``truncated``，不悄悄丢数据。

两阶段（避免"先算指纹再补指标"的自我矛盾）：

1. ``plan_circles``：把求解结果归一化成图面坐标（等比缩放，保持面积比例），
   产出质量 oracle 需要的全部输入；
2. ``assemble_layout``：用独立质量报告 + 参考轮廓组装最终 artifact 与指纹。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from app.services.cartogram_authoring.contract import (
    ALGORITHM_ID,
    ALGORITHM_VERSION,
    CITATION,
    COORDINATE_SPACE,
)
from app.services.cartogram_authoring.errors import AuthoringError
from app.services.cartogram_authoring.geography import PreparedGeography
from app.services.cartogram_authoring.quality import (
    RADIUS_DECIMALS,
    Circle,
    QualityReport,
)
from app.services.cartogram_authoring.quantities import (
    MIN_VISIBLE_RADIUS,
    AreaScale,
    QuantityScan,
    reference_circles,
)
from app.services.cartogram_authoring.solver import SolveOutcome

LAYOUT_SCHEMA = "cartogram_layout.v1"
MAX_REFERENCE_POINTS = 9000
MAX_REFERENCE_RING_POINTS = 48
LAYOUT_MARGIN = 0.04
#: 圆心/anchor/参考轮廓的落盘小数位（与半径同精度，避免"圆心 4 位、半径 6 位"
#: 这种下游对不齐的错位）。
GEOMETRY_DECIMALS = RADIUS_DECIMALS


@dataclass
class NormalizedLayout:
    circles: List[Circle]
    labels: Dict[str, str]
    coefficient: float              # 归一后的共同系数 k'
    factor: float                   # 原始 → 图面的等比缩放
    max_radius: float
    extent: float
    reference_rings: List[Dict[str, Any]]
    reference_truncated: bool
    adjacency_edges: List[Tuple[str, str, float]]


@dataclass(frozen=True)
class LayoutBuild:
    layout: Dict[str, Any]
    fingerprint: str


def _ring_points(coords: Sequence[Sequence[float]]) -> List[List[float]]:
    return [[float(p[0]), float(p[1])] for p in coords if len(p) >= 2]


def _outline_rings(geom) -> List[List[List[float]]]:
    from shapely.geometry import MultiPolygon, Polygon

    rings: List[List[List[float]]] = []
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else (
        [geom] if isinstance(geom, Polygon) else [])
    for poly in polys:
        rings.append(_ring_points(poly.exterior.coords))
        for interior in poly.interiors:
            rings.append(_ring_points(interior.coords))
    return rings


def _simplify_for_reference(geom, tolerance: float):
    try:
        simplified = geom.simplify(tolerance, preserve_topology=True)
        return simplified if not simplified.is_empty else geom
    except Exception:
        return geom


def plan_circles(
    *,
    plan: Any,
    scan: QuantityScan,
    prepared: PreparedGeography,
    scale: AreaScale,
    positions: Dict[str, Tuple[float, float]],
    anchors: Dict[str, Tuple[float, float]],
    source_frame: Any = None,
) -> NormalizedLayout:
    """求解结果 → 图面坐标（等比缩放 + 居中），面积比例不变。

    ``positions``/``anchors`` 都已是与半径同量纲的初始化空间坐标（投影 anchor
    先经等比归一），因此位移指标（位移/最大半径）才有意义。

    ``source_frame`` 是「投影米 → 上式初始化空间」的仿射（由 compute 层给出，
    与 anchor 归一化用的是**同一个** factor/offset）。参考轮廓的几何是投影米
    坐标，必须经它再套 ``_tx/_ty`` 才能与圆同框；不给就直接把米值当图面坐标
    ——那是错的，所以缺省为 ``None`` 时按「几何已在图面空间」处理，仅供单测。
    """
    extent = float(plan.area_scale.extent)
    usable = extent * (1.0 - 2.0 * LAYOUT_MARGIN)

    unit_index = prepared.index()
    raw: List[Dict[str, Any]] = []
    for entry in scan.positives:
        idx = unit_index.get(entry.feature_id)
        pos = positions.get(entry.feature_id)
        if idx is None or pos is None:
            continue
        x, y = pos
        r = scale.radius(float(entry.value))
        ax, ay = anchors.get(entry.feature_id, prepared.units[idx].anchor)
        raw.append({
            "feature_id": entry.feature_id,
            "label": entry.label,
            "value": float(entry.value),
            "radius_raw": r,
            "x_raw": x, "y_raw": y,
            "anchor_raw": (ax, ay),
        })
    if not raw:
        raise ValueError("layout 没有可绘制的圆")

    # 图面范围必须**同时**容纳圆、anchor 与参考轮廓：anchor 是"原位置"参照，
    # 若只按圆的包围盒定标，圆被吸引挤紧后 factor 会被放大，anchor 会被推到画布
    # 外 —— 那样"从原位置移了多远"在图上就无法表达；参考轮廓比"代表性点"的
    # 包围盒更大（点落在面内部），只按点定标会让灰线轮廓溢出 viewBox。
    # 仍然是**单一** uniform factor，因此面积比例不受影响。
    min_x = min(min(c["x_raw"] - c["radius_raw"], c["anchor_raw"][0]) for c in raw)
    max_x = max(max(c["x_raw"] + c["radius_raw"], c["anchor_raw"][0]) for c in raw)
    min_y = min(min(c["y_raw"] - c["radius_raw"], c["anchor_raw"][1]) for c in raw)
    max_y = max(max(c["y_raw"] + c["radius_raw"], c["anchor_raw"][1]) for c in raw)
    for unit in prepared.units:
        bounds = getattr(unit.geometry, "bounds", None)
        if not bounds or len(bounds) != 4:
            continue
        gx0, gy0, gx1, gy1 = (float(bounds[0]), float(bounds[1]),
                              float(bounds[2]), float(bounds[3]))
        if source_frame is not None:
            ax0, ax1 = source_frame.x(gx0), source_frame.x(gx1)
            ay0, ay1 = source_frame.y(gy0), source_frame.y(gy1)
            gx0, gx1 = min(ax0, ax1), max(ax0, ax1)
            gy0, gy1 = min(ay0, ay1), max(ay0, ay1)
        min_x = min(min_x, gx0)
        max_x = max(max_x, gx1)
        min_y = min(min_y, gy0)
        max_y = max(max_y, gy1)
    span_x = max(1e-9, max_x - min_x)
    span_y = max(1e-9, max_y - min_y)
    factor = usable / max(span_x, span_y)
    # 可见性必须在**归一化后**复核：factor 由几何包围盒（含零值/缺失单元的
    # 远端几何）决定，可把全部半径缩小数个数量级；只在 build_area_scale 里
    # 按归一前量纲把关，会产出 publication_ready 但读者看不见的圆。
    min_drawn_radius = round(min(c["radius_raw"] for c in raw) * factor,
                             RADIUS_DECIMALS)
    if min_drawn_radius < MIN_VISIBLE_RADIUS:
        raise AuthoringError(
            "EXTREME_VALUE_RANGE",
            f"归一化后最小圆半径 {min_drawn_radius:.3e} < 可见阈值 "
            f"{MIN_VISIBLE_RADIUS}（factor={factor:.3e}）——"
            "逐圆放大/clamp 会破坏面积比例",
            field="area_scale.extent",
            details={"normalization_factor": factor,
                     "min_radius": min_drawn_radius,
                     "min_visible_radius": MIN_VISIBLE_RADIUS},
            correction_hint="排除远离统计单元范围的零值/缺失单元，或换用与统计"
                            "单元范围一致的源后重试。")
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    offset = extent / 2.0

    def _tx(x: float) -> float:
        return (x - cx) * factor + offset

    def _ty(y: float) -> float:
        return (y - cy) * factor + offset

    # 几何（投影米）→ 初始化空间 → 图面：先套 source_frame，再套 _tx/_ty。
    # 只套 _tx/_ty 会把 12,384,905 这类米值直接写进声明为 diagram 的 artifact，
    # 参考轮廓全部落在画布外（"原图参照"变成空）。
    if source_frame is not None:
        def _gx(x: float) -> float:
            return _tx(source_frame.x(x))

        def _gy(y: float) -> float:
            return _ty(source_frame.y(y))
    else:
        _gx, _gy = _tx, _ty

    circles: List[Circle] = []
    labels: Dict[str, str] = {}
    for c in sorted(raw, key=lambda item: item["feature_id"]):
        labels[c["feature_id"]] = c["label"]
        # 落盘精度**只在这里**量化一次：oracle 复核的、artifact 写出的、下游
        # 回读的是同一组数（否则 round 引入的面积失真无人复核）。
        circles.append(Circle(
            feature_id=c["feature_id"],
            x=round(_tx(c["x_raw"]), GEOMETRY_DECIMALS),
            y=round(_ty(c["y_raw"]), GEOMETRY_DECIMALS),
            radius=round(c["radius_raw"] * factor, RADIUS_DECIMALS),
            value=c["value"],
            anchor=(round(_tx(c["anchor_raw"][0]), GEOMETRY_DECIMALS),
                    round(_ty(c["anchor_raw"][1]), GEOMETRY_DECIMALS)),
        ))

    # 参考轮廓：同一坐标变换 + 有界简化（读者能看到"排布 vs 原地理"）。
    ref_rings: List[Dict[str, Any]] = []
    # 简化容差必须是**几何自身的单位**（米）。span_x/span_y 是初始化空间单位，
    # 除以 frame.factor 才回到米；否则容差小到等于没简化。
    span_in_source = max(span_x, span_y)
    if source_frame is not None and getattr(source_frame, "factor", 0):
        span_in_source = span_in_source / float(source_frame.factor)
    tolerance = span_in_source / 400.0
    total_points = 0
    truncated = False
    for unit in prepared.units:
        if total_points >= MAX_REFERENCE_POINTS:
            truncated = True
            break
        simplified = _simplify_for_reference(unit.geometry, tolerance)
        out_rings: List[List[List[float]]] = []
        for ring in _outline_rings(simplified):
            if len(ring) > MAX_REFERENCE_RING_POINTS:
                step = max(1, len(ring) // MAX_REFERENCE_RING_POINTS)
                sampled = ring[::step]
                if sampled[-1] != ring[-1]:
                    sampled = sampled + [ring[-1]]
                ring = sampled
            pts = [[round(_gx(p[0]), GEOMETRY_DECIMALS),
                    round(_gy(p[1]), GEOMETRY_DECIMALS)] for p in ring]
            total_points += len(pts)
            out_rings.append(pts)
            if total_points >= MAX_REFERENCE_POINTS:
                truncated = True
                break
        if out_rings:
            ref_rings.append({"feature_id": unit.feature_id, "rings": out_rings})

    longest = max((e.shared_length for e in prepared.edges), default=0.0) or 1.0
    adjacency_edges = [(e.a, e.b, float(e.shared_length) / longest)
                       for e in prepared.edges]

    return NormalizedLayout(
        circles=circles, labels=labels,
        coefficient=float(scale.coefficient) * factor * factor,
        factor=float(factor), max_radius=max(c.radius for c in circles),
        extent=extent, reference_rings=ref_rings,
        reference_truncated=truncated, adjacency_edges=adjacency_edges)


def assemble_layout(
    *,
    plan: Any,
    source_anchor: Dict[str, Any],
    plan_digest: str,
    candidate_digest: str,
    scan: QuantityScan,
    prepared: PreparedGeography,
    scale: AreaScale,
    outcome: SolveOutcome,
    norm: NormalizedLayout,
    quality: QualityReport,
) -> LayoutBuild:
    """归一化圆 + 独立质量报告 → 可持久布局 artifact。"""
    legend = {
        "quantity": scan.field,
        "unit": scan.unit,
        "title": plan.title or f"{scan.field}（{scan.unit}）",
        "reference_circles": [
            {"value": r["value"], "radius": round(r["radius"] * norm.factor, 4)}
            for r in reference_circles(scale)
        ],
        "zero_note": "零值区域保留在清单与原图中，不绘制正面积圆",
        "missing_note": "缺失值不参与总量，也不与零合并",
    }

    failed = set(quality.failed)
    publication_ready = (
        bool(outcome.converged) and quality.status == "pass" and not failed)
    status = "converged" if outcome.converged else "not_converged"

    disclosures = [
        "圆心位置是示意布局（diagram units），不是经纬度、不是真实地理位置；"
        "不得用于距离/面积/路由计算",
        "圆面积与数量成正比（共同系数）；圆不保留原行政边界、真实距离或全部邻接关系",
        f"原邻接保留 {quality.adjacency.get('retained')}/"
        f"{quality.adjacency.get('original_edges')}；丢失部分按方法性质如实披露，"
        "不声称保持拓扑",
        f"源 {source_anchor.get('ref_id')} @ rev {source_anchor.get('content_revision')}"
        f"（{source_anchor.get('content_fingerprint')}）",
        f"算法 {ALGORITHM_ID} {ALGORITHM_VERSION}；{CITATION}",
    ]
    if scan.zeros:
        disclosures.append(f"{len(scan.zeros)} 个零值区域：不画正面积圆，保留身份与 0 说明")
    if scan.missing:
        disclosures.append(f"{len(scan.missing)} 个缺失值区域：不参与总量，不与零合并")
    if plan.scope.exclude_ids:
        disclosures.append(
            f"已排除 {len(plan.scope.exclude_ids)} 个区域（逐项理由见 excluded.reasons）——"
            "总量与覆盖范围不包含它们")
    if not publication_ready:
        disclosures.append(
            "未达到发布资格门：status=" + status + "，质量门 "
            + ("通过" if quality.status == "pass" else
               "未通过（" + ",".join(sorted(failed)) + "）")
            + "；可保存为诊断草稿，但不得标记 publication-ready")

    layout: Dict[str, Any] = {
        "kind": "cartogram_layout",
        "schema_version": LAYOUT_SCHEMA,
        "algorithm": {
            "id": ALGORITHM_ID, "version": ALGORITHM_VERSION, "citation": CITATION,
            "mode": plan.mode,
            "parameters": plan.solver.model_dump(mode="json"),
            "method": "circle displacement: overlap repulsion + original-adjacency attraction",
        },
        "coordinate_space": COORDINATE_SPACE,
        "units": "diagram_unit",
        "is_geographic": False,
        "extent": {"width": norm.extent, "height": norm.extent,
                   "margin": LAYOUT_MARGIN, "uniform_scale": True},
        "source": dict(source_anchor),
        "plan_digest": plan_digest,
        "candidate_digest": candidate_digest,
        "quantity": scan.summary(),
        "projection": {
            "source_crs": prepared.source_crs,
            "preparation_crs": prepared.target_crs,
            "use": "anchors/adjacency only; never for layout coordinates",
        },
        "area_scale": {
            **scale.as_receipt(),
            "coefficient": round(norm.coefficient, 9),
            "max_radius": round(norm.max_radius, 4),
            # 归一化后 min_radius 也要跟着缩放，否则回执里 sqrt(k*value) 复现不出
            # 它（系数是图面量纲，min_radius 原来还是归一前量纲）。
            "min_radius": round(scale.min_radius * norm.factor, 4),
            "normalization_factor": round(norm.factor, 9),
            "radius_units": "diagram_unit",
        },
        "circles": [
            # 坐标/半径在 plan_circles 里已按落盘精度量化，这里**不再**二次 round，
            # 保证 oracle 复核的与写出的逐位相同。
            {
                "feature_id": c.feature_id,
                "label": norm.labels.get(c.feature_id, c.feature_id),
                "x": c.x, "y": c.y,
                "radius": c.radius, "value": c.value,
                "anchor": [c.anchor[0], c.anchor[1]],
            }
            for c in norm.circles
        ],
        "zero_ids": list(scan.zeros),
        "missing_ids": list(scan.missing),
        "excluded": {
            "ids": list(plan.scope.exclude_ids),
            "reasons": dict(plan.scope.exclude_reasons),
        },
        "adjacency": {
            "edges": [{"a": a, "b": b, "weight": round(w, 6)}
                      for a, b, w in norm.adjacency_edges],
            "original_edges": len(norm.adjacency_edges),
            "contract": prepared.diagnostics.get("adjacency_contract"),
        },
        "metrics": {**quality.as_receipt(), "solver": outcome.as_receipt()},
        "status": status,
        "publication_ready": publication_ready,
        "legend": legend,
        "reference": {
            "kind": "original_outlines",
            "coordinate_space": COORDINATE_SPACE,
            "outlines": norm.reference_rings,
            "truncated": norm.reference_truncated,
            "disclosure": "浅灰轮廓仅用于地理定位参照，不是布局的一部分",
        },
        "diagnostics": prepared.diagnostics,
        "disclosures": disclosures,
        "budgets": plan.budgets.model_dump(mode="json"),
    }
    # 指纹回填进 artifact 自身（下游 adapter/Pi/UI 都从这里读），但**不参与**
    # 指纹计算 —— 否则自引用。先占位再回填，保证同一内容得同一指纹。
    layout["metrics"]["layout_fingerprint"] = None
    fingerprint = layout_fingerprint(layout)
    layout["metrics"]["layout_fingerprint"] = fingerprint
    return LayoutBuild(layout=layout, fingerprint=fingerprint)


#: 不参与指纹的键：``elapsed_s`` 是墙钟耗时（同内容不同时刻必然不同），
#: ``layout_fingerprint`` 是指纹自身（回填用，自引用）。两者都不能影响身份。
_VOLATILE_KEYS = {"elapsed_s", "layout_fingerprint"}


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()
                if k not in _VOLATILE_KEYS}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def layout_fingerprint(layout: Dict[str, Any]) -> str:
    payload = json.dumps(_canonical(layout), sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), default=str)
    return "g47l-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def bounded_receipt(layout: Dict[str, Any], *, max_circles: int = 8) -> Dict[str, Any]:
    """Pi/UI 有界回执：指标 + 少量样例圆 + refs，不回传全量布局。"""
    circles = list(layout.get("circles") or [])
    return {
        "kind": layout.get("kind"),
        "schema_version": layout.get("schema_version"),
        "algorithm": layout.get("algorithm"),
        "coordinate_space": layout.get("coordinate_space"),
        "units": layout.get("units"),
        "is_geographic": False,
        "status": layout.get("status"),
        "publication_ready": layout.get("publication_ready"),
        "source": layout.get("source"),
        "plan_digest": layout.get("plan_digest"),
        "candidate_digest": layout.get("candidate_digest"),
        "quantity": layout.get("quantity"),
        "area_scale": layout.get("area_scale"),
        "metrics": layout.get("metrics"),
        "legend": layout.get("legend"),
        "adjacency": {
            "original_edges": (layout.get("adjacency") or {}).get("original_edges"),
            "retained": ((layout.get("metrics") or {}).get("adjacency") or {}).get("retained"),
        },
        "circle_count": len(circles),
        "circles_sample": circles[:max_circles],
        "zero_count": len(layout.get("zero_ids") or []),
        "missing_count": len(layout.get("missing_ids") or []),
        "excluded_count": len((layout.get("excluded") or {}).get("ids") or []),
        "reference_outline_count": len((layout.get("reference") or {}).get("outlines") or []),
        "disclosures": list(layout.get("disclosures") or [])[:12],
    }
