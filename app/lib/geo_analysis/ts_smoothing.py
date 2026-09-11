"""时间序列平滑与缺口填补（Goal 07 Phase G —— temporal 域实现层）。

光学时序（NDVI/EVI 等）常见的两类预处理：

- **平滑**：``savgol``（Savitzky-Golay 卷积平滑，保峰去噪）与
  ``moving_average``（中心滑动均值）；
- **缺口填补**：``linear``（按时间线性插值）/ ``nearest``（最近有效值）
  / ``none``（只平滑不填补 —— NaN 位置保持 NaN 并披露计数）。

职责边界（CONTRACT_BACKBONE §1）：纯数值 —— 不读文件、不写 artifact、
不挂证据块（工具层职责）。

科学约定：

- **缺口语义**：填补是显式步骤（``fill`` 参数），平滑在**填补后的序列**
  上进行；输出携带 ``filled_mask``（填补位置）与 ``gap_fraction``，
  缺口不静默消失；
- **``fill="none"`` 的 NaN 窗口扩散**：滑动窗口触及缺口的输出位置亦为
  NaN（保守口径：不做部分窗口估计），扩散半径 = 窗口半径
  （meta ``nan_window_bleed`` 披露）；
- ``savgol`` 要求 ``window_length`` 为奇数、``> polyorder``、且
  ``window_length <= 有效观测数``（否则类型化报错）；
- 确定性：全部方法无随机成分（random_seed_policy=deterministic）。
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np

from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    NoValidObservations,
)

__all__ = [
    "smooth_gapfill",
]

_SAVGOL_METHODS = ("savgol", "moving_average", "none")
_FILL_METHODS = ("linear", "nearest", "none")

#: 缺口占比上限（超过即拒绝 —— 缺口主导的序列平滑结果是伪像）。
_MAX_GAP_FRACTION = 0.9


def _fill_gaps(
    values: np.ndarray,
    method: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """缺口填补 → (filled, filled_mask)。``none`` 只统计不填补。"""
    valid = np.isfinite(values)
    filled = values.astype(float, copy=True)
    filled_mask = np.zeros(values.shape, dtype=bool)
    if valid.all() or method == "none":
        return filled, filled_mask
    idx = np.arange(values.size)
    if method == "linear":
        filled[~valid] = np.interp(idx[~valid], idx[valid], values[valid])
    elif method == "nearest":
        from scipy.interpolate import interp1d

        f = interp1d(idx[valid], values[valid], kind="nearest",
                     fill_value="extrapolate")
        filled[~valid] = f(idx[~valid])
    else:
        raise ValueError(f"unknown fill method {method!r}")
    filled_mask = ~valid
    return filled, filled_mask


def smooth_gapfill(
    values: Any,
    *,
    method: str = "savgol",
    window_length: int = 5,
    polyorder: int = 2,
    fill: str = "linear",
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """时序平滑 + 缺口填补。

    参数：
        values: 1D 数值序列（NaN/None/±Inf 视为缺口）。
        method: ``savgol`` / ``moving_average`` / ``none``（只填补）。
        window_length: 滑动窗口（savgol 需奇数；moving_average 任意正整数）。
        polyorder: savgol 多项式阶（< window_length）。
        fill: 缺口填补 ``linear`` / ``nearest`` / ``none``。

    返回 ``(smoothed, filled_mask, meta)``：``smoothed`` 与输入等长
    （填补缺口参与平滑；``fill="none"`` 时 NaN 位置保持 NaN）；
    ``filled_mask`` 标记被填补的位置。
    """
    v = np.asarray(values, dtype=np.float64)
    if v.ndim != 1:
        raise NoValidObservations(
            f"values must be a 1D series (got ndim {v.ndim})")
    n = v.size
    if n < 3:
        raise InsufficientSamples(
            f"time series needs at least 3 observations (got {n})",
            correction_hint="aggregate a longer observation window",
        )
    if str(method) not in _SAVGOL_METHODS:
        raise ValueError(f"method must be one of {_SAVGOL_METHODS} "
                         f"(got {method!r})")
    if str(fill) not in _FILL_METHODS:
        raise ValueError(f"fill must be one of {_FILL_METHODS} (got {fill!r})")
    method = str(method)
    fill = str(fill)
    window_length = int(window_length)
    polyorder = int(polyorder)
    if window_length < 1:
        raise ValueError(f"window_length must be >= 1 (got {window_length})")

    gap_fraction = float((~np.isfinite(v)).sum()) / n
    if gap_fraction >= _MAX_GAP_FRACTION:
        raise DegenerateData(
            f"time series has {gap_fraction:.0%} gaps (limit "
            f"{_MAX_GAP_FRACTION:.0%}); smoothing a gap-dominated series "
            "is not meaningful",
            correction_hint="extend the observation window or lower the "
                            "quality threshold upstream",
        )

    filled, filled_mask = _fill_gaps(v, fill)
    valid_count = int(np.isfinite(v).sum())  # 填补前的有效观测数

    if method == "savgol":
        from scipy.signal import savgol_filter

        if window_length % 2 == 0:
            window_length += 1  # savgol 硬性奇数窗口（自动取整并披露）
        if window_length <= polyorder:
            raise ValueError(
                f"window_length ({window_length}) must be > polyorder "
                f"({polyorder})")
        if window_length > valid_count:
            raise InsufficientSamples(
                f"window_length {window_length} exceeds valid observations "
                f"{valid_count}",
                correction_hint="reduce window_length or gap-fill first",
            )
        smoothed = savgol_filter(filled, window_length, polyorder)
        if fill == "none":
            smoothed[~np.isfinite(v)] = np.nan
    elif method == "moving_average":
        if window_length > n:
            raise InsufficientSamples(
                f"window_length {window_length} exceeds series length {n}",
                correction_hint="reduce window_length",
            )
        kernel = np.ones(window_length) / window_length
        pad = window_length // 2
        padded = np.pad(filled, pad, mode="edge")
        smoothed = np.convolve(padded, kernel, mode="valid")[:n]
        if fill == "none":
            smoothed[~np.isfinite(v)] = np.nan
    else:  # none：只填补
        smoothed = filled.copy()

    meta: Dict[str, Any] = {
        "algorithm": "ts_smooth_gapfill",
        "method": method,
        "fill": fill,
        "window_length": window_length,
        "polyorder": polyorder if method == "savgol" else None,
        "n_observations": n,
        "valid_observations": valid_count,
        "filled_count": int(filled_mask.sum()),
        "gap_fraction": round(gap_fraction, 6),
        "nan_window_bleed": (
            bool(fill == "none" and method != "none"
                 and (~np.isfinite(v)).any())),
        "deterministic": True,
    }
    return smoothed, filled_mask, meta
