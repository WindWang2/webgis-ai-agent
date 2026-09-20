"""Measurement Semantics —— 字段级量纲契约（ADR-0204，方向 2）。

语义角色（semantic_profile，ADR-0092）回答"这是什么角色"，本模块回答
"这是什么量"：count vs density、rate vs absolute、percent vs fraction、
signed change vs absolute —— 并给每个字段一个 canonical unit 与维度。

原则（不可妥协，与 semantic_profile 同纪律）：

- **派生投影，不是第二数据真相**：输入只有 DatasetProfile、
  SemanticDatasetProfile、有界值样本（≤200/字段，调用方供数）与显式
  override；本模块绝不读 FeatureCollection / raster、绝不扫描全量数据；
- **证据分级**：user/LLM 显式 override（user_declared）> 值样本结构
  （rule_derived）> 名称 unit_hint/角色（metadata_derived）；证据不足
 如实 unknown，不虚构单位；
- **危险歧义必须留证据码**（UNIT_DIMENSION_MISMATCH / DEGREE_LIKE_METRIC /
  RATE_MISSING_TEMPORAL / DENSITY_MISSING_DENOMINATOR）—— 推导不抛异常、
  不静默猜测：错了也要让人看见是按哪条证据推的；
- nodata/NaN/Inf 先过滤（与 thematic_spec.is_finite_number 同规），
  坏值不得污染 kind/unit 判定；
- 确定性：同输入恒同输出；可序列化（to_dict/from_dict，版本化）。

消费方（生产链，ADR-0204 §Integration）：
- symbology.resolve_symbology（语义定族：qualitative/diverging/sequential）；
- data_qualification（单位维度 fail-closed 闸）；
- create_thematic_map（legend.unit 自动填充 + diverging 切换）。
"""
from __future__ import annotations

import math
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.lib.data.profile import unit_hint_for_field
from app.lib.gis.dataset_profile import DatasetProfile, MAX_PROFILE_FIELDS
from app.lib.gis.semantic_profile import (
    EvidenceSource,
    RoleConfidence,
    SemanticDatasetProfile,
    SemanticFieldRole,
)

#: 契约版本（from_dict 对未知版本 fail-closed 拒收）。
MEASUREMENT_PROFILE_VERSION = 1

#: 有界值样本上限（与 semantic_profile.MAX_VALUE_SAMPLES 同值同源纪律）。
MAX_VALUE_SAMPLES = 200

#: 有效性检查码（稳定契约：qualification/grapher 断言依赖；文案可演进）。
CHECK_UNIT_DIMENSION_MISMATCH = "UNIT_DIMENSION_MISMATCH"
CHECK_DEGREE_LIKE_METRIC = "DEGREE_LIKE_METRIC"
CHECK_RATE_MISSING_TEMPORAL = "RATE_MISSING_TEMPORAL"
CHECK_DENSITY_MISSING_DENOMINATOR = "DENSITY_MISSING_DENOMINATOR"

# ── 词表 ────────────────────────────────────────────────────────────────────


class MeasurementKind(str, Enum):
    """量的类型（ADR-0204 §Canonical Contracts）。"""

    COUNT = "count"                          # 计数（学校数量）
    ABSOLUTE_QUANTITY = "absolute_quantity"  # 绝对量（人口、面积、长度）
    RATIO = "ratio"                          # 无量纲比（fraction [0,1]）
    RATE = "rate"                            # 单位时间变化率（增长率）
    DENSITY = "density"                      # 单位面积/人口密度
    PERCENTAGE = "percentage"                # 百分比 [0,100]
    INDEX = "index"                          # 指数/得分/温度等合成量
    CATEGORY = "category"                    # 无序类别（土地利用类型）
    ORDINAL = "ordinal"                      # 有序等级（ Suitability 1-5）
    SIGNED_CHANGE = "signed_change"          # 带符号变化（增减）
    UNCERTAINTY = "uncertainty"              # 不确定度/误差


