"""G27 —— 多尺度表达策略的单一运行时解释层（INTERFACES.md 唯一依据）。

本模块不是第二套 resolver、不是新 MapSpec 文档、不是并行事实源。它只做两件
事：把「用户/Agent 显式、opt-in、有限词表」的 :class:`ScalePortrayalPolicy`
与既有权威纯函数（scene_lod / scale_rules / representation）**只读**合成为
一份有版本的 :class:`EffectiveScalePortrayal`（解释面 + 有效档位），再由
:func:`project_scale_portrayal_spec` 投影到**既有** spec 字段
（``label.zoomBands`` / ``thresholds.maxFeatures``），由
:func:`bounded_explanation` 产出有界的 UI/Agent/遥测解释面。

纪律：
- 纯函数、零 IO、零时钟、零随机；同输入恒同输出；
- 不复制阈值、不重写规则、不合并四表；不 import 服务层/DB/网络；
- ``target_dpi`` 只进入物理比例尺换算，绝不参与任何 visibility/表达裁决；
- ``decimate_budget`` / data-plane 传输 LOD 都是 display-only，非统计样本。
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.lib.cartography import representation as _representation
from app.lib.cartography import scale_rules as _scale_rules
from app.lib.cartography import scene_lod as _scene_lod

# ── 词表常量（跨语言锁定，不得改名）───────────────────────────────────────

SCALE_POLICY_VERSION = "g27-scale-portrayal/1.0.0"
#: policy 持久化命名空间（既有开放 dict ``layer.scale_profile`` 内；不新增
#: spec 字段、不新建第二份 MapSpec 文档）。前端 G27_POLICY_NAMESPACE 同源。
SCALE_POLICY_NAMESPACE = "g27_scale_portrayal"
SCALE_POLICY_MODES = ("auto_lod", "fixed_density", "disabled")
SCALE_POLICY_UNITS = ("zoom_level", "screen_px", "ground_m")
GEOMETRY_SCOPES = ("point", "line", "polygon", "raster", "unknown")
WEB_MERCATOR_CRS = ("EPSG:3857", "EPSG:900913", "EPSG:3785")
MAX_DIAGNOSTICS = 8
MAX_DISCLOSURES = 8
MAX_OVERRIDES = 8
MAX_ASSUMPTIONS = 8
MAX_EXPLANATION_BYTES = 4096

# 内部有界常量
_DETAIL_LIMIT = 160
_REVISION_LIMIT = 64

# 稳定 reason codes
REASON_HIDDEN_USER = "SCALE.HIDDEN.USER"
REASON_GATED_ZOOM = "SCALE.GATED.ZOOM"
REASON_HIDDEN_REPRESENTATION = "SCALE.HIDDEN.REPRESENTATION"

# 诊断 reason codes
CODE_UNKNOWN_POLICY = "SCALE.POLICY.UNKNOWN_POLICY"
CODE_UNKNOWN_VERSION = "SCALE.POLICY.UNKNOWN_VERSION"
CODE_UNKNOWN_MODE = "SCALE.POLICY.UNKNOWN_MODE"
CODE_UNKNOWN_UNIT = "SCALE.POLICY.UNKNOWN_UNIT"
CODE_UNKNOWN_GEOMETRY_SCOPE = "SCALE.POLICY.UNKNOWN_GEOMETRY_SCOPE"
CODE_INVALID_PINNED_RATIO = "SCALE.POLICY.INVALID_PINNED_RATIO"
CODE_FIXED_DENSITY_MISSING_RATIO = "SCALE.POLICY.FIXED_DENSITY_MISSING_RATIO"
CODE_GHOST_PRIORITY_FIELD = "SCALE.POLICY.GHOST_PRIORITY_FIELD"
CODE_INVALID_HIDDEN = "SCALE.POLICY.INVALID_HIDDEN"

# 四条固定 disclosures（缺一不可；A11/A12/A13/A14/A15）
_DISCLOSURE_DISPLAY_ONLY = (
    "小尺度省略标注/符号是显示策略（display-only）——不写回 source 数据、"
    "分析结果或统计 count；抽稀只改变渲染可见集合，不改变要素统计口径"
)
_DISCLOSURE_UNITS = (
    "radius_px 是屏幕像素，bandwidth_m 是真实地面距离（米）——量纲不同，"
    "跨 zoom 不保证同量纲，不伪称可直接比较或等价换算"
)
_DISCLOSURE_DPI = (
    "输出 DPI（target_dpi）只影响瓦片采样与物理尺寸/字号缩放，不等价于语义 zoom；"
    "可见性与表达裁决恒取语义 zoom，DPI 从不参与"
)
_DISCLOSURE_BUDGET = (
    "decimate_budget 是渲染显示预算，data-plane 传输 LOD（LodBand）是 display-only——"
    "二者均非统计样本，不改变数据总量或统计 count"
)
_FIXED_DISCLOSURES = (
    _DISCLOSURE_DISPLAY_ONLY,
    _DISCLOSURE_UNITS,
    _DISCLOSURE_DPI,
    _DISCLOSURE_BUDGET,
)
_DISCLOSURE_DEGENERATE_GATE = (
    "SCALE.GATE.DEGENERATE_OPEN: min_zoom>=max_zoom 病态 zoom 门按 visibility.ts "
    "语义开门（不留死带、不黑图），已披露"
)


# ── 数据模型（pydantic v2, frozen + extra=forbid）─────────────────────────

class ScalePolicyDiagnostic(BaseModel):
    """稳定 reason code + 出错字段 + 有界说明。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    field: str
    detail: str


