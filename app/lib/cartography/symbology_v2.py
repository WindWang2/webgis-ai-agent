"""Symbology V2 — C1 契约的表达域扩展（V11 W2，ADR-0162）。

C1 冻结契约（§2.1）的**只加不改**扩展：``SymbologyDecision`` 增四个可选
字段（``bivariate`` / ``temporal_ramp`` / ``uncertainty`` / ``cost_hint``），
由本模块的裁决函数产出。设计纪律：

- **只加不改**：全部新字段 ``Optional`` 且默认 ``None`` —— 不设置时
  ``SymbologyDecision`` 的序列化形状与 V10 逐字节一致（消费方零破坏）；
- **不重裁决**：bivariate/temporal/uncertainty 是「表达域」扩展 —— 主通道
  （method/k/palette）仍由 :func:`resolve_symbology` 唯一裁决；本模块只
  在其上叠加第二表达通道，并复用既有库（``bivariate.py`` 色矩阵、
  ``extrusion_model.py`` 高度分布、``palettes.py`` 可分辨性）；
- **确定性**：纯函数，同输入恒同输出；golden corpus 锁定。

三段能力（W2.1/2.2/2.3）+ 两个共享信号（W2.4/2.6）：

1. :func:`resolve_bivariate` —— 双变量色阵进 SymbologyDecision（点阵/方格
   两类布局）；
2. :func:`resolve_temporal_ramp` —— 时序色带（多时相同一色带，跨图 legend
   一致性由确定性 ramp_id + 固定停靠点保证）；
3. :func:`resolve_uncertainty` —— 不确定性表达（opacity/hatch/band 三模式，
   与 ``upgrade_legend_spec_v2`` 的 uncertainty 字段对齐）；
4. :func:`extrusion_dual_channel` —— 3D 挤出的高度/色彩双通道裁决（高度
   分布校验沿用 extrusion_model，色彩通道沿用 resolve_symbology）；
5. :func:`compute_pixel_density` —— 要素数/视口像素面积（W4 符号律共享的
   密度信号单点；``SymbologyProfile.feature_density`` 的标准来源）；
6. :func:`attach_cost_hint` —— 成本提示（k × 布局的确定性估算，W8 成本
   治理的决策面挂点）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.bivariate import (
    SUPPORTED_BIVARIATE_N,
    bivariate_class_colors,
    compute_bivariate_classes,
)
from app.lib.cartography.extrusion_model import analyze_height_field_distribution
from app.lib.cartography.palettes import (
    COLOR_PALETTES,
    get_color_from_palette,
)
from app.lib.cartography.symbology import (
    BivariateSpec,
    CostHint,
    SymbologyDecision,
    TemporalRampSpec,
    UncertaintySpec,
    symbology_decision_from_values,
)


# ── W2.1 双变量 ──────────────────────────────────────────────────────────

_BIVARIATE_LAYOUTS = ("grid", "dot")
_BIVARIATE_CLASSES = tuple(SUPPORTED_BIVARIATE_N)


def resolve_bivariate(
    *,
    field_x: str,
    field_y: str,
    layout: str = "grid",
    matrix: str = "BiPurpleOrange",
    classes: int = 3,
) -> BivariateSpec:
    """双变量裁决：色阵解析（复用 bivariate_class_colors）+ 布局声明。

    非法布局/类数 fail-closed（ValueError）；类数词表与 ``SUPPORTED_BIVARIATE_N``
    同源（库存矩阵均为 3×3）。分类计算与落格在 :func:`bivariate_assign`
    （需要要素数据时才做 —— 裁决期与数据期分离）。"""
    if layout not in _BIVARIATE_LAYOUTS:
        raise ValueError(f"未知布局: {layout}（合法值：{','.join(_BIVARIATE_LAYOUTS)}）")
    if classes not in _BIVARIATE_CLASSES:
        raise ValueError(f"双变量类数仅支持 {list(_BIVARIATE_CLASSES)}（库存矩阵口径）")
    n = classes * classes
    colors = bivariate_class_colors(matrix, classes)
    if len(colors) != n:
        raise ValueError(f"矩阵 {matrix} 在 n={classes} 下色数异常: {len(colors)} != {n}")
    return BivariateSpec(
        field_x=field_x, field_y=field_y, matrix=matrix, layout=layout,
        classes=classes, colors=colors,
    )


def bivariate_assign(
    spec: BivariateSpec, rows: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """逐要素落格（确定性；复用 ``compute_bivariate_classes`` 单点实现）。

    ``rows`` 形态：``[{"id":..., field_x: v, field_y: v}, ...]``（扁平字段）。
    返回 ``(落格清单, payload)``：落格清单为
    ``[{id, bin_x, bin_y, color}]``（跳过非数值要素 —— NaN 语义诚实，
    与 bivariate 库同一口径）；payload 为库返回的断点/计数审计。
    """
    features = [
        {"id": r.get("id", ""),
         "properties": {spec.field_x: r.get(spec.field_x),
                        spec.field_y: r.get(spec.field_y)}}
        for r in rows
    ]
    payload = compute_bivariate_classes(
        features, field_a=spec.field_x, field_b=spec.field_y, n=spec.classes,
    )
    n = spec.classes
    out: List[Dict[str, Any]] = []
    for f in features:
        idx = (f.get("properties") or {}).get("__biv_class", -1)
        if not isinstance(idx, int) or idx < 0:
            continue
        bx, by = idx % n, idx // n
        out.append({
            "id": f["id"], "bin_x": bx, "bin_y": by,
            "color": spec.colors[idx],
        })
    return out, payload


# ── W2.2 时序色带 ────────────────────────────────────────────────────────

#: 时序色带基准（感知均匀单色系：跨图 legend 一致性 + 时相单调）。
TEMPORAL_BASE_PALETTE = "Viridis"


def resolve_temporal_ramp(
    *,
    time_field: str,
    periods: List[str],
    base_palette: str = TEMPORAL_BASE_PALETTE,
) -> TemporalRampSpec:
    """时序色带裁决（确定性）：periods 升序去重 → 同一 ramp_id 的固定取色。

    ``ramp_id`` 由 base_palette + 时相数决定（``temporal-{palette}-{n}``），
    同参数跨会话/跨图恒同 —— 多时相同图、多图对比时 legend 逐色一致。
    """
    # 升序去重（字典序：ISO 风格时相标签天然有序）—— 跨图一致性的前提
    ordered = sorted({p.strip() for p in periods if p and p.strip()})
    if len(ordered) < 2:
        raise ValueError("时序色带需要 ≥2 个时相")
    if base_palette not in COLOR_PALETTES:
        raise ValueError(f"未知色带: {base_palette}")
    n = len(ordered)
    colors = [get_color_from_palette(base_palette, (i + 0.5) / n) for i in range(n)]
    return TemporalRampSpec(
        time_field=time_field,
        ramp_id=f"temporal-{base_palette.lower()}-{n}",
        periods=ordered,
        colors=colors,
        legend_locked=True,
    )


# ── W2.3 不确定性 ────────────────────────────────────────────────────────

_UNCERTAINTY_MODES = ("opacity", "hatch", "band")


def resolve_uncertainty(
    *,
    field: str,
    mode: str = "opacity",
    min_opacity: float = 0.25,
    max_opacity: float = 1.0,
) -> UncertaintySpec:
    """不确定性裁决：三模式 + 透明度带校验（fail-closed）。"""
    if mode not in _UNCERTAINTY_MODES:
        raise ValueError(f"未知模式: {mode}（合法值：{','.join(_UNCERTAINTY_MODES)}）")
    if not (0.0 <= min_opacity < max_opacity <= 1.0):
        raise ValueError("透明度带必须满足 0 ≤ min < max ≤ 1")
    disclosures: List[str] = []
    if mode == "opacity":
        disclosures.append(
            "透明度双编码：不确定度越高填充越透——底图纹理会透出，属表达语义而非渲染缺陷"
        )
    elif mode == "hatch":
        disclosures.append("晕渲密度随不确定度增大——打印/灰度上下文可分辨性由 hatching 图元承担")
    else:
        disclosures.append("置信区间带：以主通道色的明度变体呈现上下界")
    return UncertaintySpec(
        field=field, mode=mode, min_opacity=min_opacity, max_opacity=max_opacity,
        disclosures=disclosures,
    )


def uncertainty_opacity_map(spec: UncertaintySpec, values: List[float]) -> List[float]:
    """不确定度 → 透明度映射（线性归一，确定性；opacity 模式渲染面）。"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    return [
        round(spec.min_opacity + (spec.max_opacity - spec.min_opacity)
              * (v - lo) / span, 4)
        for v in values
    ]