class UnitDimension(str, Enum):
    """量纲（兼容性判断的判定键；unit 名只是显示层）。"""

    COUNT = "count"
    POPULATION = "population"
    LENGTH = "length"
    AREA = "area"
    CURRENCY = "currency"
    RATIO = "ratio"          # fraction [0,1] 与 percent [0,100] 同维不同标尺
    INDEX = "index"
    TEMP = "temperature"
    TIME = "time"
    NONE = "none"            # 无量纲（类别/指数/不确定度归此或专用维）


class UnitEntry(BaseModel):
    """canonical unit 注册项。"""

    dimension: UnitDimension
    display: str                     # 图例显示名（zh 口径，与仓内一致）
    scale_to_reference: float = 1.0  # 相对同维度参考单位的倍率（仅记录用）


#: canonical unit 注册表（closed vocabulary；未知 hint → dimension 推断）。
CANONICAL_UNITS: Dict[str, UnitEntry] = {
    "count": UnitEntry(dimension=UnitDimension.COUNT, display="个"),
    "persons": UnitEntry(dimension=UnitDimension.POPULATION, display="人"),
    "meters": UnitEntry(dimension=UnitDimension.LENGTH, display="m", scale_to_reference=1.0),
    "kilometers": UnitEntry(dimension=UnitDimension.LENGTH, display="km", scale_to_reference=1000.0),
    "square_meters": UnitEntry(dimension=UnitDimension.AREA, display="m²", scale_to_reference=1.0),
    "square_kilometers": UnitEntry(dimension=UnitDimension.AREA, display="km²", scale_to_reference=1e6),
    "hectares": UnitEntry(dimension=UnitDimension.AREA, display="公顷", scale_to_reference=1e4),
    "degrees": UnitEntry(dimension=UnitDimension.LENGTH, display="°", scale_to_reference=1.0),
    "cny": UnitEntry(dimension=UnitDimension.CURRENCY, display="元"),
    "usd": UnitEntry(dimension=UnitDimension.CURRENCY, display="USD"),
    "percent": UnitEntry(dimension=UnitDimension.RATIO, display="%", scale_to_reference=100.0),
    "fraction": UnitEntry(dimension=UnitDimension.RATIO, display="比例", scale_to_reference=1.0),
    "celsius": UnitEntry(dimension=UnitDimension.TEMP, display="°C"),
    "years": UnitEntry(dimension=UnitDimension.TIME, display="年"),
    "index": UnitEntry(dimension=UnitDimension.INDEX, display="指数"),
    "none": UnitEntry(dimension=UnitDimension.NONE, display=""),
}

# 角色期望维度（单位兼容性判定的判定键；unit 名只是显示层）。
# normalization_denominator 接受 population/area 两族分母。
_ROLE_EXPECTED_DIMENSION: Dict[str, Tuple[UnitDimension, ...]] = {
    SemanticFieldRole.POPULATION_MEASURE.value: (UnitDimension.POPULATION,),
    SemanticFieldRole.AREA_MEASURE.value: (UnitDimension.AREA,),
    SemanticFieldRole.DISTANCE_MEASURE.value: (UnitDimension.LENGTH,),
    SemanticFieldRole.WEIGHT_MEASURE.value: (UnitDimension.NONE,),
    SemanticFieldRole.RATIO_MEASURE.value: (UnitDimension.RATIO,),
    SemanticFieldRole.COUNT_MEASURE.value: (UnitDimension.COUNT,),
    SemanticFieldRole.NORMALIZATION_DENOMINATOR.value: (
        UnitDimension.POPULATION, UnitDimension.AREA,
    ),
}

# 名称 → canonical unit（在 unit_hint_for_field 四条之上补齐面积/比率/度）。
_UNIT_NAME_RULES: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"(平方公里|sq_?km|km2|km²)", re.I), "square_kilometers"),
    (re.compile(r"(公顷|hectare)", re.I), "hectares"),
    (re.compile(r"(平方米|m2|m²|sq_?m$)", re.I), "square_meters"),
    (re.compile(r"(千米|公里|km$)", re.I), "kilometers"),
    (re.compile(r"(米$|^m$|_m$|meter)", re.I), "meters"),
    (re.compile(r"(度|°|deg(?:ree)?)$", re.I), "degrees"),
    (re.compile(r"(万元|亿元|元$|cny|rmb)", re.I), "cny"),
    (re.compile(r"(usd|美元)", re.I), "usd"),
    (re.compile(r"(占比|比例|percent|pct|share|fraction)", re.I), "fraction"),
)

