"""SAR V3 批次 —— 多时相斑点抑制 / 复数相干性 / 地形几何校正 / ENL 图。

Foundation V3（SAR 批次）。全部纯 numpy/scipy、确定性（无随机成分）、
规模守卫先估算后分配。诚实近似**全部显式披露**（meta + descriptor
limitations，绝不静默）：

- ``multitemporal_speckle``：强度域 MT-Lee——逐切片对「时序均值」与
  「空域 Lee 估计」做逐像元逆方差加权（权重来自每像元方差代理比，
  无随机种子）。**不是** Quegan 谱域多时相滤波（需要 SLC 复数相干
  分解）；栈深 ≤24（复用 ``SAR_SCALE_LIMIT_T``）、需 ≥3 期切片；
- ``coherence_estimate``：复数相干性
  γ = |Σ a·b*| / √(Σ|a|²Σ|b|²)（窗口估计，nodata 感知）。输入为
  **双通道复 SLG**（(re, im) 二元组或 complex 数组）；imag 全零 /
  纯实数输入 → 类型化拒绝（仅凭强度无相位，相干性不物理）；
  EXPERIMENTAL（无轨道元数据/配准质量披露）；
- ``radiometric_terrain_correction``：RTC gamma 平坦化（Small 2011）
  γ_flat = σ⁰·cosθi/cosθl；本地入射角由 DEM Horn 梯度导出
  （cos θl = cosθi·cosα + sinθi·sinα·cos(β−β_r)，α=坡度、β=下坡
  方位角、β_r=雷达视线方位角）；θl ≤ 0 或 ≥ 90（叠掩/阴影）→ nodata
  （计数披露）；range-only 几何简化（无轨道元数据）；
- ``layover_shadow_mask``：同一几何的分类版——{0=normal, 1=layover,
  2=shadow, 3=nodata} + 占比；layover 判定 = 面坡（cos(β−β_r)>0）
  且 α > θi；shadow = cosθl ≤ 0（背坡超掠射角）；
- ``enl_map``：滑窗 ENL（mean²/var，nan 感知）+ 全局 ENL；
  非均匀窗口把纹理计入方差 → ENL 被低估（估计偏差，披露）。

约定（进 meta，测试锁定）：北朝上网格（行 0=北、列 0=西）；方位角
一律顺时针自北；nodata/NaN 感知（无效像元输出 NaN/3 类）；网格规模
H·W ≤ 4096×4096（与 sar_filter/sar_temporal 同源闸）。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple, Union

import numpy as np
from scipy.ndimage import correlate, uniform_filter

from app.lib.geo_analysis.sar_calibration import _incidence_plane
from app.lib.geo_analysis.sar_filter import (
    SPECKLE_SCALE_LIMIT_PIXELS,
    SPECKLE_WINDOW_SIZES,
    _lee_mmse,
    _resolve_enl,
    _window_stats,
)
from app.lib.geo_analysis.sar_temporal import (
    SAR_SCALE_LIMIT_PIXELS,
    SAR_SCALE_LIMIT_T,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    InsufficientSamples,
    InvalidUnits,
    MissingRequiredField,
    NoValidObservations,
    ResourceScaleMismatch,
    UnsupportedMethod,
)

__all__ = [
    "MT_SPECKLE_MIN_T",
    "COHERENCE_WINDOW_SIZES",
    "LAYOVER_SHADOW_CLASSES",
    "ENL_MAP_WINDOW_SIZES",
    "multitemporal_speckle",
    "coherence_estimate",
    "radiometric_terrain_correction",
    "layover_shadow_mask",
    "enl_map",
    "enl_confidence_interval",
]

ENL_EPS = 1e-12                          # ENL = mean²/var 的方差下限
_Z95 = 1.959963984540054                 # 双侧 95% 标准正态分位数
_COHERENCE_Z_CLIP = 1.0 - 1e-12          # arctanh 的 γ 上钳（避免 inf）
MT_SPECKLE_MIN_T = 3                     # MT-Lee 最少期数（诚实下限）
COHERENCE_WINDOW_SIZES = SPECKLE_WINDOW_SIZES
ENL_MAP_WINDOW_SIZES = SPECKLE_WINDOW_SIZES
LAYOVER_SHADOW_CLASSES = {
    0: "normal",
    1: "layover",
    2: "shadow",
    3: "nodata",
}

_HORNX = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=float)
_HORNY = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=float)


# ── 公共守卫 ─────────────────────────────────────────────────────────

def _check_plane(arr: np.ndarray, what: str) -> np.ndarray:
    """2D 平面入参校验 + 规模闸（先估算后分配）。"""
    plane = np.asarray(arr, dtype=float)
    if plane.ndim != 2:
        raise ValueError(f"{what} 必须是 2D 数组，got ndim={plane.ndim}")
    if plane.size > SPECKLE_SCALE_LIMIT_PIXELS:
        h, w = plane.shape
        raise ResourceScaleMismatch(
            f"{what} 网格规模超限：H×W={h}×{w}={plane.size} 像元"
            f"（≤{SPECKLE_SCALE_LIMIT_PIXELS}）",
            estimated=f"{plane.size * 8 / 1e6:.1f} MB float64",
            limit=f"H·W≤{SPECKLE_SCALE_LIMIT_PIXELS}",
            correction_hint="分块（瓦片）处理后拼接",
        )
    return plane


def _check_window(window: int, what: str = "window") -> int:
    w = int(window)
    if w not in SPECKLE_WINDOW_SIZES:
        raise ValueError(
            f"{what} 必须是 {list(SPECKLE_WINDOW_SIZES)} 之一的奇数窗口，"
            f"got {window!r}")
    return w


def _resolve_azimuth(radar_range_azimuth: Optional[float]) -> float:
    """雷达视线方位角守卫（度，[0, 360)；缺失 → MissingRequiredField）。"""
    if radar_range_azimuth is None:
        raise MissingRequiredField(
            "缺少 radar_range_azimuth——雷达视线方位角（度，[0,360)，"
            "顺时针自北、地面指向传感器）是显式必需参数（不虚构默认）",
            correction_hint="如 Sentinel-1 降轨 IW 视线 ≈ 270°（西）、升轨 ≈ 90°（东）")
    az = float(radar_range_azimuth)
    if not (np.isfinite(az) and 0.0 <= az < 360.0):
        raise InvalidUnits(
            f"radar_range_azimuth 必须在 [0, 360) 内（度），got {radar_range_azimuth!r}",
            correction_hint="提供度制方位角（顺时针自北；地面指向传感器）")
    return az


def _resolve_cell_size(cell_size: Optional[float]) -> float:
    if cell_size is None:
        raise MissingRequiredField(
            "缺少 cell_size——DEM 像元大小（米）是显式必需参数",
            correction_hint="提供与 DEM 网格一致的像元边长（米）")
    cs = float(cell_size)
    if not (np.isfinite(cs) and cs > 0):
        raise ValueError(f"cell_size 必须为正有限数（米），got {cell_size!r}")
    return cs


def _resolve_incidence(
    incidence_deg: Optional[float],
    incidence_map: Optional[np.ndarray],
    shape: Tuple[int, int],
) -> np.ndarray:
    theta = _incidence_plane(incidence_deg, incidence_map, shape)
    if theta is None:
        raise MissingRequiredField(
            "缺少入射角——RTC/几何分类需要本地入射角"
            "（incidence_deg 标量或 incidence_map 逐像元平面）",
            correction_hint="提供度制入射角（0-90 开区间）或逐像元 LUT")
    return theta


# ── 地形几何（Horn 梯度 → 坡度/坡向/本地入射角余弦）───────────────────

def _horn_gradient(dem: np.ndarray, cell_size: float) -> Tuple[np.ndarray, np.ndarray]:
    """Horn 3×3 梯度（北朝上约定）：gx=∂z/∂东、gy=∂z/∂北（米/米）。"""
    dz_dcol = correlate(dem, _HORNX, mode="reflect") / (8.0 * cell_size)
    dz_drow = correlate(dem, _HORNY, mode="reflect") / (8.0 * cell_size)
    gx = dz_dcol                       # 列 0=西、列增向东
    gy = -dz_drow                      # 行 0=北、行增向南 → 取负为北向
    return gx, gy


def _terrain_geometry(
    dem: np.ndarray,
    cell_size: float,
    theta_rad: np.ndarray,
    az_rad: float,
) -> Dict[str, np.ndarray]:
    """坡度 α / 下坡方位角 β / 本地入射角余弦（公式约定进 meta）。"""
    gx, gy = _horn_gradient(dem, cell_size)
    with np.errstate(invalid="ignore"):
        slope = np.arctan(np.hypot(gx, gy))
        # 下坡方位角（顺时针自北）：atan2(东分量, 北分量)，归一到 [0, 2π)
        beta = np.mod(np.arctan2(-gx, -gy), 2.0 * np.pi)
        cos_tl = (np.cos(theta_rad) * np.cos(slope)
                  + np.sin(theta_rad) * np.sin(slope)
                  * np.cos(beta - az_rad))
        # 面坡（下坡朝向雷达）：cos(β − β_r) > 0。
        facing = np.cos(beta - az_rad) > 0
    return {"slope": slope, "beta": beta, "cos_tl": cos_tl, "facing": facing}


_TERRAIN_CONVENTION = (
    "Horn 3×3 DEM 梯度；北朝上网格（行 0=北、列 0=西）；方位角顺时针自北；"
    "坡向 β = 下坡方位角；radar_range_azimuth = 地面指向传感器的水平方位角；"
    "range-only 几何简化（无轨道元数据/传感器位置/方位向分量，诚实披露）")


# ── 多时相斑点抑制（MT-Lee）──────────────────────────────────────────

def multitemporal_speckle(
    stack: np.ndarray,
    *,
    window: int = 3,
    enl: Optional[float] = None,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """强度域 MT-Lee：时序均值与空域 Lee 估计的逐像元逆方差加权。

    权重（每像元、每切片、确定性、无随机成分）：

    - σ²_temporal = Var_temp(t)/n_t（时序方差 / 有效期数——时序均值的
      噪声方差代理）；
    - σ²_spatial = k_t²·Var_t（Lee 残差方差代理，k_t 为该切片窗口 MMSE
      增益）；
    - w_temporal = σ²_spatial/(σ²_spatial + σ²_temporal)（逆方差）。

    诚实边界：**不是** Quegan 谱域多时相滤波（需 SLC 复数相干分解）；
    栈须已配准对齐；Temporal 方差把真实地物变化计入 → 权重自动偏向
    空域估计（保守，披露）；有效期数 <2 的像元回退纯空域 Lee。

    Args:
        stack: (T, H, W) 强度栈（非负；dB 被拒绝）。T ≥ 3
            （``MT_SPECKLE_MIN_T``）、T ≤ 24、H·W ≤ 4096²。
        window: 空域 Lee 窗口 ∈ {3,5,7}。
        enl: 等效视数（显式优先；缺省整图矩估计并披露）。
        nodata: 标量哨兵值（逐切片 + 时序统计共同感知）。

    Returns:
        dict: stack（(T,H,W) 滤波后；切片内无效像元 → NaN）、
        mean_temporal_weight、meta。
    """
    w = _check_window(window, "window")
    arr = np.asarray(stack, dtype=float)
    if arr.ndim != 3:
        raise ValueError(
            f"MT-Lee 栈必须是 (T, H, W) 三维，got ndim={arr.ndim}")
    t_n, h, w_n = arr.shape
    pixels = int(h) * int(w_n)
    if t_n > SAR_SCALE_LIMIT_T or pixels > SAR_SCALE_LIMIT_PIXELS:
        raise ResourceScaleMismatch(
            f"MT-Lee 栈规模超限：T={t_n}（≤{SAR_SCALE_LIMIT_T}），"
            f"H×W={h}×{w_n}={pixels}（≤{SAR_SCALE_LIMIT_PIXELS}）",
            estimated=f"{t_n * pixels * 8 / 1e6:.1f} MB float64",
            limit=f"T≤{SAR_SCALE_LIMIT_T}, H·W≤{SAR_SCALE_LIMIT_PIXELS}",
            correction_hint="按时间分批或空间分块后拼接",
        )
    if t_n < MT_SPECKLE_MIN_T:
        raise InsufficientSamples(
            f"MT-Lee 需要 ≥{MT_SPECKLE_MIN_T} 期切片（时序方差才有意义），"
            f"got T={t_n}",
            correction_hint="补齐时序切片，或用单切片 speckle_filter（lee）")

    valid = np.isfinite(arr)
    if nodata is not None:
        valid &= arr != float(nodata)
    if not valid.any():
        raise NoValidObservations(
            "MT-Lee：栈内无有效像元", correction_hint="检查 nodata 设置或输入栈")
    if (arr[valid] < 0).any():
        raise UnsupportedMethod(
            "MT-Lee 检测到负值——假定线性强度输入（非负）；输入疑似 dB 对数域",
            correction_hint="先做线性定标（见 sar.radiometric_calibration）")
    enl_val, enl_src = _resolve_enl(arr, valid, enl)

    filled = np.where(valid, arr, np.nan)
    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", RuntimeWarning)
        with np.errstate(invalid="ignore"):
            temp_mean = np.nanmean(filled, axis=0)
            temp_var = np.nanvar(filled, axis=0)     # 总体方差 ddof=0
    n_t = np.sum(valid, axis=0)

    out = np.full_like(arr, np.nan)
    weights_sum = 0.0
    weights_count = 0
    fallback_temporal = 0        # 时序不可用（n_t < 2）→ 纯空域像元数
    fallback_spatial = 0         # 空域窗口退化 → 纯时序像元数

    for idx in range(t_n):
        valid_f = valid[idx].astype(float)
        filled_t = np.where(valid[idx], arr[idx], 0.0)
        mean_t, var_t = _window_stats(filled_t, valid_f, w)
        est_spatial = _lee_mmse(arr[idx], mean_t, var_t, enl_val)
        with np.errstate(divide="ignore", invalid="ignore"):
            k_t = var_t / (var_t + (mean_t * mean_t) / enl_val)
            sig_sp = (k_t * k_t) * var_t
            sig_tm = temp_var / n_t
            denom = sig_sp + sig_tm
            # 双方差皆 0（ temporally 常数且窗口均匀）→ 两估计一致 → w=1；
            # 仅时序不可用（n_t<2）保持 NaN → 回退空域。
            w_temp = np.where(
                np.isfinite(sig_sp) & np.isfinite(sig_tm) & (n_t >= 2),
                np.where(denom > 0, sig_sp / np.where(denom > 0, denom, 1.0),
                         1.0),
                np.nan)
        # 回退：时序不可用（n_t<2）→ 纯空域（w=0）；空域窗口退化但时序
        # 可用 → 纯时序（w=1）。
        fb_spatial_mask = valid[idx] & ~np.isfinite(w_temp) & (n_t < 2)
        fb_temporal_mask = (valid[idx] & ~np.isfinite(est_spatial)
                            & np.isfinite(temp_mean) & np.isfinite(w_temp)
                            & (n_t >= 2))
        w_temp = np.where(fb_temporal_mask, 1.0,
                          np.where(fb_spatial_mask, 0.0, w_temp))
        combined = w_temp * temp_mean + (1.0 - w_temp) * est_spatial
        slice_out = np.where(
            valid[idx] & np.isfinite(combined), combined,
            np.where(valid[idx] & np.isfinite(temp_mean) & np.isfinite(w_temp),
                     w_temp * temp_mean, np.nan))
        out[idx] = slice_out

        sel = valid[idx] & np.isfinite(w_temp)
        weights_sum += float(np.sum(w_temp[sel]))
        weights_count += int(np.sum(sel))
        fallback_temporal += int(np.sum(fb_temporal_mask))
        fallback_spatial += int(np.sum(fb_spatial_mask & ~fb_temporal_mask))

    enl_disclosure = ("显式参数" if enl_src == "explicit"
                      else "整图矩估计 mean²/var（均匀假设）")
    disclosure = (
        "强度域 MT-Lee（**非** Quegan 谱域多时相滤波——后者需 SLC 复数相干"
        f"分解）；栈假定已配准对齐；ENL={enl_disclosure}；"
        "权重 = 逆方差（σ²_spatial=k²·Var 的 Lee 残差代理 vs "
        "σ²_temporal=Var_temp/n_t，代理近似披露）；有效期数 <2 的像元回退"
        "纯空域；时序方差计入真实地物变化 → 保守偏向空域（披露）")
    meta: Dict[str, object] = {
        "time_slices": int(t_n),
        "window": w,
        "enl": float(enl_val),
        "enl_source": enl_src,
        "mean_temporal_weight": (weights_sum / weights_count
                                 if weights_count else None),
        "pixels_fallback_temporal": fallback_temporal,
        "pixels_fallback_spatial": fallback_spatial,
        "min_valid_slices": int(np.min(n_t)),
        "pixels_no_valid_slice": int(np.sum(n_t == 0)),
        "formula": ("R̂ = w·mean_temp + (1−w)·Lee_t, "
                    "w = σ²_spatial/(σ²_spatial + σ²_temporal)"),
        "disclosure": disclosure,
    }
    return {
        "stack": out,
        "mean_temporal_weight": meta["mean_temporal_weight"],
        "meta": meta,
    }


# ── 复数相干性（EXPERIMENTAL）────────────────────────────────────────

def _as_complex(
    value: Union[np.ndarray, Tuple[np.ndarray, np.ndarray]], name: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """复 SLC 输入归一：(re, im) 二元组或 complex 数组 → (复平面, 有效掩膜)。

    强度-only（纯实数数组，或 imag 全零的 complex）→ UnsupportedMethod
    （相干性仅凭强度不物理——诚实拒绝）。
    """
    if isinstance(value, tuple) and len(value) == 2:
        re = np.asarray(value[0], dtype=float)
        im = np.asarray(value[1], dtype=float)
        if re.ndim != 2 or im.ndim != 2:
            raise ValueError(
                f"{name} 双通道须为 2D 数组，got re ndim={re.ndim}, "
                f"im ndim={im.ndim}")
        if re.shape != im.shape:
            raise ValueError(
                f"{name} re/im 形状不一致：{re.shape} vs {im.shape}")
        comp = re + 1j * im
    else:
        arr = np.asarray(value)
        if not np.iscomplexobj(arr):
            raise UnsupportedMethod(
                f"{name} 为纯实数（强度）数组——相干性需要复 SLC 相位"
                "（(re, im) 双通道或 complex 数组）；仅凭强度不物理",
                correction_hint="提供单视复数（SLC）的 re/im 双通道")
        comp = arr.astype(np.complex128)

    valid = np.isfinite(comp.real) & np.isfinite(comp.imag)
    if valid.any() and np.all(np.abs(comp.imag[valid]) == 0.0):
        raise UnsupportedMethod(
            f"{name} 的虚部全零（强度-only）——相干性需要非零相位信息",
            correction_hint="确认输入为 SLC 复数（re/im），而非强度/幅度")
    return comp, valid


def coherence_estimate(
    slc_pair_a: Union[np.ndarray, Tuple[np.ndarray, np.ndarray]],
    slc_pair_b: Union[np.ndarray, Tuple[np.ndarray, np.ndarray]],
    *,
    window: int = 5,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """复数相干性估计（窗口化）：γ = |Σ a·b*| / √(Σ|a|²·Σ|b|²)。

    通道契约：每历元为一个**双通道复 SLC**——``(re, im)`` 二元组
    （纯 JSON 工具传 re/im 嵌套列表）或 complex 数组；两历元同网格。
    nodata 感知：窗口内只在双方有效像元上累加；分母为 0 的窗口 → NaN。

    EXPERIMENTAL：无轨道元数据/配准质量输入，窗口估计有偏差（披露）；
    不输出干涉相位/解缠。

    R-4（审计 §6）：逐窗 95% 置信区间走 Fisher z 近似——
    z = atanh(γ)、SE ≈ 1/√(n_pairs−3)、γ CI = tanh(z ± 1.96·SE)
    （n_pairs ≤ 3 → SE 无定义 → CI NaN，诚实不虚构）。

    Returns:
        dict: gamma（相干性栅格，钳 [0,1]，超 1 计数披露）、
        valid_pairs（逐像元窗口有效对数）、gamma_ci95_low/high（逐窗
        95% CI 下/上界）、array（= gamma）、meta。
    """
    w = _check_window(window, "window")
    comp_a, valid_a = _as_complex(slc_pair_a, "slc_pair_a")
    comp_b, valid_b = _as_complex(slc_pair_b, "slc_pair_b")
    if comp_a.shape != comp_b.shape:
        raise ValueError(
            f"两历元形状不一致：{comp_a.shape} vs {comp_b.shape}")
    _check_plane(comp_a.real, "coherence 网格")   # 网格规模闸（先估算后分配）

    valid = valid_a & valid_b
    if nodata is not None:
        valid &= (comp_a.real != float(nodata)) \
            & (comp_b.real != float(nodata))
    if not valid.any():
        raise NoValidObservations(
            "相干性估计：无公共有效像元",
            correction_hint="检查 nodata 设置或两历元输入")
    valid_f = valid.astype(float)

    cross = comp_a * np.conj(comp_b)
    num_re = uniform_filter(cross.real * valid_f, size=w, mode="reflect")
    num_im = uniform_filter(cross.imag * valid_f, size=w, mode="reflect")
    pow_a = uniform_filter((comp_a.real ** 2 + comp_a.imag ** 2) * valid_f,
                           size=w, mode="reflect")
    pow_b = uniform_filter((comp_b.real ** 2 + comp_b.imag ** 2) * valid_f,
                           size=w, mode="reflect")
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = np.sqrt(pow_a * pow_b)
        gamma = np.hypot(num_re, num_im) / np.where(denom > 0, denom, np.nan)
    gamma = np.where(denom > 0, gamma, np.nan)

    over = np.isfinite(gamma) & (gamma > 1.0 + 1e-12)
    clamped = int(np.sum(over))
    gamma = np.clip(gamma, 0.0, 1.0)

    # uniform_filter = 窗口均值 → 有效对占比 × w² = 有效对计数
    # （reflect 边界把镜像像元计入——边界计数偏高，披露）。
    n_pairs = uniform_filter(valid_f, size=w, mode="reflect") * float(w * w)

    # R-4：Fisher z 95% CI（z=atanh(γ)、SE≈1/√(n_pairs−3)）。γ 钳到
    # [0, 1−ε] 再 atanh（γ=1 自相干 → 大而有限的 z，CI 收敛到 [≈1, 1]）；
    # n_pairs ≤ 3 或 γ 无定义（NaN）的窗口 → CI NaN。
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.arctanh(np.clip(gamma, 0.0, _COHERENCE_Z_CLIP))
        n_eff = n_pairs - 3.0
        se = np.where(n_eff > 0, 1.0 / np.sqrt(np.where(n_eff > 0, n_eff, 1.0)),
                      np.nan)
        ci_ok = np.isfinite(z) & (n_eff > 0)
        ci_lo = np.tanh(z - _Z95 * se)
        ci_hi = np.tanh(z + _Z95 * se)
    gamma_ci_lo = np.where(ci_ok, np.clip(ci_lo, 0.0, 1.0), np.nan)
    gamma_ci_hi = np.where(ci_ok, np.clip(ci_hi, 0.0, 1.0), np.nan)

    disclosure = (
        "EXPERIMENTAL：窗口复相干性 γ = |Σ a·b*|/√(Σ|a|²Σ|b|²)；无轨道"
        "元数据/配准质量输入（估计偏差披露）；仅复 SLC（re/im 双通道）——"
        "强度-only 输入被类型化拒绝（相位不可虚构）；不输出干涉相位/解缠；"
        "γ 钳 [0,1]（数值超 1 像元 " + str(clamped) + "，披露）；"
        "逐窗 95% CI 为 Fisher z 近似（z=atanh γ、SE≈1/√(n_pairs−3)、"
        "正态近似，小样本/高 γ 下区间偏窄——披露）")
    meta: Dict[str, object] = {
        "window": w,
        "scientific_status": "EXPERIMENTAL",
        "clamped_pixels": clamped,
        "valid_pair_pixels": int(np.sum(valid)),
        "ci_method": ("Fisher z：z=atanh(γ)、SE≈1/√(n_pairs−3)、双侧 95%"
                      "（n_pairs≤3 → NaN）"),
        "formula": "γ = |Σ a·b*| / √(Σ|a|²·Σ|b|²)",
        "disclosure": disclosure,
    }
    return {
        "gamma": np.asarray(gamma, dtype=float),
        "array": np.asarray(gamma, dtype=float),
        "valid_pairs": n_pairs,
        "gamma_ci95_low": np.asarray(gamma_ci_lo, dtype=float),
        "gamma_ci95_high": np.asarray(gamma_ci_hi, dtype=float),
        "meta": meta,
    }


# ── RTC 地形辐射校正（Small 2011）────────────────────────────────────

def radiometric_terrain_correction(
    sigma0: np.ndarray,
    dem: np.ndarray,
    cell_size: Optional[float],
    radar_range_azimuth: Optional[float],
    *,
    incidence_deg: Optional[float] = None,
    incidence_map: Optional[np.ndarray] = None,
    nodata: Optional[float] = None,
    dem_nodata: Optional[float] = None,
) -> Dict[str, object]:
    """RTC gamma 平坦化（Small 2011）：γ_flat = σ⁰·cosθi/cosθl。

    本地入射角 θl 由 DEM Horn 梯度导出：
    cos θl = cosθi·cosα + sinθi·sinα·cos(β − β_r)
    （α = 坡度、β = 下坡方位角、β_r = radar_range_azimuth）。
    θl ≤ 0 或 ≥ 90（叠掩/阴影）→ nodata（计数披露）；标量或逐像元
    入射角均可（LUT 计数披露）；range-only 几何简化（无轨道元数据）。

    Returns:
        dict: array（γ_flat；无效像元 NaN）、local_incidence_deg（θl 栅格）、
        meta（公式 + 约定 + 披露）。
    """
    cs = _resolve_cell_size(cell_size)
    az = _resolve_azimuth(radar_range_azimuth)
    sig = _check_plane(sigma0, "sigma0")
    dem_p = np.asarray(dem, dtype=float)
    if dem_p.ndim != 2 or dem_p.shape != sig.shape:
        raise ValueError(
            f"dem 必须是与 sigma0 同形的 2D 数组：{sig.shape} vs "
            f"{dem_p.shape if dem_p.ndim == 2 else 'ndim=' + str(dem_p.ndim)}")
    theta = _resolve_incidence(incidence_deg, incidence_map, sig.shape)

    valid_sig = np.isfinite(sig)
    if nodata is not None:
        valid_sig &= sig != float(nodata)
    valid_dem = np.isfinite(dem_p)
    if dem_nodata is not None:
        valid_dem &= dem_p != float(dem_nodata)
    if not (valid_sig & valid_dem).any():
        raise NoValidObservations(
            "RTC：σ⁰ 与 DEM 无公共有效像元",
            correction_hint="检查 nodata 设置或两输入对齐")

    geom = _terrain_geometry(dem_p, cs, np.deg2rad(theta), np.deg2rad(az))
    cos_tl = geom["cos_tl"]
    # 无效几何 = 阴影（cosθl ≤ 0）∪ 叠掩（面坡且 α > θi——cos 无符号公式
    # 对 α−θi 为偶函数，须显式剔除，否则把叠掩当合法小角放大）。
    layover_mask = geom["facing"] & (geom["slope"] > np.deg2rad(theta))
    with np.errstate(divide="ignore", invalid="ignore"):
        gamma_flat = np.where(
            valid_sig & valid_dem & (cos_tl > 0) & np.isfinite(cos_tl)
            & ~layover_mask,
            sig * np.cos(np.deg2rad(theta)) / np.where(cos_tl > 0, cos_tl, 1.0),
            np.nan)

    both_valid = valid_sig & valid_dem
    invalid_geom = int(np.sum(both_valid & (
        ~(np.isfinite(cos_tl) & (cos_tl > 0)) | layover_mask)))
    disclosure = (
        "RTC gamma 平坦化 γ_flat = σ⁰·cosθi/cosθl（Small 2011）；"
        + _TERRAIN_CONVENTION
        + "；θl ≤ 0 或 ≥ 90（阴影）与叠掩（面坡且坡度陡于入射角）→ nodata"
          f"（invalid_geometry_pixels = {invalid_geom}，计数披露）；"
          "DEM 无效像元及其 1 像元梯度裙边同样 nodata")
    meta: Dict[str, object] = {
        "cell_size": cs,
        "radar_range_azimuth": az,
        "incidence_mode": ("per_pixel" if incidence_map is not None
                           else ("scalar" if incidence_deg is not None
                                 else "none")),
        "lut_pixels": int(sig.size) if incidence_map is not None else None,
        "invalid_geometry_pixels": invalid_geom,
        "dem_invalid_pixels": int(np.sum(~valid_dem)),
        "formula": "γ_flat = σ⁰·cos(θi)/cos(θl)",
        "convention": _TERRAIN_CONVENTION,
        "disclosure": disclosure,
    }
    return {
        "array": np.asarray(gamma_flat, dtype=float),
        "local_incidence_deg": np.rad2deg(np.arccos(np.clip(cos_tl, -1.0, 1.0))),
        "meta": meta,
    }


# ── 叠掩/阴影几何分类 ────────────────────────────────────────────────

def layover_shadow_mask(
    dem: np.ndarray,
    cell_size: Optional[float],
    radar_range_azimuth: Optional[float],
    *,
    incidence_deg: Optional[float] = None,
    incidence_map: Optional[np.ndarray] = None,
    dem_nodata: Optional[float] = None,
) -> Dict[str, object]:
    """几何分类（逐像元、range-only 简化）：{0=normal,1=layover,2=shadow,3=nodata}。

    - layover：面坡（cos(β − β_r) > 0）且坡度陡于入射角（α > θi）；
    - shadow：cos θl ≤ 0（背坡超过掠射角）；
    - nodata：DEM/入射角无效（含梯度裙边）。

    诚实边界：逐像元几何判定，**不含**视线遮蔽（ray-casting cast
    shadow）；传感器位置无关的简化（无轨道元数据，披露）。
    """
    cs = _resolve_cell_size(cell_size)
    az = _resolve_azimuth(radar_range_azimuth)
    dem_p = np.asarray(dem, dtype=float)
    _check_plane(dem_p, "dem")
    theta = _resolve_incidence(incidence_deg, incidence_map, dem_p.shape)

    valid_dem = np.isfinite(dem_p)
    if dem_nodata is not None:
        valid_dem &= dem_p != float(dem_nodata)
    theta_rad = np.deg2rad(theta)
    theta_valid = np.isfinite(theta_rad) & (theta_rad > 0) \
        & (theta_rad < np.pi / 2)
    geom = _terrain_geometry(dem_p, cs, theta_rad, np.deg2rad(az))
    cos_tl = geom["cos_tl"]

    finite_geom = valid_dem & theta_valid & np.isfinite(cos_tl)
    facing = geom["facing"]
    shadow = finite_geom & (cos_tl <= 0)
    layover = finite_geom & ~shadow & facing & (geom["slope"] > theta_rad)
    normal = finite_geom & ~shadow & ~layover

    mask = np.full(dem_p.shape, 3.0, dtype=float)
    mask[normal] = 0.0
    mask[layover] = 1.0
    mask[shadow] = 2.0
    total = float(mask.size)
    fractions = {
        "normal": round(float(np.sum(mask == 0)) / total, 6),
        "layover": round(float(np.sum(mask == 1)) / total, 6),
        "shadow": round(float(np.sum(mask == 2)) / total, 6),
        "nodata": round(float(np.sum(mask == 3)) / total, 6),
    }
    disclosure = (
        "逐像元几何分类 {0=normal,1=layover,2=shadow,3=nodata}；"
        "layover = 面坡且坡度陡于入射角（α > θi）；shadow = cosθl ≤ 0；"
        + _TERRAIN_CONVENTION
        + "；**不含**视线遮蔽（ray-casting cast shadow）——单像元几何"
          "判定，非可视域/投影阴影（诚实披露）")
    meta: Dict[str, object] = {
        "cell_size": cs,
        "radar_range_azimuth": az,
        "incidence_mode": ("per_pixel" if incidence_map is not None
                           else ("scalar" if incidence_deg is not None
                                 else "none")),
        "classes": {str(k): v for k, v in LAYOVER_SHADOW_CLASSES.items()},
        "fractions": fractions,
        "formula": ("facing: cos(β−β_r)>0; layover: facing ∧ α>θi; "
                    "shadow: cosθl ≤ 0"),
        "convention": _TERRAIN_CONVENTION,
        "disclosure": disclosure,
    }
    return {
        "mask": mask,
        "array": mask,
        "fractions": fractions,
        "meta": meta,
    }


# ── 滑窗 ENL 估计图 ──────────────────────────────────────────────────

def enl_confidence_interval(
    enl: float, n_samples: int, *, level: float = 0.95,
) -> Tuple[float, float]:
    """ENL 矩估计的 Wald 置信区间（delta 法；均匀场景假设）。

    R-3（审计 §6）：对 ENL = mean²/var 在 gamma(L) 斑点（均匀场景，
    Oliver & Quegan §4 量级）上做 delta 法方差传播：

        var(ENL̂) ≈ 2·ENL·(ENL + 1) / n

    （n = 有效像元数；由 m̂ 的方差 σ²/n、v̂ 的方差 (κ−1)σ⁴/n、
    Cov(m̂, v̂) = μ₃/n 代入 f=m²/v 的线性化，L=ENL。）
    下界钳 0（ENL 物理非负）；level 支持 0.90/0.95/0.99，其余 ValueError。
    """
    z = {0.90: 1.6448536269514722, 0.95: _Z95,
         0.99: 2.5758293035489004}.get(round(float(level), 2))
    if z is None:
        raise ValueError(
            f"level 仅支持 0.90/0.95/0.99，got {level!r}")
    enl_f = float(enl)
    n = int(n_samples)
    if not (np.isfinite(enl_f) and enl_f > 0):
        raise ValueError(f"enl 必须为正有限数，got {enl!r}")
    if n < 1:
        raise ValueError(f"n_samples 必须为正整数（有效像元数），got {n_samples!r}")
    se = float(np.sqrt(2.0 * enl_f * (enl_f + 1.0) / n))
    return max(enl_f - z * se, 0.0), enl_f + z * se


def enl_map(
    intensity: np.ndarray,
    *,
    window: int = 7,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """滑窗 ENL 估计图（ENL = mean²/var，nan 感知）+ 全局 ENL（附 95% CI）。

    估计偏差披露：非均匀窗口（纹理/边缘）把纹理方差计入 → ENL 被低估；
    全局 ENL 假定整图均匀。方差 ≤ ε 的退化窗口 → NaN（计数披露）。
    enl_ci95：全局 ENL 的 Wald 95% 置信区间（delta 法，均匀场景；
    只覆盖抽样噪声、不覆盖非均匀偏差——披露）。
    """
    w = _check_window(window, "window")
    plane = _check_plane(intensity, "intensity")
    valid = np.isfinite(plane)
    if nodata is not None:
        valid &= plane != float(nodata)
    if not valid.any():
        raise DegenerateData(
            "无有效像元：ENL 不可估",
            correction_hint="检查 nodata 设置或输入网格")
    if (plane[valid] < 0).any():
        raise UnsupportedMethod(
            "ENL 估计检测到负值——假定线性强度输入（非负）；输入疑似 dB 对数域",
            correction_hint="先做线性定标，或显式用 sar_log_scale 换算")

    filled = np.where(valid, plane, 0.0)
    valid_f = valid.astype(float)
    mean, var = _window_stats(filled, valid_f, w)
    # 退化窗口判定用**相对**阈值（var ≤ ε·mean²）：uniform_filter 运行和
    # 在常数窗口上留下 ~1e-16·mean² 的数值残差，绝对阈值会把它们当真实
    # 方差（ENL ~1e16 的伪值）。
    threshold = ENL_EPS * np.maximum(mean * mean, ENL_EPS)
    with np.errstate(divide="ignore", invalid="ignore"):
        enl_plane = np.where(
            var > threshold,
            mean * mean / np.where(var > threshold, var, 1.0), np.nan)
    degenerate_windows = int(np.sum(valid & ~np.isfinite(enl_plane)))

    vals = plane[valid]
    var0 = float(vals.var())
    if var0 <= ENL_EPS:
        raise DegenerateData(
            "整图方差为 0（常数场）——ENL = mean²/var 无定义",
            correction_hint="常数场无斑点语义；ENL 估计不适用")
    global_enl = (float(vals.mean()) ** 2) / var0

    # R-3：全局 ENL 的 95% CI（delta 法 var(ENL̂)≈2L(L+1)/n；均匀场景
    # 假设——非均匀场景 CI 只反映抽样噪声，不覆盖纹理偏差，披露）。
    ci_lo, ci_hi = enl_confidence_interval(global_enl, int(vals.size))

    disclosure = (
        "滑窗 ENL = mean²/var（总体方差 ddof=0，nan 感知）；非均匀窗口把"
        "纹理方差计入 → ENL 被低估（估计偏差，披露）；全局 ENL 假定整图"
        f"均匀；退化窗口（方差 ≤ ε·mean²，含常数窗口数值残差）→ NaN"
        f"（{degenerate_windows} 像元披露）；enl_ci95 为 delta 法 Wald "
        "区间（var(ENL̂)≈2·ENL·(ENL+1)/n，只覆盖抽样噪声、不覆盖非均匀"
        "偏差）")
    meta: Dict[str, object] = {
        "window": w,
        "global_enl": global_enl,
        "enl_ci95": [ci_lo, ci_hi],
        "enl_ci_method": ("Wald 95%（delta 法：var(ENL̂)≈2·ENL·(ENL+1)/n，"
                          "均匀场景；下界钳 0）"),
        "enl_ci_samples": int(vals.size),
        "degenerate_windows": degenerate_windows,
        "formula": "ENL = mean²/var（窗口总体方差）",
        "disclosure": disclosure,
    }
    return {
        "enl_map": np.asarray(enl_plane, dtype=float),
        "array": np.asarray(enl_plane, dtype=float),
        "global_enl": global_enl,
        "enl_ci95": [float(ci_lo), float(ci_hi)],
        "meta": meta,
    }
