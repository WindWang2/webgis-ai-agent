"""量纲义务（GC-05）—— 统计语义 → 单位/分母要求的静态对账。

单位维度封闭词表（时间/长度/面积/无量纲…）：行程时间（min/h）与距离
（m/km）维度互斥——网络距离分区汇总不得被行程秒数满足（Issue #2005
形态的静态对账面）。本模块只做**声明性对账**；执行期权威仍在
families unit_contract + scientific_preconditions + 工具 apply_contract。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.execution_compiler.contracts import (
    ObligationSemantics,
    UnitObligation,
    UnitObligationVerdict,
)
from app.services.gis_harness.requirement_ir.contracts import (
    GISIntentSpec,
)

#: 单位 → 量纲维度（封闭；与 families UNIT_VOCABULARY 对齐 + 派生单位）。
UNIT_DIMENSIONS: Dict[str, str] = {
    # 长度
    "m": "length",
    "km": "length",
    # 时间
    "s": "time",
    "min": "time",
    "h": "time",
    # 面积（密度分母派生单位）
    "m2": "area",
    "km2": "area",
    # 无量纲
    "unitless": "dimensionless",
    "percent": "dimensionless",
    "ratio": "dimensionless",
}

#: 统计词 → (量纲语义, 期望单位, 分母类型)。
_STATISTIC_SEMANTICS: Dict[str, Tuple[ObligationSemantics, str, str]] = {
    "count": ("count", "unitless", ""),
    "sum": ("count", "unitless", ""),
    "mean": ("none", "unitless", ""),
    "median": ("none", "unitless", ""),
    "min": ("none", "unitless", ""),
    "max": ("none", "unitless", ""),
    "density": ("density", "unitless", "area"),
    "share": ("share", "percent", "total"),
    "ratio": ("rate", "ratio", "field"),
    "rate": ("rate", "ratio", "field"),
    "index": ("index", "unitless", ""),
    "none": ("none", "unitless", ""),
}

#: 归一化口径 → 语义强化（statistics.normalization 优先于 measure 词面）。
_NORMALIZATION_SEMANTICS: Dict[str, Tuple[ObligationSemantics, str, str]] = {
    "per_area": ("per_area", "unitless", "area"),
    "per_capita": ("per_capita", "ratio", "field"),
}

#: 表达 kind → 量纲语义（expression_candidates 的 semantics 来源）。
EXPRESSION_SEMANTICS: Dict[str, ObligationSemantics] = {
    "point_map": "count",
    "admin_choropleth": "count",
    "area_density": "per_area",
    "per_capita_choropleth": "per_capita",
    "grid_density": "density",
    "heatmap": "density",
    "service_area_outline": "duration",
    "network_distance_summary": "distance",
    "exposure_inventory": "count",
    "change_map": "per_area",
}

#: 分母可用性判定输入的 data_facts 形状（role → facts）。
#: facts: {"feature_count": int, "geometry_types": [...], "fields": [...]}。


def statistic_semantics(
    statistic: str, normalization: str = "none",
) -> Tuple[ObligationSemantics, str, str]:
    """统计词 + 归一化口径 → (语义, 期望单位, 分母类型)。

    归一化口径（用户显式「每万人/每平方公里」）优先——measure 词面
    「数量」+ 口径「每万人」的合成语义是 per-capita 率，不是计数。
    """
    if normalization in _NORMALIZATION_SEMANTICS:
        return _NORMALIZATION_SEMANTICS[normalization]
    return _STATISTIC_SEMANTICS.get(
        statistic or "none", _STATISTIC_SEMANTICS["none"])


def build_unit_obligations(
    spec: GISIntentSpec,
    *,
    data_facts: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[UnitObligation]:
    """IR measures/statistics → 单位义务清单（纯函数）。

    分母裁决（fail-closed）：

    - per_capita / rate / ratio 语义：需要 ``denominator`` 角色数据事实
      （data_facts["denominator"] 或 role 绑定事实）在场才算 satisfied；
      缺席 → ``missing_denominator``（只给计数 + 披露，绝不伪装率）；
    - density / per_area：几何自足（面积由 boundary 角色几何计算）——
      boundary 事实在场即 satisfied，缺席 → not_applicable + 披露；
    - count：无分母义务。
    """
    facts = data_facts or {}
    obligations: List[UnitObligation] = []
    normalization = str(spec.statistics.normalization or "none")
    for measure in spec.measures[:4]:
        semantics, unit, denom_kind = statistic_semantics(
            str(measure.statistic or "none"), normalization)
        verdict, detail = _adjudicate_denominator(
            semantics, denom_kind, facts)
        obligations.append(UnitObligation(
            measure_id=measure.id,
            statistic=str(measure.statistic or "none"),
            expected_semantics=semantics,
            expected_unit=unit,
            denominator_kind=denom_kind,
            verdict=verdict,
            detail=detail[:200],
        ))
    # 无 measure 但有归一化口径（话语显式「人均」）→ 义务仍然成立
    if not obligations and normalization in _NORMALIZATION_SEMANTICS:
        semantics, unit, denom_kind = _NORMALIZATION_SEMANTICS[normalization]
        verdict, detail = _adjudicate_denominator(
            semantics, denom_kind, facts)
        obligations.append(UnitObligation(
            measure_id="m0",
            statistic="none",
            expected_semantics=semantics,
            expected_unit=unit,
            denominator_kind=denom_kind,
            verdict=verdict,
            detail=detail[:200],
        ))
    return obligations[:8]


def _adjudicate_denominator(
    semantics: ObligationSemantics, denom_kind: str,
    facts: Dict[str, Dict[str, Any]],
) -> Tuple[UnitObligationVerdict, str]:
    """分母在证裁决（closed verdict；数据事实缺席 = 披露而非虚构）。"""
    if semantics in ("per_capita", "rate"):
        denom = facts.get("denominator") or {}
        if denom:
            return ("satisfied",
                    "分母角色数据事实在场（per-capita 率可计算）")
        return ("missing_denominator",
                "率/人均语义但 denominator 角色数据事实缺席——只输出"
                "原始计数并披露，不得伪装率")
    if semantics in ("density", "per_area"):
        boundary = facts.get("boundary") or {}
        if boundary:
            return ("satisfied", "面积分母几何自足（boundary 在证）")
        return ("not_applicable",
                "密度语义；boundary 角色数据事实未注入——单位义务按"
                "未知处理（执行期 family 单位合同权威裁决）")
    return ("satisfied", "")


def unit_dimension_of(unit: str) -> str:
    """单位 → 量纲维度（词表外 = "unknown"，不虚构）。"""
    return UNIT_DIMENSIONS.get(str(unit or "").strip().lower(), "unknown")


def dimensions_compatible(expected_unit: str, actual_unit: str) -> bool:
    """两个单位量纲是否一致（秒≠米、米≠千分——#2005 形态静态守卫）。"""
    d_expected = unit_dimension_of(expected_unit)
    d_actual = unit_dimension_of(actual_unit)
    if "unknown" in (d_expected, d_actual):
        # 词表外单位：无法裁决 = 不宣称兼容（fail-closed 披露面）。
        return False
    return d_expected == d_actual


__all__ = [
    "UNIT_DIMENSIONS",
    "EXPRESSION_SEMANTICS",
    "statistic_semantics",
    "build_unit_obligations",
    "unit_dimension_of",
    "dimensions_compatible",
]