_DENSITY_NAME_RE = re.compile(
    r"(密度|每平方公里|每平方千米|每万人|人均|per_?(?:km|sq|capita)|density)", re.I
)
_DENSITY_AREA_RE = re.compile(r"(每平方公里|每平方千米|per_?km|per_?sq|density)", re.I)
_SIGNED_NAME_RE = re.compile(r"(变化|增减|增长|净变化|change|growth|delta|net_)", re.I)
_RATE_NAME_RE = re.compile(r"(增长率|变化率|增速|速率|growth_?rate|rate_?of|_rate$|速率)", re.I)
_ORDINAL_NAME_RE = re.compile(r"(等级|级别|排名|grade|rank|tier|level$)", re.I)
_UNCERTAINTY_NAME_RE = re.compile(r"(误差|不确定|置信|std_err?|uncertainty|confidence|_se$|_sigma)", re.I)
_TEMP_NAME_RE = re.compile(r"(温度|气温|temperature|temp$|lst)", re.I)

#: 度级值结构判据：LENGTH/AREA 维度 + 地理 CRS 下的可疑量级（|max| ≤ 360
#: 且存在非整数 → 疑似 degrees 冒充 meters/km；整数大值不判）。
_DEGREE_LIKE_MAX_ABS = 360.0


class FieldSemantics(BaseModel):
    """单字段的量纲语义判定（有界、可序列化）。"""

    field: str
    measurement_kind: str = ""          # MeasurementKind.value；"" = 证据不足
    unit_dimension: str = ""            # UnitDimension.value；"" = 未知
    unit: str = ""                      # canonical unit 名（CANONICAL_UNITS 键）
    kind_confidence: str = RoleConfidence.UNKNOWN.value
    unit_confidence: str = RoleConfidence.UNKNOWN.value
    domain_hint: Optional[List[float]] = None   # 有界域证据 [min,max]（如 [0,100]）
    center_hint: Optional[float] = None         # diverging 中心（signed change → 0）
    evidence: List[str] = Field(default_factory=list)   # ≤6
    checks: List[Dict[str, str]] = Field(default_factory=list)  # ≤4（code+detail）

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "measurement_kind": self.measurement_kind,
            "unit_dimension": self.unit_dimension,
            "unit": self.unit,
            "kind_confidence": self.kind_confidence,
            "unit_confidence": self.unit_confidence,
            "domain_hint": (
                [round(float(v), 6) for v in self.domain_hint[:2]]
                if self.domain_hint and len(self.domain_hint) == 2 else None
            ),
            "center_hint": self.center_hint,
            "evidence": list(self.evidence[:6]),
            "checks": [dict(c) for c in self.checks[:4]],
        }


