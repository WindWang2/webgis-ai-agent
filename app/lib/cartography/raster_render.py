"""栅格渲染助手 — hillshade / 分级栅格 / 分层设色合成（Design System V4）.

raster_cartography_converter 的通用机制是「数组 + bounds + palette →
colormap PNG + raster 层」。本模块补齐三个纯 numpy 渲染模式，让
hillshade / classified_raster / elevation_tint_hillshade / bivariate_raster
走同一条诚实渲染链：

- ``hillshade_array``：Horn 法山体阴影（方位角/高度角显式参数 —— 图面
  必须随图披露光源参数，pitfalls 载明）；
- ``classify_array``：连续栅格按断点分级为离散色阶索引（分级栅格图）；
- ``blend_arrays``：分层设色 + 晕渲的确定性 alpha 合成。

全部是纯数组函数（无 I/O、确定性），converter 负责编码 PNG 与 bounds。
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np


def hillshade_array(
    dem: np.ndarray,
    *,
    cell_size: float = 1.0,
    azimuth: float = 315.0,
    altitude: float = 45.0,
) -> np.ndarray:
    """Horn 法山体阴影（0-255 灰度 float 数组）。

    几何：表面法向 n = normalize((-∂z/∂x, -∂z/∂y, 1)) 与光源单位向量
    l = (cos(alt)·sin(az), cos(alt)·cos(az), sin(alt)) 的点积
    （Lambert 漫反射；``azimuth`` 自北顺时针，``altitude`` 光源高度角）。
    平地 shade ≡ sin(alt) —— **与方位角无关**；东坡（∂z/∂x>0）背向
    西侧光源 → 变暗。NaN 边界区输出 NaN（渲染为透明，不伪装阴影）。
    """
    a = np.asarray(dem, dtype=np.float64)
    if a.ndim != 2:
        raise ValueError("dem 必须是 2D 数组")
    z = np.where(np.isfinite(a), a, np.nan)
    # Horn 3×3 差分（边界 1 圈不参与，输出 NaN）。窗口 [a b c / d e f /
    # g h i]，列 +1 = 东、行 +1 = 南（行 0 = 北缘）：
    #   ∂z/∂x（东向）= ((c+2f+i) − (a+2d+g)) / 8·cell
    #   ∂z/∂y（北向）= ((a+2b+c) − (g+2h+i)) / 8·cell
    a_ = z[0:-2, 0:-2]; b_ = z[0:-2, 1:-1]; c_ = z[0:-2, 2:]
    d_ = z[1:-1, 0:-2];                     f_ = z[1:-1, 2:]
    g_ = z[2:, 0:-2];   h_ = z[2:, 1:-1];   i_ = z[2:, 2:]
    dz_dx = np.full_like(z, np.nan)
    dz_dy = np.full_like(z, np.nan)
    dz_dx[1:-1, 1:-1] = ((c_ + 2 * f_ + i_) - (a_ + 2 * d_ + g_)) / (8 * cell_size)
    dz_dy[1:-1, 1:-1] = ((a_ + 2 * b_ + c_) - (g_ + 2 * h_ + i_)) / (8 * cell_size)

    az = math.radians(azimuth)
    alt = math.radians(altitude)
    # 光源单位向量（x=东, y=北, z=上）
    lx = math.cos(alt) * math.sin(az)
    ly = math.cos(alt) * math.cos(az)
    lz = math.sin(alt)
    # 表面法向（未归一）＝ (-∂z/∂x, -∂z/∂y, 1)
    nx = -dz_dx
    ny = -dz_dy
    norm = np.sqrt(nx ** 2 + ny ** 2 + 1.0)
    shade = (nx * lx + ny * ly + lz) / norm
    gray = np.clip(shade, 0.0, 1.0) * 255.0
    gray[~np.isfinite(z)] = np.nan
    return gray


def classify_array(
    values: np.ndarray,
    breaks: Sequence[float],
) -> np.ndarray:
    """连续数组 → 类别索引（0..len(breaks)）；NaN → -1（透明语义）。"""
    a = np.asarray(values, dtype=np.float64)
    idx = np.full(a.shape, -1, dtype=np.int16)
    finite = np.isfinite(a)
    cls = np.zeros(a.shape, dtype=np.int16)
    for b in breaks:
        cls[finite & (a > b)] += 1
    idx[finite] = cls[finite]
    return idx


def blend_arrays(
    base: np.ndarray,
    overlay: np.ndarray,
    *,
    overlay_alpha: float = 0.4,
) -> np.ndarray:
    """分层设色合成：base（0-1 归一色阶值）与 overlay（0-1 灰度）按
    alpha 混合（base*(1-α)+shade*α）。NaN 以另一侧兜底（晕渲缺失处保留
    设色），双侧 NaN 输出 NaN。"""
    b = np.asarray(base, dtype=np.float64)
    o = np.asarray(overlay, dtype=np.float64)
    if b.shape != o.shape:
        raise ValueError("base/overlay 形状必须一致")
    alpha = float(np.clip(overlay_alpha, 0.0, 1.0))
    b_fill = np.where(np.isfinite(b), b, 0.0)
    o_fill = np.where(np.isfinite(o), o, 0.0)
    blended = b_fill * (1 - alpha) + o_fill * alpha
    both_nan = ~np.isfinite(b) & ~np.isfinite(o)
    blended[both_nan] = np.nan
    return blended


def normalize_min_max(values: np.ndarray) -> Tuple[float, float]:
    """finite min/max（全 NaN 时返回 (0,1) 的中性区间）。"""
    a = np.asarray(values, dtype=np.float64)
    finite = a[np.isfinite(a)]
    if finite.size == 0:
        return (0.0, 1.0)
    lo = float(finite.min())
    hi = float(finite.max())
    if hi <= lo:
        hi = lo + 1.0
    return (lo, hi)


def equal_interval_breaks(values: np.ndarray, n: int) -> List[float]:
    lo, hi = normalize_min_max(values)
    step = (hi - lo) / n
    return [lo + step * i for i in range(1, n)]


__all__ = [
    "hillshade_array",
    "classify_array",
    "blend_arrays",
    "normalize_min_max",
    "equal_interval_breaks",
]
