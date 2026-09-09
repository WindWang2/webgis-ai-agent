"""Phenology Feature Engine（science-v5 W8）—— 物候特征引擎。

填补 ``rs_v3.temporal_features`` 显式披露的边界（"无物候模型拟合——
无双谐波、无 Savitzky-Golay、无物候期提取"）：

1. **有界缺口填充**：跨无效切片的线性插值，run ≤ ``max_gap`` 才填、
   更长 run 保持 NaN（逐像元计数披露——绝不静默长距外推）；
2. **Savitzky–Golay 平滑**：沿时间轴（窗口有界奇数、polyorder ≤ 3；
   只平滑填充后完整的序列——NaN 窗口会整体投毒，先填后平滑，挑战
   R0-#14）；
3. **双谐波联合 LS**：[1, t, sin ωt, cos ωt, sin 2ωt, cos 2ωt]（趋势与
   谐波联合估计，与 temporal_features 单谐波同哲学的推广）；
4. **阈值法物候期**：平滑序列的 SOS/EOS/LOS/峰值（阈值 = min +
   frac·amplitude；索引制，换算日期由调用方按时间轴解释）。

诚实边界：无项目专属类别；无物候模型的真实日期反演（索引制）；
quality=0 切片先于填充视为无效。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from app.lib.gis.scientific_errors import (
    DegenerateData,
)

PHENO_MIN_SLICES = 8                     # 双谐波（6 参数）+ 有效自由度
PHENO_WINDOW_MAX = 31                    # SG 窗口上界（切片数可再收紧）
PHENO_POLYORDER_MAX = 3

_DESIGN_PARAMS = 6                       # [1, t, sin, cos, sin2, cos2]


def _fill_short_gaps(
    values: np.ndarray, valid: np.ndarray, max_gap: int,
) -> tuple[np.ndarray, np.ndarray]:
    """向量化短缺口线性插值。

    values/valid: (T, N)；返回 (filled, filled_mask)——仅 run ≤ max_gap
    的无效位被插值；长缺口保持 NaN。跨序列边界（开头/结尾缺口）不外推。
    """
    t_len, n = values.shape
    idx = np.arange(t_len, dtype=float)[:, None]
    v = np.where(valid, values, np.nan)
    # 前向/后向最近有效索引（列向 cummax / 反向 cummax）
    with np.errstate(invalid="ignore"):
        prev_idx = np.where(valid, idx, -np.inf)
        prev_idx = np.maximum.accumulate(prev_idx, axis=0)
        next_idx = np.where(valid, idx, np.inf)
        next_idx = np.minimum.accumulate(next_idx[::-1], axis=0)[::-1]
    has_prev = np.isfinite(prev_idx)
    has_next = np.isfinite(next_idx)
    interpolable = (~valid) & has_prev & has_next
    # run 长度：无效位到前/后有效位的距离和
    run_len = (next_idx - prev_idx - 1.0)
    fillable = interpolable & (run_len <= float(max_gap))
    p = prev_idx.astype(float)
    span = np.maximum(next_idx - p, 1.0)
    w = (idx - p) / span
    p_i = np.where(has_prev, prev_idx, 0).astype(int)
    n_i = np.where(has_next, next_idx, t_len - 1).astype(int)
    prev_v = np.take_along_axis(np.where(valid, values, np.nan), p_i, axis=0)
    next_v = np.take_along_axis(np.where(valid, values, np.nan), n_i, axis=0)
    filled_vals = prev_v * (1.0 - w) + next_v * w
    filled = np.where(fillable, filled_vals, v)
    return filled, fillable


def _smooth_complete(
    filled: np.ndarray, complete: np.ndarray, window: int, polyorder: int,
) -> np.ndarray:
    """对完整序列（列）批量 SG 平滑；不完整列保持 NaN。"""
    from scipy.signal import savgol_filter

    out = np.full_like(filled, np.nan)
    if complete.any():
        series = filled[:, complete]
        out[:, complete] = savgol_filter(
            series, window, polyorder, axis=0, mode="interp")
    return out


def _double_harmonic_fit(
    smoothed: np.ndarray, complete: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """联合 LS：[1, t, sin ωt, cos ωt, sin 2ωt, cos 2ωt]（t 归一 [0,1]）。

    返回 (coef (6, N) NaN 填充, amp_total, phase_main)。
    """
    t_len, n = smoothed.shape
    coef = np.full((_DESIGN_PARAMS, n), np.nan)
    amp_total = np.full(n, np.nan)
    phase_main = np.full(n, np.nan)
    if not complete.any():
        return coef, amp_total, phase_main
    t_grid = np.arange(t_len, dtype=float) / max(t_len - 1, 1)
    design = np.stack([
        np.ones(t_len), t_grid,
        np.sin(2 * np.pi * t_grid), np.cos(2 * np.pi * t_grid),
        np.sin(4 * np.pi * t_grid), np.cos(4 * np.pi * t_grid),
    ], axis=1)                                          # (T, 6)
    if np.linalg.matrix_rank(design) < _DESIGN_PARAMS:
        return coef, amp_total, phase_main              # 秩亏 → 全 NaN（诚实）
    series = smoothed[:, complete]
    sol, *_ = np.linalg.lstsq(design, series, rcond=None)   # (6, n_ok)
    coef[:, complete] = sol
    amp_main = np.hypot(sol[2], sol[3])
    amp_second = np.hypot(sol[4], sol[5])
    amp_total[complete] = amp_main + amp_second
    phase_main[complete] = np.arctan2(sol[3], sol[2])
    return coef, amp_total, phase_main


def phenology_features(
    cube,
    *,
    window: int = 5,
    polyorder: int = 2,
    max_gap: int = 2,
    threshold_frac: float = 0.5,
) -> dict:
    """逐像元物候特征（索引制；时间语义由调用方按 ``cube.times_sec`` 解释）。

    返回 ``{"features": {...(H,W) arrays}, "meta": {...}}``：

    - ``sos_idx``/``eos_idx``：生长季起/止切片索引（首次/末次越过阈值）；
    - ``los``：EOS − SOS + 1（切片数；无有效季 → NaN）；
    - ``peak_value``/``peak_time``：平滑序列峰值幅值与位置；
    - ``amplitude``：平滑序列 max−min；``harmonic_amplitude``（双谐波
      幅值和）与 ``harmonic_phase_main``（主谐波相位）；
    - ``n_valid_slices``：参与统计的有效切片数（质量掩膜后）。

    Raises：切片数 < ``PHENO_MIN_SLICES`` → ``InsufficientSamples``（经
    DegenerateData 族语义——样本下限）；窗口/阶次非法 → ``DegenerateData``。
    """
    from app.lib.gis.scientific_errors import InsufficientSamples

    t_len = cube.n_slices
    if t_len < PHENO_MIN_SLICES:
        raise InsufficientSamples(
            f"物候特征至少需要 {PHENO_MIN_SLICES} 个时间切片（双谐波 "
            f"{_DESIGN_PARAMS} 参数 + 自由度），got {t_len}")
    polyorder = int(polyorder)
    if not 1 <= polyorder <= PHENO_POLYORDER_MAX:
        raise DegenerateData(
            f"polyorder 必须在 [1, {PHENO_POLYORDER_MAX}]，got {polyorder}")
    window = int(window)
    if window % 2 == 0:
        window += 1                                    # 偶数窗 +1（披露）
    win_cap = int(min(PHENO_WINDOW_MAX, t_len))
    if window > win_cap:
        window = win_cap if win_cap % 2 == 1 else win_cap - 1
    if window <= polyorder:
        raise DegenerateData(
            f"window ({window}) 必须 > polyorder ({polyorder})")
    if not 0.0 < float(threshold_frac) < 1.0:
        raise DegenerateData(
            f"threshold_frac 必须在 (0, 1)，got {threshold_frac!r}")

    valid = cube.valid_mask()                          # (T, H, W)
    values = np.where(valid, cube.stack, np.nan)
    flat = values.reshape(t_len, -1)                   # (T, N)
    flat_valid = np.isfinite(flat)

    filled, fillable = _fill_short_gaps(flat, flat_valid, int(max_gap))
    n_gap_filled = int(fillable.sum())
    filled = np.where(flat_valid | fillable, filled, np.nan)
    complete = np.isfinite(filled).all(axis=0)         # (N,) 完整序列
    n_excluded = int((~complete).sum())

    smoothed = _smooth_complete(filled, complete, window, polyorder)
    coef, amp_total, phase_main = _double_harmonic_fit(smoothed, complete)

    n_pix = flat.shape[1]
    sos = np.full(n_pix, np.nan)
    eos = np.full(n_pix, np.nan)
    los = np.full(n_pix, np.nan)
    peak_value = np.full(n_pix, np.nan)
    peak_time = np.full(n_pix, np.nan)
    amplitude = np.full(n_pix, np.nan)
    n_valid_slices = flat_valid.sum(axis=0).astype(float)

    if complete.any():
        sm = smoothed[:, complete]                     # (T, n_ok)
        vmax = np.max(sm, axis=0)
        vmin = np.min(sm, axis=0)
        amplitude[complete] = vmax - vmin
        peak_value[complete] = vmax
        peak_time[complete] = np.argmax(sm, axis=0).astype(float)
        thr = vmin + float(threshold_frac) * (vmax - vmin)
        above = sm >= thr[None, :]
        any_above = above.any(axis=0)
        # 首次/末次越过：argmax 作用于布尔（全 False → 0，用 any_above 掩）
        first = np.argmax(above, axis=0).astype(float)
        last = t_len - 1 - np.argmax(above[::-1], axis=0).astype(float)
        first = np.where(any_above, first, np.nan)
        last = np.where(any_above, last, np.nan)
        sos[complete] = first
        eos[complete] = last
        los[complete] = np.where(any_above, last - first + 1.0, np.nan)

    def _unflatten(a):
        return a.reshape(cube.stack.shape[1:])

    meta = {
        "algorithm": "temporal.phenology",
        "n_slices": int(t_len),
        "grid": [int(cube.stack.shape[1]), int(cube.stack.shape[2])],
        "smooth_window": int(window),
        "polyorder": int(polyorder),
        "max_gap": int(max_gap),
        "threshold_frac": float(threshold_frac),
        "harmonic_definition": (
            "联合 LS [1, t, sin/cos ωt, sin/cos 2ωt]（t 归一 [0,1]）——"
            "趋势与双谐波联合估计"),
        "sos_eos_definition": (
            f"平滑序列阈值法：thr = min + {threshold_frac}·(max−min)；"
            "索引制（0 起），日期换算由调用方按 times_sec 解释"),
        "n_pixels": int(n_pix),
        "n_gap_filled_cells": n_gap_filled,
        "n_excluded_pixels": n_excluded,
        "excluded_fraction": round(n_excluded / max(n_pix, 1), 6),
        "disclosures": [
            f"短缺口（run ≤ {max_gap}）线性插值 {n_gap_filled} 格-切片；"
            f"长缺口保持 NaN（不外推）——{n_excluded} 像元被排除",
            "SG 平滑只作用于填充后完整序列（NaN 窗口会整体投毒）",
            "物候期为切片索引制——非真实日期反演（诚实边界）",
            *list(cube.disclosures),
        ],
    }
    features = {
        "sos_idx": _unflatten(sos),
        "eos_idx": _unflatten(eos),
        "los": _unflatten(los),
        "peak_value": _unflatten(peak_value),
        "peak_time": _unflatten(peak_time),
        "amplitude": _unflatten(amplitude),
        "harmonic_amplitude": _unflatten(amp_total),
        "harmonic_phase_main": _unflatten(phase_main),
        "n_valid_slices": _unflatten(n_valid_slices),
    }
    return {"features": features, "meta": meta}


def temporal_anomaly(
    cube,
    *,
    baseline_slices: Optional[int] = None,
) -> dict:
    """逐像元时间异常/变化（z-score；全期气候态或前段基线）。

    - ``anomaly_last``：最后切片的 z = (x − mean)/std（std ddof=1，n<2
      → NaN 诚实缺省）；
    - ``change_mean``：后半段均值 − 前半段均值（``baseline_slices`` 显式
      指定前段长度时改用该前段 vs 其余）；``change_z``：Welch 近似
      z = diff/√(s1²/n1 + s2²/n2)（非显著性检验——近似披露）。
    """
    t_len = cube.n_slices
    if t_len < 4:
        from app.lib.gis.scientific_errors import InsufficientSamples

        raise InsufficientSamples(
            f"时间异常至少需要 4 个时间切片，got {t_len}")
    v = np.where(cube.valid_mask(), cube.stack, np.nan)
    clim = cube.climatology()
    mean, std = clim["mean"], clim["std"]
    with np.errstate(invalid="ignore", divide="ignore"):
        anomaly = (v[-1] - mean) / std
    anomaly = np.where(np.isfinite(std) & (std > 0), anomaly, np.nan)

    split = int(baseline_slices) if baseline_slices is not None \
        else t_len // 2
    if not 1 <= split < t_len:
        raise DegenerateData(
            f"baseline_slices ({baseline_slices!r}) 须在 [1, {t_len - 1}]")
    with np.errstate(invalid="ignore"):
        front = np.nanmean(v[:split], axis=0)
        back = np.nanmean(v[split:], axis=0)
        n1 = np.sum(np.isfinite(v[:split]), axis=0)
        n2 = np.sum(np.isfinite(v[split:]), axis=0)
        s1 = np.nanstd(v[:split], axis=0, ddof=1)
        s2 = np.nanstd(v[split:], axis=0, ddof=1)
        change = back - front
        denom = np.sqrt(s1 ** 2 / np.maximum(n1, 1)
                        + s2 ** 2 / np.maximum(n2, 1))
        change_z = np.where(denom > 0, change / denom, np.nan)

    meta = {
        "algorithm": "temporal.anomaly",
        "n_slices": int(t_len),
        "baseline_split": int(split),
        "anomaly_definition": "最后切片 z-score（全期气候态，std ddof=1）",
        "change_definition": (
            "后半段均值 − 前半段均值；change_z = Welch 近似"
            "（非正式显著性检验——近似语义已披露）"),
        "disclosures": [
            "z-score 分母为零/样本 <2 的像元 → NaN（诚实缺省）",
            *list(cube.disclosures),
        ],
    }
    features = {
        "anomaly_last": anomaly,
        "change_mean": change,
        "change_z": change_z,
        "front_mean": front,
        "back_mean": back,
    }
    return {"features": features, "meta": meta}
