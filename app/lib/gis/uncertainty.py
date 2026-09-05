"""Scientific Uncertainty Framework —— 类型化不确定性输出契约（VNext §25）。

原则：

- 不把所有不确定性压成一个标量：不同算法暴露不同**类型**的证据
  （标准误/方差/区间/置换 p 值/MC 分布摘要/敏感性包络/CV 残差）；
- 全部有界（列表截断、小数位收敛）—— 证据块不是数据搬运工；完整
  不确定面（如克里金方差格网）走既有 artifact/ref 通道，这里只挂
  摘要 + ref；
- ``UNCERTAINTY_TYPE_VOCABULARY`` 是 descriptor ``uncertainty_outputs``
  的封闭词表（validate() 强制）—— 声明了就必须真的由实现产出。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

UNCERTAINTY_TYPE_VOCABULARY = frozenset({
    "scalar_uncertainty",        # 单值 ± 标准误/区间
    "field_uncertainty",         # 逐要素不确定性（含 ref 摘要）
    "raster_uncertainty",        # 不确定面（ref + 统计摘要）
    "statistical_significance",  # 检验统计量 + p 值（含多重校正）
    "sensitivity_envelope",      # 权重/参数扰动下的结论稳定性
    "validation_metrics",        # 交叉验证 RMSE/MAE/bias/R²
    "monte_carlo_summary",       # MC 分布摘要（分位数 + P(约束)）
})

_BoundedStr = str


def _r6(value: Optional[float]) -> Optional[float]:
    """证据块数值统一 6 位小数收敛（确定性、有界体积）。"""
    if value is None:
        return None
    return round(float(value), 6)


class UncertaintyMeasure(BaseModel):
    """一次度量：值 + 方法 + （可选）区间/分位。"""

    measure: Literal[
        "standard_error", "variance", "confidence_interval",
        "prediction_interval", "quantile", "value",
    ]
    value: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None
    level: Optional[float] = None          # 区间置信水平（0-1）
    method: str = ""                       # 如 "kriging variance", "permutation 999"
    unit: str = ""                         # 空 = 与主输出同单位

    @field_validator("level")
    @classmethod
    def _level_range(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not 0.0 < float(v) < 1.0:
            raise ValueError("level must be in (0, 1)")
        return float(v)


class ScalarUncertainty(BaseModel):
    """单值不确定性（如全局 Moran's I 的置换 p 值、Sen 斜率的置信区间）。"""

    target: str                            # 度量对象名（"morans_i" / "sen_slope"）
    uncertainty_type: Literal["scalar_uncertainty"] = "scalar_uncertainty"
    measures: List[UncertaintyMeasure] = Field(default_factory=list, max_length=8)

    def to_evidence(self) -> Dict[str, Any]:
        return self.model_dump()


class FieldUncertainty(BaseModel):
    """逐要素不确定性摘要（全量数据走 ref；这里只有有界摘要）。"""

    target: str                            # 字段名/角色
    uncertainty_type: Literal["field_uncertainty"] = "field_uncertainty"
    field_name: str = ""
    data_ref: str = ""                     # 全量逐要素表的 ref（有则挂）
    summary: List[UncertaintyMeasure] = Field(default_factory=list, max_length=8)
    sample_count: Optional[int] = None

    def to_evidence(self) -> Dict[str, Any]:
        return self.model_dump()


class RasterUncertainty(BaseModel):
    """不确定面摘要（克里金方差等）：ref + min/max/mean，不搬格网。"""

    target: str
    uncertainty_type: Literal["raster_uncertainty"] = "raster_uncertainty"
    data_ref: str = ""
    interpretation: str = ""               # "kriging prediction variance (m²)"
    summary: List[UncertaintyMeasure] = Field(default_factory=list, max_length=8)

    def to_evidence(self) -> Dict[str, Any]:
        return self.model_dump()


