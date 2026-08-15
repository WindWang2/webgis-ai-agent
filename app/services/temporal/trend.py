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
    def compute_sens_slope(values: List[float]) -> float:
        """
        Computes Sen's Slope (median of slopes between all pair of points (i, j) with i < j).

        Vectorized over all pairs for n ≤ ``_SENS_SLOPE_MAX_N``; larger series
        are evenly subsampled to that cap (with a warning) so the computation
        stays bounded instead of materializing ~n²/2 Python floats.
        """
        n = len(values)
        if n < 2:
            return 0.0

        import numpy as np
        arr = np.asarray(values, dtype=float)

        if n > TemporalTrendEngine._SENS_SLOPE_MAX_N:
            logger.warning(
                "Sen's slope input truncated: %d points > max %d; "
                "subsampling to evenly spaced points (approximation).",
                n, TemporalTrendEngine._SENS_SLOPE_MAX_N,
            )
            idx = np.unique(np.linspace(0, n - 1, TemporalTrendEngine._SENS_SLOPE_MAX_N).astype(int))
            arr = arr[idx]
            # x coordinates stay the ORIGINAL time indices — slopes are per
            # index step, not per subsampled position.
            x = idx
            n = arr.size
        else:
            x = np.arange(n)

        if n < 2:
            return 0.0

        i, j = np.triu_indices(n, k=1)
        if i.size == 0:
            return 0.0
        slopes = (arr[j] - arr[i]) / (x[j] - x[i])
        return float(np.median(slopes))

    @staticmethod
    def compute_linear_regression(values: List[float]) -> Tuple[float, float, float]:
        """
        Computes OLS linear regression (slope, intercept, r_squared) for x = 0..n-1.
        """
        n = len(values)
        if n < 2:
            return 0.0, values[0] if values else 0.0, 0.0

        x = list(range(n))
        y = values

        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(x[i] * y[i] for i in range(n))
        sum_xx = sum(x[i] ** 2 for i in range(n))

        denom = n * sum_xx - sum_x ** 2
        if denom == 0:
            return 0.0, sum_y / n, 0.0

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        # R-squared
        y_mean = sum_y / n
        ss_tot = sum((y[i] - y_mean) ** 2 for i in range(n))
        ss_res = sum((y[i] - (slope * x[i] + intercept)) ** 2 for i in range(n))

        r_squared = (1.0 - (ss_res / ss_tot)) if ss_tot > 0 else 1.0
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
        """
        n = len(values)
        if n < 3:
            return []

        mean_val = sum(values) / n
        variance = sum((x - mean_val) ** 2 for x in values) / n
        std_dev = math.sqrt(variance)

        if std_dev == 0:
            return []

        anomalies = []
        for i, v in enumerate(values):
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
    ) -> TemporalTrendResult:
        """
        Main trend analysis seam. Takes a list of numeric values or dict objects.
        """
        values: List[float] = []
        timestamps: List[str] = []

        if isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    values.append(float(item))
                    timestamps.append(f"t_{i}")
                elif isinstance(item, dict):
                    # Try metric_name first, or value
                    val = item.get(metric_name)
                    if val is None:
                        val = item.get("value")
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        values.append(float(val))

                        # Extract time
                        t_val = item.get(time_field) if time_field else (item.get("timestamp") or item.get("period") or item.get("time"))
                        if t_val:
                            inst = parse_value_to_instant(t_val)
                            timestamps.append(inst[0].iso_string if inst else str(t_val))
                        else:
                            timestamps.append(f"t_{i}")

        if not values:
            return TemporalTrendResult(metric_name=metric_name, total_points=0)

        # 1. Moving average
        ma = self.compute_moving_average(values, window_size=moving_avg_window)

        # 2. Sen's slope & Linear Regression
        sens_slope = self.compute_sens_slope(values)
        ols_slope, intercept, r_squared = self.compute_linear_regression(values)

        # Prefer OLS / Sen slope direction
        effective_slope = sens_slope if sens_slope != 0 else ols_slope
        if effective_slope > 0.001:
            direction = "increasing"
        elif effective_slope < -0.001:
            direction = "decreasing"
        else:
            direction = "stable"

        # 3. Anomaly detection
        anomalies = self.detect_anomalies(values, timestamps=timestamps, z_threshold=z_threshold)

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
            timestamps=timestamps,
        )