class ScalePortrayalPolicy(BaseModel):
    """用户/Agent 显式、opt-in 的尺度策略（有限词表）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = SCALE_POLICY_VERSION
    mode: Literal["auto_lod", "fixed_density", "disabled"] = "disabled"
    layer_id: str = ""
    hidden: Optional[bool] = None
    pinned_size_ratio: Optional[float] = None
    pinned_label_top_ratio: Optional[float] = None
    priority_field: str = ""
    unit: Literal["zoom_level", "screen_px", "ground_m"] = "zoom_level"
    geometry_scope: Literal["point", "line", "polygon", "raster", "unknown"] = "unknown"


class EffectiveScalePortrayal(BaseModel):
    """一次尺度解析的有版本运行时工件（解释面 + 有效档位）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: str
    applied: bool
    effective_zoom: float
    tier: str
    band_id: str
    label_zoom_bands: List[Dict[str, float]]
    active_band: Dict[str, float]
    visibility: Literal[
        "visible", "hidden_by_user", "gated_by_zoom", "hidden_by_representation"
    ]
    hidden_reason: str
    label_top_ratio: float
    symbol_size_ratio: float
    decimate_budget: int
    terrain_maxzoom: int
    user_overrides: List[str]
    diagnostics: List[ScalePolicyDiagnostic]
    disclosures: List[str]
    source_revision: str
    scale_denominator: Optional[int]
    scale_assumptions: List[str]
    render_thinned: Optional[int]
    render_thinned_exact: bool


# ── 内部纯函数助手 ────────────────────────────────────────────────────────

def _diag(code: str, field: str, detail: str) -> ScalePolicyDiagnostic:
    return ScalePolicyDiagnostic(code=code, field=field, detail=detail[:_DETAIL_LIMIT])


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _clamp_zoom(zoom: Any) -> float:
    """语义 zoom → [0, 24]（非有限数值 fail-closed 到 0）。"""
    if not _is_finite_number(zoom):
        return 0.0
    return min(24.0, max(0.0, float(zoom)))


def _active_band_index(zoom: float, bands: Sequence[Mapping[str, float]]) -> int:
    """基于 build_label_zoom_bands() 输出定位当前档（越界钳首/末档）。"""
    if not bands:
        return 0
    if zoom < bands[0]["minZoom"]:
        return 0
    for idx, band in enumerate(bands):
        if band["minZoom"] <= zoom < band["maxZoom"]:
            return idx
    return len(bands) - 1