class StatisticalSignificance(BaseModel):
    """显著性检验证据：统计量 + p 值 + 推断方法 + 多重检验处理。"""

    target: str
    uncertainty_type: Literal["statistical_significance"] = "statistical_significance"
    statistic_name: str = ""               # "Gi*" / "Moran's I" / "Mann-Kendall S"
    statistic_value: Optional[float] = None
    p_value: Optional[float] = None
    method: Literal["permutation", "analytic_normal", "exact", "bootstrap", ""] = ""
    permutations: Optional[int] = None
    multiple_testing: str = ""             # "BH-FDR" / ""（未校正）
    alternative: str = ""                  # "two-sided" / "greater" / ...

    @field_validator("p_value")
    @classmethod
    def _p_range(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not 0.0 <= float(v) <= 1.0:
            raise ValueError("p_value must be in [0, 1]")
        return float(v)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "uncertainty_type": self.uncertainty_type,
            "statistic_name": self.statistic_name,
            "statistic_value": _r6(self.statistic_value),
            "p_value": self.p_value,
            "method": self.method,
            "permutations": self.permutations,
            "multiple_testing": self.multiple_testing,
            "alternative": self.alternative,
        }


class SensitivityEnvelope(BaseModel):
    """参数/权重扰动下的结论稳定性（决策与 MCDA 场景）。"""

    target: str
    uncertainty_type: Literal["sensitivity_envelope"] = "sensitivity_envelope"
    perturbation_scheme: str = ""          # "weight ±20% dirichlet, 500 draws"
    rank_stability: Optional[float] = None  # 首选项保持率 [0,1]
    tipping_points: List[str] = Field(default_factory=list, max_length=8)
    notes: str = ""

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "uncertainty_type": self.uncertainty_type,
            "perturbation_scheme": self.perturbation_scheme,
            "rank_stability": _r6(self.rank_stability),
            "tipping_points": list(self.tipping_points),
            "notes": self.notes,
        }


class ValidationMetrics(BaseModel):
    """交叉验证指标（插值等方法）：LOOCV / k-fold。"""

    target: str
    uncertainty_type: Literal["validation_metrics"] = "validation_metrics"
    method: Literal["loocv", "k_fold", "holdout", "in_sample"] = "loocv"
    rmse: Optional[float] = None
    mae: Optional[float] = None
    bias: Optional[float] = None
    r_squared: Optional[float] = None
    folds: Optional[int] = None
    sample_count: Optional[int] = None
    unit: str = ""

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "uncertainty_type": self.uncertainty_type,
            "method": self.method,
            "rmse": _r6(self.rmse),
            "mae": _r6(self.mae),
            "bias": _r6(self.bias),
            "r_squared": _r6(self.r_squared),
            "folds": self.folds,
            "sample_count": self.sample_count,
            "unit": self.unit,
        }


class MonteCarloSummary(BaseModel):
    """蒙特卡洛分布摘要：分位数 + 概率陈述（不搬全分布）。"""

    target: str
    uncertainty_type: Literal["monte_carlo_summary"] = "monte_carlo_summary"
    draws: Optional[int] = None
    seed: Optional[int] = None
    quantiles: Dict[str, float] = Field(default_factory=dict)  # "p5"/"p50"/"p95"
    probability_statements: List[str] = Field(default_factory=list, max_length=8)
    data_ref: str = ""

    @field_validator("quantiles")
    @classmethod
    def _bounded_quantiles(cls, v: Dict[str, float]) -> Dict[str, float]:
        return {str(k): _r6(float(x)) for k, x in list(v.items())[:8]}

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "uncertainty_type": self.uncertainty_type,
            "draws": self.draws,
            "seed": self.seed,
            "quantiles": dict(self.quantiles),
            "probability_statements": list(self.probability_statements),
            "data_ref": self.data_ref,
        }


UncertaintyBlock = (
    ScalarUncertainty | FieldUncertainty | RasterUncertainty
    | StatisticalSignificance | SensitivityEnvelope | ValidationMetrics
    | MonteCarloSummary
)


def uncertainty_blocks_to_evidence(
    blocks: List[UncertaintyBlock], *, max_blocks: int = 6,
) -> List[Dict[str, Any]]:
    """有界证据化（≤6 块；重复声明同一 target 的后块丢弃）。"""
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for block in blocks[: max_blocks * 2]:
        key = f"{block.uncertainty_type}:{block.target}"
        if key in seen:
            continue
        seen.add(key)
        out.append(block.to_evidence())
        if len(out) >= max_blocks:
            break
    return out
