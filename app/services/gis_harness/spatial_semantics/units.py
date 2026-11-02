"""空间语义单位解析与换算（GOAL 01 · M2/M3）。

职责（纯函数、零 IO、确定性）：

- ``parse_distance_expression``：自然语言距离 → (值, 单位, 米值, 换算链)
  —— intent 层距离至今是原始字符串（intent_semantic.py:991-993
  SlotConstraint value="500米"），这里补上归一（BASELINE G10）；
- ``convert_area`` / ``AREA_UNIT_FACTORS``：m²/km²/公顷 换算（display 名
  引用 measurement.CANONICAL_UNITS，不建第二注册表）；
- ``compose_density_unit``：分子 × 分母 → 图例口径串（"个/km²"）；
- ``convert_density``：分母单位换算下密度值换算（per_m² → per_km² ×1e6）。

纪律：词表外单位 → ``ValueError``（fail-closed），绝不猜（决策 D2/D4）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from app.services.gis_harness.spatial_semantics.vocabulary import (
    AREA_UNIT_VOCABULARY,
    DISTANCE_UNIT_VOCABULARY,
)

#: 距离单位 → 米（封闭；解析词表之外的输入直接拒绝）。
DISTANCE_UNIT_TO_M: Dict[str, float] = {"m": 1.0, "km": 1000.0}

#: 面积单位 → m²（display 名见 measurement.CANONICAL_UNITS 同名条目）。
AREA_UNIT_FACTORS: Dict[str, float] = {"m2": 1.0, "km2": 1e6, "ha": 1e4}

#: 面积单位图例显示（与 measurement.CANONICAL_UNITS display 同源同值；
#: 单独成表只因 measurement 的键是 square_kilometers 全名族，本模块口径
#: 是换算短名族 —— 值必须与 legend_unit_display 一致，测试锁定）。
AREA_UNIT_DISPLAY: Dict[str, str] = {"m2": "m²", "km2": "km²", "ha": "公顷"}

# 自然语言距离模式（中英；"500米"/"500 m"/"0.5km"/"0.5 公里"/"1千米"）。
_DISTANCE_RE = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m|米|km|公里|千米)\s*$",
    re.IGNORECASE,
)
_UNIT_ALIASES: Dict[str, str] = {
    "m": "m", "米": "m", "km": "km", "公里": "km", "千米": "km",
}


def parse_distance_expression(
    text: str, *, default_unit: Optional[str] = None,
) -> Tuple[float, str, float, List[str]]:
    """距离表达 → (值, 单位, 米值, 换算链)。

    ``default_unit``：无单位裸数值时的显式缺省（调用方必须有意给出，
    如工具契约 BufferAnalysisArgs.unit="m"——这层显式参数不是猜测）；
    未给且输入无单位 → ValueError（不猜米）。
    词表外单位 → ValueError。链记录形如 "0.5km→500m(factor=1000)"。
    """
    raw = str(text or "").strip()
    m = _DISTANCE_RE.match(raw)
    if not m:
        # 裸数值 + 显式缺省单位（调用方契约层声明，非本模块猜测）
        if re.match(r"^\d+(?:\.\d+)?$", raw) and default_unit in DISTANCE_UNIT_TO_M:
            value = float(raw)
            unit = default_unit
        else:
            raise ValueError(
                f"距离表达不可解析或单位不在词表 {list(DISTANCE_UNIT_TO_M)}: {text!r}"
            )
    else:
        value = float(m.group("value"))
        unit = _UNIT_ALIASES[m.group("unit").lower()]
    value_m = value * DISTANCE_UNIT_TO_M[unit]
    chain: List[str] = []
    if unit != "m":
        chain.append(f"{value}{unit}→{value_m}m(factor={DISTANCE_UNIT_TO_M[unit]:g})")
    return value, unit, value_m, chain


def normalize_distance_params(
    distance: float, unit: str,
) -> Tuple[float, List[str]]:
    """工具参数（值, 单位）→ (米值, 换算链)；单位越词表即拒。"""
    u = str(unit or "").strip().lower()
    if u not in DISTANCE_UNIT_TO_M:
        raise ValueError(
            f"距离单位 {unit!r} 不在词表 {DISTANCE_UNIT_VOCABULARY} —— 拒绝执行而非猜米"
        )
    value_m = float(distance) * DISTANCE_UNIT_TO_M[u]
    chain: List[str] = []
    if u != "m":
        chain.append(f"{distance}{u}→{value_m}m(factor={DISTANCE_UNIT_TO_M[u]:g})")
    return value_m, chain


def convert_area(value: float, from_unit: str, to_unit: str) -> float:
    """面积换算（词表外单位 → ValueError）。"""
    if from_unit not in AREA_UNIT_FACTORS or to_unit not in AREA_UNIT_FACTORS:
        raise ValueError(
            f"面积单位越词表: {from_unit!r}→{to_unit!r}"
            f"（词表 {AREA_UNIT_VOCABULARY}）"
        )
    return float(value) * AREA_UNIT_FACTORS[from_unit] / AREA_UNIT_FACTORS[to_unit]


def area_conversion_chain(
    value: float, from_unit: str, to_unit: str,
) -> List[str]:
    """面积换算链记录（值不变时空链）。"""
    if from_unit == to_unit:
        return []
    converted = convert_area(value, from_unit, to_unit)
    factor = AREA_UNIT_FACTORS[from_unit] / AREA_UNIT_FACTORS[to_unit]
    return [f"{value}{AREA_UNIT_DISPLAY.get(from_unit, from_unit)}"
            f"→{converted}{AREA_UNIT_DISPLAY.get(to_unit, to_unit)}"
            f"(factor={factor:g})"]


def compose_density_unit(
    numerator_display: str, denominator_unit: str,
) -> str:
    """分子显示名 × 分母单位 → 图例口径串（如 "个"/"km2" → "个/km²"）。"""
    denom = AREA_UNIT_DISPLAY.get(denominator_unit, denominator_unit)
    if denominator_unit == "population":
        return f"{numerator_display}/万人"
    return f"{numerator_display}/{denom}"


def convert_density(
    value: float, from_denom_unit: str, to_denom_unit: str,
) -> float:
    """面积分母单位换算下的密度值换算（per_m² → per_km² ×1e6）。"""
    if from_denom_unit == to_denom_unit:
        return float(value)
    if from_denom_unit == "population" or to_denom_unit == "population":
        raise ValueError("人口分母密度不做单位换算（口径切换需重新聚合）")
    # per_from → per_to：分母放大 N 倍 ⇒ 密度值 ÷ N
    n = AREA_UNIT_FACTORS[from_denom_unit] / AREA_UNIT_FACTORS[to_denom_unit]
    return float(value) / n


__all__ = [
    "DISTANCE_UNIT_TO_M",
    "AREA_UNIT_FACTORS",
    "AREA_UNIT_DISPLAY",
    "parse_distance_expression",
    "normalize_distance_params",
    "convert_area",
    "area_conversion_chain",
    "compose_density_unit",
    "convert_density",
]
