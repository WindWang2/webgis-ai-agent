"""G47 数量语义与面积比例（WP02 / WP04）。

核心裁决都在这里，而且都是**显式**的：

* 缺失（``None``/键不存在）、零、负数、布尔、字符串、NaN/Infinity 五类分开
  统计，**不合并**：零不是缺失，缺失不会被当成 0 参与总量，负数不被取绝对值。
* 只有"正值"参与面积比例。共同系数 ``k`` 由**最大值**决定（``r = sqrt(k*v)``，
  面积 = ``π k v`` 与 v 成正比），逐圆 clamp 被禁止 —— 极端跨度若无法可靠表达
  （最小圆半径低于图面可见阈值）就 typed 拒绝，而不是"最小可见圆"伪装比例。
* 零值区域保留身份与"0"说明（不出正面积圆）；缺失区域单列。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.cartogram_authoring.errors import AuthoringError

#: 最小可见半径（diagram 单位）：低于它的圆在图面上无法被读者比较面积。
MIN_VISIBLE_RADIUS = 1e-3
#: 诊断/回执里有界列出的问题要素数。
MAX_OFFENDERS = 12


@dataclass(frozen=True)
class QuantityEntry:
    feature_id: str
    kind: str           # positive | zero | missing | invalid
    value: Optional[float]
    reason: str = ""    # invalid 时的精确原因
    label: str = ""


@dataclass
class QuantityScan:
    """一次字段语义扫描的完整（有界）事实。"""

    field: str
    unit: str
    entries: List[QuantityEntry] = field(default_factory=list)
    positives: List[QuantityEntry] = field(default_factory=list)
    zeros: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    invalid: List[Tuple[str, str]] = field(default_factory=list)
    total: float = 0.0
    max_value: float = 0.0
    min_positive: float = 0.0

    @property
    def valid_units(self) -> int:
        return len(self.positives)

    @property
    def excluded_zero(self) -> int:
        return len(self.zeros)

    def counts(self) -> Dict[str, int]:
        return {
            "total_units": len(self.entries),
            "positive": len(self.positives),
            "zero": len(self.zeros),
            "missing": len(self.missing),
            "invalid": len(self.invalid),
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "unit": self.unit,
            "counts": self.counts(),
            "total": _round(self.total),
            "max_value": _round(self.max_value),
            "min_positive": _round(self.min_positive),
            "zero_ids": self.zeros[:MAX_OFFENDERS],
            "missing_ids": self.missing[:MAX_OFFENDERS],
            "invalid": [{"feature_id": fid, "reason": why}
                        for fid, why in self.invalid[:MAX_OFFENDERS]],
            "truncated": any(len(x) > MAX_OFFENDERS
                             for x in (self.zeros, self.missing, self.invalid)),
        }


def _round(v: float) -> float:
    return float(f"{v:.6g}") if math.isfinite(v) else v


def classify_value(raw: Any) -> Tuple[str, Optional[float], str]:
    """→ (kind, value, reason)。``bool`` 先于 ``int`` 判定（Python 里 bool 是 int）。"""
    if raw is None:
        return "missing", None, ""
    if isinstance(raw, bool):
        return "invalid", None, "boolean"
    if isinstance(raw, (int, float)):
        val = float(raw)
        if not math.isfinite(val):
            return "invalid", None, "nonfinite"
        if val < 0:
            return "invalid", None, "negative"
        if val == 0:
            return "zero", 0.0, ""
        return "positive", val, ""
    if isinstance(raw, str):
        return "invalid", None, "non_numeric"
    return "invalid", None, "non_numeric"


def _label_of(props: Dict[str, Any], fallback: str) -> str:
    for key in ("name", "NAME", "title", "label"):
        v = props.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:80]
    return fallback


def scan_quantities(
    features: List[Dict[str, Any]],
    *,
    field_name: str,
    unit: str,
    zero_policy: str,
    missing_policy: str,
    max_offenders: int = MAX_OFFENDERS,
) -> QuantityScan:
    """扫描一份已冻结 FeatureCollection 的数量字段。

    严格性：
      * 字段在**任何**要素上都不存在 → ``FIELD_NOT_FOUND``（不是"全缺失"）；
      * 负数/布尔/字符串/NaN → 一律 ``FIELD_NOT_NUMERIC``（附要素与原因），
        不接受"取绝对值/加常数"修正；
      * ``zero_policy=reject`` / ``missing_policy=reject`` 时对应类非空即拒绝；
      * 没有正值 → ``NO_VALID_QUANTITY``（全零是 ``ZERO_TOTAL``）。
    """
    scan = QuantityScan(field=field_name, unit=unit)
    field_seen = False
    invalid: List[Tuple[str, str]] = []

    for idx, feat in enumerate(features):
        props = feat.get("properties") if isinstance(feat, dict) else None
        props = props if isinstance(props, dict) else {}
        fid_raw = feat.get("id") if isinstance(feat, dict) else None
        if isinstance(fid_raw, (int, float)) and not isinstance(fid_raw, bool):
            # 与 geography 层的 id 裁决逐字一致：整数值去小数（123.0 → "123"）、
            # 非整数值原样（1.5 → "1.5"）。若这里不认 float，浮点 id 会落入
            # "#rowN" 占位：正值圆与 geography 单元对不上而被静默丢弃，却仍计入
            # total/max_value（共同系数 k），zero/missing_ids 也会出现幻影 id。
            fid = str(int(fid_raw)) if float(fid_raw).is_integer() else str(fid_raw)
        else:
            fid = str(fid_raw) if isinstance(fid_raw, (str, int)) and str(fid_raw) else ""
        if not fid:
            # 身份由 geography 层统一裁决；这里用序号占位以便诊断可定位。
            fid = f"#row{idx}"
        label = _label_of(props, fid)
        if field_name in props:
            field_seen = True
        kind, value, reason = classify_value(props.get(field_name))
        entry = QuantityEntry(feature_id=fid, kind=kind, value=value,
                              reason=reason, label=label)
        scan.entries.append(entry)
        if kind == "positive":
            scan.positives.append(entry)
            scan.total += float(value)
            scan.max_value = max(scan.max_value, float(value))
            scan.min_positive = (float(value) if scan.min_positive == 0.0
                                 else min(scan.min_positive, float(value)))
        elif kind == "zero":
            scan.zeros.append(fid)
        elif kind == "missing":
            scan.missing.append(fid)
        else:
            scan.invalid.append((fid, reason))
            invalid.append((fid, reason))

    if not field_seen:
        raise AuthoringError(
            "FIELD_NOT_FOUND",
            f"源中没有任何要素包含字段 {field_name!r}",
            field="quantity.field",
            correction_hint="先 inspect 取值域与字段名（大小写/空格敏感）。")

    if invalid:
        reasons = sorted({why for _fid, why in invalid})
        raise AuthoringError(
            "FIELD_NOT_NUMERIC",
            f"字段 {field_name!r} 有 {len(invalid)} 个非法值（{', '.join(reasons)}）——"
            "不取绝对值、不加常数、不静默丢弃",
            field="quantity.field",
            details={
                "invalid_count": len(invalid),
                "offenders": [{"feature_id": fid, "reason": why}
                              for fid, why in invalid[:max_offenders]],
                "reasons": reasons,
            },
            correction_hint="先修数据，或用 scope.exclude_ids 逐项排除并写理由；"
                            "排除后总量与覆盖范围必须在发布说明中如实披露。")

    if missing_policy == "reject" and scan.missing:
        raise AuthoringError(
            "MISSING_QUANTITY",
            f"字段 {field_name!r} 在 {len(scan.missing)} 个区域缺失，策略为 reject",
            field="missing_policy",
            details={"missing_ids": scan.missing[:max_offenders]},
            correction_hint="改用 missing_policy=list_only（缺失区域单列、不画圆），"
                            "或先补齐数据。")
    if zero_policy == "reject" and scan.zeros:
        raise AuthoringError(
            "ZERO_QUANTITY",
            f"字段 {field_name!r} 有 {len(scan.zeros)} 个零值区域，策略为 reject",
            field="zero_policy",
            details={"zero_ids": scan.zeros[:max_offenders]},
            correction_hint="改用 zero_policy=list_only（零值保留身份与 0 说明），"
                            "或确认这些区域应被排除并写理由。")

    if not scan.positives:
        if scan.zeros and not scan.missing and not scan.invalid:
            raise AuthoringError(
                "ZERO_TOTAL",
                f"字段 {field_name!r} 全部为 0 —— 没有可计算的面积比例；原地图保留",
                field="quantity.field",
                correction_hint="换一个有正值总量的字段，或先修数据。")
        raise AuthoringError(
            "NO_VALID_QUANTITY",
            f"字段 {field_name!r} 没有可用正值（缺失 {len(scan.missing)} / 零 "
            f"{len(scan.zeros)}）—— 不能生成面积 cartogram",
            field="quantity.field",
            correction_hint="确认字段与总体（是否取错年份/子集），再重试。")
    return scan


@dataclass(frozen=True)
class AreaScale:
    """共同面积系数与派生半径。``area = π * k * value``。"""

    coefficient: float          # k
    max_radius: float
    min_radius: float
    max_value: float
    total_value: float

    def radius(self, value: float) -> float:
        return math.sqrt(self.coefficient * float(value))

    def area(self, value: float) -> float:
        return math.pi * self.coefficient * float(value)

    def as_receipt(self) -> Dict[str, Any]:
        return {
            "coefficient": _round(self.coefficient),
            "max_radius": _round(self.max_radius),
            "min_radius": _round(self.min_radius),
            "max_value": _round(self.max_value),
            "total_value": _round(self.total_value),
            "formula": "radius = sqrt(k * value); area = pi * k * value",
            "shared_coefficient": True,
            "per_circle_clamp": False,
        }


def build_area_scale(scan: QuantityScan, *, max_radius: float) -> AreaScale:
    """由最大值确定共同系数；极端跨度无法可靠表达时 typed 拒绝。"""
    if not scan.positives or scan.max_value <= 0:
        raise AuthoringError("NO_VALID_QUANTITY", "没有正值，无法建立面积比例")
    k = (float(max_radius) ** 2) / float(scan.max_value)
    min_r = math.sqrt(k * float(scan.min_positive))
    if not math.isfinite(k) or k <= 0:
        raise AuthoringError(
            "EXTREME_VALUE_RANGE",
            f"面积系数不可用（k={k!r}）—— 数量跨度或数值下溢超出可靠范围",
            field="area_scale.max_radius",
            correction_hint="换用同量纲的更稳定单位（如千人/万元），或合并过小单元。")
    if min_r < MIN_VISIBLE_RADIUS:
        ratio = scan.min_positive / scan.max_value
        raise AuthoringError(
            "EXTREME_VALUE_RANGE",
            f"数量跨度 {ratio:.3e}（最小 {scan.min_positive:.6g} / 最大 "
            f"{scan.max_value:.6g}）使最小圆半径 {min_r:.3e} < 可见阈值 "
            f"{MIN_VISIBLE_RADIUS}；逐圆放大/clamp 会破坏面积比例",
            field="quantity.field",
            details={"min_positive": scan.min_positive,
                     "max_value": scan.max_value, "min_radius": min_r},
            correction_hint="换单位/量纲或合并极小单元后重试；不接受最小可见圆伪装比例。")
    return AreaScale(
        coefficient=k, max_radius=float(max_radius), min_radius=float(min_r),
        max_value=float(scan.max_value), total_value=float(scan.total))


def reference_circles(scale: AreaScale, *, count: int = 3) -> List[Dict[str, Any]]:
    """图例参考圆（最大值、1/4、1/16 …——每级半径减半，便于读者核对面积）。"""
    refs: List[Dict[str, Any]] = []
    value = scale.max_value
    for _ in range(max(1, count)):
        if value <= 0:
            break
        refs.append({"value": _round(value), "radius": _round(scale.radius(value))})
        value = value / 4.0
    return refs
