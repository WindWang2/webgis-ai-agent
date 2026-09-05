"""
Temporal Trend Engine.
Analyzes temporal time series trends using moving averages, Sen's slope / linear trend fitting,
and statistical anomaly detection (Z-score / IQR).

VNext（ADR-0099）加法扩展：非参数趋势检验族（Mann-Kendall / 季节
Mann-Kendall，tie 校正方差 + 连续性校正正态近似）与 CUSUM 均值变点
（固定种子 bootstrap 显著性）。既有函数（compute_sens_slope /
compute_linear_regression / analyze_trend 缺省路径）逐位不变；新方法经
``analyze_trend(method=...)`` 选择，显著性证据以 StatisticalSignificance
块（app.lib.gis.uncertainty）结构化附加。
"""

import math
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from pydantic import Field

from app.lib.gis.scientific_errors import InsufficientSamples, MissingRequiredField
from app.lib.gis.uncertainty import StatisticalSignificance
from app.services.temporal.models import TemporalTrendResult
from app.services.temporal.profiler import parse_value_to_instant

logger = logging.getLogger(__name__)

# #594: when the series carries parseable timestamps, Sen's slope / OLS are
# fit against REAL time instead of slice indices — unequal slice spacing
# (cloud gaps, missing scenes) previously returned per-step slopes with no
# physical unit (a 2015/2016/2024 series came back 4.48x inflated). x is
# normalized to years so the reported slope is directly per-year.
_SECONDS_PER_YEAR = 365.25 * 86400.0


def _timestamps_to_year_axis(timestamps: List[str]) -> Optional[List[float]]:
    """Year-normalized x-axis from timestamp strings, or None when unusable.

    Returns [0.0, (t1-t0)/yr, ...] relative to the first point. If ANY
    timestamp fails to parse, the whole axis is rejected (a mixed
    index/real-time axis is meaningless) and the caller falls back to the
    per-step index axis.
    """
    epochs = []
    for t in timestamps:
        try:
            inst = parse_value_to_instant(t)
        except Exception:
            return None
        if inst is None:
            return None
        epochs.append(inst[0].epoch_seconds)
    if len(epochs) < 2:
        return None
    t0 = epochs[0]
    return [(e - t0) / _SECONDS_PER_YEAR for e in epochs]


