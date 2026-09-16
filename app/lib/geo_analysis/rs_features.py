"""时序特征编排 —— cube 级年度/季节合成 + percentile/Sen 斜率/变点。

定位（本方向 ownership：temporal feature extraction / phenology profiles）：

- **编排优先**：基础时序特征（min/max/mean/std/amplitude/harmonic）复用
  ``rs_v3.temporal_features``；物候（SOS/EOS/双谐波/有界填充）复用
  ``phenology``——本模块只补齐 cube 级编排缺口：周期分组合成、
  分位数套件、Sen 稳健斜率、逐像元 CUSUM 变点；
- **诚实边界**：NaN 传播（无效切片永不充当 0）；Sen 斜率有切片数上限
  （``THEIL_SEN_MAX_T``，超限诚实跳过并披露——不做 O(T²·N) 无界爆炸）；
  变点只给 CUSUM 指数/量级（page1954），无逐像元 bootstrap 显著性
  （披露）；季节合成是 nan-aware median（``min_valid`` 不足 → NaN +
  计数，不降级为部分均值）；
- 规模守卫：栈元素 ≤ ``CUBE_MAX_ELEMENTS``（与 temporal_cube 同顶）；
  分组数 ≤ ``COMPOSITE_MAX_GROUPS``（先拒绝不 OOM）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

from app.lib.geo_analysis.temporal_cube import CUBE_MAX_ELEMENTS
from app.lib.gis.scientific_errors import ResourceScaleMismatch

#: Sen 斜率切片数上限（成对斜率 O(T²)；24 → 276 对，分块向量化有界）。
THEIL_SEN_MAX_T = 24

#: 周期合成组数上限（4 季 × 30 年量级；先拒绝不 OOM）。
COMPOSITE_MAX_GROUPS = 128

#: 季节合成单组最小有效切片数缺省。
DEFAULT_MIN_VALID = 2

#: 季节定义（北半球气象季节；月 → (season_idx, 年偏移)）。
_MONTH_SEASON = {
    12: (1, 1), 1: (1, 0), 2: (1, 0),      # DJF：12 月归次年冬季
    3: (2, 0), 4: (2, 0), 5: (2, 0),       # MAM
    6: (3, 0), 7: (3, 0), 8: (3, 0),       # JJA
    9: (4, 0), 10: (4, 0), 11: (4, 0),     # SON
}
_SEASON_NAME = {1: "winter", 2: "spring", 3: "summer", 4: "fall"}


def _check_scale(arr: np.ndarray, what: str) -> None:
    if arr.size > CUBE_MAX_ELEMENTS:
        raise ResourceScaleMismatch(
            f"{what} 栈规模 {arr.shape} = {arr.size:,} 元素超过上限 "
            f"{CUBE_MAX_ELEMENTS:,}",
            estimated=f"{arr.size * 8 / 1024 ** 2:.0f} MiB float64",
            limit=f"T·H·W ≤ {CUBE_MAX_ELEMENTS:,}",
            correction_hint="缩小格网窗口或降低时间粒度后重试",
        )


# ── 周期分组合成（年度/季节）──────────────────────────────────────────

def period_composites(
    stack: np.ndarray,
    times_sec: np.ndarray,
    *,
    periods_per_year: int = 4,
    min_valid: int = DEFAULT_MIN_VALID,
) -> Dict[str, Any]:
    """按年内周期分组做 nan-aware median 合成（年度=1 组/年，季节=4 组/年）。

    分组确定性（UTC）；DJF 惯例：12 月归次年冬季（meta 披露）。
    有效切片 < ``min_valid`` 的组：median=NaN + ``n_valid`` 计数 +
    ``excluded_insufficient``（不降级为部分均值）。
    """
    arr = np.asarray(stack, dtype=float)
    if arr.ndim != 3:
        raise ValueError(f"period_composites 需要 (T,H,W) 栈，got {arr.shape}")
    t = np.asarray(times_sec, dtype=float)
    if t.ndim != 1 or len(t) != arr.shape[0]:
        raise ValueError(
            f"times_sec 须与栈时间轴等长（{arr.shape[0]}），got {len(t)}")
    if periods_per_year not in (1, 4):
        raise ValueError(
            f"periods_per_year 只支持 1（年）或 4（季），got {periods_per_year}")
    _check_scale(arr, "周期合成")

    groups: Dict[Tuple[int, int], list] = {}
    for i, ts in enumerate(t):
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        if periods_per_year == 1:
            key = (dt.year, 1)
        else:
            season, year_shift = _MONTH_SEASON[dt.month]
            key = (dt.year + year_shift, season)
        groups.setdefault(key, []).append(i)
    if len(groups) > COMPOSITE_MAX_GROUPS:
        raise ResourceScaleMismatch(
            f"周期组数 {len(groups)} 超过上限 {COMPOSITE_MAX_GROUPS}",
            estimated=f"{len(groups)} groups",
            limit=f"≤{COMPOSITE_MAX_GROUPS}",
            correction_hint="按年窗切片或降低 periods_per_year",
        )

    out_groups = []
    for key in sorted(groups):
        year, period = key
        idx = groups[key]
        vals = arr[idx]
        with np.errstate(invalid="ignore"):
            n_valid = int(np.sum(np.isfinite(vals).any(axis=(1, 2))))
        median = np.full(arr.shape[1:], np.nan)
        if n_valid >= max(1, int(min_valid)):
            with np.errstate(invalid="ignore"):
                median = np.nanmedian(vals, axis=0)
        out_groups.append({
            "key": f"{year}-S{period}" if periods_per_year == 4
            else f"{year}-P1",
            "year": year,
            "period": period,
            "n_slices": len(idx),
            "n_valid": n_valid,
            "median": median,
            "excluded_insufficient": n_valid < max(1, int(min_valid)),
        })
    meta = {
        "periods_per_year": int(periods_per_year),
        "min_valid": int(min_valid),
        "n_groups": len(out_groups),
        "djf_convention": "12 月归次年冬季（北半球气象季节，UTC）",
        "disclosures": [
            "季节合成为 nan-aware median：无效切片不充当 0",
            f"有效切片 < {min_valid} 的组诚实输出 NaN 并计数",
        ],
    }
    return {"groups": out_groups, "meta": meta}


# ── 特征包（percentile + Sen 斜率 + CUSUM 变点 + 基础特征复用）────────

def _sen_slope(values: np.ndarray, t_norm: np.ndarray) -> np.ndarray:
    """逐像元 Theil-Sen 斜率（每单位 t；成对斜率中位数，分块向量化）。

    values: (T, N)；NaN = 无效。有效对 < 3 的像元 → NaN。
    """
    n_t, n = values.shape
    out = np.full(n, np.nan)
    pairs = [(i, j) for i in range(n_t) for j in range(i + 1, n_t)]
    chunk = max(1, int(4_000_000 / max(1, len(pairs))))
    v = values.T                       # (N, T)
    for s in range(0, n, chunk):
        block = v[s:s + chunk]         # (Nc, T)
        slopes = np.empty((len(pairs), block.shape[0]))
        for k, (i, j) in enumerate(pairs):
            dt = t_norm[j] - t_norm[i]
            if dt <= 0:
                continue
            a = block[:, i]
            b = block[:, j]
            ok = np.isfinite(a) & np.isfinite(b)
            sl = np.where(ok, (b - a) / dt, np.nan)
            slopes[k] = sl
        with np.errstate(invalid="ignore"):
            med = np.nanmedian(slopes, axis=0)
            n_ok = np.sum(np.isfinite(slopes), axis=0)
        med = np.where(n_ok >= 3, med, np.nan)
        out[s:s + chunk] = med
    return out


def _cusum_changepoint(values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """逐像元 CUSUM 均值变点（page1954；无逐像元 bootstrap 显著性）。

    Returns (change_index, cusum_magnitude)：零方差/有效样本 <4 → NaN 量级
    与指数 0。
    """
    n_t, n = values.shape
    v = np.where(np.isfinite(values), values, np.nan)
    with np.errstate(invalid="ignore"):
        mean = np.nanmean(v, axis=0)
        std = np.nanstd(v, axis=0, ddof=1)
    dev = v - mean
    cs = np.nancumsum(np.where(np.isfinite(dev), dev, 0.0), axis=0)
    # 与比例基线的偏差：S_k = cs_k − (k/(T−1))·cs_{T−1}（k = 1..T−1 处评估）
    ks = np.arange(1, n_t)
    denom = max(n_t - 1, 1)
    sk = cs[1:] - (ks[:, None] / denom) * cs[n_t - 1]
    with np.errstate(invalid="ignore"):
        mag = np.nanmax(np.abs(sk), axis=0) if n_t > 1 else np.zeros(n)
        arg = np.nanargmax(np.abs(sk), axis=0) + 1 if n_t > 1 else np.zeros(
            n, dtype=int)
    degenerate = ~np.isfinite(std) | (std <= 0)
    n_valid = np.sum(np.isfinite(v), axis=0)
    degenerate |= n_valid < 4
    mag = np.where(degenerate, 0.0, mag / np.where(std > 0, std, 1.0))
    change_index = np.where(degenerate, np.nan, arg.astype(float))
    return change_index, mag


def temporal_feature_pack(
    stack: np.ndarray,
    times_sec: np.ndarray,
    *,
    nodata: Optional[float] = None,
    percentiles: Sequence[float] = (0.1, 0.5, 0.9),
    compute_changepoint: bool = True,
) -> Dict[str, Any]:
    """cube 级时序特征包：基础特征（rs_v3）+ 分位数 + Sen 斜率 + 变点。

    - NaN/nodata → 无效；特征只在有效观测上计算（NaN 传播，不充当 0）；
    - ``sen_slope`` 单位 = 值/天（时间轴按 epoch 秒归一）；
    - T > ``THEIL_SEN_MAX_T``：斜率诚实跳过（NaN + 披露），不做无界 O(T²)；
    - ``change_index``/``cusum_magnitude``：CUSUM 均值变点指数（1-based
      切片序）与 std 归一量级；零方差/有效样本 <4 → 幅值 0、指数 NaN。
    """
    arr = np.asarray(stack, dtype=float)
    if arr.ndim != 3:
        raise ValueError(f"temporal_feature_pack 需要 (T,H,W) 栈，got {arr.shape}")
    t = np.asarray(times_sec, dtype=float)
    if t.ndim != 1 or len(t) != arr.shape[0]:
        raise ValueError(
            f"times_sec 须与栈时间轴等长（{arr.shape[0]}），got {len(t)}")
    _check_scale(arr, "时序特征包")
    invalid = ~np.isfinite(arr)
    if nodata is not None:
        invalid |= arr == float(nodata)
    values = np.where(invalid, np.nan, arr)
    n_t, height, width = arr.shape

    # 基础特征（min/max/mean/std/amplitude/first_last/harmonic）——复用 rs_v3
    from app.lib.geo_analysis import rs_v3

    base = rs_v3.temporal_features(values)

    flat = values.reshape(n_t, -1)
    features: Dict[str, np.ndarray] = dict(base.get("features", {}))
    disclosures = [
        "基础特征复用 rs_v3.temporal_features（harmonic 要求完整序列）",
        "NaN 传播：无效切片/像元不充当 0，不静默插值",
    ]

    # 分位数套件（nan-aware）
    for q in percentiles:
        if not (0.0 <= float(q) <= 1.0):
            raise ValueError(f"percentile {q!r} 须在 [0,1]")
        with np.errstate(invalid="ignore"):
            qv = np.nanpercentile(flat, float(q) * 100.0, axis=0)
        features[f"p{int(round(float(q) * 100))}"] = qv.reshape(height, width)

    # Sen 稳健斜率（有 T 上界；成对斜率中位数）
    if n_t >= 4 and n_t <= THEIL_SEN_MAX_T:
        t_norm = (t - t[0]) / 86400.0        # 天
        with np.errstate(invalid="ignore"):
            slope = _sen_slope(flat, t_norm)
        features["sen_slope"] = slope.reshape(height, width)
    else:
        features["sen_slope"] = np.full((height, width), np.nan)
        disclosures.append(
            f"T={n_t} 超出 THEIL_SEN_MAX_T={THEIL_SEN_MAX_T}（或 <4）——"
            "Sen 斜率诚实跳过（NaN）；无界成对斜率不做")

    # CUSUM 变点
    if compute_changepoint:
        change_index, magnitude = _cusum_changepoint(flat)
        features["change_index"] = change_index.reshape(height, width)
        features["cusum_magnitude"] = magnitude.reshape(height, width)
        disclosures.append(
            "CUSUM 变点：指数=1-based 切片序、量级=std 归一；无逐像元 "
            "bootstrap 显著性（诚实边界）")

    meta: Dict[str, Any] = {
        "n_time_slices": int(n_t),
        "grid": [int(height), int(width)],
        "percentiles": [float(q) for q in percentiles],
        "sen_slope_unit": "value/day",
        "sen_slope_applied": bool(4 <= n_t <= THEIL_SEN_MAX_T),
        "theil_sen_max_t": THEIL_SEN_MAX_T,
        "n_invalid_pixel_slices": int(invalid.sum()),
        "disclosures": disclosures,
    }
    return {"features": features, "meta": meta}