#: spec 投影的 zoom 域（MapLibre / ADR-0154 契约）。``scene_lod.LOD_BANDS``
#: 的 3..19 是**参考区间**，不是渲染器全域；直接投影进
#: ``layer.label.zoomBands`` 会让 z<3 与 z>=19 落入无档死带，且前端
#: ``activeBandIndex`` 查表与后端 ``lod_for_zoom`` 的 [3,18] 钳制结果
#: 在 z=19 处漂移（前端回落 sizeRatio=1.0，后端=1.1）—— A03/A04 实证
#: 漂移点。投影时只钳首尾边界到渲染器全域，四档数值/词表一律不改。
SPEC_ZOOM_DOMAIN_MIN = 0.0
SPEC_ZOOM_DOMAIN_MAX = 24.0


def spec_label_zoom_bands() -> List[Dict[str, float]]:
    """build_label_zoom_bands() → 覆盖 [0,24) 全域的 spec 投影。

    只改首档 minZoom / 末档 maxZoom；topRatio/sizeRatio 与相邻边界完全
    取自权威表（不复制阈值、不新建表）。前端 ``G27_LABEL_ZOOM_BANDS``
    与共享 corpus 的 ``label_zoom_bands`` 由此函数双向漂移闸锁定。
    """
    bands = [dict(b) for b in _scene_lod.build_label_zoom_bands()]
    if not bands:
        return []
    bands[0]["minZoom"] = SPEC_ZOOM_DOMAIN_MIN
    bands[-1]["maxZoom"] = SPEC_ZOOM_DOMAIN_MAX
    return bands


def _layer_user_hidden(layer: Mapping[str, Any]) -> bool:
    """用户 durable 隐藏（与 policy 无关，最优先，不因 zoom 放大恢复）。"""
    if not isinstance(layer, Mapping):
        return False
    layout = layer.get("layout")
    if isinstance(layout, Mapping) and layout.get("visibility") == "none":
        return True
    return layer.get("visible") is False


def _is_gated(zoom: float, gate: Mapping[str, Any]) -> bool:
    """结构 zoom 门：半开区间 [min, max)。"""
    min_zoom = gate.get("min_zoom")
    max_zoom = gate.get("max_zoom")
    if min_zoom is not None and zoom < min_zoom:
        return True
    if max_zoom is not None and zoom >= max_zoom:
        return True
    return False


def _has_degenerate_gate(layer: Mapping[str, Any], gate: Mapping[str, Any]) -> bool:
    """显式门数值被 zoom_gate_of_layer 消解为空门 → 病态门（开门+披露）。"""
    if gate.get("min_zoom") is not None or gate.get("max_zoom") is not None:
        return False
    vis = layer.get("visibility") if isinstance(layer, Mapping) else None
    if not isinstance(vis, dict):
        return False
    return any(_is_finite_number(vis.get(k)) for k in ("min_zoom", "max_zoom"))


def _policy_usable(policy: Any) -> bool:
    """None / 缺 version 的旧 schema dict = 旧项目（不应用、不报错）。"""
    if policy is None:
        return False
    if isinstance(policy, Mapping) and "version" not in policy:
        return False
    return True


def _coerce_policy(policy: Any) -> ScalePortrayalPolicy:
    if isinstance(policy, ScalePortrayalPolicy):
        return policy
    data = dict(policy) if isinstance(policy, Mapping) else {}
    allowed = set(ScalePortrayalPolicy.model_fields)
    return ScalePortrayalPolicy(**{k: v for k, v in data.items() if k in allowed})


def _center_latitude(frame: Optional[Mapping[str, Any]]) -> Optional[float]:
    """从 frame 取有效中心纬度（[-90, 90] 有限数值）；取不到 → None。"""
    if not isinstance(frame, Mapping):
        return None
    lat: Optional[float] = None
    for key in ("latitude", "lat"):
        value = frame.get(key)
        if _is_finite_number(value):
            lat = float(value)
            break
    if lat is None:
        center = frame.get("center")
        if isinstance(center, Mapping):
            for key in ("lat", "latitude"):
                value = center.get(key)
                if _is_finite_number(value):
                    lat = float(value)
                    break
        elif (isinstance(center, (list, tuple)) and len(center) >= 2
              and _is_finite_number(center[1])):
            lat = float(center[1])
    if lat is None:
        extent = frame.get("extent")
        if (isinstance(extent, (list, tuple)) and len(extent) >= 4
                and _is_finite_number(extent[1]) and _is_finite_number(extent[3])):
            lat = (float(extent[1]) + float(extent[3])) / 2.0
    if lat is None or not (-90.0 <= lat <= 90.0):
        return None
    return lat


