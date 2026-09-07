"""SAR 辐射定标 —— DN → β⁰/σ⁰/γ⁰ 常数定标基元（Foundation V2 · A6）。

标准 SAR 辐射定标关系（Oliver & Quegan 1998 §定标语义；逐字公式）：

    I  = DN²            （input_domain="dn_amplitude" 时先平方；intensity 直通）
    β⁰ = I / K          （K = calibration_constant，雷达方程定标常数；
                          Sentinel-1 惯称 A²/AUT）
    σ⁰ = β⁰ · sin(θ_i)  （θ_i = 本地入射角；σ⁰ = 单位地表面积后向散射）
    γ⁰ = β⁰ · tan(θ_i)  （γ⁰ = 单位投影面积后向散射）

诚实边界（进 meta / descriptor limitations，绝不静默假装）：

- **只实现常数定标常数 K**——`calibration_constant` 是**必需显式参数**，
  缺失抛 MissingRequiredField（绝不虚构/默认定标常数）；Sentinel-1 的
  **σ⁰ 逐像元定标 LUT**（SAFE annotation XML）仍不解析；
- **入射角 LUT 已支持**（Foundation V3）：标量 `incidence_deg` 或与网格
  同形的逐像元 2D 平面 `incidence_map`（互斥），形状 + (0,90) 开区间
  校验，`lut_pixels` 像元数进 meta；
- **热噪声去除**已作为独立算法提供（``remove_thermal_noise``，
  sar.thermal_noise_removal）——本模块不做隐式前置/后置；
- 输入必须为**非负**：负振幅/负强度物理无意义 → UnsupportedMethod；
- 入射角：标量或逐像元平面（形状须与网格一致），开区间 (0, 90)——
  越界抛 InvalidUnits；σ⁰/γ⁰ 需要入射角而未提供 → MissingRequiredField；
- dB 输出 = 10·log₁₀（强度量纲惯例），可选旗标；量纲换算恒等式见
  ``sar_log_scale``。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple, Union

import numpy as np

from app.lib.gis.scientific_errors import (
    InvalidUnits,
    MissingRequiredField,
    ResourceScaleMismatch,
    UnsupportedMethod,
)

__all__ = [
    "CALIBRATION_INPUT_DOMAINS",
    "CALIBRATION_PRODUCTS",
    "CALIBRATION_SCALE_LIMIT_PIXELS",
    "LOG_SCALE_MODES",
    "calibrate_sar",
    "remove_thermal_noise",
    "sar_log_scale",
]

CALIBRATION_INPUT_DOMAINS = ("dn_amplitude", "dn_intensity")
CALIBRATION_PRODUCTS = ("sigma0", "beta0", "gamma0", "all")

_INCIDENCE_EPS = 1e-9
CALIBRATION_SCALE_LIMIT_PIXELS = 4096 * 4096   # 与 sar_temporal/sar_filter 闸一致（16M）


def _incidence_plane(
    incidence_deg: Optional[float],
    incidence_map: Optional[np.ndarray],
    shape: tuple,
) -> Optional[np.ndarray]:
    """入射角解析：标量或逐像元平面（形状一致 + (0,90) 开区间守卫）。"""
    if incidence_deg is None and incidence_map is None:
        return None
    if incidence_deg is not None and incidence_map is not None:
        raise ValueError(
            "incidence_deg 与 incidence_map 只能二选一（标量或逐像元平面）")
    if incidence_deg is not None:
        deg = float(incidence_deg)
        if not (0.0 + _INCIDENCE_EPS < deg < 90.0 - _INCIDENCE_EPS):
            raise InvalidUnits(
                f"入射角必须在 (0, 90) 开区间（度），got {incidence_deg!r}",
                correction_hint="提供度制入射角，如 Sentinel-1 IW 30-45°",
            )
        return np.full(shape, deg, dtype=float)
    plane = np.asarray(incidence_map, dtype=float)
    if plane.shape != tuple(shape):
        raise ValueError(
            f"逐像元入射角平面形状 {plane.shape} 与网格 {tuple(shape)} 不一致")
    ok = np.isfinite(plane) & (plane > _INCIDENCE_EPS) \
        & (plane < 90.0 - _INCIDENCE_EPS)
    if not ok.all():
        raise InvalidUnits(
            "逐像元入射角存在越界/非有限值（须全部落在 (0, 90) 开区间，度）",
            correction_hint="检查入射角平面的单位与 nodata 填充",
        )
    return plane


def calibrate_sar(
    arr: np.ndarray,
    *,
    calibration_constant: float,
    incidence_deg: Optional[float] = None,
    incidence_map: Optional[np.ndarray] = None,
    input_domain: str = "dn_intensity",
    output_product: str = "sigma0",
    to_db: bool = False,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """DN → β⁰/σ⁰/γ⁰ 常数定标（公式见模块 docstring；绝不虚构元数据）。

    Args:
        arr: 2D DN/强度栅格（非负）。
        calibration_constant: 定标常数 K（**必需**；Sentinel-1 A²/AUT 等）。
            缺失/None → MissingRequiredField（附 LUT 未实现提示）。
        incidence_deg: 标量入射角（度，(0,90) 开区间）。
        incidence_map: 逐像元入射角平面（与网格同形；与 incidence_deg 互斥）。
        input_domain: ``dn_amplitude``（先平方为强度，披露）或
            ``dn_intensity``（直通）。
        output_product: ``sigma0`` / ``beta0`` / ``gamma0`` / ``all``。
        to_db: True 时输出 10·log₁₀（强度量纲惯例）。
        nodata: 标量哨兵值；NaN/Inf 自动视为无效。

    Returns:
        dict: products（{"sigma0"/"beta0"/"gamma0": 2D 数组或 None}）、
        products_computed、to_db、meta（公式 + 诚实边界披露；
        incidence_mode="per_pixel" 时含 lut_pixels）。
    """
    if calibration_constant is None:
        raise MissingRequiredField(
            "缺少定标常数（calibration_constant）——辐射定标绝不虚构 K",
            correction_hint="需要定标常数（如 Sentinel-1 A²/AUT 或逐像元 "
                            "LUT——逐像元 LUT 未实现，仅支持常数定标）",
        )
    k_const = float(calibration_constant)
    if not (np.isfinite(k_const) and k_const > 0):
        raise ValueError(
            f"calibration_constant 必须为正有限数，got {calibration_constant!r}")

    domain_key = (input_domain or "dn_intensity").lower()
    if domain_key not in CALIBRATION_INPUT_DOMAINS:
        raise ValueError(
            f"unsupported input_domain '{input_domain}'; "
            f"valid: {list(CALIBRATION_INPUT_DOMAINS)}")
    product_key = (output_product or "sigma0").lower()
    if product_key not in CALIBRATION_PRODUCTS:
        raise ValueError(
            f"unsupported output_product '{output_product}'; "
            f"valid: {list(CALIBRATION_PRODUCTS)}")

    plane = np.asarray(arr, dtype=float)
    if plane.ndim != 2:
        raise ValueError(
            f"SAR 定标输入必须是 2D 数组，got ndim={plane.ndim}")

    valid = np.isfinite(plane)
    if nodata is not None:
        valid &= plane != float(nodata)
    if valid.any() and (plane[valid] < 0).any():
        raise UnsupportedMethod(
            f"检测到负 DN（input_domain={domain_key}）——振幅/强度均非负；"
            "负值像元物理无意义",
            correction_hint="检查输入量纲（振幅 vs dB vs 强度）后再定标",
        )

    # I = DN²（振幅域先平方，披露）；无效像元置 NaN。
    intensity = np.where(valid, plane, np.nan)
    if domain_key == "dn_amplitude":
        intensity = intensity * intensity

    beta0 = intensity / k_const

    want_sigma = product_key in ("sigma0", "all")
    want_gamma = product_key in ("gamma0", "all")
    want_beta = product_key in ("beta0", "all")
    needs_incidence = want_sigma or want_gamma
    theta = None
    if needs_incidence:
        theta = _incidence_plane(incidence_deg, incidence_map, plane.shape)
        if theta is None:
            raise MissingRequiredField(
                f"output_product={product_key} 需要本地入射角 "
                "(incidence_deg 标量或 incidence_map 平面)",
                correction_hint="提供 incidence_deg（度，0-90 开区间）或"
                                "逐像元 incidence_map",
            )

    products: Dict[str, Optional[np.ndarray]] = {}
    if want_beta:
        products["beta0"] = beta0
    else:
        products["beta0"] = None
    if want_sigma:
        products["sigma0"] = beta0 * np.sin(np.deg2rad(theta))
    else:
        products["sigma0"] = None
    if want_gamma:
        products["gamma0"] = beta0 * np.tan(np.deg2rad(theta))
    else:
        products["gamma0"] = None

    computed = [p for p in ("sigma0", "beta0", "gamma0")
                if products[p] is not None]
    if to_db:
        for p in computed:
            with np.errstate(divide="ignore", invalid="ignore"):
                products[p] = 10.0 * np.log10(np.asarray(products[p]))

    # 按请求序输出（sigma0 优先），同时保留完整 products 视图。
    primary = computed[0] if computed else None
    relation = {
        "sigma0": "σ⁰ = (DN²/K)·sin(θ) 或 (DN/K)²·sin(θ)",
        "beta0": "β⁰ = DN²/K 或 (DN/K)²",
        "gamma0": "γ⁰ = (DN²/K)·tan(θ) 或 (DN/K)²·tan(θ)",
    }
    disclosure = (
        "常数定标（定标常数 K 为标量——σ⁰ 逐像元定标 LUT 不解析 SAFE "
        "annotation XML；入射角支持标量或逐像元 LUT（lut_pixels 披露）；"
        "热噪声去除为独立算法 sar.thermal_noise_removal，本工具不做隐式"
        "前置）；振幅域输入先平方为强度（已披露）；dB = 10·log₁₀（强度"
        "量纲惯例）")
    meta: Dict[str, object] = {
        "input_domain": domain_key,
        "output_product": product_key,
        "products_computed": computed,
        "primary_product": primary,
        "formula": "；".join(relation[p] for p in computed) or "",
        "to_db": bool(to_db),
        "incidence_mode": (
            "per_pixel" if incidence_map is not None
            else ("scalar" if incidence_deg is not None else "none")),
        "lut_pixels": (int(plane.size)
                       if incidence_map is not None else None),
        "disclosure": disclosure,
    }
    return {
        "products": products,
        "array": products.get(primary) if primary else None,
        "to_db": bool(to_db),
        "meta": meta,
    }


def calibration_evidence_facts(
    result: Dict[str, object],
) -> Dict[str, Union[str, bool]]:
    """工具层证据 facts 抽取（input_facts 有界字段）。"""
    meta = result.get("meta", {})
    return {
        "calibration_input_domain": str(meta.get("input_domain", "")),
        "calibration_products": ",".join(meta.get("products_computed", [])),
        "calibration_to_db": bool(result.get("to_db", False)),
    }


# ── Foundation V3：热噪声去除 / 量纲换算 ─────────────────────────────

def _noise_input_plane(arr: object, what: str, nodata: Optional[float]) -> Tuple[np.ndarray, np.ndarray]:
    """强度输入解析：2D + 规模闸 + 负值守卫（dB/负强度物理无意义）。"""
    plane = np.asarray(arr, dtype=float)
    if plane.ndim != 2:
        raise ValueError(
            f"{what} 必须是 2D 数组，got ndim={plane.ndim}")
    if plane.size > CALIBRATION_SCALE_LIMIT_PIXELS:
        h, w = plane.shape
        raise ResourceScaleMismatch(
            f"{what} 网格规模超限：H×W={h}×{w}={plane.size} 像元"
            f"（≤{CALIBRATION_SCALE_LIMIT_PIXELS}）",
            estimated=f"{plane.size * 8 / 1e6:.1f} MB float64",
            limit=f"H·W≤{CALIBRATION_SCALE_LIMIT_PIXELS}",
            correction_hint="分块（瓦片）处理后拼接",
        )
    valid = np.isfinite(plane)
    if nodata is not None:
        valid &= plane != float(nodata)
    if valid.any() and (plane[valid] < 0).any():
        raise UnsupportedMethod(
            f"{what} 检测到负值——热噪声去除/换算假定线性强度输入（非负）；"
            "输入疑似 dB 对数域",
            correction_hint="先做线性定标（见 sar.radiometric_calibration）"
                            "或改用 db_to_linear 换算",
        )
    return plane, valid


def remove_thermal_noise(
    intensity: np.ndarray,
    noise_floor: Optional[float] = None,
    noise_lut: Optional[np.ndarray] = None,
    *,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """SAR 热噪声去除：I_dn = max(I − N, 0)（标量噪声底或逐像元 LUT）。

    honest 边界（进 meta，绝不静默）：真实 Sentinel-1 GRD IPF 噪声 LUT 是
    **annotation XML**（denoising 需逐 swath 重采样/插值）——本工具接收
    **已提取**的噪声底标量或同形 LUT，**不解析 SAFE XML**。

    Args:
        intensity: 2D 线性强度栅格（非负；dB 输入被拒绝）。
        noise_floor: 标量噪声底（≥0；与 noise_lut 互斥）。
        noise_lut: 逐像元噪声 LUT（与网格同形；与 noise_floor 互斥）。
        nodata: 标量哨兵值；NaN/Inf 自动视为无效（输出 NaN）。

    Returns:
        dict: array（无效像元 NaN；钳 0 像元数披露 clamped_pixels）、
        mode（scalar/lut）、meta（公式 + SAFE-XML 诚实披露）。
    """
    if noise_floor is None and noise_lut is None:
        raise MissingRequiredField(
            "需要 noise_floor（标量噪声底）或 noise_lut（逐像元 LUT）"
            "之一——热噪声去除绝不虚构噪声参数",
            correction_hint="从产品注记提取噪声底/LUT 后显式传入",
        )
    if noise_floor is not None and noise_lut is not None:
        raise ValueError(
            "noise_floor 与 noise_lut 只能二选一（标量或逐像元 LUT）")

    plane, valid = _noise_input_plane(intensity, "intensity", nodata)

    floor_plane: Optional[np.ndarray] = None
    mode = "scalar"
    if noise_floor is not None:
        floor_f = float(noise_floor)
        if not (np.isfinite(floor_f) and floor_f >= 0):
            raise ValueError(
                f"noise_floor 必须为非负有限数（线性强度域），got {noise_floor!r}")
        floor_plane = np.full(plane.shape, floor_f, dtype=float)
    else:
        mode = "lut"
        lut = np.asarray(noise_lut, dtype=float)
        if lut.shape != plane.shape:
            raise ValueError(
                f"noise_lut 形状 {lut.shape} 与网格 {plane.shape} 不一致")
        if np.isfinite(lut).all() and (lut < 0).any():
            raise ValueError(
                "noise_lut 存在负值——噪声底在强度域非负，检查单位/量纲")
        lut_valid = valid & np.isfinite(lut)
        floor_plane = np.where(lut_valid, lut, np.nan)

    with np.errstate(invalid="ignore"):
        denoised = plane - floor_plane
    clampable = valid & np.isfinite(denoised) & (denoised < 0)
    clamped = int(np.sum(clampable))
    denoised = np.where(clampable, 0.0, denoised)
    denoised = np.where(valid & np.isfinite(floor_plane), denoised, np.nan)

    disclosure = (
        "热噪声去除 I_dn = max(I − N, 0)：真实 Sentinel-1 GRD IPF 噪声 LUT "
        "为 annotation XML（denoising 需逐 swath 插值）——本工具接收已提取"
        "的噪声底/LUT，不解析 SAFE XML；负值钳 0（clamped_pixels 披露，"
        "弱信号统计右偏）；dB 输入被拒绝（线性强度必需）")
    meta: Dict[str, object] = {
        "mode": mode,
        "noise_floor": float(noise_floor) if noise_floor is not None else None,
        "clamped_pixels": clamped,
        "invalid_pixels": int(plane.size - np.sum(np.isfinite(denoised))),
        "lut_pixels": int(plane.size) if mode == "lut" else None,
        "formula": "I_dn = max(I − N, 0)",
        "disclosure": disclosure,
    }
    return {
        "array": np.asarray(denoised, dtype=float),
        "mode": mode,
        "meta": meta,
    }


LOG_SCALE_MODES = (
    "amplitude_to_intensity", "intensity_to_amplitude",
    "linear_to_db", "db_to_linear",
)
_LOG_EPS = 1e-12                      # log(0) 的 ε 下限（披露，计数）


def sar_log_scale(
    arr: np.ndarray,
    mode: str,
    *,
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """SAR 量纲换算（纯代数恒等式；round-trip 精确）。

    mode（``LOG_SCALE_MODES``）：

    - ``amplitude_to_intensity``：I = A²（振幅→强度）；
    - ``intensity_to_amplitude``：A = √I（强度→振幅；负值 → NaN 披露）；
    - ``linear_to_db``：dB = 10·log₁₀(x)（x ≤ 0 钳 ε=1e-12 下限，计数披露）；
    - ``db_to_linear``：x = 10^(dB/10)（全实数有定义）。

    Returns:
        dict: array、mode、meta（公式 + ε/NaN 计数披露）。
    """
    mode_key = (mode or "").lower()
    if mode_key not in LOG_SCALE_MODES:
        raise ValueError(
            f"unsupported log scale mode '{mode}'; valid: {list(LOG_SCALE_MODES)}")

    plane = np.asarray(arr, dtype=float)
    if plane.ndim != 2:
        raise ValueError(f"输入必须是 2D 数组，got ndim={plane.ndim}")
    valid = np.isfinite(plane)
    if nodata is not None:
        valid &= plane != float(nodata)
    work = np.where(valid, plane, np.nan)

    floored = 0
    nonpositive = 0
    if mode_key == "amplitude_to_intensity":
        out = work * work
        bad = valid & (work < 0)
        nonpositive = int(np.sum(bad))
        out = np.where(bad, np.nan, out)
        formula = "I = A²（振幅→强度；负振幅 → NaN 披露）"
    elif mode_key == "intensity_to_amplitude":
        with np.errstate(invalid="ignore"):
            out = np.sqrt(work)
        bad = valid & (work < 0)
        nonpositive = int(np.sum(bad))
        out = np.where(bad, np.nan, out)
        formula = "A = √I（强度→振幅；负强度 → NaN 披露）"
    elif mode_key == "linear_to_db":
        floormask = valid & (work <= 0.0)
        floored = int(np.sum(floormask))
        floored_plane = np.where(floormask, _LOG_EPS, work)
        with np.errstate(divide="ignore", invalid="ignore"):
            out = 10.0 * np.log10(floored_plane)
        out = np.where(valid, out, np.nan)
        formula = (f"dB = 10·log₁₀(x)（x ≤ 0 钳 ε={_LOG_EPS:g} 下限，"
                   f"{floored} 像元披露）")
    else:  # db_to_linear
        with np.errstate(over="ignore"):
            out = np.power(10.0, work / 10.0)
        out = np.where(valid, out, np.nan)
        formula = "x = 10^(dB/10)"

    disclosure = (
        "纯代数恒等式换算（round-trip 精确；无定标语义——量纲假定由调用方"
        "负责）；dB 换算的 0/负值以 ε 下限处理并披露（非静默钳制）；"
        "振幅/强度域负值物理无意义 → NaN（计数披露）")
    meta: Dict[str, object] = {
        "mode": mode_key,
        "formula": formula,
        "epsilon_floor": _LOG_EPS if mode_key == "linear_to_db" else None,
        "floored_cells": floored,
        "nonpositive_to_nan": nonpositive,
        "disclosure": disclosure,
    }
    return {
        "array": np.asarray(out, dtype=float),
        "mode": mode_key,
        "meta": meta,
    }