# ── W2.4 3D extrusion 双通道 ─────────────────────────────────────────────

def extrusion_dual_channel(
    *,
    height_field: str,
    heights: List[float],
    color_field: Optional[str] = None,
    color_values: Optional[List[float]] = None,
    color_context: str = "screen",
) -> Dict[str, Any]:
    """3D 挤出双通道裁决：高度通道（分布校验沿用 extrusion_model）+
    色彩通道（resolve_symbology 独立裁决 method/k/palette）。

    双通道各自裁决、互不覆盖：高度单调表达量级，色彩表达另一变量（或
    高度同变量时按决策声明披露「冗余双编码」）。返回可序列化工件。
    """
    height_report = analyze_height_field_distribution(heights)
    decision: Optional[SymbologyDecision] = None
    redundant = False
    if color_values and color_field:
        decision = symbology_decision_from_values(
            [v for v in color_values
             if isinstance(v, (int, float)) and not isinstance(v, bool)],
            context=color_context,
        )
        redundant = color_field == height_field
    disclosures: List[str] = []
    if redundant:
        disclosures.append(
            "高度/色彩同变量双编码：冗余强化（认知冗余可接受，但 legend 必须只描述一条映射）"
        )
    return {
        "height_field": height_field,
        "height_channel": height_report,
        "color_channel": decision.model_dump() if decision else None,
        "color_field": color_field,
        "redundant_encoding": redundant,
        "disclosures": disclosures,
    }