def _byte_len(data: Dict[str, Any]) -> int:
    return len(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))


# ── 公共函数 ──────────────────────────────────────────────────────────────

def validate_scale_policy(
    policy: Any, *, available_fields: Sequence[str] = ()
) -> List[ScalePolicyDiagnostic]:
    """校验 opt-in 策略；旧项目（None / 缺 version 的旧 schema）返回 []。

    词表外 / 非法 pin / 幽灵字段引用 → typed diagnostics，供 resolve 置
    ``applied=false``（绝不静默当已应用）。
    """
    if policy is None:
        return []
    if isinstance(policy, ScalePortrayalPolicy):
        data = policy.model_dump()
    elif isinstance(policy, Mapping):
        data = dict(policy)
    else:
        return [_diag(CODE_UNKNOWN_POLICY, "policy",
                      f"policy 类型 {type(policy).__name__} 不可识别——按未应用处理")]

    # 旧 schema：完全没有 version 键 → 旧项目兼容，零诊断、默认行为逐位不变
    if "version" not in data:
        return []

    diagnostics: List[ScalePolicyDiagnostic] = []
    version = data.get("version")
    if version != SCALE_POLICY_VERSION:
        diagnostics.append(_diag(
            CODE_UNKNOWN_VERSION, "version",
            f"未知 version={version!r}，期望 {SCALE_POLICY_VERSION}"))

    mode = data.get("mode", "disabled")
    if mode not in SCALE_POLICY_MODES:
        diagnostics.append(_diag(CODE_UNKNOWN_MODE, "mode",
                                 f"mode={mode!r} 不在词表 {SCALE_POLICY_MODES}"))
    unit = data.get("unit", "zoom_level")
    if unit not in SCALE_POLICY_UNITS:
        diagnostics.append(_diag(CODE_UNKNOWN_UNIT, "unit",
                                 f"unit={unit!r} 不在词表 {SCALE_POLICY_UNITS}"))
    scope = data.get("geometry_scope", "unknown")
    if scope not in GEOMETRY_SCOPES:
        diagnostics.append(_diag(CODE_UNKNOWN_GEOMETRY_SCOPE, "geometry_scope",
                                 f"geometry_scope={scope!r} 不在词表"))

    psize = data.get("pinned_size_ratio")
    if psize is not None and (not _is_finite_number(psize)
                              or not (0.1 <= float(psize) <= 4.0)):
        diagnostics.append(_diag(CODE_INVALID_PINNED_RATIO, "pinned_size_ratio",
                                 f"pinned_size_ratio={psize!r} 越界 [0.1, 4.0]"))
    ptop = data.get("pinned_label_top_ratio")
    if ptop is not None and (not _is_finite_number(ptop)
                             or not (0.0 <= float(ptop) <= 1.0)):
        diagnostics.append(_diag(CODE_INVALID_PINNED_RATIO, "pinned_label_top_ratio",
                                 f"pinned_label_top_ratio={ptop!r} 越界 [0.0, 1.0]"))
    if mode == "fixed_density" and ptop is None:
        diagnostics.append(_diag(
            CODE_FIXED_DENSITY_MISSING_RATIO, "pinned_label_top_ratio",
            "fixed_density 必须给 pinned_label_top_ratio，缺失 → 退回 disabled"))

    priority_field = data.get("priority_field", "")
    if isinstance(priority_field, str) and priority_field:
        if priority_field not in set(available_fields):
            diagnostics.append(_diag(
                CODE_GHOST_PRIORITY_FIELD, "priority_field",
                f"priority_field={priority_field!r} 不在 available_fields（幽灵字段引用）"))

    hidden = data.get("hidden")
    if hidden is not None and not isinstance(hidden, bool):
        diagnostics.append(_diag(CODE_INVALID_HIDDEN, "hidden",
                                 f"hidden={hidden!r} 必须为 bool/None"))
    return diagnostics[:MAX_DIAGNOSTICS]


