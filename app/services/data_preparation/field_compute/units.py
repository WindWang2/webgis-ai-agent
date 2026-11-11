"""单位换算与显示（G19 / WP05）—— 只消费 ``measurement.CANONICAL_UNITS``。

``scale_to_reference`` 是同维度参考单位倍率；换算 = ``v * from_scale / to_scale``。
``percent``(100) ↔ ``fraction``(1) 因此得到 ×100 / ÷100 的显式标尺变化，
而 ``square_meters``(1) ↔ ``square_kilometers``(1e6) 得到面积换算。

纪律：不同量纲**拒绝**；无倍率证据的单位（count/persons/index/none）同维换算
值不变但必须披露 ``scale_evidence: "none"``；绝不按字段名猜单位。
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from app.lib.gis.measurement import CANONICAL_UNITS, legend_unit_display

from app.services.data_preparation.field_compute.functions import (
    dimension_of, unit_scale,
)


class UnitConversionError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def convert_value(value: float, from_unit: str, to_unit: str
                  ) -> Tuple[float, Dict[str, Any]]:
    """显式同量纲换算 → (结果, 有界证据)。失败抛 ``UnitConversionError``。"""
    if from_unit not in CANONICAL_UNITS:
        raise UnitConversionError("UNIT_UNDECLARED", f"unknown from unit {from_unit!r}")
    if to_unit not in CANONICAL_UNITS:
        raise UnitConversionError("UNIT_UNDECLARED", f"unknown to unit {to_unit!r}")
    d1, d2 = dimension_of(from_unit), dimension_of(to_unit)
    if d1 != d2:
        raise UnitConversionError("UNIT_INCOMPATIBLE",
                                  f"{from_unit}({d1}) ≠ {to_unit}({d2})")
    s1, s2 = unit_scale(from_unit), unit_scale(to_unit)
    if s1 is None or s2 is None or s2 == 0 or s1 == 0:
        raise UnitConversionError("UNIT_UNDECLARED", "scale_to_reference 缺席")
    # ``scale_to_reference`` 对物理量是「1 单位 = scale 个参考单位」
    # （m²→km² = ×1/1e6）；对 RATIO 的 percent/fraction 则是**数值标尺**
    # （fraction=1、percent=100；0.5 → 50%），方向相反 —— 显式区分，
    # 不按字段名或单位名猜，也不把两种语义混成一套公式。
    factor = (float(s2) / float(s1)) if d1 == "ratio" else (float(s1) / float(s2))
    result = float(value) * factor
    evidence = {
        "from_unit": from_unit, "to_unit": to_unit,
        "dimension": d1, "factor": factor,
        "scale_semantics": "numeric_scale" if d1 == "ratio" else "physical_scale",
        "scale_evidence": "measurement.CANONICAL_UNITS",
    }
    return result, evidence


def unit_display(unit: str) -> str:
    """canonical unit → 图例显示串（未知名原样返回，不虚构）。"""
    return legend_unit_display(unit)


def describe(unit: str, status: str, dimension: str = "") -> Dict[str, Any]:
    """面向 UI/Pi 的有界单位披露（``undeclared`` 保持真实语义）。"""
    known = unit in CANONICAL_UNITS
    return {
        "unit": unit,
        "unit_status": status,
        "dimension": dimension or (dimension_of(unit) if known else ""),
        "display": unit_display(unit) if known else "",
        "declared": bool(known and status == "declared"),
    }


__all__ = ["UnitConversionError", "convert_value", "unit_display", "describe"]