class TemporalTrendEngine:
    """
    Provides trend analysis algorithms (moving averages, Sen's slope/OLS linear regression, anomaly detection).
    """

    @staticmethod
    def compute_moving_average(values: List[float], window_size: int = 3) -> List[Optional[float]]:
        """
        Computes simple moving average over a numeric sequence.
        Returns a list of same length with None for initial unpopulated window positions if window centered or leading.
        """
        if not values or window_size <= 0:
            return []

        n = len(values)
        result: List[Optional[float]] = [None] * n

        if window_size > n:
            window_size = n

        for i in range(n):
            if i < window_size - 1:
                # Partial window or None
                sub = values[0 : i + 1]
                result[i] = round(sum(sub) / len(sub), 4)
            else:
                sub = values[i - window_size + 1 : i + 1]
                result[i] = round(sum(sub) / len(sub), 4)

        return result

    # Sen's slope is O(n²) in the number of pairwise slopes (n(n-1)/2). With
    # 50k records that is ~1.25e9 Python slopes and multi-GB of intermediate
    # lists — unbounded. Above this point the series is deterministically
    # subsampled (evenly spaced) before the vectorized pair computation.
    _SENS_SLOPE_MAX_N = 1024

    @staticmethod
    def compute_sens_slope(
        values: List[float], x_axis: Optional[List[float]] = None
    ) -> float:
        """
        Computes Sen's Slope (median of slopes between all pair of points (i, j) with i < j).

        ``x_axis``, when supplied, carries the x coordinate for each input
        value (index-aligned, i.e. same length as ``values``; #594) — the
        pairwise slope is then (y_j - y_i) / (x_j - x_i), the true rate per
        x unit (e.g. per year). Without it, x is the index axis and slopes
        are per step.

        Vectorized over all pairs for n ≤ ``_SENS_SLOPE_MAX_N``; larger series
        are evenly subsampled to that cap (with a warning) so the computation
        stays bounded instead of materializing ~n²/2 Python floats.

        #452: non-finite (NaN/Inf) points are dropped first — np.median over
        NaN-containing pairwise slopes returned NaN.
        """
        import numpy as np
        arr = np.asarray(values, dtype=float)
        finite = np.isfinite(arr)
        arr = arr[finite]
        n = int(arr.size)
        if n < 2:
            return 0.0

        if x_axis is not None and len(x_axis) == len(values):
            # Index-aligned real-time axis; drop the coordinate of dropped
            # points so surviving gaps keep their true spacing.
            x_arr = np.asarray(x_axis, dtype=float)[finite]
        else:
            x_arr = np.arange(n)

        if n > TemporalTrendEngine._SENS_SLOPE_MAX_N:
            logger.warning(
                "Sen's slope input truncated: %d points > max %d; "
                "subsampling to evenly spaced points (approximation).",
                n, TemporalTrendEngine._SENS_SLOPE_MAX_N,
            )
            idx = np.unique(np.linspace(0, n - 1, TemporalTrendEngine._SENS_SLOPE_MAX_N).astype(int))
            arr = arr[idx]
            # x coordinates stay the ORIGINAL time indices (or real-time
            # offsets) — slopes are per unit, not per subsampled position.
            x_arr = x_arr[idx]
            n = arr.size

        if n < 2:
            return 0.0

        i, j = np.triu_indices(n, k=1)
        if i.size == 0:
            return 0.0
        slopes = (arr[j] - arr[i]) / (x_arr[j] - x_arr[i])
        # #594: coincident x (duplicate timestamps) yields inf/NaN pairwise
        # slopes — drop them rather than poisoning the median.
        slopes = slopes[np.isfinite(slopes)]
        if slopes.size == 0:
            return 0.0
        return float(np.median(slopes))

    @staticmethod
    def compute_linear_regression(
        values: List[float], x_axis: Optional[List[float]] = None
    ) -> Tuple[float, float, float]:
        """
        Computes OLS linear regression (slope, intercept, r_squared) for
        x = 0..n-1 — or over the supplied ``x_axis`` coordinates (index-aligned
        with ``values``; #594) so slope/intercept are expressed per x unit
        (e.g. per year) instead of per step.

        #452: non-finite (NaN/Inf) points are dropped first — with NaN in the
        sums the slope came back NaN and the [0,1] clamp silently turned the
        NaN r_squared into a spurious perfect fit of 1.0.
        """
        finite = [float(v) for v in values if math.isfinite(v)]
        n = len(finite)
        if n < 2:
            return 0.0, finite[0] if finite else 0.0, 0.0

        if x_axis is not None and len(x_axis) == len(values):
            # Keep the coordinate of each surviving point; dropping a point
            # must not change the others' real-time spacing.
            xs = [float(x_axis[i]) for i in range(len(values)) if math.isfinite(values[i])]
        else:
            xs = [float(i) for i in range(n)]
        y = finite

        sum_x = sum(xs)
        sum_y = sum(y)
        sum_xy = sum(xs[i] * y[i] for i in range(n))
        sum_xx = sum(xs[i] ** 2 for i in range(n))

        denom = n * sum_xx - sum_x ** 2
        if denom == 0:
            return 0.0, sum_y / n, 0.0

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        # R-squared
        y_mean = sum_y / n
        ss_tot = sum((y[i] - y_mean) ** 2 for i in range(n))
        ss_res = sum((y[i] - (slope * xs[i] + intercept)) ** 2 for i in range(n))

        r_squared = (1.0 - (ss_res / ss_tot)) if ss_tot > 0 else 1.0
        if not math.isfinite(r_squared):  # NaN must not clamp to 1.0
            r_squared = 0.0
        r_squared = max(0.0, min(1.0, r_squared))

        return round(slope, 6), round(intercept, 4), round(r_squared, 4)

    @staticmethod
    def detect_anomalies(
        values: List[float],
        timestamps: Optional[List[str]] = None,
        z_threshold: float = 2.0,
    ) -> List[Dict[str, Any]]:
        """
        Detects anomalies using Z-score thresholding.

        #452: mean/std are computed over the finite subset only (NaN inputs
        previously made std_dev NaN, silently suppressing every anomaly);
        reported indices remain those of the original series.
        """
        finite = [(i, v) for i, v in enumerate(values)
                  if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
        n = len(finite)
        if n < 3:
            return []

        mean_val = sum(v for _, v in finite) / n
        variance = sum((v - mean_val) ** 2 for _, v in finite) / n
        std_dev = math.sqrt(variance)

        if std_dev == 0:
            return []

        anomalies = []
        for i, v in finite:
            z_score = (v - mean_val) / std_dev
            if abs(z_score) >= z_threshold:
                t_str = timestamps[i] if timestamps and i < len(timestamps) else f"index_{i}"
                anomalies.append({
                    "index": i,
                    "timestamp": t_str,
                    "value": v,
                    "z_score": round(z_score, 3),
                    "anomaly_type": "high" if z_score > 0 else "low",
                })

        return anomalies

    def analyze_trend(
        self,
        data: Union[List[float], List[Dict[str, Any]]],
        metric_name: str = "value",
        time_field: Optional[str] = None,
        moving_avg_window: int = 3,
        z_threshold: float = 2.0,
        timestamps: Optional[List[str]] = None,
        method: str = "ols_sen",
    ) -> TemporalTrendResult:
        """
        Main trend analysis seam. Takes a list of numeric values or dict objects.

        ``timestamps`` supplies the time label per numeric input value
        (index-aligned, #594). When the series carries parseable timestamps —
        either here or in dict items — the regressions run on a REAL-TIME
        axis (x normalized to years, slope per year) instead of slice indices;
        ``slope_unit`` discloses which axis was used.

        VNext ``method``（默认 ``ols_sen``，行为与历史逐位一致）：

        - ``ols_sen``: Sen 斜率 + OLS（现状）；
        - ``mann_kendall``: 附加非参数 MK 检验（S / tie 校正方差 /
          连续性校正 z / 双侧 p / lag-1 秩自相关警告）；
        - ``seasonal_mann_kendall``: 季节 MK（需要逐点可解析日期；
          逐季节 S/Var 按 hirsch_slack1984 池化）。

        MK 族返回 ``TemporalTrendResultWithSignificance``（子类，携带
        ``trend_method`` / ``significance_evidence`` / ``method_warnings``）
        —— 对既有消费者（instanceof TemporalTrendResult）零破坏。
        """
        values: List[float] = []
        ts_labels: List[str] = []
        dropped_nan = 0

        if isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    # #452: NaN/Inf points (missing period, failed raster
                    # slice) are dropped, not fed into the trend math.
                    if not math.isfinite(item):
                        dropped_nan += 1
                        continue
                    values.append(float(item))
                    if timestamps and i < len(timestamps):
                        ts_labels.append(str(timestamps[i]))
                    else:
                        ts_labels.append(f"t_{i}")
                elif isinstance(item, dict):
                    # Try metric_name first, or value
                    val = item.get(metric_name)
                    if val is None:
                        val = item.get("value")
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        if not math.isfinite(val):
                            dropped_nan += 1
                            continue
                        values.append(float(val))

                        # Extract time
                        t_val = item.get(time_field) if time_field else (item.get("timestamp") or item.get("period") or item.get("time"))
                        if t_val:
                            inst = parse_value_to_instant(t_val)
                            ts_labels.append(inst[0].iso_string if inst else str(t_val))
                        else:
                            ts_labels.append(f"t_{i}")

        if not values:
            return TemporalTrendResult(metric_name=metric_name, total_points=0, dropped_nan=dropped_nan)

        # 1. Moving average
        ma = self.compute_moving_average(values, window_size=moving_avg_window)

        # 2. Sen's slope & Linear Regression — on REAL time when the series
        # carries parseable timestamps (#594), else the per-step index axis.
        x_axis = _timestamps_to_year_axis(ts_labels)
        sens_slope = self.compute_sens_slope(values, x_axis=x_axis)
        ols_slope, intercept, r_squared = self.compute_linear_regression(values, x_axis=x_axis)

        # Prefer OLS / Sen slope direction
        effective_slope = sens_slope if sens_slope != 0 else ols_slope
        if effective_slope > 0.001:
            direction = "increasing"
        elif effective_slope < -0.001:
            direction = "decreasing"
        else:
            direction = "stable"

        # 3. Anomaly detection
        anomalies = self.detect_anomalies(values, timestamps=ts_labels, z_threshold=z_threshold)

        # VNext（ADR-0099）：非参数方法分支 —— ols_sen 路径逐位不变。
        method_key = (method or "ols_sen").lower()
        if method_key not in TREND_METHODS:
            raise ValueError(
                f"unsupported trend method '{method}'; valid: {list(TREND_METHODS)}")
        if method_key != "ols_sen":
            significance_blocks: List[StatisticalSignificance] = []
            method_warnings: List[str] = []
            direction_out = direction
            if method_key == "mann_kendall":
                mk = mann_kendall(values)
                method_warnings = list(mk["warnings"])
                significance_blocks.append(StatisticalSignificance(
                    target="mann_kendall",
                    statistic_name="Mann-Kendall S",
                    statistic_value=float(mk["S"]),
                    p_value=float(mk["p_value"]),
                    method="analytic_normal",
                    alternative="two-sided",
                ))
                if mk["p_value"] < mk["alpha"]:
                    direction_out = mk["direction"]
                else:
                    direction_out = "stable"
            else:  # seasonal_mann_kendall —— 需要逐点可解析日期
                if x_axis is None:
                    raise MissingRequiredField(
                        "seasonal_mann_kendall 需要逐点可解析日期"
                        "（timestamps 或记录内时间字段）",
                        correction_hint="传入 timestamps 或确保每条记录带时间字段",
                    )
                smk = seasonal_mann_kendall(values, ts_labels)
                method_warnings = list(smk["limitations"])
                significance_blocks.append(StatisticalSignificance(
                    target="seasonal_mann_kendall",
                    statistic_name="Seasonal MK S (pooled)",
                    statistic_value=float(smk["S"]),
                    p_value=float(smk["p_value"]),
                    method="analytic_normal",
                    alternative="two-sided",
                ))
                if smk["p_value"] < smk["alpha"]:
                    direction_out = smk["direction"]
                else:
                    direction_out = "stable"
            return TemporalTrendResultWithSignificance(
                metric_name=metric_name,
                total_points=len(values),
                moving_average=ma,
                slope=round(effective_slope, 6),
                intercept=intercept,
                r_squared=r_squared,
                direction=direction_out,
                anomalies=anomalies,
                values=values,
                timestamps=ts_labels,
                dropped_nan=dropped_nan,
                slope_unit="per_year" if x_axis is not None else "per_step",
                trend_method=method_key,
                significance_evidence=[b.to_evidence() for b in significance_blocks],
                method_warnings=method_warnings,
            )

        return TemporalTrendResult(
            metric_name=metric_name,
            total_points=len(values),
            moving_average=ma,
            slope=round(effective_slope, 6),
            intercept=intercept,
            r_squared=r_squared,
            direction=direction,
            anomalies=anomalies,
            values=values,
            timestamps=ts_labels,
            dropped_nan=dropped_nan,
            slope_unit="per_year" if x_axis is not None else "per_step",
        )


# ══════════════════════════════════════════════════════════════════════
# VNext（ADR-0099）非参数趋势 / 变点检验 —— 模块级纯函数。
# 既有类方法不动；工具层与本节共享同一实现（工具不当第二算法）。
# ══════════════════════════════════════════════════════════════════════

TREND_METHODS = ("ols_sen", "mann_kendall", "seasonal_mann_kendall")


class TemporalTrendResultWithSignificance(TemporalTrendResult):
    """带结构化显著性证据的趋势结果（analyze_trend 的 MK 族分支）。

    子类而非改 models.py：对既有消费者零破坏（isinstance 仍成立）；
    ``model_dump()`` 自然携带新增字段。
    """

    trend_method: str = "ols_sen"
    significance_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    method_warnings: List[str] = Field(default_factory=list)


def _finite_values(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(list(values), dtype=float)
    return arr[np.isfinite(arr)]


def _average_ranks(x: np.ndarray) -> np.ndarray:
    """并列值取平均秩（1-based；MK/自相关检验用）。"""
    n = x.size
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    sx = x[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sx[j + 1] == sx[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


_MK_MAX_N = 1024   # 与 Sen 斜率同策略：n×n 差矩阵的确定性上限


def _mk_s_and_var(vals: np.ndarray) -> Tuple[int, float, int]:
    """Mann-Kendall S、tie 校正 Var(S)、并列组数（kendall1975）。

    评审 MAJOR-3：n×n 差矩阵无上限会在 n=20k 时吃 3.2 GB+。与
    ``compute_sens_slope`` 同策略 —— 超限时确定性等距子采样（披露给
    调用方），统计结论仍有效但需在 evidence 中注明。
    """
    n = int(vals.size)
    subsampled = False
    if n > _MK_MAX_N:
        stride = int(np.ceil(n / _MK_MAX_N))
        vals = vals[::stride]
        n = int(vals.size)
        subsampled = True
    # D[i, j] = x_j − x_i；上三角 (i<j) 的符号和即 S = Σ_{i<j} sign(x_j − x_i)。
    d = vals[None, :] - vals[:, None]
    iu = np.triu_indices(n, k=1)
    s = int(np.sum(np.sign(d[iu])))
    # tie 校正：Σ_p t_p(t_p−1)(2t_p+5)，t_p 为各组并列数。
    _, counts = np.unique(vals, return_counts=True)
    tie_term = int(np.sum([t * (t - 1) * (2 * t + 5) for t in counts if t > 1]))
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    return s, float(var_s), int(np.sum(counts > 1)), subsampled


def _mk_z_and_p(s: int, var_s: float) -> Tuple[float, float]:
    """连续性校正正态 z + 双侧 p（Var=0 的常量序列 → z=0, p=1）。"""
    if var_s <= 0:
        return 0.0, 1.0
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0
    p = math.erfc(abs(z) / math.sqrt(2.0))   # 2·(1 − Φ(|z|)) 的数值稳定形
    return float(z), float(min(max(p, 0.0), 1.0))


def _lag1_rank_autocorr(vals: np.ndarray) -> float:
    """秩序列的 lag-1 自相关（MK 显著性受序列相关污染的经典诊断）。"""
    ranks = _average_ranks(vals)
    n = ranks.size
    if n < 3:
        return 0.0
    centered = ranks - ranks.mean()
    denom = float(np.sum(centered ** 2))
    if denom <= 0:
        return 0.0
    return float(np.sum(centered[:-1] * centered[1:]) / denom)


def mann_kendall(values: Sequence[float], alpha: float = 0.05) -> Dict[str, Any]:
    """Mann-Kendall 趋势检验（mann1945 / kendall1975）。

    - S = Σ_{i<j} sign(x_j − x_i)；tie 校正 Var(S)（kendall1975）；
    - 连续性校正正态 z（|S|−1 折算）+ 双侧 p（erfc）；
    - lag-1 秩自相关 |r1| > 1.96/√n → 警告「序列相关可能夸大显著性」；
    - n < 4 → ``InsufficientSamples``（n=2 专属提示：两个时间点无法
      定义趋势统计量）；4 ≤ n < 8 → 照常计算 + 警告「样本过少，仅
      描述性解读」。

    与 scipy.stats.kendalltau 的有意差异：本实现 z 用连续性校正
    （S∓1），scipy 的 kendalltau 无此折算——小样本 p 值略保守。
    """
    vals = _finite_values(values)
    n = int(vals.size)
    if n == 2:
        raise InsufficientSamples(
            "两个时间点无法定义趋势统计量（n=2）",
            correction_hint="至少 4 个时间点才能做 MK 检验；两点差异只能描述",
        )
    if n < 4:
        raise InsufficientSamples(
            f"MK 检验样本不足：n={n} < 4",
            correction_hint="至少 4 个时间点（建议 ≥8 做显著性声明）",
        )

    warnings: List[str] = []
    if n < 8:
        warnings.append(f"样本过少（n={n}），仅描述性解读")

    s, var_s, tie_groups, mk_subsampled = _mk_s_and_var(vals)
    if var_s <= 0:
        warnings.append("序列并列结构使 Var(S)=0（常量/近常量序列）——无趋势可检")
    if mk_subsampled:
        warnings.append(
            f"序列长度超过 {_MK_MAX_N}，已确定性等距子采样后计算 MK "
            f"（评审 MAJOR-3 内存护栏；p 值为子采样近似）")
    z, p = _mk_z_and_p(s, var_s)

    r1 = _lag1_rank_autocorr(vals)
    if abs(r1) > 1.96 / math.sqrt(n):
        warnings.append(
            f"lag-1 秩自相关 r1={r1:.3f} 超过 1.96/√n——序列相关可能夸大显著性")

    direction = "increasing" if s > 0 else ("decreasing" if s < 0 else "stable")
    return {
        "n": n,
        "S": s,
        "var_s": float(var_s),   # 全精度（证据块自行收敛小数位）
        "z": float(z),
        "p_value": float(p),
        "alpha": float(alpha),
        "direction": direction,
        "significant": bool(p < alpha),
        "tie_groups": tie_groups,
        "lag1_rank_autocorr": round(r1, 6),
        "warnings": warnings,
        "reference": "mann1945+kendall1975（连续性校正正态近似）",
    }


SEASON_MODES = ("monthly", "quarterly")


def seasonal_mann_kendall(
    values: Sequence[float],
    dates: Sequence[str],
    season: str = "monthly",
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """季节 Mann-Kendall（hirsch_slack1984：逐季 S/Var 独立池化求和）。

    - ``season="monthly"``：月为季节（12 组）；``"quarterly"``：季度
      （(month−1)//3+1，4 组）；
    - 观测数 < 3 的季节**跳过并披露**（诚实：不并入池化）；
    - 无任何合格季节 → ``InsufficientSamples``；
    - 局限披露：无预白化（prewhitening 未实现）——序列相关会夸大
      池化显著性（Hirsch-Slack 原文的已知边界）。
    """
    season_key = (season or "monthly").lower()
    if season_key not in SEASON_MODES:
        raise ValueError(
            f"unsupported season mode '{season}'; valid: {list(SEASON_MODES)}")
    vals_all = np.asarray(list(values), dtype=float)
    if len(dates) != len(values):
        raise ValueError(
            f"dates 长度 {len(dates)} 与 values 长度 {len(values)} 不一致")

    months: List[int] = []
    for d in dates:
        inst = parse_value_to_instant(str(d))
        if inst is None:
            raise ValueError(f"无法解析日期 {d!r}（季节 MK 需要逐点日期）")
        months.append(inst[0].to_datetime().month)

    groups: Dict[int, List[float]] = {}
    for m, v, d in zip(months, vals_all, dates):
        if not np.isfinite(v):
            continue
        key = m if season_key == "monthly" else (m - 1) // 3 + 1
        groups.setdefault(key, []).append(float(v))

    per_season: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    s_total, var_total = 0, 0.0
    for key in sorted(groups):
        season_vals = np.asarray(groups[key], dtype=float)
        if season_vals.size < 3:
            skipped.append({
                "season": int(key),
                "observations": int(season_vals.size),
                "reason": "观测数 < 3，跳过（不并入池化）",
            })
            continue
        s_k, var_k, ties_k, _ = _mk_s_and_var(season_vals)
        s_total += s_k
        var_total += var_k
        per_season.append({
            "season": int(key),
            "n": int(season_vals.size),
            "S": s_k,
            "var_s": float(var_k),
            "tie_groups": ties_k,
        })

    if not per_season:
        raise InsufficientSamples(
            f"季节 MK 无合格季节（每季需 ≥3 观测；mode={season_key}）",
            correction_hint="加长时序或改用 monthly 细分/普通 mann_kendall",
        )

    z, p = _mk_z_and_p(s_total, var_total)
    direction = (
        "increasing" if s_total > 0
        else ("decreasing" if s_total < 0 else "stable")
    )
    limitations = ["无预白化（prewhitening 未实现）——序列相关会夸大池化显著性"]
    if skipped:
        limitations.append(
            f"{len(skipped)} 个季节观测数 < 3 被跳过（未并入池化）")

    return {
        "n": int(sum(g["n"] for g in per_season)),
        "season_mode": season_key,
        "seasons_used": len(per_season),
        "per_season": per_season,
        "skipped_seasons": skipped,
        "S": s_total,
        "var_s": float(var_total),
        "z": float(z),
        "p_value": float(p),
        "alpha": float(alpha),
        "direction": direction,
        "significant": bool(p < alpha),
        "limitations": limitations,
        "reference": "hirsch_slack1984（逐季独立 + 池化 S/Var 求和）",
    }


def cusum_change_point(
    values: Sequence[float],
    bootstrap_draws: int = 200,
    seed: int = 42,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """标准化 CUSUM 单均值变点检验（bootstrap 显著性，固定种子）。

    - 变点指标 = argmax_k |Σ_{i≤k}(x_i − x̄)|（k 取 1..n−1；k=n 恒 0）；
    - 显著性：固定种子 ``seed`` 的 ``bootstrap_draws`` 次无变化零假设
      （随机重排）下 max-CUSUM 分布，p = (1 + #{boot ≥ obs}) / (draws+1)；
    - ``change_point_index`` 仅在 p < alpha 时给出（否则 None），
      ``candidate_index`` 恒给（候选即 argmax 位置）；
    - ``magnitude`` = 变点后均值 − 变点前均值（候选切分处）；
    - n < 10 → 警告（变点定位不稳定）。

    确定性：同 seed 同输入 → 逐位同输出（random_seed_policy=fixed_seed）。
    """
    vals = _finite_values(values)
    n = int(vals.size)
    if n < 3:
        raise InsufficientSamples(
            f"CUSUM 变点检验样本不足：n={n} < 3",
            correction_hint="均值变点至少需要 3 个观测（建议 n ≥ 10）",
        )
    draws = int(bootstrap_draws)
    if not (100 <= draws <= 1000):
        raise ValueError(
            f"bootstrap_draws 必须在 [100, 1000]，got {bootstrap_draws}")

    warnings: List[str] = []
    if n < 10:
        warnings.append(f"样本过少（n={n} < 10），CUSUM 变点定位不稳定")

    def _max_cusum(x: np.ndarray) -> float:
        dev = np.cumsum(x - x.mean())
        return float(np.max(np.abs(dev[:-1]))) if n > 1 else 0.0

    obs_max = _max_cusum(vals)
    dev = np.cumsum(vals - vals.mean())
    candidate = int(np.argmax(np.abs(dev[:-1])))   # 0-based 切分指标

    rng = np.random.default_rng(int(seed))
    exceed = 0
    for _ in range(draws):
        perm = rng.permutation(vals)
        if _max_cusum(perm) >= obs_max:
            exceed += 1
    p_value = (1 + exceed) / (draws + 1)

    magnitude = float(np.mean(vals[candidate + 1:]) - np.mean(vals[:candidate + 1]))
    significant = bool(p_value < alpha)
    if not significant:
        warnings.append(
            f"p={p_value:.4f} ≥ alpha={alpha}——无充分证据拒绝均值不变")

    return {
        "n": n,
        "candidate_index": candidate,
        "change_point_index": candidate if significant else None,
        "magnitude": round(magnitude, 6),
        "max_cusum": round(obs_max, 6),
        "p_value": float(p_value),
        "alpha": float(alpha),
        "significant": significant,
        "bootstrap_draws": draws,
        "seed": int(seed),
        "warnings": warnings,
    }