class DatasetMeasurementProfile(BaseModel):
    """量纲语义投影（versioned + bounded + serializable）。

    是 DatasetProfile / SemanticDatasetProfile 的派生视图 —— 不是第二数据
    真相（ADR-0204 Decision #1）。
    """

    measurement_profile_version: int = MEASUREMENT_PROFILE_VERSION
    fields: List[FieldSemantics] = Field(default_factory=list)

    def by_field(self, name: str) -> Optional[FieldSemantics]:
        for f in self.fields:
            if f.field == str(name):
                return f
        return None

    def fields_with_kind(self, kind: MeasurementKind) -> List[str]:
        return [f.field for f in self.fields if f.measurement_kind == kind.value]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "measurement_profile_version": self.measurement_profile_version,
            "fields": [f.to_bounded_dict() for f in self.fields[:MAX_PROFILE_FIELDS]],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "DatasetMeasurementProfile":
        """反序列化（fail-closed：未知版本拒收，不静默降级）。"""
        if not isinstance(data, dict):
            raise ValueError("measurement profile 必须是 dict")
        version = data.get("measurement_profile_version")
        if version != MEASUREMENT_PROFILE_VERSION:
            raise ValueError(
                f"不支持的 measurement_profile_version: {version!r}"
                f"（本契约只认 {MEASUREMENT_PROFILE_VERSION}）"
            )
        raw_fields = data.get("fields")
        out: List[FieldSemantics] = []
        if isinstance(raw_fields, list):
            for item in raw_fields[:MAX_PROFILE_FIELDS]:
                if isinstance(item, dict) and item.get("field"):
                    checks = [
                        c for c in (item.get("checks") or [])
                        if isinstance(c, dict) and c.get("code")
                    ]
                    out.append(FieldSemantics(
                        field=str(item["field"])[:128],
                        measurement_kind=str(item.get("measurement_kind") or ""),
                        unit_dimension=str(item.get("unit_dimension") or ""),
                        unit=str(item.get("unit") or ""),
                        kind_confidence=str(item.get("kind_confidence") or RoleConfidence.UNKNOWN.value),
                        unit_confidence=str(item.get("unit_confidence") or RoleConfidence.UNKNOWN.value),
                        domain_hint=(
                            [float(v) for v in item["domain_hint"][:2]]
                            if isinstance(item.get("domain_hint"), list)
                            and len(item["domain_hint"]) == 2
                            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in item["domain_hint"][:2])
                            else None
                        ),
                        center_hint=(
                            float(item["center_hint"])
                            if isinstance(item.get("center_hint"), (int, float))
                            and not isinstance(item.get("center_hint"), bool) else None
                        ),
                        evidence=[str(e) for e in (item.get("evidence") or [])[:6]],
                        checks=checks[:4],
                    ))
        return cls(fields=out)


# ── 值样本结构证据（全部先过 finite 过滤）──────────────────────────────────


def _finite_samples(values: Sequence[Any]) -> List[float]:
    out: List[float] = []
    for v in values[:MAX_VALUE_SAMPLES]:
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            out.append(float(v))
    return out


def _span_structure(vals: List[float]) -> Tuple[Optional[float], Optional[float], bool]:
    """(min, max, 跨 0)。空样本 → (None, None, False)。"""
    if not vals:
        return None, None, False
    lo, hi = min(vals), max(vals)
    return lo, hi, (lo < 0.0 < hi)


def _all_in_unit_interval(vals: List[float]) -> bool:
    return bool(vals) and all(0.0 <= v <= 1.0 for v in vals)


def _percent_like(vals: List[float]) -> bool:
    """值结构呈百分比标尺（有 >1 的值且都在 [0,100]）。"""
    return bool(vals) and all(0.0 <= v <= 100.0 for v in vals) and any(v > 1.0 for v in vals)


def _degree_like(vals: List[float]) -> bool:
    """度级值结构：|max| ≤ 360 且存在非整数（坐标/度量的混合形态）。"""
    if not vals:
        return False
    return max(abs(v) for v in vals) <= _DEGREE_LIKE_MAX_ABS and any(
        not float(v).is_integer() for v in vals
    )


def _unit_from_name(field: str) -> str:
    """字段名 → canonical unit 名（本模块扩展表优先，回退 unit_hint_for_field）。"""
    for pattern, unit in _UNIT_NAME_RULES:
        if pattern.search(field or ""):
            return unit
    hint = unit_hint_for_field(field)
    return hint if hint in CANONICAL_UNITS else ""


# ── kind / unit 推导（纯函数）──────────────────────────────────────────────


