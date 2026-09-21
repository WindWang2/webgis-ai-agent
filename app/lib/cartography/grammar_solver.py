"""Cartographic Grammar Constraint Solver（C2/C4/C5 接线，ADR-0205）.

把「Intent + Data Semantics + Scale + Product Goal → 制图约束 → 表达/
通道/图例/组件/标注意图」做成**确定性、可解释、带 reason code** 的纯
函数求解面。它是**规划层**（ADR-0205 D1），不是第二裁决：

- ``data_kind`` 与 recommended 只作为**输入**喂 ``resolve_symbology``
  （ADR-0152 唯一裁决；本模块不选 palette/k/分类法）；
- 标注委托 ``label_plan.build_label_spec``（ADR-0154，不建第二引擎）；
- 必配组件复用 ``required_components_for``（ADR-0156 词表）；
- 表达词表 = ``model_library`` 注册的 MapModel id（不另立词表）；
- 布局求解产出 ``LayoutParticipantV3`` 形参与者，放置归
  ``layout_solver.solve_layout_v3``；
- ``audit`` 只读对账（图例↔colorbar 配对、通道过载、表达层型），无
  第二 verdict（ADR-0200 D2 同纪律）。

确定性：同输入两次求解 ``model_dump()`` 完全相等；指纹 = sha256
(版本 + 规范化输入)。user-wins：显式 pin 记录入 ``user_wins``，与
矩阵冲突只披露不覆盖（ADR-0118 同语义）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.lib.cartography.component_composer import (
    CONTENT_FLAGS,
    OUTPUT_PURPOSES,
    required_components_for,
)
from app.lib.cartography.grammar_types import (  # re-export 便于消费方单点 import
    COLLAPSE_KEEP_CLASSES,
    GRAMMAR_VERSION,
    LEGEND_FORM_BY_DATA_KIND,
    MAX_CATEGORICAL_CLASSES,
    MAX_THEMATIC_FIELDS,
)
from app.lib.cartography.label_plan import build_label_spec
from app.lib.cartography.model_library import get_map_model
from app.lib.cartography.scale_rules import ScaleDecision, scale_actions
from app.lib.cartography.semantic_checks import (
    _VISUALVAR_FAIL_COUNT,
    _VISUALVAR_WARN_COUNT,
    _paint_methods,
)
from app.lib.cartography.standards.rule import (
    MAP_AUDIENCES,
    MAP_MEDIUMS,
    MAP_PURPOSES,
)
from app.lib.cartography.visual_variables import (
    MEASUREMENT_KINDS,
    VISUAL_VARIABLES,
    ChannelFit,
    MeasurementDecision,
    allocatable,
    channel_fit,
    channel_fit_order,
    infer_measurement_kind,
)

# ── 请求 / 证据 ──────────────────────────────────────────────────────────

class FieldEvidence(BaseModel):
    """单字段证据（有界：值样本 ≤4096，与 label_plan.FieldStats 同风格）。"""

    name: str
    dtype: str = "number"             # string | number | int | float | bool | other
    values: List[float] = Field(default_factory=list)
    unique_count: Optional[int] = None
    total_count: int = 0
    null_count: int = 0
    is_secondary: bool = False        # 次要字段（可作次通道/不绑定则披露）
    measurement: Optional[str] = None  # 显式 pin（user-wins，最高优先）


class GrammarRequest(BaseModel):
    """语法求解请求（几何 × 字段证据 × 尺度 × 上下文 × 用户 pin）。"""

    geometry: str = "polygon"         # point | multi_point | line | polygon | raster
    feature_count: int = 0
    zoom: float = 11.0
    viewport_px: Tuple[int, int] = (1280, 720)
    # standards 同词表（ADR-0200；空串 = wildcard）
    purpose: str = ""
    audience: str = ""
    medium: str = ""
    # required_components_for 的输出用途词表
    output_purpose: str = "screen_16_9"
    fields: List[FieldEvidence] = Field(default_factory=list)
    # user-wins pins（显式即最高优先，冲突只披露）
    pinned_representation: Optional[str] = None
    pinned_channels: Dict[str, str] = Field(default_factory=dict)
    pinned_palette: Optional[str] = None
    # 标注契约委托入参（label_plan profile 形态；None = 本请求不管标注）
    label_profile: Optional[Dict[str, Any]] = None
    # required_components_for content 开关（CONTENT_FLAGS 子集）
    content_flags: Dict[str, bool] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """fail-closed 构造期校验（与 CartographicRule 同纪律）。"""
        self._validate_vocab()

    def _validate_vocab(self) -> None:
        for axis, value, vocab in (
            ("purpose", self.purpose, MAP_PURPOSES),
            ("audience", self.audience, MAP_AUDIENCES),
            ("medium", self.medium, MAP_MEDIUMS),
        ):
            if value and value not in vocab:
                raise ValueError(f"{axis}={value!r} 不在 standards 词表 {vocab}")
        if self.output_purpose not in OUTPUT_PURPOSES:
            raise ValueError(
                f"output_purpose={self.output_purpose!r} 不在 {OUTPUT_PURPOSES}")
        if len(self.fields) > MAX_THEMATIC_FIELDS:
            raise ValueError(
                f"fields 数 {len(self.fields)} 超出语法有界词表上限 {MAX_THEMATIC_FIELDS}")
        for name, variable in self.pinned_channels.items():
            if variable not in VISUAL_VARIABLES:
                raise ValueError(
                    f"pinned_channels[{name}]={variable!r} 不在视觉变量词表")
        for f in self.fields:
            if f.measurement is not None and f.measurement not in MEASUREMENT_KINDS:
                raise ValueError(
                    f"fields[{f.name}].measurement={f.measurement!r} 非法"
                    f"（合法：{', '.join(MEASUREMENT_KINDS)}）")


# ── 工件 ────────────────────────────────────────────────────────────────

class ChannelBinding(BaseModel):
    """字段 → 视觉通道绑定（含测量语义决策与适配等级）。"""

    field: str
    role: str                        # primary | secondary | unbound
    measurement: MeasurementDecision
    variable: Optional[str] = None   # None = 无可分配通道（披露）
    fit_level: Optional[str] = None
    reason_codes: List[str] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)


class RepresentationChoice(BaseModel):
    """表达选择：选中 + 候选排序 + 落选者（全部是 MapModel 注册 id）。"""

    selected: Optional[str] = None
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    rejected: List[Dict[str, str]] = Field(default_factory=list)
    collapse: Optional[Dict[str, Any]] = None   # 类别收纳动作（keep N-1 + Other）
    reason_codes: List[str] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)


class LegendPlan(BaseModel):
    """图例形态契约：data_kind → 合法形态；非法配对在 audit 报 finding。"""

    form: str                        # categorical | graduated | continuous | divergent | none
    reason_codes: List[str] = Field(default_factory=list)


#: 组件 → 语法默认布局意图（制图惯例锚位；user-pinned 覆盖。放置权仍在
#: layout_solver —— 这里只是 requested_zone 的先验，槽位容量/互斥/回退
#: 全部由求解器裁决）。
_DEFAULT_ZONES: Dict[str, str] = {
    "title": "top-center",
    "subtitle": "top-center",
    "legend": "top-left",
    "colorbar": "top-left",
    "scale_bar": "bottom-left",
    "north_arrow": "top-right",
    "attribution": "bottom-right",
    "graticule": "none",
    "inset_map": "bottom-right",
}
_DEFAULT_FALLBACKS: Dict[str, Tuple[str, ...]] = {
    "title": ("top-left",),
    "legend": ("bottom-right", "bottom-left"),
    "colorbar": ("bottom-right", "bottom-left"),
    "scale_bar": ("bottom-center",),
    "north_arrow": ("top-left",),
    "attribution": ("bottom-center",),
}


class GrammarDecision(BaseModel):
    """制图语法决策一等工件（可序列化、版本化、带指纹）。"""

    grammar_version: str = GRAMMAR_VERSION
    fingerprint: str = ""
    geometry: str = ""
    scale: Optional[ScaleDecision] = None
    bindings: List[ChannelBinding] = Field(default_factory=list)
    data_kind: str = "sequential"
    symbology_inputs: Dict[str, Any] = Field(default_factory=dict)
    representation: RepresentationChoice = Field(default_factory=RepresentationChoice)
    legend: LegendPlan = Field(default_factory=LegendPlan)
    component_obligations: List[str] = Field(default_factory=list)
    label_spec: Optional[Dict[str, Any]] = None
    disclosures: List[str] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)
    user_wins: List[Dict[str, Any]] = Field(default_factory=list)

    # ── C5：布局求解参与者（放置归 layout_solver，本模块不摆位置）──────
    def layout_participants(
        self,
        *,
        pinned_zones: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """把组件义务投影为 ``LayoutParticipantV3`` 形参与者（dict 形态）。

        ``pinned_zones``：用户钉扎的组件槽位（component_id → zone）——
        user-wins 语义由求解器的 requested_zone 承担（本方法只透传）。
        """
        pins = pinned_zones or {}
        legend_type = "legend" if self.legend.form != "none" else None
        wanted: List[Tuple[str, int]] = []
        for ctype in self.component_obligations:
            priority = 10 if ctype in ("title", "legend", "scale_bar") else 50
            wanted.append((ctype, priority))
        if legend_type and legend_type not in {c for c, _ in wanted}:
            wanted.append((legend_type, 10))
        out: List[Dict[str, Any]] = []
        for ctype, priority in wanted:
            participant: Dict[str, Any] = {
                "id": ctype,
                "type": ctype,
                "requested_zone": pins.get(ctype, _DEFAULT_ZONES.get(ctype, "none")),
                "priority": priority,
                "optional": ctype not in ("title", "scale_bar", "north_arrow",
                                          "attribution", "legend"),
                "fallback_zones": list(_DEFAULT_FALLBACKS.get(ctype, ())),
                "width_units": 1,
                "collision_group": "legend_family" if ctype in ("legend", "colorbar", "uncertainty_panel") else "",
            }
            out.append(participant)
        return out

    # ── critique 消费面（只读；无第二 verdict）──────────────────────────
    def audit(self, layers: List[Dict[str, Any]]) -> "GrammarAudit":
        return audit_layers_against_decision(layers, self)


class GrammarFinding(BaseModel):
    code: str
    severity: str                    # info | warning
    message: str
    layer_id: Optional[str] = None


class GrammarAudit(BaseModel):
    """只读对账报告：findings + evaluated；绝不输出替代 SEMANTIC_VALID
    的顶层裁决（ADR-0200 D2 / ADR-0205 D7）。"""

    decision_fingerprint: str = ""
    grammar_version: str = GRAMMAR_VERSION
    evaluated: bool = True
    findings: List[GrammarFinding] = Field(default_factory=list)

    @property
    def has_blocking_risk(self) -> bool:
        """有没有 warn 级发现（信息性字段，不是 verdict）。"""
        return any(f.severity == "warning" for f in self.findings)


# ── 求解 ────────────────────────────────────────────────────────────────

def _fingerprint(request: GrammarRequest) -> str:
    canonical = json.dumps(
        request.model_dump(), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(
        f"{GRAMMAR_VERSION}|{canonical}".encode("utf-8")).hexdigest()[:32]


def _bind_channels(
    request: GrammarRequest,
    decisions: List[Tuple[FieldEvidence, MeasurementDecision]],
    disclosures: List[str],
    user_wins: List[Dict[str, Any]],
) -> List[ChannelBinding]:
    """逐字段绑定视觉通道：矩阵适配序（preferred → allowed）× runtime
    支持门槛；显式 pin 记 user-wins，rejected 级 pin 只披露不覆盖。"""
    bindings: List[ChannelBinding] = []
    seen_primary = False
    for evidence, measurement in decisions:
        codes = list(measurement.reason_codes)
        role = "unbound"
        if not evidence.is_secondary and not seen_primary:
            role = "primary"
            seen_primary = True
        elif not evidence.is_secondary:
            role = "secondary"
        pin = request.pinned_channels.get(evidence.name)
        variable: Optional[str] = None
        fit: Optional[ChannelFit] = None
        local_disclosures: List[str] = []
        if pin is not None:
            pin_fit = channel_fit(measurement.kind, pin)
            user_wins.append({
                "kind": "channel", "field": evidence.name, "value": pin,
                "fit_level": pin_fit.level, "reason_code": pin_fit.reason_code,
            })
            codes.append("GRAMMAR.PIN.CHANNEL")
            if pin_fit.level == "rejected":
                local_disclosures.append(
                    f"用户 pin 通道 {pin} 与测量语义 {measurement.kind} 的适配矩阵冲突"
                    f"（{pin_fit.reason_code}）——尊重用户选择，冲突如实披露")
                codes.append("GRAMMAR.PIN.CHANNEL_CONFLICT")
            variable, fit = pin, pin_fit
        else:
            row = channel_fit_order(measurement.kind)
            for level in ("preferred", "allowed"):
                for candidate in row.get(level, ()):  # 矩阵声明序 = 制图学优先序
                    if allocatable(candidate):
                        variable, fit = candidate, channel_fit(measurement.kind, candidate)
                        break
                if variable:
                    break
        if variable is None:
            local_disclosures.append(
                f"字段 {evidence.name}（{measurement.kind}）无 runtime 可分配通道——不绑定")
            codes.append("GRAMMAR.CHAN.NO_ALLOCATABLE_CHANNEL")
        if fit is not None and fit.level == "preferred":
            codes.append(fit.reason_code)
        bindings.append(ChannelBinding(
            field=evidence.name, role=role, measurement=measurement,
            variable=variable, fit_level=fit.level if fit else None,
            reason_codes=codes, disclosures=local_disclosures,
        ))
        disclosures.extend(local_disclosures)
    return bindings


def _select_representation(
    request: GrammarRequest,
    primary: Optional[ChannelBinding],
    scale: ScaleDecision,
    disclosures: List[str],
    user_wins: List[Dict[str, Any]],
) -> RepresentationChoice:
    """表达选择（全部落在 MapModel 注册 id 词表内，确定性排序）。"""
    codes: List[str] = []
    candidates: List[Dict[str, Any]] = []
    rejected: List[Dict[str, str]] = []
    collapse: Optional[Dict[str, Any]] = None
    kind = primary.measurement.kind if primary else None
    unique = None
    if request.fields and primary is not None:
        for f in request.fields:
            if f.name == primary.field:
                unique = f.unique_count

    def _cand(model_id: str, why: str) -> None:
        candidates.append({"model_id": model_id, "rank": len(candidates) + 1,
                           "reason_code": why})

    if request.geometry in ("point", "multi_point"):
        if primary is None or kind is None:
            _cand("simple_point_map", "GRAMMAR.REP.NO_THEMATIC_FIELD")
        elif scale.is_dense_points:
            # 密集 → 聚合族按尺度带优先序；heatmap 对 nominal 不可用
            for model_id in scale.point_candidates:
                if model_id == "visual_heatmap" and kind == "nominal":
                    rejected.append({
                        "model_id": model_id,
                        "reason_code": "GRAMMAR.REP.HEATMAP_NOMINAL",
                        "reason": "热力图表达量级疏密，类别场不适用",
                    })
                    continue
                _cand(model_id, "GRAMMAR.REP.DENSE_AGGREGATE")
            _cand("point_overlay", "GRAMMAR.REP.TOP_N_FALLBACK")
            codes.append("GRAMMAR.REP.DENSE_POINTS_AGGREGATE")
        elif kind in ("quantitative", "ratio", "rate"):
            _cand("proportional_symbol", f"GRAMMAR.REP.SIZE_FOR_{kind.upper()}")
            _cand("point_overlay", "GRAMMAR.REP.PLAIN_SYMBOL_FALLBACK")
            if kind == "rate":
                codes.append("GRAMMAR.REP.RATE_ON_POINTS_OK")
        else:
            _cand("point_overlay", f"GRAMMAR.REP.CHANNEL_FOR_{kind.upper()}")
            if kind == "nominal" and unique is not None and unique > MAX_CATEGORICAL_CLASSES:
                collapse = _collapse_spec(unique)
                codes.append("GRAMMAR.REP.TOO_MANY_CATEGORIES")
        if 0 < request.feature_count < 8:
            disclosures.append(
                f"n={request.feature_count} < 8：统计证据不足（resolve_symbology 将降置信）")
    elif request.geometry == "polygon":
        if primary is None or kind is None:
            codes.append("GRAMMAR.REP.NO_THEMATIC_FIELD")
        elif kind == "nominal":
            if unique is not None and unique > MAX_CATEGORICAL_CLASSES:
                collapse = _collapse_spec(unique)
                rejected.append({
                    "model_id": "categorical_thematic",
                    "reason_code": "GRAMMAR.REP.TOO_MANY_CATEGORIES",
                    "reason": f"类别数 {unique} 超过定性色带容量 {MAX_CATEGORICAL_CLASSES}",
                })
                codes.append("GRAMMAR.REP.TOO_MANY_CATEGORIES")
            _cand("categorical_thematic", "GRAMMAR.REP.NOMINAL_POLYGON")
        else:
            _cand("administrative_choropleth", f"GRAMMAR.REP.GRADUATED_FOR_{kind.upper()}")
            if kind == "rate":
                codes.append("GRAMMAR.REP.RATE_NORMALIZED")
                disclosures.append(
                    "率/密度字段：必须以分母归一化口径成图（count_vs_rate 语义族）")
            elif kind == "ratio":
                codes.append("GRAMMAR.REP.COUNT_VS_RATE_ADVISORY")
                disclosures.append(
                    "计数/总量面：直接上色会受区域大小偏置——若存在面积/人口分母，"
                    "优先转为率（advisory，不阻断）")
            elif kind == "signed_change":
                codes.append("GRAMMAR.REP.SIGNED_DIVERGING")
                disclosures.append("带符号变化：diverging 色带族（正负语义保序）")
            elif kind == "uncertainty":
                codes.append("GRAMMAR.REP.UNCERTAINTY_SECONDARY")
                disclosures.append(
                    "不确定度宜作次通道（opacity/hatch）——单独作主表达语义有限，"
                    "建议与主变量图层叠加")
    elif request.geometry == "raster":
        if kind in ("quantitative", "ratio", "rate", "temporal", "signed_change", "ordinal"):
            _cand("raster_surface", f"GRAMMAR.REP.RASTER_FOR_{kind.upper()}")
        elif kind == "nominal":
            rejected.append({
                "model_id": "raster_surface",
                "reason_code": "GRAMMAR.REP.RASTER_NOMINAL_UNBOUNDED",
                "reason": "本 grammar 版本不对类别栅格给表达（目录内无 native 模型）",
            })
    else:  # line 族
        codes.append("GRAMMAR.REP.LINE_GEOMETRY_NOT_COVERED")
        disclosures.append("线几何的表达目录未在本 grammar 版本覆盖（诚实不虚构）")

    selected: Optional[str] = None
    if request.pinned_representation:
        pinned_model = get_map_model(request.pinned_representation)
        if pinned_model is None:
            raise ValueError(
                f"pinned_representation={request.pinned_representation!r} "
                "不在 MapModel 注册词表")
        user_wins.append({
            "kind": "representation", "value": pinned_model.id,
            "reason_code": "GRAMMAR.PIN.REPRESENTATION",
        })
        selected = pinned_model.id
        codes.append("GRAMMAR.PIN.REPRESENTATION")
    else:
        selected = candidates[0]["model_id"] if candidates else None

    return RepresentationChoice(
        selected=selected, candidates=candidates, rejected=rejected,
        collapse=collapse, reason_codes=codes, disclosures=list(disclosures),
    )


def _collapse_spec(unique: int) -> Dict[str, Any]:
    """类别收纳动作（声明式：保 top N-1 + Other；执行在组件构建层）。"""
    return {
        "keep_classes": COLLAPSE_KEEP_CLASSES,
        "other_label": "Other",
        "observed_classes": unique,
        "reason_code": "GRAMMAR.REP.TOO_MANY_CATEGORIES",
    }


def _legend_form(data_kind: str) -> LegendPlan:
    """图例形态契约（data_kind → 合法形态词表；映射表在 grammar_types）。"""
    form = LEGEND_FORM_BY_DATA_KIND.get(data_kind, "graduated")
    codes = [f"GRAMMAR.PAIR.FORM_{form.upper()}_FOR_{data_kind.upper()}"]
    if data_kind == "cyclic":
        codes.append("GRAMMAR.PAIR.CYCLIC_GRADUATED_DISCLOSURE")
    return LegendPlan(form=form, reason_codes=codes)


def solve_grammar(request: GrammarRequest) -> GrammarDecision:
    """唯一求解入口（确定性纯函数；非法词表构造期即 fail-closed）。"""
    disclosures: List[str] = []
    user_wins: List[Dict[str, Any]] = []

    scale = scale_actions(
        zoom=request.zoom,
        feature_count=request.feature_count,
        geometry=request.geometry,
        viewport_px_w=request.viewport_px[0],
        viewport_px_h=request.viewport_px[1],
    )

    measured: List[Tuple[FieldEvidence, MeasurementDecision]] = []
    for f in request.fields:
        pin = f.measurement or None
        decision = infer_measurement_kind(
            f.name, dtype=f.dtype, values=f.values,
            unique_count=f.unique_count, explicit=pin,
        )
        if pin is not None:
            user_wins.append({
                "kind": "measurement", "field": f.name, "value": pin,
                "reason_code": "GRAMMAR.PIN.MEASUREMENT",
            })
        measured.append((f, decision))

    bindings = _bind_channels(request, measured, disclosures, user_wins)
    primary = next((b for b in bindings if b.role == "primary"), None)

    secondary_kinds = sorted({
        b.measurement.kind for b in bindings
        if b.role != "primary" and b.measurement.kind != (
            primary.measurement.kind if primary else None)
    })
    if secondary_kinds:
        disclosures.append(
            f"次通道字段测量语义 {secondary_kinds} 与主通道不同——"
            "跨通道可解码性由 carto.visualvar.overload 事后校验兜底")

    data_kind = primary.measurement.data_kind if primary else "sequential"
    representation = _select_representation(
        request, primary, scale, representation_disclosures(request), user_wins)
    disclosures.extend(representation.disclosures)

    content = {k: bool(request.content_flags.get(k)) for k in CONTENT_FLAGS}
    if primary is not None:
        content["has_thematic_layer"] = True
    plan = required_components_for(request.output_purpose, content)
    obligations = [c.type for c in plan.required]
    if primary is not None and "legend" not in obligations:
        obligations.append("legend")

    label_spec = None
    if request.label_profile is not None:
        label_spec = build_label_spec(request.label_profile)

    legend = _legend_form(data_kind) if primary is not None else LegendPlan(form="none")

    symbology_inputs: Dict[str, Any] = {
        "data_kind": data_kind,
        "origin": f"grammar@{GRAMMAR_VERSION}",
        "recommended_palette": request.pinned_palette,
        "recommended_classifiers": [],
    }
    if request.pinned_palette:
        user_wins.append({
            "kind": "palette", "value": request.pinned_palette,
            "reason_code": "GRAMMAR.PIN.PALETTE",
        })

    codes: List[str] = list(scale.reason_codes)
    codes.extend(representation.reason_codes)
    codes.extend(legend.reason_codes)
    for b in bindings:
        codes.extend(c for c in b.reason_codes if c not in codes)

    decision = GrammarDecision(
        fingerprint=_fingerprint(request),
        geometry=request.geometry,
        scale=scale,
        bindings=bindings,
        data_kind=data_kind,
        symbology_inputs=symbology_inputs,
        representation=representation,
        legend=legend,
        component_obligations=obligations,
        label_spec=label_spec,
        disclosures=disclosures,
        reason_codes=codes,
        user_wins=user_wins,
    )
    return decision


def representation_disclosures(request: GrammarRequest) -> List[str]:
    """请求级表达披露（geometry×密度的不变事实，选择前已知）。"""
    out: List[str] = []
    if request.geometry == "point" and request.feature_count == 0:
        out.append("feature_count=0：密集判定不可用，表达选择退化为符号图路径")
    return out


# ── 只读对账（critique 消费面）───────────────────────────────────────────

#: data_kind → 期望 legend_spec.type 词表（sequential 的 raster 连续色条
#: 以 continuous 形态合法；cyclic 降级 graduated 已在上游披露）。
_EXPECTED_LEGEND_TYPES: Dict[str, Tuple[str, ...]] = {
    "qualitative": ("categorical",),
    "diverging": ("divergent",),
    "sequential": ("graduated", "continuous"),
    "cyclic": ("graduated", "continuous"),
}


def audit_layers_against_decision(
    layers: List[Dict[str, Any]],
    decision: GrammarDecision,
) -> GrammarAudit:
    """只读对账：成图 layer 是否遵守 grammar 决策（无 mutation、无 verdict）。

    检查面（全部信息性/告警级）：
    - GRAMMAR.AUDIT.PAIRING：图例形态 vs 决策 data_kind（categorical↔
      colorbar 互斥；diverging 必须 divergent 形态）；
    - GRAMMAR.AUDIT.CHANNEL_LOAD：单层 paint 的数据字段数 vs
      semantic_checks 的 warn/fail 阈值（常量同源，契约测试锁定）；
    - GRAMMAR.AUDIT.REPRESENTATION：层型 vs 选中表达的 maplibre 层型
      （best-effort；证据不足 → info 披露，不假设）。
    """
    audit = GrammarAudit(
        decision_fingerprint=decision.fingerprint)
    if not decision.bindings:
        return audit
    expected_types = _EXPECTED_LEGEND_TYPES.get(decision.data_kind)
    bound_fields = {b.field for b in decision.bindings}

    for layer in layers or []:
        if not isinstance(layer, dict):
            continue
        lid = layer.get("id")
        legend_spec = layer.get("legend_spec")
        if isinstance(legend_spec, dict) and expected_types:
            ltype = legend_spec.get("type")
            if ltype and ltype not in expected_types:
                audit.findings.append(GrammarFinding(
                    code="GRAMMAR.AUDIT.PAIRING",
                    severity="warning",
                    message=(
                        f"层 {lid} 图例形态 {ltype} 与决策 data_kind="
                        f"{decision.data_kind} 的合法形态 {list(expected_types)} 不配对"),
                    layer_id=lid,
                ))
        paint = layer.get("paint")
        if isinstance(paint, dict):
            fields: set = set()
            for _prop, spec in _paint_methods(paint):
                field = spec.get("field") if isinstance(spec, dict) else None
                if isinstance(field, str) and field:
                    fields.add(field)
            if fields - bound_fields:
                audit.findings.append(GrammarFinding(
                    code="GRAMMAR.AUDIT.UNBOUND_FIELD",
                    severity="info",
                    message=(
                        f"层 {lid} 编码了决策未绑定的字段 "
                        f"{sorted(fields - bound_fields)[:4]}"),
                    layer_id=lid,
                ))
            if len(fields) >= _VISUALVAR_FAIL_COUNT:
                audit.findings.append(GrammarFinding(
                    code="GRAMMAR.AUDIT.CHANNEL_LOAD",
                    severity="warning",
                    message=(
                        f"层 {lid} 数据字段数 {len(fields)} ≥ 失败阈值 "
                        f"{_VISUALVAR_FAIL_COUNT}（carto.visualvar.overload 同源）"),
                    layer_id=lid,
                ))
            elif len(fields) >= _VISUALVAR_WARN_COUNT:
                audit.findings.append(GrammarFinding(
                    code="GRAMMAR.AUDIT.CHANNEL_LOAD",
                    severity="warning",
                    message=(
                        f"层 {lid} 数据字段数 {len(fields)} ≥ 告警阈值 "
                        f"{_VISUALVAR_WARN_COUNT}（carto.visualvar.overload 同源）"),
                    layer_id=lid,
                ))
        if decision.representation.selected:
            model = get_map_model(decision.representation.selected)
            expected_layer_type = model.maplibre_layer_type if model else None
            if expected_layer_type and layer.get("type") and (
                layer.get("type") != expected_layer_type
            ):
                audit.findings.append(GrammarFinding(
                    code="GRAMMAR.AUDIT.REPRESENTATION",
                    severity="info",
                    message=(
                        f"层 {lid} 类型 {layer.get('type')} 与选中表达 "
                        f"{decision.representation.selected}"
                        f"（{expected_layer_type}）不同——可能是伴生层"),
                    layer_id=lid,
                ))
    return audit


__all__ = [
    "GRAMMAR_VERSION",
    "FieldEvidence",
    "GrammarRequest",
    "GrammarDecision",
    "ChannelBinding",
    "RepresentationChoice",
    "LegendPlan",
    "GrammarAudit",
    "GrammarFinding",
    "solve_grammar",
    "audit_layers_against_decision",
]
