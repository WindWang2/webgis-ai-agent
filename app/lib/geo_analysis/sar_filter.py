"""SAR 斑点噪声滤波 —— Lee / Refined Lee / Frost（Foundation V2 · A6）。

乘性斑点模型（Goodman）：x = R·n，n 均值 1、方差 1/ENL（等效视数）。
三个滤波器都是**局部统计 MMSE 家族**，公式在 docstring 逐字声明（固定、
确定性、无随机成分）：

- **Lee 1980**：窗口内 R̂ = m + k·(x−m)，k = var / (var + m²/ENL)。
  均匀区 k→小（强平滑），纹理区 k→1（保留 x）。
- **Refined Lee（Lee 1981 / Lopes 1990 边缘方向 MMSE）**：3×3 方向
  梯度核选边缘方向 → 7 个子窗（中心块 + 两侧带 + 两对角对）中选
  **局部方差最小**（最均匀、MSE 代理最小）的子窗 → 子窗内做 Lee MMSE。
  诚实声明：这是 7 子窗方向的 MMSE 实现，不是 Lopes 1990 的完整 MAP
  变体（后者含逐像元先验与阈值化）——近似披露进 meta。
- **Frost 1982**：R̂ = Σ w·x / Σ w，w = exp(−k·d)（d 为窗口内城市块
  距离，对称确定性），阻尼系数 k = D·(CV/CVF)²，CVF = 1/√ENL
  （充分发育斑点变异系数），D 为阻尼参数（默认 1，0.5-5）。

Foundation V3（additive，既有三滤波器行为不变）：

- **Kuan 1985（闭式 MMSE）**：R̂ = m + k·(x−m)，
  k = (1 − Cu²/Cv²) / (1 + Cu²)，Cu² = 1/ENL、Cv² = var/m²；
  Cv² ≤ Cu²（均匀窗）→ k 钳 0 → R̂ = m。
- **Gamma MAP（Lopes 1990 / Lee & Jurkevich 1994；Oliver & Quegan 1998）**：
  三分支——Cv ≤ Cu（均匀）→ R̂ = m；Cv > √2·Cu（点目标）→ R̂ = x；
  其余按 gamma 先验形状参数 α = (1+Cu²)/(Cv²−Cu²) 解 MAP 方程
  α·R²/m + (N−α+1)·R − N·x = 0（N=ENL）：闭式正根初始化后做
  **Newton 迭代** R̂←R̂−f(R̂)/f'(R̂)，迭代上限 max_iterations（披露
  iterations_used），发散/越界像元冻结在上一迭代（确定性）。

诚实边界：

- ENL 显式参数优先；未提供时用**矩估计法**从整图估计
  （ENL = mean²/var，均匀场景假设）——估计事实进 meta（enl_source）；
- 滤波器假定输入为**线性强度**（非负、非 dB）；负值输入抛
  UnsupportedMethod（ENL 估计与 CV 都对 dB 输入无意义）；
- nodata/NaN 感知：窗口统计只在有效像元上计算，全无效窗口 → NaN
  （不是 any-NaN 即 NaN 的粗暴语义，披露进 meta）；
- 规模守卫：H·W ≤ 4096×4096（16M 像元）——先估算后分配，超限抛
  ``ResourceScaleMismatch``（与 sar_temporal 的栅格闸同源）；
- 窗口 ∈ {3,5,7}（奇数）；窗口统计边界模式 scipy 'reflect'。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
from scipy.ndimage import correlate, uniform_filter

from app.lib.gis.scientific_errors import (
    DegenerateData,
    ResourceScaleMismatch,
    UnsupportedMethod,
)

__all__ = [
    "SPECKLE_FILTER_METHODS",
    "SPECKLE_WINDOW_SIZES",
    "SPECKLE_SCALE_LIMIT_PIXELS",
    "GAMMA_MAP_MAX_ITERATIONS",
    "speckle_filter",
    "gamma_map_filter",
    "kuan_filter",
]

SPECKLE_FILTER_METHODS = ("lee", "refined_lee", "frost", "gamma_map", "kuan")
SPECKLE_WINDOW_SIZES = (3, 5, 7)
SPECKLE_SCALE_LIMIT_PIXELS = 4096 * 4096   # 与 sar_temporal 栅格闸一致（16M）

_ENL_EPS = 1e-12
_FROST_DAMPING_RANGE = (0.5, 5.0)
_GAMMA_MAP_TOL = 1e-3                  # Newton 迭代相对收敛容差（|ΔR|/m）
GAMMA_MAP_MAX_ITERATIONS = 10          # Newton 迭代上限（契约默认，可调）


# ── 公共守卫 ─────────────────────────────────────────────────────────

def _check_grid_scale(arr: np.ndarray) -> None:
    if arr.ndim != 2:
        raise ValueError(
            f"SAR 斑点滤波输入必须是 2D 数组，got ndim={arr.ndim}")
    h, w = arr.shape
    cells = int(h) * int(w)
    if cells > SPECKLE_SCALE_LIMIT_PIXELS:
        raise ResourceScaleMismatch(
            f"SAR 斑点滤波网格规模超限：H×W={h}×{w}={cells} 像元"
            f"（≤{SPECKLE_SCALE_LIMIT_PIXELS}）",
            estimated=f"{cells * 8 / 1e6:.1f} MB float64 ({h}×{w}×8B)",
            limit=f"H·W≤{SPECKLE_SCALE_LIMIT_PIXELS}",
            correction_hint="分块（瓦片）滤波后拼接",
        )


def _check_window(window: int) -> int:
    if window not in SPECKLE_WINDOW_SIZES:
        raise ValueError(
            f"window 必须是 {list(SPECKLE_WINDOW_SIZES)} 之一的奇数窗口，"
            f"got {window!r}")
    return int(window)


def _resolve_enl(
    arr: np.ndarray, valid: np.ndarray, enl: Optional[float],
) -> Tuple[float, str]:
    """ENL 解析：显式参数优先；缺失则矩估计（ENL = mean²/var）。"""
    if enl is not None:
        enl_f = float(enl)
        if not (np.isfinite(enl_f) and enl_f > 0):
            raise ValueError(
                f"enl 必须为正有限数（等效视数），got {enl!r}")
        return enl_f, "explicit"

    vals = arr[valid]
    if vals.size == 0:
        raise DegenerateData(
            "无有效像元：ENL 无法估计，滤波不能进行",
            correction_hint="检查 nodata 设置或输入网格")
    if (vals < 0).any():
        raise UnsupportedMethod(
            "ENL 矩估计检测到负值——斑点滤波假定线性强度输入（非负）；"
            "输入疑似 dB 对数域",
            correction_hint="先做线性功率定标（见 sar.radiometric_calibration），"
                            "或显式提供 enl 参数跳过估计",
        )
    mean = float(vals.mean())
    var = float(vals.var())          # 总体方差（ddof=0），与窗口统计一致
    if var <= _ENL_EPS:
        raise DegenerateData(
            "整图方差为 0（常数场）——ENL = mean²/var 无定义，矩估计退化",
            correction_hint="常数场无斑点可滤；或显式提供 enl 参数",
        )
    return (mean * mean) / var, "estimated"


def _window_stats(
    filled: np.ndarray, valid_f: np.ndarray, size: int
) -> Tuple[np.ndarray, np.ndarray]:
    """nodata 感知的局部 mean/var（uniform_filter 反射边界）。

    filled = 无效处为 0 的平面；valid_f = 有效掩膜 float。
    mean = Σx/n、var = Σx²/n − mean²（总体方差 ddof=0）；n=0 → NaN。
    """
    s = uniform_filter(filled, size=size, mode="reflect")
    n = uniform_filter(valid_f, size=size, mode="reflect")
    s2 = uniform_filter(filled * filled, size=size, mode="reflect")
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = s / n
        var = s2 / n - mean * mean
    var = np.where(n > 0, var, np.nan)
    mean = np.where(n > 0, mean, np.nan)
    var = np.maximum(var, 0.0)       # 数值误差可致极小负值 → 钳 0（仅方差）
    return mean, var


# ── Lee 1980 ─────────────────────────────────────────────────────────

def _lee_mmse(
    x: np.ndarray, mean: np.ndarray, var: np.ndarray, enl: float
) -> np.ndarray:
    """R̂ = m + k·(x−m)，k = var/(var + m²/ENL)；窗口无效（m=NaN）→ NaN。"""
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = var + (mean * mean) / enl
        safe_denom = np.where(denom > 0, denom, 1.0)
        k = np.where(denom > 0, var / safe_denom, 0.0)
    out = mean + k * (x - mean)
    return np.where(np.isfinite(mean), out, np.nan)


def _filter_lee(
    x: np.ndarray, filled: np.ndarray, valid_f: np.ndarray,
    window: int, enl: float,
) -> np.ndarray:
    mean, var = _window_stats(filled, valid_f, window)
    return _lee_mmse(x, mean, var, enl)


# ── Refined Lee（Lee 1981 / Lopes 1990 边缘方向 7 子窗）──────────────

# 4 个 3×3 方向梯度核（|响应| 最大者定边缘朝向；序即平局优先序）。
_EDGE_KERNELS: Tuple[Tuple[str, np.ndarray], ...] = (
    ("grad_0", np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=float)),
    ("grad_90", np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=float)),
    ("grad_45", np.array([[0, 1, 2], [-1, 0, 1], [-2, -1, 0]], dtype=float)),
    ("grad_135", np.array([[-2, -1, 0], [-1, 0, 1], [0, 1, 2]], dtype=float)),
)


def _subwindow_masks(size: int) -> Dict[str, np.ndarray]:
    """7 个子窗掩膜（1=纳入统计）：中心块 + 两侧带（0/90°）+ 两对角对。

    size=5 为经典布局；size∈{3,7} 同构缩放（侧带 = 边缘半窗带，
    中心块 = 去带块）。size=3 时子窗退化（诚实披露：建议 5/7）。
    """
    n = size
    half_band = (n - 3) // 2          # 5→1, 7→2；3→0（侧带 = 单行/列）
    masks: Dict[str, np.ndarray] = {}

    center = np.zeros((n, n), dtype=float)
    lo, hi = half_band, n - 1 - half_band
    if lo > hi:                       # size=3：中心块退化为单像元
        lo = hi = n // 2
    center[lo:hi + 1, lo:hi + 1] = 1.0
    masks["center"] = center

    top = np.zeros((n, n), dtype=float)
    top[:half_band + 1, :] = 1.0
    masks["top"] = top
    bottom = np.zeros((n, n), dtype=float)
    bottom[n - 1 - half_band:, :] = 1.0
    masks["bottom"] = bottom
    left = np.zeros((n, n), dtype=float)
    left[:, :half_band + 1] = 1.0
    masks["left"] = left
    right = np.zeros((n, n), dtype=float)
    right[:, n - 1 - half_band:] = 1.0
    masks["right"] = right

    i, j = np.mgrid[0:n, 0:n]
    # 45° 反对角分侧三角（含两翼）：diag_45 取反对角两侧；
    # 135° 主对角分侧三角：diag_135 取主对角两侧。
    masks["diag_45"] = ((i - j <= -half_band - 1)
                        | (i - j >= half_band + 1)).astype(float)
    masks["diag_135"] = ((i + j <= n - 1 - half_band - 1)
                         | (i + j >= n - 1 + half_band + 1)).astype(float)
    return masks


def _subwindow_stats(
    filled: np.ndarray, valid_f: np.ndarray, mask: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """一个子窗掩膜的 nodata 感知局部 mean/var（correlate 实现卷积求和）。

    纳入像元 < 掩膜 1/4 时视为跨边缘污染（NaN，不参与选择——保守回退）。
    """
    mask_sum = float(mask.sum())
    s = correlate(filled, mask, mode="reflect")
    n = correlate(valid_f, mask, mode="reflect")
    s2 = correlate(filled * filled, mask, mode="reflect")
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = s / n
        var = s2 / n - mean * mean
    ok = n >= max(1.0, mask_sum * 0.25)
    mean = np.where(ok, mean, np.nan)
    var = np.where(ok, np.maximum(var, 0.0), np.nan)
    return mean, var


def _filter_refined_lee(
    x: np.ndarray, filled: np.ndarray, valid_f: np.ndarray,
    window: int, enl: float,
) -> np.ndarray:
    """边缘方向 7 子窗选择 + 子窗内 Lee MMSE（全向量化、确定性）。

    选择规则：4 个方向梯度 |响应| 逐像元最大者定边缘朝向（argmax 首遇
    平局胜，序 = grad_0/90/45/135）→ 该朝向的候选对中取 **MSE 代理**
    最小的子窗：score = var + (x − m)²（子窗方差 + 中心像元偏差²——
    纯噪声侧/正确边缘侧都能胜出；NaN 视为劣；平局前者胜，确定性）：
    垂直边缘 → left/right，水平边缘 → top/bottom，对角 → 对角对/中心。
    """
    masks = _subwindow_masks(window)
    stats = {name: _subwindow_stats(filled, valid_f, m)
             for name, m in masks.items()}
    mean_c, var_c = stats["center"]

    # 候选对（边缘朝向 → 两个对侧子窗）。
    pair_for_direction = {
        0: ("left", "right"),      # grad_0 最大 → 垂直边缘
        1: ("top", "bottom"),      # grad_90 最大 → 水平边缘
        2: ("diag_45", "center"),  # 对角：三角对 + 中心兜底
        3: ("diag_135", "center"),
    }

    grads = [np.abs(correlate(filled, k, mode="reflect"))
             for _, k in _EDGE_KERNELS]
    direction = np.argmax(np.stack(grads, axis=0), axis=0).astype(np.int8)

    def score_plane(name: str) -> np.ndarray:
        m_i, v_i = stats[name]
        with np.errstate(invalid="ignore"):
            s = v_i + (x - m_i) ** 2
        return np.where(np.isfinite(s), s, np.inf)

    sel_m = np.full_like(filled, np.nan)
    sel_v = np.full_like(filled, np.nan)
    for d_idx, (a, b) in pair_for_direction.items():
        score_a = score_plane(a)
        score_b = score_plane(b)
        a_better = score_a <= score_b       # 平局 a 胜（确定性）
        ma, va = stats[a]
        mb, vb = stats[b]
        m_pair = np.where(a_better, ma, mb)
        v_pair = np.where(a_better, va, vb)
        take = direction == d_idx
        sel_m = np.where(take, m_pair, sel_m)
        sel_v = np.where(take, v_pair, sel_v)

    # 所选子窗退化（NaN）→ 回退中心块
    fb = np.isnan(sel_m) | np.isnan(sel_v)
    sel_m = np.where(fb, mean_c, sel_m)
    sel_v = np.where(fb, var_c, sel_v)

    return _lee_mmse(x, sel_m, sel_v, enl)


# ── Frost 1982 ───────────────────────────────────────────────────────

def _shift_plane(plane: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """邻域偏移取值（越界为 0；配合有效掩膜实现边界外圈不计入）。

    Frost 窗口权重按**有效像元归一**（Σw 只计有效像元）；边界处越界
    像元不参与——与窗口统计的 'reflect' 不同，此差异诚实披露进 meta。
    """
    h, w = plane.shape
    out = np.zeros_like(plane)
    ys_src = slice(max(0, dy), min(h, h + dy))
    ys_dst = slice(max(0, -dy), min(h, h - dy))
    xs_src = slice(max(0, dx), min(w, w + dx))
    xs_dst = slice(max(0, -dx), min(w, w - dx))
    out[ys_dst, xs_dst] = plane[ys_src, xs_src]
    return out


def _filter_frost(
    filled: np.ndarray, valid_f: np.ndarray, window: int,
    enl: float, damping: float,
) -> np.ndarray:
    """R̂ = Σ w·x / Σ w，w = exp(−k·d)，k = D·(CV/CVF)²，CVF = 1/√ENL。

    d = 窗口内城市块距离（对称确定性）；逐偏移向量化（≤49 个偏移平面）。
    """
    mean, var = _window_stats(filled, valid_f, window)
    with np.errstate(divide="ignore", invalid="ignore"):
        cv = np.sqrt(var) / mean
    cvf = 1.0 / np.sqrt(enl)
    cv_ratio = np.where(np.isfinite(cv), np.maximum(cv, 0.0) / cvf, 0.0)
    k_plane = damping * cv_ratio * cv_ratio     # 阻尼系数平面（逐像元）
    # mean 无效/≤0 → 无斑点语义 → k=0；输出仍按窗口有效性掩为 NaN。
    k_plane = np.where(np.isfinite(mean) & (mean > 0), k_plane, 0.0)

    half = window // 2
    num = np.zeros_like(filled)
    den = np.zeros_like(filled)
    for dy in range(-half, half + 1):
        for dx in range(-half, half + 1):
            d = abs(dy) + abs(dx)               # 城市块距离
            weight = np.exp(-k_plane * d)
            num += weight * _shift_plane(filled, dy, dx) \
                * _shift_plane(valid_f, dy, dx)
            den += weight * _shift_plane(valid_f, dy, dx)
    out = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
    return np.where(np.isfinite(mean), out, np.nan)


# ── Kuan 1985（闭式 MMSE）────────────────────────────────────────────

def _filter_kuan(
    x: np.ndarray, filled: np.ndarray, valid_f: np.ndarray,
    window: int, enl: float,
) -> np.ndarray:
    """R̂ = m + k·(x−m)，k = (1 − Cu²/Cv²)/(1 + Cu²)，Cu² = 1/ENL。

    Cv² = var/m²；Cv² ≤ Cu²（均匀窗）→ k ≤ 0 钳 0 → R̂ = m（Kuan 分支）。
    k 数值上钳 [0, 1]（理论范围；数值误差保护）。
    """
    mean, var = _window_stats(filled, valid_f, window)
    cu2 = 1.0 / enl
    with np.errstate(divide="ignore", invalid="ignore"):
        cv2 = var / (mean * mean)
        k = (1.0 - cu2 / cv2) / (1.0 + cu2)
    k = np.where(np.isfinite(k), np.clip(k, 0.0, 1.0), 0.0)
    out = mean + k * (x - mean)
    return np.where(np.isfinite(mean), out, np.nan)


# ── Gamma MAP（Lopes 1990 / Lee & Jurkevich 1994）─────────────────────

def _filter_gamma_map(
    x: np.ndarray, filled: np.ndarray, valid_f: np.ndarray,
    window: int, enl: float, max_iterations: int,
) -> Tuple[np.ndarray, int]:
    """Gamma MAP：均匀/点目标两分支 + MAP 方程 Newton 迭代（确定性）。

    三分支（Cu²=1/N、Cv²=var/m²，N=ENL）：

    - Cv ≤ Cu（均匀窗）→ R̂ = m；
    - Cv > √2·Cu（点目标）→ R̂ = x（保留，不平滑强散射体）；
    - 否则 α = (1+Cu²)/(Cv²−Cu²)，MAP 方程
      f(R) = α·R²/m̄ + (N−α+1)·R − N·x = 0：
      闭式正根 R₀ = [(α−N−1)m̄ + √((α−N−1)²m̄² + 4α·N·m̄·x)]/(2α)
      初始化 → Newton 迭代 R̂←R̂−f(R̂)/f'(R̂)，相对增量 |ΔR|/(m̄+ε)<tol
      收敛；上限 max_iterations（返回全局实际迭代轮数 iterations_used）；
      发散/非有限像元冻结上一迭代值（确定性，无随机成分）。
    """
    mean, var = _window_stats(filled, valid_f, window)
    n_looks = enl
    cu2 = 1.0 / n_looks
    with np.errstate(divide="ignore", invalid="ignore"):
        cv2 = var / (mean * mean)

    r_est = np.array(mean, dtype=float, copy=True)
    iterations_used = 0

    homogeneous = np.isfinite(mean) & (cv2 <= cu2)
    point_target = np.isfinite(mean) & (cv2 > 2.0 * cu2)
    active = np.isfinite(mean) & ~homogeneous & ~point_target

    if active.any():
        m_safe = np.where(active, np.maximum(mean, _ENL_EPS), 1.0)
        alpha = np.where(active, (1.0 + cu2) / np.maximum(cv2 - cu2, _ENL_EPS),
                         1.0)
        a_m1 = alpha - n_looks - 1.0
        disc = a_m1 * a_m1 * m_safe * m_safe \
            + 4.0 * alpha * n_looks * m_safe * np.maximum(x, 0.0)
        with np.errstate(invalid="ignore"):
            r0 = (a_m1 * m_safe + np.sqrt(np.maximum(disc, 0.0))) \
                / (2.0 * alpha)
        r_est = np.where(active, r0, r_est)

        for _ in range(max_iterations):
            iterations_used += 1
            f_val = alpha * r_est * r_est / m_safe \
                + (n_looks - alpha + 1.0) * r_est \
                - n_looks * np.maximum(x, 0.0)
            f_der = 2.0 * alpha * r_est / m_safe + (n_looks - alpha + 1.0)
            with np.errstate(divide="ignore", invalid="ignore"):
                step = np.where(f_der != 0, f_val / np.where(f_der != 0, f_der, 1.0),
                                0.0)
            r_next = r_est - step
            # 发散/越界 → 冻结（确定性：保持上一迭代值）
            ok = active & np.isfinite(r_next) & (r_next > 0)
            with np.errstate(invalid="ignore"):
                moved = np.where(np.isfinite(mean), np.abs(r_next - r_est)
                                 / (np.abs(mean) + _ENL_EPS), np.inf)
            r_est = np.where(ok, r_next, r_est)
            if bool((moved[active] < _GAMMA_MAP_TOL).all()):
                break

    out = np.where(homogeneous, mean, r_est)
    out = np.where(point_target, x, out)
    return np.where(np.isfinite(mean), out, np.nan), iterations_used


# ── 主入口 ───────────────────────────────────────────────────────────

def speckle_filter(
    arr: np.ndarray,
    method: str = "lee",
    *,
    window: int = 3,
    enl: Optional[float] = None,
    damping: float = 1.0,
    nodata: Optional[float] = None,
    max_iterations: int = GAMMA_MAP_MAX_ITERATIONS,
) -> Dict[str, object]:
    """SAR 斑点滤波（Lee / Refined Lee / Frost / Gamma MAP / Kuan；公式见模块 docstring）。

    Args:
        arr: 2D 线性强度栅格（非负；dB 输入被负值守卫拒绝）。
        method: ``lee`` / ``refined_lee`` / ``frost`` / ``gamma_map`` /
            ``kuan``（V3 additive：后两者既有行为不变）。
        window: 奇数窗口 ∈ {3,5,7}。
        enl: 等效视数（显式优先）；缺省用整图矩估计 ENL=mean²/var
            （均匀场景假设，enl_source="estimated" 披露）。
        damping: Frost 阻尼参数 D（0.5-5，默认 1；仅 frost 使用）。
        nodata: 标量哨兵值（如 -9999）；NaN/Inf 自动视为无效。
        max_iterations: Gamma MAP Newton 迭代上限（仅 gamma_map 使用；
            默认 10；iterations_used 披露）。

    Returns:
        dict: array（全无效窗口 → NaN）、method、window、enl、
        enl_source（explicit/estimated）、meta（公式 + 诚实边界披露）。
    """
    method_key = (method or "lee").lower()
    if method_key not in SPECKLE_FILTER_METHODS:
        raise ValueError(
            f"unsupported speckle filter '{method}'; "
            f"valid: {list(SPECKLE_FILTER_METHODS)}")
    window = _check_window(int(window))
    damping_f = float(damping)
    if method_key == "frost" and not (
            _FROST_DAMPING_RANGE[0] - 1e-12
            <= damping_f <= _FROST_DAMPING_RANGE[1] + 1e-12):
        raise ValueError(
            f"frost damping 必须在 {_FROST_DAMPING_RANGE} 内，got {damping!r}")
    max_iter = int(max_iterations)
    if method_key == "gamma_map" and not (1 <= max_iter <= 50):
        raise ValueError(
            f"gamma_map max_iterations 必须在 [1, 50] 内，got {max_iterations!r}")

    plane = np.asarray(arr, dtype=float)
    _check_grid_scale(plane)

    valid = np.isfinite(plane)
    if nodata is not None:
        valid &= plane != float(nodata)
    enl_val, enl_src = _resolve_enl(plane, valid, enl)

    filled = np.where(valid, plane, 0.0)
    valid_f = valid.astype(float)

    iterations_used: Optional[int] = None
    if method_key == "lee":
        out = _filter_lee(plane, filled, valid_f, window, enl_val)
        formula = "R = m + k·(x−m), k = var/(var + m²/ENL)  [Lee 1980]"
    elif method_key == "refined_lee":
        out = _filter_refined_lee(plane, filled, valid_f, window, enl_val)
        formula = ("3×3 方向梯度选边 + 7 子窗 MSE 代理选择 + 子窗内 Lee MMSE "
                   "[Lee 1981 / Lopes 1990 近似实现]")
    elif method_key == "frost":
        out = _filter_frost(filled, valid_f, window, enl_val, damping_f)
        formula = ("R = Σ exp(−k·d)·x / Σ exp(−k·d), "
                   "k = D·(CV·√ENL)², d = 城市块距离  [Frost 1982]")
    elif method_key == "gamma_map":
        out, iterations_used = _filter_gamma_map(
            plane, filled, valid_f, window, enl_val, max_iter)
        formula = ("Gamma MAP 三分支（均匀→m / 点目标→x / MAP 方程闭式根 + "
                   f"Newton 迭代 ≤{max_iter} 轮，tol={_GAMMA_MAP_TOL:g}）"
                   "  [Lopes 1990 / Lee & Jurkevich 1994]")
    else:  # kuan
        out = _filter_kuan(plane, filled, valid_f, window, enl_val)
        formula = ("R = m + k·(x−m), k = (1 − Cu²/Cv²)/(1 + Cu²), "
                   "Cu² = 1/ENL  [Kuan 1985 闭式 MMSE]")

    enl_disclosure = ("显式参数" if enl_src == "explicit"
                      else "整图矩估计 mean²/var（均匀假设）")
    disclosure = (
        "斑点为乘性噪声假设（ENL=" + enl_disclosure + "）；"
        "nodata 感知窗口统计：无效像元不参与统计、无效像元输出 NaN、"
        "全无效窗口 → NaN；"
        "refined_lee 为 7 子窗方向 MMSE 近似（非完整 MAP 变体）；"
        "dB 输入不适用（先做线性定标）")
    if method_key == "gamma_map":
        disclosure += ("；gamma_map 的 Newton 迭代以闭式正根初始化、"
                       "发散像元冻结上一迭代（确定性），迭代上限与实际轮数披露")
    meta: Dict[str, object] = {
        "method": method_key,
        "window": window,
        "damping": damping_f if method_key == "frost" else None,
        "max_iterations": max_iter if method_key == "gamma_map" else None,
        "iterations_used": iterations_used,
        "formula": formula,
        "disclosure": disclosure,
        "boundary": "uniform/correlate reflect（frost 有效集归一 zero-pad）",
    }
    return {
        "array": np.asarray(out, dtype=float),
        "method": method_key,
        "window": window,
        "enl": float(enl_val),
        "enl_source": enl_src,
        "meta": meta,
    }


def gamma_map_filter(
    arr: np.ndarray,
    *,
    window: int = 3,
    enl: Optional[float] = None,
    nodata: Optional[float] = None,
    max_iterations: int = GAMMA_MAP_MAX_ITERATIONS,
) -> Dict[str, object]:
    """Gamma MAP 斑点滤波（``speckle_filter(method="gamma_map")`` 门面）。"""
    return speckle_filter(
        arr, "gamma_map", window=window, enl=enl, nodata=nodata,
        max_iterations=max_iterations)


def kuan_filter(
    arr: np.ndarray,
    *,
    window: int = 3,
    enl: Optional[float] = None,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """Kuan 闭式 MMSE 斑点滤波（``speckle_filter(method="kuan")`` 门面）。"""
    return speckle_filter(arr, "kuan", window=window, enl=enl, nodata=nodata)