def _kind_from_role(
    roles: Sequence[str],
    *,
    vals: List[float],
    has_temporal: bool,
) -> Tuple[str, str, List[str]]:
    """语义角色 → (kind, confidence, evidence)。名称/值证据可细分。

    多角色冲突时按特异性取主角色（count 的 ``n_`` 名称正则最宽松，凡与
    ratio/population 等并存时不得以 count 压制更特异角色）。
    """
    evidence: List[str] = [EvidenceSource.FIELD_NAME.value]
    if not roles:
        return "", RoleConfidence.UNKNOWN.value, evidence
    _SPECIFICITY = (
        SemanticFieldRole.RATIO_MEASURE.value,
        SemanticFieldRole.POPULATION_MEASURE.value,
        SemanticFieldRole.AREA_MEASURE.value,
        SemanticFieldRole.DISTANCE_MEASURE.value,
        SemanticFieldRole.WEIGHT_MEASURE.value,
        SemanticFieldRole.COUNT_MEASURE.value,
        SemanticFieldRole.CONTINUOUS_MEASURE.value,
    )
    primary = next((r for r in _SPECIFICITY if r in roles), roles[0])

    def _result(kind: str, conf: str) -> Tuple[str, str, List[str]]:
        return kind, conf, evidence

    if primary == SemanticFieldRole.CATEGORY.value:
        return _result(MeasurementKind.CATEGORY.value, RoleConfidence.RULE_DERIVED.value)
    if primary == SemanticFieldRole.RATIO_MEASURE.value:
        # ratio 的标尺由值结构细分：fraction vs percent（调用方做）。
        return _result(MeasurementKind.RATIO.value, RoleConfidence.METADATA_DERIVED.value)
    if primary == SemanticFieldRole.POPULATION_MEASURE.value:
        # 负值人口的带符号性由 _overlay_kind 按「名称 signed + 样本跨 0」判。
        return _result(MeasurementKind.ABSOLUTE_QUANTITY.value, RoleConfidence.METADATA_DERIVED.value)
    if primary in (
        SemanticFieldRole.AREA_MEASURE.value,
        SemanticFieldRole.DISTANCE_MEASURE.value,
        SemanticFieldRole.WEIGHT_MEASURE.value,
    ):
        return _result(MeasurementKind.ABSOLUTE_QUANTITY.value, RoleConfidence.METADATA_DERIVED.value)
    if primary == SemanticFieldRole.COUNT_MEASURE.value:
        if vals and min(vals) < 0:
            # "count" 命名但样本带负值：不是计数（名字会撒谎，值不会）。
            return _result(MeasurementKind.SIGNED_CHANGE.value, RoleConfidence.RULE_DERIVED.value)
        return _result(MeasurementKind.COUNT.value, RoleConfidence.METADATA_DERIVED.value)
    if primary == SemanticFieldRole.CONTINUOUS_MEASURE.value:
        return _result(MeasurementKind.INDEX.value, RoleConfidence.METADATA_DERIVED.value)
    # 其余角色（id/label/坐标/分母）不产生量纲 kind。
    return "", RoleConfidence.UNKNOWN.value, evidence


def _overlay_kind(
    field: str,
    *,
    vals: List[float],
    has_temporal: bool,
) -> Tuple[str, str, List[str]]:
    """名称/值结构叠加证据（密度/带符号/率/等级/不确定度）。"""
    evidence: List[str] = []
    lo, hi, crosses_zero = _span_structure(vals)
    if _DENSITY_NAME_RE.search(field):
        evidence.append("name:density")
        return MeasurementKind.DENSITY.value, RoleConfidence.METADATA_DERIVED.value, evidence
    if crosses_zero and _SIGNED_NAME_RE.search(field):
        evidence.append("name:signed+sample:crosses_zero")
        return MeasurementKind.SIGNED_CHANGE.value, RoleConfidence.RULE_DERIVED.value, evidence
    if _RATE_NAME_RE.search(field):
        # 率的细分由调用方在 checks 里补 RATE_MISSING_TEMPORAL。
        evidence.append("name:rate")
        conf = (
            RoleConfidence.RULE_DERIVED.value
            if (has_temporal or (vals is not None and lo is not None and lo < 0))
            else RoleConfidence.METADATA_DERIVED.value
        )
        return MeasurementKind.RATE.value, conf, evidence
    if _UNCERTAINTY_NAME_RE.search(field):
        evidence.append("name:uncertainty")
        return MeasurementKind.UNCERTAINTY.value, RoleConfidence.METADATA_DERIVED.value, evidence
    if _ORDINAL_NAME_RE.search(field) and vals and _all_small_ordered_ints(vals):
        evidence.append("name:ordinal+sample:int_range")
        return MeasurementKind.ORDINAL.value, RoleConfidence.RULE_DERIVED.value, evidence
    # 裸率名 + 单位区间样本（无语义角色时的弱证据兜底；与 name_kind_hint
    # 同一名称词表口径，避免 resolver 与推导对『率』的判定不一致）。
    if (
        vals
        and _all_in_unit_interval(vals)
        and re.search(r"(率|比例|占比|percent|pct|ratio|share|fraction)", field, re.I)
    ):
        evidence.append("name:ratio+sample:unit_interval")
        return MeasurementKind.RATIO.value, RoleConfidence.RULE_DERIVED.value, evidence
    return "", "", evidence


