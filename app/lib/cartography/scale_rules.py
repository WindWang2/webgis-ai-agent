"""Scale-aware Cartography Rules（C3，ADR-0205 D5）—— 语义尺度带 × 表达动作.

定位：把「什么 zoom 该聚合 / 泛化 / 换表达 / 收可见范围」收敛成单一
可测试的确定性契约。与既有 zoom 分档的分工（不重抄数值）：

- **标注密度 / 字号 / terrain / 抽稀预算**：``label_plan``（ADR-0154
  zoomBands）与 ``scene_lod``（ADR-0199 LOD_BANDS）是唯一权威，本模块
  不产系数、不改分档；
- **表达层动作**（聚合建议 / 点表达资格 / 可见范围建议 / 边界泛化档）：
  本模块唯一权威。

边界对齐：``SCALE_TIERS`` 的 zoom 分界与 ``label_plan.DEFAULT_ZOOM_BANDS``
完全一致（0-8 / 8-11 / 11-14 / 14-24）——同一 zoom 心智模型覆盖标注与
表达两层，跨模块契约测试锁定相等（tests/cartography/
test_scale_rules_v1.py）。密度信号复用 ``symbology_v2.compute_pixel_density``
（W2.6 单点，不另立口径）。

纯函数、无 IO；同输入恒同输出。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.lib.cartography.label_plan import DEFAULT_ZOOM_BANDS
from app.lib.cartography.symbology_v2 import compute_pixel_density

# ── 尺度带（与 label_plan.DEFAULT_ZOOM_BANDS 同界）───────────────────────

class ScaleTier(BaseModel):
    """语义尺度带：zoom 半开区间 [min_zoom, max_zoom) × 语义动作表。

    ``min_zoom``/``max_zoom`` 由 :data:`SCALE_TIERS` 构建期从
    ``label_plan.DEFAULT_ZOOM_BANDS`` 抄录为字面量并由契约测试锁定相等
    （模块间只共享「分界事实」，不共享运行时对象——避免一条链改动牵着
    两个语义面）。
    """

    name: str                 # world / province / city / street
    min_zoom: float
    max_zoom: float
    aggregation_hint: str     # 建议聚合粒度（行政级或网格族）
    point_dense_candidates: Tuple[str, ...]   # 该带密集点表达的优先序
    dense_threshold: int      # 该带上「密集点」的要素数判定（表达切换门槛）
    visibility_hints: Dict[str, float] = Field(default_factory=dict)
    boundary_detail: str = "standard"   # 泛化档建议（渲染端消费者按现状落地）
    reason_code_prefix: str = "GRAMMAR.SCALE"


#: 四个语义尺度带（国家/省/市/街区等价面）。分界字面量必须等于
#: label_plan.DEFAULT_ZOOM_BANDS 的 (min_zoom, max_zoom) 序列（契约测试）。
_SCALE_TIER_ROWS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "world",
        "min_zoom": 0.0, "max_zoom": 8.0,
        "aggregation_hint": "admin0_admin1",
        "point_dense_candidates": ("visual_heatmap", "aggregate_grid", "proportional_symbol"),
        "dense_threshold": 1200,
        "visibility_hints": {"street_detail_minzoom": 14.0, "admin_boundary_detail": 0.0},
        "boundary_detail": "generalized",
    },
    {
        "name": "province",
        "min_zoom": 8.0, "max_zoom": 11.0,
        "aggregation_hint": "admin1_admin2",
        "point_dense_candidates": ("aggregate_grid", "visual_heatmap", "proportional_symbol"),
        "dense_threshold": 2000,
        "visibility_hints": {"street_detail_minzoom": 14.0, "admin_boundary_detail": 8.0},
        "boundary_detail": "standard",
    },
    {
        "name": "city",
        "min_zoom": 11.0, "max_zoom": 14.0,
        "aggregation_hint": "admin2_admin3",
        "point_dense_candidates": ("aggregate_grid", "point_cluster", "visual_heatmap"),
        "dense_threshold": 4000,
        "visibility_hints": {"street_detail_minzoom": 14.0, "admin_boundary_detail": 11.0},
        "boundary_detail": "detailed",
    },
    {
        "name": "street",
        "min_zoom": 14.0, "max_zoom": 24.0,
        "aggregation_hint": "none_raw",
        "point_dense_candidates": ("point_cluster", "point_overlay"),
        "dense_threshold": 8000,
        "visibility_hints": {"street_detail_minzoom": 0.0, "admin_boundary_detail": 11.0},
        "boundary_detail": "full",
    },
)


def _build_scale_tiers() -> Tuple[ScaleTier, ...]:
    """构建期断言：分界与 label_plan.DEFAULT_ZOOM_BANDS 完全一致。"""
    expected = [(b.min_zoom, b.max_zoom) for b in DEFAULT_ZOOM_BANDS]
    actual = [(r["min_zoom"], r["max_zoom"]) for r in _SCALE_TIER_ROWS]
    if actual != expected:
        raise AssertionError(
            f"SCALE_TIERS 分界 {actual} 与 label_plan.DEFAULT_ZOOM_BANDS {expected} 不一致")
    return tuple(ScaleTier(**row) for row in _SCALE_TIER_ROWS)


#: 冻结尺度带（import 期构建；分界漂移即 import 失败，fail-closed）。
SCALE_TIERS: Tuple[ScaleTier, ...] = _build_scale_tiers()

#: 点表达候选的合法词表（= MapModel 注册族的表达 id 子集；契约测试锁定
#: 每个词都可在 model_library registry 解析）。
POINT_REPRESENTATION_IDS: Tuple[str, ...] = (
    "visual_heatmap", "aggregate_grid", "proportional_symbol",
    "point_cluster", "point_overlay",
)


def resolve_scale_tier(zoom: float) -> ScaleTier:
    """zoom → 语义尺度带（半开区间；越界按最近带夹取，确定性）。"""
    if zoom < SCALE_TIERS[0].min_zoom:
        return SCALE_TIERS[0]
    for tier in SCALE_TIERS:
        if tier.min_zoom <= zoom < tier.max_zoom:
            return tier
    return SCALE_TIERS[-1]


#: hysteresis 死区半宽（zoom 单位）：跨带切换要求 zoom 越过带边界至少
#: ``TIER_HYSTERESIS_MARGIN``；带内回落 < margin 不回切 —— 消除临界 zoom
#: 抖动（连续手势在 8.0±0.1 徘徊不再每帧换带）。纯函数语义：见
#: :func:`resolve_scale_tier_stable`。
TIER_HYSTERESIS_MARGIN = 0.25


def resolve_scale_tier_stable(
    zoom: float,
    previous_tier: Optional[str] = None,
    *,
    margin: float = TIER_HYSTERESIS_MARGIN,
) -> ScaleTier:
    """带 hysteresis 的带解析（确定性纯函数，无时钟无状态）。

    语义：``previous_tier`` 是上一帧/上一决策的带名。zoom 仍在该带内 →
    留守；已出带但距带边界不足 ``margin`` → 仍留守（死区）；越过死区 →
    切到 :func:`resolve_scale_tier` 的无记忆结果。``previous_tier`` 缺省
    或不在词表 → 无记忆解析。非法 margin（<=0）按无记忆处理。
    """
    resolved = resolve_scale_tier(zoom)
    if previous_tier is None or margin <= 0:
        return resolved
    prev = next((t for t in SCALE_TIERS if t.name == previous_tier), None)
    if prev is None or prev.name == resolved.name:
        return resolved
    # 死区：zoom 距离带边界不足 margin 时留守前带（边界值本身属于新带 ——
    # 与无记忆解析在 margin=0 时完全一致）。
    if zoom < prev.min_zoom:
        return prev if (prev.min_zoom - zoom) < margin else resolved
    if zoom >= prev.max_zoom:
        return prev if (zoom - prev.max_zoom) < margin else resolved
    return resolved


#: Web Mercator 赤道基准（256px 瓦片，EPSG:3857 契约）：z0 单像素地面
#: 分辨率（米）。物理比例尺换算的唯一常数来源（打印/导出 parity 用）。
_MERCATOR_EQUATOR_M_PER_PX_Z0 = 156543.03392804097

#: Web Mercator 纬度钳制（±85.0511°—— Web Mercator 方形域边界；超出即
#: 投影未定义，cos 负值必须拒绝而非静默外推）。
_MERCATOR_MAX_LAT = 85.0511287798066

#: 比例尺换算缺省 DPI（CSS 基准英寸像素数；打印由调用方按 target_dpi 传入）。
DEFAULT_SCREEN_DPI = 96.0

#: 每英寸米数（比例尺分母换算常数）。
_INCH_IN_METERS = 0.0254


def meters_per_pixel(zoom: float, latitude: float = 0.0) -> float:
    """Web Mercator 地面分辨率（米/像素，256px 瓦片契约）。

    latitude 钳制到 ±85.0511（极区未定义，fail-safe 夹取而非 NaN）；
    zoom 负值夹 0（MapLibre 域 [0,24]）。确定性纯函数。
    """
    z = min(24.0, max(0.0, float(zoom)))
    lat = min(_MERCATOR_MAX_LAT, max(-_MERCATOR_MAX_LAT, float(latitude)))
    scale = 2.0 ** z
    return _MERCATOR_EQUATOR_M_PER_PX_Z0 * math.cos(math.radians(lat)) / scale


def scale_denominator(
    zoom: float,
    latitude: float = 0.0,
    *,
    dpi: float = DEFAULT_SCREEN_DPI,
) -> int:
    """物理比例尺分母 1:N（打印/导出与屏幕同口径的单一换算点）。

    N = (米/地面像素) ÷ (米/屏幕像素)——屏幕像素 = 0.0254/dpi 米。
    z0 赤道 96dpi ≈ 1:591M（Web Mercator 标准值）。结果向下取整为 ≥1
    整数（比例尺分母惯例）；非法 dpi fail-closed 到
    :data:`DEFAULT_SCREEN_DPI`。反子午线不影响（只依赖纬度），高纬度经
    钳制单调收敛。
    """
    effective_dpi = dpi if (isinstance(dpi, (int, float)) and dpi > 0) else DEFAULT_SCREEN_DPI
    m_per_px = meters_per_pixel(zoom, latitude)
    meters_per_screen_px = _INCH_IN_METERS / effective_dpi
    denom = m_per_px / meters_per_screen_px
    return max(1, int(denom))


# ── 尺度动作（表达层）────────────────────────────────────────────────────

class ScaleDecision(BaseModel):
    """一次尺度裁决工件：带 + 表达动作 + reason codes（可序列化）。"""

    zoom: float
    tier: str
    aggregation_hint: str
    boundary_detail: str
    point_candidates: List[str] = Field(default_factory=list)
    is_dense_points: bool = False
    visibility_hints: Dict[str, float] = Field(default_factory=dict)
    feature_density: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)

    @property
    def scale_too_small_for_raw_points(self) -> bool:
        """「scale too small → aggregate/generalize」契约判据（C2 规则消费）。"""
        return self.is_dense_points


def scale_actions(
    *,
    zoom: float,
    feature_count: int = 0,
    geometry: str = "",
    viewport_px_w: int = 1280,
    viewport_px_h: int = 720,
) -> ScaleDecision:
    """尺度 → 表达动作（确定性）。

    - 带：``resolve_scale_tier(zoom)``；
    - 点层密集判定：``feature_count`` 超过该带 ``dense_threshold``，或
      像素密度（``compute_pixel_density``）超过 8.0（symbology 软上限
      4.0 与硬上限 15.0 之间的表达切换带， rationale 见模块 docstring）；
    - 密集 → 候选按带优先序给出（world 偏热力/格网，street 只聚簇/原始）；
    - 非点几何（polygon/line）不给点表达候选（诚实：密集点规则不适用）。
    """
    tier = resolve_scale_tier(zoom)
    codes: List[str] = [f"GRAMMAR.SCALE.TIER_{tier.name.upper()}"]
    disclosures: List[str] = []
    density: Optional[float] = None
    is_pointish = geometry.lower().startswith("point") or geometry == "multi_point"

    if is_pointish and feature_count > 0:
        density = compute_pixel_density(feature_count, viewport_px_w, viewport_px_h)
    dense = bool(
        is_pointish
        and feature_count > tier.dense_threshold
    ) or bool(density is not None and density > 8.0)

    candidates: List[str] = []
    if is_pointish:
        if dense:
            candidates = list(tier.point_dense_candidates)
            codes.append("GRAMMAR.SCALE.DENSE_POINTS_AGGREGATE")
            disclosures.append(
                f"zoom={zoom}（{tier.name} 带）下 n={feature_count} 判定为密集点——"
                "原始符号图过载，建议聚合族表达"
            )
        else:
            codes.append("GRAMMAR.SCALE.SPARSE_POINTS_RAW")
    elif geometry:
        codes.append("GRAMMAR.SCALE.NON_POINT_NO_POINT_CANDIDATES")

    return ScaleDecision(
        zoom=zoom,
        tier=tier.name,
        aggregation_hint=tier.aggregation_hint,
        boundary_detail=tier.boundary_detail,
        point_candidates=candidates,
        is_dense_points=dense,
        visibility_hints=dict(tier.visibility_hints),
        feature_density=density,
        reason_codes=codes,
        disclosures=disclosures,
    )


__all__ = [
    "SCALE_TIERS",
    "ScaleTier",
    "ScaleDecision",
    "POINT_REPRESENTATION_IDS",
    "resolve_scale_tier",
    "resolve_scale_tier_stable",
    "TIER_HYSTERESIS_MARGIN",
    "meters_per_pixel",
    "scale_denominator",
    "scale_actions",
]