def resolve_effective_scale_portrayal(
    *,
    zoom: float,
    layer: Mapping[str, Any],
    policy: Any = None,
    available_fields: Sequence[str] = (),
    previous_tier: Optional[str] = None,
    source_revision: str = "",
    frame: Optional[Mapping[str, Any]] = None,
    crs: str = "EPSG:3857",
    target_dpi: float = 96.0,
) -> EffectiveScalePortrayal:
    """权威纯函数只读复用 + precedence 合成 → 单一运行时解释。

    precedence（不可调换）：
    1. 用户隐藏（全 zoom 保持、applied=false）；
    2. 人工 pin/锁覆盖 LOD 数值；
    3. 结构 zoom 门（半开区间、病态门披露不黑图）；
    4. 表达窗口 hidden；
    5. 自动 LOD（3 个零消费者符号在此成为真实调用）；
    6. hysteresis 仅影响 tier 显示；
    7. 物理比例尺（仅 Web Mercator + 有效纬度，DPI 不参与 1–6）。
    """
    effective_zoom = _clamp_zoom(zoom)
    layer_map: Mapping[str, Any] = layer if isinstance(layer, Mapping) else {}
    layer_dict = dict(layer_map)

    # ── policy 校验 / 构造（fail-safe：非法 → diagnostics + applied=false）
    diagnostics = list(validate_scale_policy(policy, available_fields=available_fields))
    pol: Optional[ScalePortrayalPolicy] = None
    if not diagnostics and _policy_usable(policy):
        try:
            pol = _coerce_policy(policy)
        except Exception:  # pydantic ValidationError 等：词表外/类型错 → typed 诊断
            diagnostics.append(_diag(
                CODE_UNKNOWN_POLICY, "policy",
                "policy 字段无法构造为 ScalePortrayalPolicy——按未应用处理"))

    # ── precedence 1：用户隐藏（层事实与 policy 无关，最优先、不恢复）
    layer_user_hidden = _layer_user_hidden(layer_map)
    policy_hidden = bool(not diagnostics and pol is not None and pol.hidden is True)
    hidden_by_user = layer_user_hidden or policy_hidden

    # ── precedence 3 的门准备（与显隐正交；病态门由权威函数消解为空门）
    gate = _representation.zoom_gate_of_layer(layer_dict)

    # ── precedence 5：自动 LOD（三个零消费符号成为真实生产调用）
    # review F8：解释面与 spec 投影必须取**同一份**档表。权威
    # `LOD_BANDS` 的 [3,19) 参考区间经 spec_label_zoom_bands() 钳到渲染器
    # 全域 [0,24)（只钳首尾，四档系数/内部边界一字不改），否则 UI/Agent
    # 解释与实际写入 label.zoomBands 的档位数值会漂移。
    bands = spec_label_zoom_bands()
    active_idx = _active_band_index(effective_zoom, bands)
    active_band = dict(bands[active_idx])
    lod = _scene_lod.lod_for_zoom(effective_zoom)
    terrain_maxzoom = int(_scene_lod.terrain_maxzoom_for_zoom(effective_zoom))
    # LOD_BANDS.decimate_budget 的真实消费（不重写阈值）
    decimate_budget = int(_scene_lod.LOD_BANDS[active_idx]["decimate_budget"])
    label_top_ratio = float(lod["label_top_ratio"])
    symbol_size_ratio = float(lod["symbol_scale"])

    # ── precedence 6：hysteresis 只影响 tier 显示，不改变门/策略数值
    tier = _scale_rules.resolve_scale_tier_stable(effective_zoom, previous_tier).name

    # ── precedence 2：人工 pin/锁覆盖 LOD 数值（记入 user_overrides）
    user_overrides: List[str] = []
    if (not diagnostics and pol is not None and not hidden_by_user
            and pol.mode != "disabled"):
        if pol.pinned_label_top_ratio is not None:
            label_top_ratio = float(pol.pinned_label_top_ratio)
            user_overrides.append(f"pinned_label_top_ratio={label_top_ratio}")
        if pol.pinned_size_ratio is not None:
            symbol_size_ratio = float(pol.pinned_size_ratio)
            user_overrides.append(f"pinned_size_ratio={symbol_size_ratio}")

    # ── visibility 裁决（用户隐藏 > 结构门 > 表达窗口 hidden）
    if hidden_by_user:
        visibility = "hidden_by_user"
        hidden_reason = REASON_HIDDEN_USER
    elif _is_gated(effective_zoom, gate):
        visibility = "gated_by_zoom"
        hidden_reason = REASON_GATED_ZOOM
    elif _representation.export_adjudicate_layer(layer_dict, effective_zoom) == "hidden":
        visibility = "hidden_by_representation"
        hidden_reason = REASON_HIDDEN_REPRESENTATION
    else:
        visibility = "visible"
        hidden_reason = ""

    # applied：策略是否真实生效（用户隐藏 / 非法 / 旧项目 / disabled → false）
    applied = bool(
        not hidden_by_user
        and not diagnostics
        and pol is not None
        and pol.mode != "disabled"
    )
    if not applied:
        band_id = "disabled"
    elif pol.mode == "fixed_density":
        band_id = "fixed"
    else:
        band_id = f"lod-{active_idx}"

    # ── precedence 7：物理比例尺（DPI 仅进入此通道）
    physical = physical_scale_context(
        zoom=effective_zoom, frame=frame, crs=crs, target_dpi=target_dpi)

    # ── precedence 8：四条固定 disclosures + 病态门披露
    disclosures: List[str] = [s[:_DETAIL_LIMIT] for s in _FIXED_DISCLOSURES]
    if _has_degenerate_gate(layer_map, gate):
        disclosures.append(_DISCLOSURE_DEGENERATE_GATE[:_DETAIL_LIMIT])
    disclosures = disclosures[:MAX_DISCLOSURES]

    return EffectiveScalePortrayal(
        policy_version=SCALE_POLICY_VERSION,
        applied=applied,
        effective_zoom=effective_zoom,
        tier=tier,
        band_id=band_id,
        label_zoom_bands=[dict(b) for b in bands],
        active_band=active_band,
        visibility=visibility,
        hidden_reason=hidden_reason,
        label_top_ratio=label_top_ratio,
        symbol_size_ratio=symbol_size_ratio,
        decimate_budget=decimate_budget,
        terrain_maxzoom=terrain_maxzoom,
        user_overrides=user_overrides[:MAX_OVERRIDES],
        diagnostics=diagnostics[:MAX_DIAGNOSTICS],
        disclosures=disclosures,
        source_revision=str(source_revision or "")[:_REVISION_LIMIT],
        scale_denominator=physical["scale_denominator"],
        scale_assumptions=list(physical["assumptions"])[:MAX_ASSUMPTIONS],
        render_thinned=None,
        render_thinned_exact=False,
    )