def _all_small_ordered_ints(vals: List[float]) -> bool:
    return all(float(v).is_integer() and 0 <= v <= 10 for v in vals)


def derive_field_semantics(
    field: str,
    roles: Sequence[str],
    *,
    value_samples: Optional[Sequence[Any]] = None,
    has_temporal: bool = False,
    crs: str = "",
    unit_override: str = "",
) -> FieldSemantics:
    """单字段量纲推导（纯函数；derive_measurement_profile 的内核）。"""
    vals = _finite_samples(value_samples or [])
    lo, hi, _crosses = _span_structure(vals)
    checks: List[Dict[str, str]] = []
    evidence: List[str] = []

    # 1) kind：角色基线 + 名称/值叠加（叠加优先 —— 名称密度/率/带符号是
    #    更特异的证据，角色只是基线）。
    kind, kind_conf, kind_ev = _kind_from_role(
        roles, vals=vals, has_temporal=has_temporal)
    evidence.extend(kind_ev)
    ov_kind, ov_conf, ov_ev = _overlay_kind(
        field, vals=vals, has_temporal=has_temporal)
    if ov_kind:
        kind, kind_conf = ov_kind, ov_conf
        evidence.extend(ov_ev)
    # 值结构细分 ratio 标尺（fraction vs percent）。
    if kind == MeasurementKind.RATIO.value:
        if _percent_like(vals):
            kind = MeasurementKind.PERCENTAGE.value
            kind_conf = RoleConfidence.RULE_DERIVED.value
            evidence.append("sample:percent_scale")
        elif _all_in_unit_interval(vals) and vals:
            kind_conf = RoleConfidence.RULE_DERIVED.value
            evidence.append("sample:unit_interval")
    if kind == MeasurementKind.RATE.value and _SIGNED_NAME_RE.search(field) and _crosses:
        # 带符号变化率（如人口增长率有负增长）：diverging 语义。
        evidence.append("sample:signed_rate")

    # 2) unit：override > 值结构 > 名称 hint > 维度缺省。
    unit_conf = RoleConfidence.UNKNOWN.value
    unit = ""
    if unit_override and unit_override in CANONICAL_UNITS:
        unit = unit_override
        unit_conf = RoleConfidence.USER_DECLARED.value
        evidence.append(EvidenceSource.USER_DECLARATION.value)
    elif kind == MeasurementKind.PERCENTAGE.value:
        unit, unit_conf = "percent", RoleConfidence.RULE_DERIVED.value
        evidence.append("sample:percent_scale")
    elif kind == MeasurementKind.RATIO.value and vals:
        unit, unit_conf = "fraction", RoleConfidence.RULE_DERIVED.value
        evidence.append("sample:unit_interval")
    else:
        unit = _unit_from_name(field)
        if unit:
            unit_conf = RoleConfidence.METADATA_DERIVED.value
            evidence.append("name:unit_hint")

    # 3) dimension：unit 决定；无 unit 时 kind 缺省。
    dimension = ""
    if unit:
        entry = CANONICAL_UNITS.get(unit)
        if entry is not None:
            dimension = entry.dimension.value
    elif kind == MeasurementKind.COUNT.value:
        dimension = UnitDimension.COUNT.value
    elif kind == MeasurementKind.INDEX.value:
        dimension = UnitDimension.INDEX.value
    elif kind == MeasurementKind.CATEGORY.value or kind == MeasurementKind.ORDINAL.value:
        dimension = UnitDimension.NONE.value

    # 温度名称 + INDEX kind → TEMP 维度（unit 保持 index：显示层无温标证据）。
    if kind == MeasurementKind.INDEX.value and _TEMP_NAME_RE.search(field):
        dimension = UnitDimension.TEMP.value
        evidence.append("name:temperature")

    # 4) danger checks（fail-closed 证据；不改变 kind/unit，只留证据码）。
    expected: Optional[Tuple[UnitDimension, ...]] = None
    for r in roles:
        if r in _ROLE_EXPECTED_DIMENSION:
            expected = _ROLE_EXPECTED_DIMENSION[r]
            break
    if expected is not None and dimension and UnitDimension(dimension) not in expected:
        # ratio 维与 count 维的错配是生产事故高发（每万人学校数绑成 count）。
        checks.append({
            "code": CHECK_UNIT_DIMENSION_MISMATCH,
            "detail": f"角色期望维度 {'/'.join(d.value for d in expected)}"
                      f" ≠ 单位维度 {dimension}",
        })
    if (
        dimension in (UnitDimension.LENGTH.value, UnitDimension.AREA.value)
        and unit not in ("degrees",)
        and _degree_like(vals)
        and str(crs or "").upper() in ("EPSG:4326", "EPSG:4490", "CRS84", "OGC:CRS84", "WGS84")
    ):
        # 地理 CRS + 度级值结构 + 米制单位声明：疑似 degrees 冒充 meters。
        checks.append({
            "code": CHECK_DEGREE_LIKE_METRIC,
            "detail": f"地理 CRS（{crs}）下值结构呈度级（|max|≤360 带小数）"
                      f"而单位为 {unit}——需澄清或投影后再度量",
        })
    if kind == MeasurementKind.RATE.value and not has_temporal:
        checks.append({
            "code": CHECK_RATE_MISSING_TEMPORAL,
            "detail": "率语义字段但画像无时间字段证据——时序归属未证实",
        })
    if kind == MeasurementKind.DENSITY.value and not _DENSITY_AREA_RE.search(field):
        checks.append({
            "code": CHECK_DENSITY_MISSING_DENOMINATOR,
            "detail": "密度语义字段但名称未证实面积分母——分母维度需澄清",
        })

    domain: Optional[List[float]] = None
    if kind == MeasurementKind.PERCENTAGE.value and vals:
        domain = [0.0, 100.0]
    elif kind == MeasurementKind.RATIO.value and vals and _all_in_unit_interval(vals):
        domain = [0.0, 1.0]
    center: Optional[float] = None
    if kind == MeasurementKind.SIGNED_CHANGE.value:
        center = 0.0

    return FieldSemantics(
        field=str(field)[:128],
        measurement_kind=kind,
        unit_dimension=dimension,
        unit=unit,
        kind_confidence=kind_conf,
        unit_confidence=unit_conf,
        domain_hint=domain,
        center_hint=center,
        evidence=evidence[:6],
        checks=checks[:4],
    )


