"""SAR 辐射定标 —— DN → β⁰/σ⁰/γ⁰ 常数定标基元（Foundation V2 · A6）。

标准 SAR 辐射定标关系（Oliver & Quegan 1998 §定标语义；逐字公式）：

    I  = DN²            （input_domain="dn_amplitude" 时先平方；intensity 直通）
    β⁰ = I / K          （K = calibration_constant，雷达方程定标常数；
                          Sentinel-1 惯称 A²/AUT）
    σ⁰ = β⁰ · sin(θ_i)  （θ_i = 本地入射角；σ⁰ = 单位地表面积后向散射）
    γ⁰ = β⁰ · tan(θ_i)  （γ⁰ = 单位投影面积后向散射）

诚实边界（进 meta / descriptor limitations，绝不静默假装）：

- **只实现常数定标**——逐像元定标 LUT（Sentinel-1 σ⁰ LUT、denoising LUT）
  未实现；`calibration_constant` 是**必需显式参数**，缺失抛
  MissingRequiredField（绝不虚构/默认定标常数）；
- **热噪声去除未实现**（Sentinel-1 GRD 的 thermal noise 未扣）；
- 输入必须为**非负**：负振幅/负强度物理无意义 → UnsupportedMethod；
- 入射角：标量或逐像元平面（形状须与网格一致），开区间 (0, 90)——
  越界抛 InvalidUnits；σ⁰/γ⁰ 需要入射角而未提供 → MissingRequiredField；
- dB 输出 = 10·log₁₀（强度量纲惯例），可选旗标。
"""
from __future__ import annotations

from typing import Dict, Optional, Union

import numpy as np

from app.lib.gis.scientific_errors import (
    InvalidUnits,
    MissingRequiredField,
    UnsupportedMethod,
)

__all__ = [
    "CALIBRATION_INPUT_DOMAINS",
    "CALIBRATION_PRODUCTS",
    "calibrate_sar",
]

CALIBRATION_INPUT_DOMAINS = ("dn_amplitude", "dn_intensity")
CALIBRATION_PRODUCTS = ("sigma0", "beta0", "gamma0", "all")

_INCIDENCE_EPS = 1e-9


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
        products_computed、to_db、meta（公式 + LUT/热噪声未实现披露）。
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
        "常数定标（逐像元定标 LUT 未实现；热噪声去除未实现——Sentinel-1 GRD "
        "噪声底未扣，弱信号像元偏乐观）；振幅域输入先平方为强度（已披露）；"
        "dB = 10·log₁₀（强度量纲惯例）")
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