def project_scale_portrayal_spec(
    decision: EffectiveScalePortrayal, *,
    include_bands: bool = True,
    include_budget: bool = False,
) -> Dict[str, Any]:
    """EffectiveScalePortrayal → 既有 spec 字段（不新增 spec 字段）。

    ``applied=false`` / disabled → ``{}``（旧 spec 零改动）。zoomBands 取
    :func:`spec_label_zoom_bands`（camelCase，匹配
    ``label-layout.ts::normalizeBands``）。

    review F5：``decimate_budget`` **默认不投影**到文档级
    ``thresholds.maxFeatures``。该字段是既有导出预算阈值，自动覆写会改变
    存量导出语义且两条落笔路径都只处理 layer（原实现投影后被静默丢弃，
    属"看起来接了其实没接"）。它保留在 ``EffectiveScalePortrayal`` 的有界
    解释面作为 display budget 披露；仅当调用方显式
    ``include_budget=True``（明确要改导出预算）时才返回该投影。
    """
    if not decision.applied:
        return {}
    spec: Dict[str, Any] = {}
    if include_budget:
        spec["thresholds"] = {"maxFeatures": int(decision.decimate_budget)}
    if include_bands:
        bands = [
            {k: b[k] for k in ("minZoom", "maxZoom", "topRatio", "sizeRatio")}
            for b in spec_label_zoom_bands()
        ]
        spec = {"label": {"zoomBands": bands}, **spec}
    return spec