def derive_measurement_profile(
    profile: DatasetProfile,
    semantic_profile: Optional[SemanticDatasetProfile] = None,
    *,
    value_samples: Optional[Dict[str, Sequence[Any]]] = None,
    unit_overrides: Optional[Dict[str, str]] = None,
) -> DatasetMeasurementProfile:
    """DatasetProfile ⊕ SemanticDatasetProfile ⊕ 有界样本 → 量纲画像（纯函数）。

    ``value_samples``: field → 非空值列表（≤MAX_VALUE_SAMPLES，工具层供数）；
    ``unit_overrides``: field → canonical unit 名（user/LLM 显式，最高置信）。
    """
    samples = value_samples or {}
    overrides = unit_overrides or {}
    sem = semantic_profile
    assignments: Dict[str, Tuple[List[str], str]] = {}
    if sem is not None:
        for a in (sem.field_roles or [])[:MAX_PROFILE_FIELDS]:
            assignments[str(a.field)] = (list(a.roles or []), str(a.confidence))
    has_temporal = any(
        SemanticFieldRole.TEMPORAL_DIMENSION.value in roles
        for roles, _ in assignments.values()
    ) if sem is not None else False

    out: List[FieldSemantics] = []
    for name in list((profile.fields or {}).keys())[:MAX_PROFILE_FIELDS]:
        fname = str(name)
        roles, _conf = assignments.get(fname, ([], ""))
        out.append(derive_field_semantics(
            fname,
            roles,
            value_samples=samples.get(fname) or [],
            has_temporal=has_temporal,
            crs=str(profile.crs or ""),
            unit_override=str(overrides.get(fname) or ""),
        ))
    return DatasetMeasurementProfile(fields=out)