# ── W2.6 像素密度（与 W4 符号律共享的单点信号）──────────────────────────

def compute_pixel_density(
    feature_count: int, viewport_px_w: int, viewport_px_h: int
) -> float:
    """要素数 / 视口像素面积（千px²）—— ``SymbologyProfile.feature_density``
    与 W4 前端符号律共享的密度信号单点。非法视口 fail-closed（0.0）。"""
    if viewport_px_w <= 0 or viewport_px_h <= 0 or feature_count <= 0:
        return 0.0
    # 量纲与 SymbologyConstraints.density_soft_cap/hard_cap 同源（每千平方
    # 像素 4/15 的软硬上限）：1280×720 = 921.6 千px²，5000 要素 ≈ 5.43。
    area_kpx2 = (viewport_px_w * viewport_px_h) / 1_000.0
    return round(feature_count / area_kpx2, 4)


# ── 成本提示 ─────────────────────────────────────────────────────────────

def attach_cost_hint(decision: SymbologyDecision) -> SymbologyDecision:
    """给决策挂 cost_hint（确定性估算；W8 成本治理的决策面挂点）。"""
    est_paint_ops = 2 + int(decision.k)          # fill + stroke + 逐级 legend
    est_legend_rows = int(decision.k)
    heavy = est_paint_ops > 9
    decision.cost_hint = CostHint(
        est_paint_ops=est_paint_ops, est_legend_rows=est_legend_rows, heavy=heavy,
    )
    return decision


__all__ = [
    "BivariateSpec",
    "TemporalRampSpec",
    "UncertaintySpec",
    "CostHint",
    "TEMPORAL_BASE_PALETTE",
    "resolve_bivariate",
    "bivariate_assign",
    "resolve_temporal_ramp",
    "resolve_uncertainty",
    "uncertainty_opacity_map",
    "extrusion_dual_channel",
    "compute_pixel_density",
    "attach_cost_hint",
]