def physical_scale_context(
    *, zoom: float, frame: Optional[Mapping[str, Any]],
    crs: str, target_dpi: float
) -> Dict[str, Any]:
    """物理比例尺上下文：仅 Web Mercator + 有效中心纬度才套固定公式。

    非 Web Mercator / 缺有效纬度 → ``scale_denominator=None`` +
    ``scale_assumptions`` 披露。``target_dpi`` 只在此参与换算，不进入任何
    visibility / 表达裁决。
    """
    assumptions: List[str] = []
    web_mercator = crs in WEB_MERCATOR_CRS
    latitude = _center_latitude(frame)
    denominator: Optional[int] = None
    if not web_mercator:
        assumptions.append(
            f"crs={crs!r} 非 Web Mercator——该 CRS 不套 Web Mercator 固定物理比例尺公式，"
            "scale_denominator=None")
    elif latitude is None:
        assumptions.append(
            "crs 为 Web Mercator 但 frame 缺有效中心纬度——不套固定公式，"
            "scale_denominator=None")
    else:
        denominator = _scale_rules.scale_denominator(zoom, latitude, dpi=target_dpi)
        assumptions.append(
            f"Web Mercator 固定公式：z={zoom}, lat={latitude}, dpi={target_dpi}"
            "（DPI 仅进入物理比例尺换算）")
    return {
        "crs": crs,
        "web_mercator": web_mercator,
        "latitude": latitude,
        "target_dpi": float(target_dpi) if _is_finite_number(target_dpi) else 96.0,
        "scale_denominator": denominator,
        "assumptions": assumptions[:MAX_ASSUMPTIONS],
    }


def component_portrayal_state(
    decision: EffectiveScalePortrayal,
    components: Sequence[Mapping[str, Any]],
    *,
    supported_variants: Optional[Mapping[str, Sequence[str]]] = None,
) -> List[Dict[str, Any]]:
    """组件消费**同一解析结果**（P3）—— 不新增 variant、不改数据域。

    纪律（A12/A17/A18）：
    - legend / colorbar / categorical_legend / labels 恒 ``full_domain``：
      小尺度省略部分标注/符号是渲染显示策略，不改变图例分类、单位、
      完整数据域或统计 count；
    - scale_bar 只附加物理比例尺注记（有分母才写），不放缩组件；
    - variant 只**原样保留**组件已声明值；传入 ``supported_variants`` 时
      只做白名单核对并披露 unsupported（绝不发明新 variant）；
    - 不改动组件 placement / 布局矩形 —— 档位切换不产生重叠。
    """
    full_domain_types = frozenset({
        "legend", "categorical_legend", "continuous_colorbar", "label_layer",
    })
    out: List[Dict[str, Any]] = []
    for comp in components[:32]:
        if not isinstance(comp, Mapping):
            continue
        cid = str(comp.get("id") or "")[:64]
        ctype = str(comp.get("type") or "")[:64]
        variant = comp.get("variant")
        variant_str = str(variant)[:64] if variant is not None else None
        disclosures: List[str] = []
        if variant_str is not None and supported_variants:
            allowed = supported_variants.get(ctype, ())
            if variant_str not in allowed:
                disclosures.append(
                    "SCALE.COMPONENT.UNSUPPORTED_VARIANT: 保留原声明但不应用新变体")
        if ctype in full_domain_types:
            state = "full_domain"
            disclosures.append(
                "完整数据域/分类/单位保持不变；小尺度省略标注与符号只是显示策略，"
                "不写回 source、分析结果或统计 count")
        elif ctype == "scale_bar":
            state = "scale_annotated" if decision.scale_denominator else "scale_unavailable"
            disclosures.append(
                "物理比例尺依赖 CRS/纬度/投影假设；非 Web Mercator 不套固定公式")
        else:
            state = "display_only"
            disclosures.append("档位切换不改变组件布局矩形（A18）")
        out.append({
            "id": cid,
            "type": ctype,
            "variant": variant_str,
            "state": state,
            "band_id": decision.band_id,
            "disclosures": disclosures[:3],
        })
    return out