# ── 语义 → 符号化映射（S2 接线：语义定族，分布定法）───────────────────────


def name_kind_hint(field: str) -> str:
    """字段名 → MeasurementKind 值（弱证据，仅 metadata 级；未命中 ""）。

    供 field_resolver 在无量纲画像时做 kind 匹配（单一名称词表源：复用
    本模块 _overlay 的正则，不在 resolver 里复制规则）。
    """
    f = str(field or "")
    if not f:
        return ""
    if _DENSITY_NAME_RE.search(f):
        return MeasurementKind.DENSITY.value
    if _RATE_NAME_RE.search(f):
        return MeasurementKind.RATE.value
    if _SIGNED_NAME_RE.search(f):
        return MeasurementKind.SIGNED_CHANGE.value
    if _UNCERTAINTY_NAME_RE.search(f):
        return MeasurementKind.UNCERTAINTY.value
    if _ORDINAL_NAME_RE.search(f):
        return MeasurementKind.ORDINAL.value
    if re.search(r"(率|比例|占比|percent|pct|ratio|share|fraction)", f, re.I):
        return MeasurementKind.RATIO.value
    return ""


def measurement_to_data_kind(kind: str) -> Optional[str]:
    """MeasurementKind → symbology DataKind（None = 不提供语义证据）。

    契约（ADR-0204）：CATEGORY/ORDINAL → qualitative；SIGNED_CHANGE →
    diverging；UNCERTAINTY 不映射（走透明度通道，非色相族）；其余 →
    sequential。RATIO/PERCENTAGE 等有界正值族归 sequential —— 分布证据
    （重尾等）仍可在其上决定分类法。
    """
    if not kind:
        return None
    try:
        k = MeasurementKind(kind)
    except ValueError:
        return None
    if k in (MeasurementKind.CATEGORY, MeasurementKind.ORDINAL):
        return "qualitative"
    if k == MeasurementKind.SIGNED_CHANGE:
        return "diverging"
    if k == MeasurementKind.UNCERTAINTY:
        return None
    return "sequential"


def measurement_diverging_center(kind: str) -> Optional[float]:
    """SIGNED_CHANGE → diverging 中心 0；其余 None。"""
    return 0.0 if kind == MeasurementKind.SIGNED_CHANGE.value else None


def legend_unit_display(unit: str) -> str:
    """canonical unit 名 → 图例显示串（未知名原样返回，不虚构）。"""
    entry = CANONICAL_UNITS.get(unit)
    return entry.display if entry is not None else str(unit or "")


__all__ = [
    "MEASUREMENT_PROFILE_VERSION",
    "MeasurementKind",
    "UnitDimension",
    "UnitEntry",
    "CANONICAL_UNITS",
    "FieldSemantics",
    "DatasetMeasurementProfile",
    "derive_field_semantics",
    "derive_measurement_profile",
    "name_kind_hint",
    "measurement_to_data_kind",
    "measurement_diverging_center",
    "legend_unit_display",
    "CHECK_UNIT_DIMENSION_MISMATCH",
    "CHECK_DEGREE_LIKE_METRIC",
    "CHECK_RATE_MISSING_TEMPORAL",
    "CHECK_DENSITY_MISSING_DENOMINATOR",
    "MAX_VALUE_SAMPLES",
]
