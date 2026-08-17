"""
Temporal Trend Engine.
Analyzes temporal time series trends using moving averages, Sen's slope / linear trend fitting,
and statistical anomaly detection (Z-score / IQR).
"""

import math
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

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
    ) -> TemporalTrendResult:
        """
        Main trend analysis seam. Takes a list of numeric values or dict objects.

        ``timestamps`` supplies the time label per numeric input value
        (index-aligned, #594). When the series carries parseable timestamps —
        either here or in dict items — the regressions run on a REAL-TIME
        axis (x normalized to years, slope per year) instead of slice indices;
        ``slope_unit`` discloses which axis was used.
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
