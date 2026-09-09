"""Uncertainty Artifact（science-v5 W6）—— 不确定性输出的统一契约。

定位（01-architecture.md D5 + 挑战 R0-#13）：

- 上层 :mod:`app.lib.gis.uncertainty` 是 descriptor/evidence 的**有界摘要**
  层（封闭词表）；本模块是逐目标 **artifact** 层——完整不确定面的结构化
  载体，工具输出经 ``metadata["uncertainty"]``（有界摘要）+ records 逐格
  字段消费它；
- **estimator 必填无默认**：``sgs_ensemble``（实现间离散度，后向变换域）
  vs ``kriging_variance`` / ``st_kriging_variance``（模型方差）是不同
  估计器——渲染方不得跨估计器混读模型不确定性；
- **模型不确定性 vs 数据质量显式分离**：``model_uncertainty`` 只描述
  预测分布的离散度；``data_quality`` 只来自真实样本密度/nodata 元数据，
  绝不合成、绝不与前者混合成单一数字；
- R<2 的 ensemble std 诚实为 None + 披露（不是 0——伪精确）；
- 有界性：``to_dict`` 数值 6 位收敛、披露列表有界——artifact 摘要不是
  数据搬运工，完整数组仍走 records/artifact 通道。

错误语义：全部 ``ValueError`` 子类（scientific_errors 词表）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from app.lib.gis.scientific_errors import DegenerateData

# estimator 封闭词表（跨估计器混读是渲染层事故源——封闭可校验）
UNCERTAINTY_ESTIMATOR_VOCABULARY = frozenset({
    "kriging_variance",       # 单变量/泛化克里金预测方差（高斯近似）
    "sgs_ensemble",           # 条件模拟实现间离散度（后向变换域）
    "st_kriging_variance",    # 时空克里金预测方差
    "cokriging_variance",     # 共克里金预测方差
})

# 区间构造语义（按 estimator 声明，渲染方据此解释 low/high）
_INTERVAL_SEMANTICS = {
    "kriging_variance": "gaussian_predictive",
    "st_kriging_variance": "gaussian_predictive",
    "cokriging_variance": "gaussian_predictive",
    "sgs_ensemble": "ensemble_quantiles",
}

_Z95 = 1.959963984540054
_Z80 = 1.2815515655446004

_DISCLOSURE_CAP = 16


def _r6(x: Any) -> Optional[float]:
    if x is None:
        return None
    v = float(x)
    return round(v, 6) if np.isfinite(v) else None


def _range6(a: np.ndarray) -> list:
    a = np.asarray(a, dtype=float)
    if a.size == 0:
        return [None, None]
    return [_r6(np.min(a)), _r6(np.max(a))]


@dataclass
class UncertaintyArtifact:
    """逐目标不确定性 artifact（完整数组 + 有界摘要）。"""

    estimator: str                          # 必填（无默认）——词表成员
    n_targets: int
    mean: np.ndarray                        # 中心预测（E-type 或克里金预测）
    std: Optional[np.ndarray]               # None = 诚实缺省（如 R<2 ensemble）
    q10: Optional[np.ndarray] = None
    q50: Optional[np.ndarray] = None
    q90: Optional[np.ndarray] = None
    interval_level: float = 0.95
    interval_low: Optional[np.ndarray] = None
    interval_high: Optional[np.ndarray] = None
    provenance: dict = field(default_factory=dict)
    calibration: Optional[dict] = None      # CV z-score 校准（缺 = 未评估）
    model_uncertainty: dict = field(default_factory=dict)   # 类型 + 定义
    data_quality: dict = field(default_factory=dict)        # 真实元数据，不合成
    disclosures: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.estimator not in UNCERTAINTY_ESTIMATOR_VOCABULARY:
            raise DegenerateData(
                f"estimator 必须是 {sorted(UNCERTAINTY_ESTIMATOR_VOCABULARY)} "
                f"之一，got {self.estimator!r}"
            )
        if len(self.mean) != self.n_targets:
            raise DegenerateData("mean 与 n_targets 不一致")
        if self.model_uncertainty.get("definition") is None:
            self.model_uncertainty.setdefault(
                "definition", _MODEL_DEFS.get(self.estimator, ""))
        self.model_uncertainty.setdefault("type", self.estimator)
        self.model_uncertainty["interval_semantics"] = \
            _INTERVAL_SEMANTICS[self.estimator]

    def to_dict(self) -> dict:
        """有界摘要（进 metadata["uncertainty"]；完整数组走 records）。"""
        out: dict[str, Any] = {
            "estimator": self.estimator,
            "interval_semantics": _INTERVAL_SEMANTICS[self.estimator],
            "n_targets": int(self.n_targets),
            "mean_range": _range6(self.mean),
            "std_available": self.std is not None,
        }
        if self.std is not None:
            out["std_range"] = _range6(self.std)
        for key, arr in (("q10", self.q10), ("q50", self.q50),
                         ("q90", self.q90), ("interval_low", self.interval_low),
                         ("interval_high", self.interval_high)):
            if arr is not None:
                out[f"{key}_range"] = _range6(arr)
        out["interval_level"] = round(float(self.interval_level), 3)
        out["model_uncertainty"] = dict(self.model_uncertainty)
        if self.data_quality:
            out["data_quality"] = dict(self.data_quality)
        if self.calibration:
            out["calibration"] = dict(self.calibration)
        if self.provenance:
            out["provenance"] = {
                k: self.provenance[k] for k in sorted(self.provenance)
            }
        if self.disclosures:
            out["disclosures"] = list(self.disclosures[:_DISCLOSURE_CAP])
        return out

    def to_renderer_metadata(self) -> dict:
        """渲染友好元数据：值语义 + 分位数断点建议（有界，确定性）。"""
        breaks: dict[str, list] = {}
        for key, arr in (("p50", self.q50 if self.q50 is not None else self.mean),
                         ("p90", self.q90), ("std", self.std)):
            if arr is None:
                continue
            a = np.asarray(arr, dtype=float)
            a = a[np.isfinite(a)]
            if a.size == 0:
                continue
            breaks[key] = [round(float(v), 6)
                           for v in np.quantile(a, [0.05, 0.5, 0.95])]
        return {
            "value_semantics": (
                f"uncertainty estimator = {self.estimator} "
                f"({self.model_uncertainty.get('definition', '')})"),
            "suggested_breaks": breaks,
            "separation": (
                "model_uncertainty = 预测分布离散度；data_quality = 采样/"
                "覆盖质量——两者不可混合解释"),
        }


_MODEL_DEFS = {
    "kriging_variance": "克里金方差的高斯预测区间（pred ± z·√var）",
    "st_kriging_variance": "时空克里金方差的高斯预测区间（pred ± z·√var）",
    "cokriging_variance": "共克里金方差的高斯预测区间（pred ± z·√var）",
    "sgs_ensemble": "条件模拟实现间分位数/离散度（后向变换域）",
}


def _check_arrays(n: int, *arrays: Optional[np.ndarray]) -> None:
    for a in arrays:
        if a is None:
            continue
        if len(a) != n:
            raise DegenerateData("不确定性数组长度与目标数不一致")


def data_quality_summary(
    n_samples: int,
    n_targets: int,
    *,
    nodata_fraction: Optional[float] = None,
    value_field: str = "",
    working_crs: str = "",
) -> dict:
    """数据质量块（只来自真实输入元数据；无值字段诚实缺省）。"""
    dq: dict[str, Any] = {
        "n_samples": int(n_samples),
        "n_targets": int(n_targets),
        "samples_per_target": _r6(n_samples / max(n_targets, 1)),
    }
    if nodata_fraction is not None:
        dq["nodata_fraction"] = _r6(nodata_fraction)
    if value_field:
        dq["value_field"] = str(value_field)[:64]
    if working_crs:
        dq["working_crs"] = str(working_crs)[:32]
    return dq


def from_variance(
    estimator: str,
    predictions: np.ndarray,
    variances: np.ndarray,
    *,
    provenance: Optional[dict] = None,
    calibration: Optional[dict] = None,
    data_quality: Optional[dict] = None,
    disclosures: Optional[list] = None,
) -> UncertaintyArtifact:
    """克里金族方差 → artifact（高斯预测近似，语义随 estimator 声明）。"""
    predictions = np.asarray(predictions, dtype=float)
    variances = np.asarray(variances, dtype=float)
    if predictions.ndim != 1 or variances.shape != predictions.shape:
        raise DegenerateData("预测/方差须为同形一维数组")
    if not np.isfinite(variances).all():
        raise DegenerateData("方差含非有限值")
    std = np.sqrt(np.maximum(variances, 0.0))
    disc = list(disclosures or [])
    disc.append(
        "interval = gaussian predictive approximation (pred ± z·√var); "
        "quantiles p10/p90 = pred ± 1.2816·σ (same approximation)")
    return UncertaintyArtifact(
        estimator=estimator,
        n_targets=int(len(predictions)),
        mean=predictions,
        std=std,
        q10=predictions - _Z80 * std,
        q50=predictions.copy(),
        q90=predictions + _Z80 * std,
        interval_level=0.95,
        interval_low=predictions - _Z95 * std,
        interval_high=predictions + _Z95 * std,
        provenance=dict(provenance or {}),
        calibration=calibration,
        model_uncertainty={"type": estimator},
        data_quality=dict(data_quality or {}),
        disclosures=disc,
    )


def from_kriging(
    predictions: np.ndarray,
    variances: np.ndarray,
    *,
    provenance: Optional[dict] = None,
    calibration: Optional[dict] = None,
    data_quality: Optional[dict] = None,
) -> UncertaintyArtifact:
    return from_variance(
        "kriging_variance", predictions, variances,
        provenance=provenance, calibration=calibration,
        data_quality=data_quality)


def from_sgs(
    ensemble,
    *,
    data_quality: Optional[dict] = None,
    provenance_extra: Optional[dict] = None,
) -> UncertaintyArtifact:
    """SGS ensemble → artifact（分位数区间，后向变换域）。"""
    disc = list(ensemble.disclosures)
    provenance = {
        "seed": int(ensemble.seed),
        "n_realizations": int(ensemble.n_realizations),
        "backend": str(getattr(ensemble, "backend", "numpy_reference")),
        "variogram": ensemble.variogram.params(),
        "transform": dict(ensemble.transform_info),
    }
    provenance.update(provenance_extra or {})
    std: Optional[np.ndarray]
    if ensemble.n_realizations >= 2:
        std = np.asarray(ensemble.std, dtype=float)
    else:
        std = None
        disc.append(
            "n_realizations < 2: ensemble std/quantile spread unavailable "
            "(honest absence, not zero)")
    return UncertaintyArtifact(
        estimator="sgs_ensemble",
        n_targets=int(len(ensemble.mean)),
        mean=np.asarray(ensemble.mean, dtype=float),
        std=std,
        q10=np.asarray(ensemble.p10, dtype=float),
        q50=np.asarray(ensemble.p50, dtype=float),
        q90=np.asarray(ensemble.p90, dtype=float),
        interval_level=0.8,
        interval_low=(np.asarray(ensemble.p10, dtype=float)
                      if std is not None else None),
        interval_high=(np.asarray(ensemble.p90, dtype=float)
                       if std is not None else None),
        provenance=provenance,
        calibration=None,
        model_uncertainty={"type": "sgs_ensemble"},
        data_quality=dict(data_quality or {}),
        disclosures=disc,
    )