def bounded_explanation(decision: EffectiveScalePortrayal) -> Dict[str, Any]:
    """UI / Agent / 遥测的有界解释面（A24）。

    - 所有数组截到各自上限；字符串 ≤160 字符、source_revision ≤64；
    - 序列化字节数 > ``MAX_EXPLANATION_BYTES`` → 按
      ``disclosures → scale_assumptions → user_overrides → diagnostics``
      顺序裁剪并置 ``truncated=true``（**优先保留 diagnostics**）；
    - 不遍历/不上传任何要素属性；``render_thinned`` 只在调用方传入精确
      计数时非 None（本解析面恒 None + render_thinned_exact=false）。
    """
    data: Dict[str, Any] = decision.model_dump(mode="json")
    data["truncated"] = False
    truncated = False

    def cap(key: str, limit: int) -> None:
        nonlocal truncated
        arr = data.get(key)
        if isinstance(arr, list) and len(arr) > limit:
            data[key] = arr[:limit]
            truncated = True

    cap("diagnostics", MAX_DIAGNOSTICS)
    cap("disclosures", MAX_DISCLOSURES)
    cap("user_overrides", MAX_OVERRIDES)
    cap("scale_assumptions", MAX_ASSUMPTIONS)

    for key, value in list(data.items()):
        if isinstance(value, str):
            limit = _REVISION_LIMIT if key == "source_revision" else _DETAIL_LIMIT
            if len(value) > limit:
                data[key] = value[:limit]
                truncated = True
    for diag in data.get("diagnostics", []):
        if isinstance(diag, dict):
            for key, value in list(diag.items()):
                if isinstance(value, str) and len(value) > _DETAIL_LIMIT:
                    diag[key] = value[:_DETAIL_LIMIT]
                    truncated = True

    if _byte_len(data) > MAX_EXPLANATION_BYTES:
        truncated = True
        # 先把 narratives 裁空，最后才动 diagnostics —— diagnostics 优先保留
        for key in ("disclosures", "scale_assumptions", "user_overrides", "diagnostics"):
            arr = data.get(key)
            if not isinstance(arr, list):
                continue
            while arr and _byte_len(data) > MAX_EXPLANATION_BYTES:
                arr.pop()
        # 兜底：数组裁空仍超（长字符串字段）→ 逐字符串截到 40 字符
        if _byte_len(data) > MAX_EXPLANATION_BYTES:
            for key, value in list(data.items()):
                if isinstance(value, str) and len(value) > 40:
                    data[key] = value[:40]

    data["truncated"] = bool(truncated)
    return data


__all__ = [
    "SCALE_POLICY_VERSION",
    "SCALE_POLICY_MODES",
    "SCALE_POLICY_UNITS",
    "GEOMETRY_SCOPES",
    "WEB_MERCATOR_CRS",
    "MAX_DIAGNOSTICS",
    "MAX_DISCLOSURES",
    "MAX_OVERRIDES",
    "MAX_ASSUMPTIONS",
    "MAX_EXPLANATION_BYTES",
    "SPEC_ZOOM_DOMAIN_MIN",
    "SPEC_ZOOM_DOMAIN_MAX",
    "spec_label_zoom_bands",
    "ScalePolicyDiagnostic",
    "ScalePortrayalPolicy",
    "EffectiveScalePortrayal",
    "validate_scale_policy",
    "resolve_effective_scale_portrayal",
    "project_scale_portrayal_spec",
    "physical_scale_context",
    "component_portrayal_state",
    "bounded_explanation",
]
