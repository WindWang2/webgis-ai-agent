"""ScaleRepresentationProfile.v1 —— 多尺度表达档案纯数据契约（M1）。

定位（GOAL 04 / DECISIONS.md D1-D8）：

- **纯数据 + 纯函数**：零 IO、零时钟；同输入恒同输出（指纹确定性）。
- **双基准显式分口径（D6）**：``display_*`` = MapLibre 512px worldSize
  （用户屏幕所见，与 frontend/lib/map-kit/meters-per-pixel.ts 同式）；
  ``computation_*`` = 256px 瓦片基准（与 app/lib/cartography/scale_rules.py
  同源）。两基准同 zoom 恰差 2 倍 —— 历史事实，本契约不统一数值、只
  显式标注基准，防止静默混用。
- **非破坏性证据（D2）**：``source`` 记录综合时点的原 ref 身份
  （ref + content_revision + data_fingerprint + feature_count）——档案的
  一切派生工件都以该时点为界；原 ref 永不被修改。
- **统计口径（D7）**：每带 ``stats`` 区分 original/displayed/suppressed；
  分类基准恒 ``original_source``（分类 breaks 不因渲染副本变化）。
- **词表**：``representation_mode`` ⊆ v1.6 ``REPRESENTATION_MODES``
  （representation.py 权威，本模块只读引用不复制词表内容语义）；
  ``tier`` ⊆ ``SCALE_TIERS`` 带名（scale_rules 权威）。

MapSpec 落笔（v1.8 additive）：
- ``representation_windows_of_profile`` → ``layer.representation.windows``
  （含 artifact 带的 ``window.source`` 源覆盖）；
- ``to_mapspec_layer_block`` → ``layer.scale_profile``（有界声明 dict）。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.lib.cartography.representation import (
    REPRESENTATION_MODES,
    ZOOM_DOMAIN_MAX,
    ZOOM_DOMAIN_MIN,
)
from app.lib.cartography.scale_rules import SCALE_TIERS

#: 契约版本（schema_version 字段值；演进 additive 递增）。
SCALE_REPRESENTATION_PROFILE_VERSION = 1

#: Web Mercator 赤道周长（米）—— 与 frontend meters-per-pixel.ts /
#: scale_rules._MERCATOR_EQUATOR_M_PER_PX_Z0 同一物理常数（40075016.686 m）。
EARTH_CIRCUMFERENCE_M = 40_075_016.686

#: 比例尺基准词表（D6）。display = MapLibre worldSize 512·2^z（屏幕所见）；
#: computation = 瓦片 256·2^z（scale_rules 权威、打印/导出换算口径）。
SCALE_BASIS_DISPLAY = "maplibre_512px"
SCALE_BASIS_COMPUTATION = "tile_256px"

#: 屏幕基准 DPI 与英寸米数（scale_rules 同值；显示分母缺省口径）。
DEFAULT_SCREEN_DPI = 96.0
_INCH_IN_METERS = 0.0254

_TIER_NAMES = tuple(t.name for t in SCALE_TIERS)

#: 几何综合策略词表（D5；generalize 引擎的执行词表）。
GEOMETRY_STRATEGIES = (
    "none",
    "bounded_simplify",
    "shared_boundary_protect",
    "safe_no_generalization",
)

#: 要素取舍证据词表（D4）：none = 无可信字段 → 不取舍。
IMPORTANCE_EVIDENCE_KINDS = (
    "none",
    "field_schema+aggregate+vocabulary",
    "user_override",
)

#: 披露语义检查状态词表（裁决 pass/fail/warning/not_evaluated 既有纪律）。
SEMANTIC_CHECK_STATUSES = ("pass", "warning", "fail", "not_evaluated")


# ── 比例尺换算（双基准；display 与 frontend 孪生逐常数一致）────────────────


def display_meters_per_pixel(zoom: float, latitude: float = 0.0) -> float:
    """MapLibre 512px worldSize 基准的地面分辨率（米/显示像素）。

    与 ``frontend/lib/map-kit/meters-per-pixel.ts::metersPerPixelAt`` 同式：
    ``EARTH_CIRCUMFERENCE * cos(φ) / (512 * 2^z)``。纬度钳制 ±85.0511°
    （Web Mercator 域），zoom 钳制 [0,24]（MapLibre 契约域）。
    """
    z = min(24.0, max(0.0, float(zoom)))
    lat = min(85.0511287798066, max(-85.0511287798066, float(latitude)))
    return EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / (512 * 2.0 ** z)


def display_scale_denominator(
    zoom: float,
    latitude: float = 0.0,
    *,
    dpi: float = DEFAULT_SCREEN_DPI,
) -> int:
    """显示比例尺分母 1:N（512px 基准；屏幕 DPI 缺省 96）。

    非法 dpi（≤0/非数）fail-closed 到 :data:`DEFAULT_SCREEN_DPI`（与
    scale_rules.scale_denominator 同纪律）。结果取整 ≥1（分母惯例）。
    """
    effective = dpi if (isinstance(dpi, (int, float)) and dpi > 0) else DEFAULT_SCREEN_DPI
    m_per_display_px = display_meters_per_pixel(zoom, latitude)
    return max(1, int(m_per_display_px / (_INCH_IN_METERS / effective)))


# ── 契约模型 ──────────────────────────────────────────────────────────────


class _ProfileModel(BaseModel):
    """round-trip 保真基类（repo 纪律：extra=allow，未知键不丢）。"""

    model_config = ConfigDict(extra="allow")


class GeographicScaleRange(_ProfileModel):
    """带内地理尺度区间（zoom 窗 + 双基准换算快照）。

    ``display_denominator_at_min/at_max``：带两端 zoom 在 ``latitude`` 处的
    显示比例尺分母（512 基准，人读/图例消费）；``computation_m_per_px_at_min``
    ：带最粗端的计算基准分辨率（256 基准，容差计算消费）。快照在**模型
    构造期**统一推导（model_validator —— 任何构造路径含 json round-trip
    都得到一致快照）；消费方不得自算混基准。
    """

    min_zoom: float
    max_zoom: float
    tier: str
    latitude: float = 0.0
    display_denominator_at_min: int = 1
    display_denominator_at_max: int = 1
    display_scale_basis: str = SCALE_BASIS_DISPLAY
    computation_m_per_px_at_min: float = 0.0
    computation_basis: str = SCALE_BASIS_COMPUTATION

    @model_validator(mode="after")
    def _derive_scale_snapshots(self) -> "GeographicScaleRange":
        self.display_denominator_at_min = display_scale_denominator(
            self.min_zoom, self.latitude)
        self.display_denominator_at_max = display_scale_denominator(
            self.max_zoom, self.latitude)
        self.computation_m_per_px_at_min = (
            display_meters_per_pixel(self.min_zoom, self.latitude) * 2.0)
        return self


def build_scale_range(
    min_zoom: float, max_zoom: float, tier: str, latitude: float = 0.0
) -> GeographicScaleRange:
    """带几何 → 双基准换算快照（唯一推导点；测试锁与换算函数一致）。"""
    return GeographicScaleRange(
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        tier=tier,
        latitude=latitude,
        display_denominator_at_min=display_scale_denominator(min_zoom, latitude),
        display_denominator_at_max=display_scale_denominator(max_zoom, latitude),
        computation_m_per_px_at_min=display_meters_per_pixel(min_zoom, latitude) * 2.0,
    )


class GeometryGeneralizationPolicy(_ProfileModel):
    """带内几何综合策略（D5；执行体 = generalize 引擎）。

    - ``strategy``：none（不改几何）/ bounded_simplify（有界拓扑保持简化）/
      shared_boundary_protect（共享边界锚点保护，面族行政边界）/
      safe_no_generalization（高风险输入显式不综合，只允许要素级取舍）；
    - ``tolerance`` × ``tolerance_units``：meters_ground 为主单位（派生
      工件是 geojson，地面米单位无像素歧义）；pixels_display 仅在带端
      zoom 由消费者换算；degrees 兼容 lon/lat 词表（MVT 通道同族）；
    - ``max_area_error_ratio``：面族面积相对误差不变量上界（测试锁）；
    - ``preserve_holes``：洞保留硬开关（洞丢失 = 拓扑回退信号）。
    """

    strategy: str = "none"
    tolerance: float = 0.0
    tolerance_units: str = "meters_ground"
    topology_risk: str = "low"
    max_area_error_ratio: float = 0.02
    preserve_holes: bool = True
    min_ring_area_m2: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)


class FeatureSelectionPolicy(_ProfileModel):
    """带内要素取舍（M2 输出的落点；D4 fail-honest）。

    ``importance_evidence=none``（无可信字段）⇒ ``kept_values`` 必须为空
    —— 不取舍、不伪造「主干道路」。``mandatory_features``：用户显式点名
    的要素 id，任何带都不被综合抑制（user-wins D8b）。
    """

    importance_field: Optional[str] = None
    value_order: List[str] = Field(default_factory=list)
    kept_values: List[str] = Field(default_factory=list)
    importance_evidence: str = "none"
    mandatory_features: List[str] = Field(default_factory=list)
    suppress_reason_codes: List[str] = Field(default_factory=list)


class DeclutteringPolicy(_ProfileModel):
    """带内去密策略（点族聚合/热力/密度切换，复用既有阈值语义）。"""

    mode: str = "none"
    threshold_feature_count: Optional[int] = None
    reason_codes: List[str] = Field(default_factory=list)


class BandDisplayStats(_ProfileModel):
    """带内显示统计口径（D7：displayed ≠ original 时必须可解释）。"""

    original_feature_count: int = 0
    displayed_feature_count: int = 0
    suppressed_feature_count: int = 0
    geometry_modified_count: int = 0
    degrade_reasons: List[str] = Field(default_factory=list)


class ScaleBandPlan(_ProfileModel):
    """单带计划：尺度区间 × 表达模式 × 几何/取舍/去密策略 × 统计。"""

    scale: GeographicScaleRange
    representation_mode: str = "raw"
    artifact_source_id: Optional[str] = None
    artifact_ref: Optional[str] = None
    geometry: GeometryGeneralizationPolicy = Field(
        default_factory=GeometryGeneralizationPolicy)
    selection: FeatureSelectionPolicy = Field(
        default_factory=FeatureSelectionPolicy)
    decluttering: DeclutteringPolicy = Field(default_factory=DeclutteringPolicy)
    stats: BandDisplayStats = Field(default_factory=BandDisplayStats)
    reason_codes: List[str] = Field(default_factory=list)


class SourceRevision(_ProfileModel):
    """档案锚定的原数据时点（非破坏性证据 D2）。"""

    ref: str
    content_revision: Optional[int] = None
    data_fingerprint: Optional[str] = None
    feature_count: int = 0
    geometry_family: str = "unknown"


class UserScaleOverride(_ProfileModel):
    """用户显式覆盖（D8；origin 恒可审计）。"""

    kind: str  # pin_mode | mandatory_feature | simplify_tolerance | band_source
    value: str
    actor: str = "user"
    detail: Dict[str, Any] = Field(default_factory=dict)


class SemanticCheckRecord(_ProfileModel):
    """档案级语义检查结果（词表 = SEMANTIC_CHECK_STATUSES）。"""

    check: str
    status: str = "not_evaluated"
    detail: Dict[str, Any] = Field(default_factory=dict)


class ScaleRepresentationProfile(_ProfileModel):
    """多尺度表达档案（一份 = 一个图层在 [0,24) 全域的完整计划）。"""

    schema_version: int = SCALE_REPRESENTATION_PROFILE_VERSION
    profile_id: str
    layer_id: str
    source: SourceRevision
    bands: List[ScaleBandPlan] = Field(default_factory=list)
    user_overrides: List[UserScaleOverride] = Field(default_factory=list)
    semantic_checks: List[SemanticCheckRecord] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)
    classification_basis: str = "original_source"
    classification_breaks_fingerprint: Optional[str] = None
    generated_by: str = ""


# ── 校验 / 查询 / 投影（纯函数）───────────────────────────────────────────


def validate_profile(profile: ScaleRepresentationProfile) -> List[str]:
    """档案结构合法性 → 错误码列表（空 = 合法）。

    规则（与 v1.6 normalize_windows 同族但独立执行 —— 档案是更丰富的
    声明，错误可诊断优先于整体拒绝）：
    - 带按 min_zoom 排序后必须连续覆盖 [0,24)（无缺口/无重叠）；
    - tier/representation_mode/策略词表合法；
    - generalized/aggregate_grid 带 ⇒ artifact_source_id 与 artifact_ref 在场；
    - stats：displayed ≤ original、suppressed = original - displayed、
      selection 证据 none ⇒ kept_values 空；
    - 源时点身份在场（ref 非空）。
    """
    errors: List[str] = []
    if not profile.source.ref:
        errors.append("source.ref_missing")
    bands = sorted(profile.bands, key=lambda b: b.scale.min_zoom)
    if not bands:
        return errors + ["bands_empty"]
    if bands[0].scale.min_zoom > ZOOM_DOMAIN_MIN:
        errors.append("bands_do_not_cover_zoom_0")
    cursor = bands[0].scale.min_zoom
    for b in bands:
        if abs(b.scale.min_zoom - cursor) > 1e-9:
            errors.append(f"bands_not_contiguous@{b.scale.min_zoom}")
        if not (b.scale.min_zoom < b.scale.max_zoom):
            errors.append(f"band_empty_window@{b.scale.min_zoom}")
        if b.scale.tier not in _TIER_NAMES:
            errors.append(f"band_unknown_tier@{b.scale.tier}")
        if b.representation_mode not in REPRESENTATION_MODES:
            errors.append(f"band_unknown_mode@{b.representation_mode}")
        if b.geometry.strategy not in GEOMETRY_STRATEGIES:
            errors.append(f"band_unknown_geometry_strategy@{b.geometry.strategy}")
        if b.selection.importance_evidence not in IMPORTANCE_EVIDENCE_KINDS:
            errors.append(
                f"band_unknown_importance_evidence@{b.selection.importance_evidence}")
        if b.representation_mode in ("generalized", "aggregate_grid"):
            if not b.artifact_source_id or not b.artifact_ref:
                errors.append(f"band_artifact_missing@{b.scale.tier}")
        st = b.stats
        if st.displayed_feature_count > st.original_feature_count:
            errors.append(f"stats_displayed_exceeds_original@{b.scale.tier}")
        if st.suppressed_feature_count != max(
            0, st.original_feature_count - st.displayed_feature_count
        ):
            errors.append(f"stats_suppressed_inconsistent@{b.scale.tier}")
        if b.selection.importance_evidence == "none" and b.selection.kept_values:
            errors.append(f"selection_kept_without_evidence@{b.scale.tier}")
        cursor = b.scale.max_zoom
    if bands[-1].scale.max_zoom < ZOOM_DOMAIN_MAX:
        errors.append("bands_do_not_cover_zoom_24")
    return errors


def band_at_zoom(profile: ScaleRepresentationProfile, zoom: float) -> ScaleBandPlan:
    """zoom → 带计划（半开区间 [min,max)；越界钳到首/末带）。

    与 representation_mode_at_zoom 同语义（越界钳制，不抛异常 —— 查询面
    永不成为渲染路径的异常源）。
    """
    bands = sorted(profile.bands, key=lambda b: b.scale.min_zoom)
    if not bands:
        raise ValueError("profile has no bands")
    z = min(ZOOM_DOMAIN_MAX - 1e-9, max(ZOOM_DOMAIN_MIN, float(zoom)))
    for b in bands:
        if b.scale.min_zoom <= z < b.scale.max_zoom:
            return b
    return bands[-1]


def representation_windows_of_profile(
    profile: ScaleRepresentationProfile,
) -> List[Dict[str, Any]]:
    """档案 → v1.6 representation windows 声明（含 artifact 源覆盖）。

    - 同模式相邻带合并（v1.6 normalize 语义的预处理，减少窗口数）；
    - artifact 带（generalized/aggregate_grid）带 ``source`` 键（v1.8
      additive：窗口级源覆盖，克隆子层/导出按该 source 取数据）；
    - raw 带不带 source 键（主层 source 语义不变）。
    """
    bands = sorted(profile.bands, key=lambda b: b.scale.min_zoom)
    windows: List[Dict[str, Any]] = []
    for b in bands:
        win: Dict[str, Any] = {
            "min_zoom": b.scale.min_zoom,
            "max_zoom": b.scale.max_zoom,
            "mode": b.representation_mode,
            "reason_codes": list(
                dict.fromkeys([*b.reason_codes, *b.decluttering.reason_codes]))[:8],
        }
        if b.representation_mode in ("generalized", "aggregate_grid") and b.artifact_source_id:
            win["source"] = b.artifact_source_id
        prev = windows[-1] if windows else None
        if (
            prev is not None
            and prev["mode"] == win["mode"]
            and prev.get("source") == win.get("source")
            and abs(prev["max_zoom"] - win["min_zoom"]) < 1e-9
        ):
            prev["max_zoom"] = win["max_zoom"]
            merged_codes = list(dict.fromkeys(prev["reason_codes"] + win["reason_codes"]))
            prev["reason_codes"] = merged_codes[:8]
        else:
            windows.append(win)
    return windows


def to_mapspec_layer_block(profile: ScaleRepresentationProfile) -> Dict[str, Any]:
    """档案 → ``layer.scale_profile`` 有界声明（v1.8 additive 落笔面）。

    只携带 desired-state 事实（身份/带计划/披露/检查），不带运行时状态；
    可直接 json 序列化进 MapSpec 开放 dict 面。
    """
    return {
        "schema_version": profile.schema_version,
        "profile_id": profile.profile_id,
        "fingerprint": profile_fingerprint(profile),
        "layer_id": profile.layer_id,
        "source": profile.source.model_dump(exclude_none=True),
        "bands": [b.model_dump(exclude_none=True) for b in profile.bands],
        "user_overrides": [o.model_dump(exclude_none=True) for o in profile.user_overrides],
        "semantic_checks": [c.model_dump(exclude_none=True) for c in profile.semantic_checks],
        "disclosures": list(profile.disclosures[:12]),
        "classification_basis": profile.classification_basis,
        "classification_breaks_fingerprint": profile.classification_breaks_fingerprint,
        "generated_by": profile.generated_by,
    }


def profile_fingerprint(profile: ScaleRepresentationProfile) -> str:
    """档案内容指纹（确定性；不含 profile_id/generated_by —— 身份字段
    不参与内容等价判定，重生成的档案内容同即指纹同）。"""
    payload = {
        "schema_version": profile.schema_version,
        "layer_id": profile.layer_id,
        "source": profile.source.model_dump(mode="json", exclude_none=True),
        "bands": [b.model_dump(mode="json", exclude_none=True) for b in profile.bands],
        "user_overrides": [o.model_dump(mode="json", exclude_none=True)
                           for o in profile.user_overrides],
        "classification_basis": profile.classification_basis,
        "classification_breaks_fingerprint": profile.classification_breaks_fingerprint,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "msp-sha256:" + hashlib.sha256(blob).hexdigest()[:16]


__all__ = [
    "SCALE_REPRESENTATION_PROFILE_VERSION",
    "EARTH_CIRCUMFERENCE_M",
    "SCALE_BASIS_DISPLAY",
    "SCALE_BASIS_COMPUTATION",
    "DEFAULT_SCREEN_DPI",
    "GEOMETRY_STRATEGIES",
    "IMPORTANCE_EVIDENCE_KINDS",
    "SEMANTIC_CHECK_STATUSES",
    "GeographicScaleRange",
    "GeometryGeneralizationPolicy",
    "FeatureSelectionPolicy",
    "DeclutteringPolicy",
    "BandDisplayStats",
    "ScaleBandPlan",
    "SourceRevision",
    "UserScaleOverride",
    "SemanticCheckRecord",
    "ScaleRepresentationProfile",
    "build_scale_range",
    "display_meters_per_pixel",
    "display_scale_denominator",
    "validate_profile",
    "band_at_zoom",
    "representation_windows_of_profile",
    "to_mapspec_layer_block",
    "profile_fingerprint",
]
