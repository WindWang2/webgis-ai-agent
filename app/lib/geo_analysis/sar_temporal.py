"""SAR 时序科学扩展 —— 极化/轨道/入射角语义 + 时序统计 + 比值变化。

诚实边界（进结果 meta，测试锁定）：

- **不做** 斑点噪声滤波（speckle filtering 未实现）、**不做** 辐射定标
  （radiometric calibration 未实现）——输入假定已几何校正（配准/地形
  校正）并对齐；这两项在注册表里是 planned 能力，不是本模块的隐式
  假装（见 capabilities: sar_speckle_filtering / sar_radiometric_calibration）；
- 时序统计是逐像元的描述性统计（无趋势检验——趋势见 temporal 域的
  Mann-Kendall / 季节 MK）；
- 规模守卫：栈深 T ≤ 24 且 H·W ≤ 4096×4096，超限抛
  ``ResourceScaleMismatch``（附估算体积），先拒绝不 OOM。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from pydantic import BaseModel, Field, field_validator

from app.lib.geo_analysis.raster_change import ratio_change
from app.lib.gis.scientific_errors import ResourceScaleMismatch

__all__ = [
    "SARAcquisitionMeta",
    "SAR_PRODUCTS",
    "SAR_SCALE_LIMIT_T",
    "SAR_SCALE_LIMIT_PIXELS",
    "temporal_stack_statistics",
    "vh_ratio",
    "temporal_log_ratio_change",
]


# ── 获取语义（极化/日期/入射角/轨道）────────────────────────────────

class SARAcquisitionMeta(BaseModel):
    """一次 SAR 获取的语义元数据（极化/日期/入射角/轨道方向）。

    入射角与轨道方向是可比较性事实（升/降轨、入射角差 > 5° 的两景
    后向散射不可直接比值——进 disclosure，不静默混栈）。
    """

    polarization: str = Field(description="极化方式：vv / vh / hh / hv")
    acquisition_date: str = Field(description="获取日期（YYYY-MM-DD）")
    incidence_angle_deg: Optional[float] = Field(
        default=None, description="入射角（度，0-90 开区间；缺省未知）")
    orbit_direction: Optional[str] = Field(
        default=None, description="轨道方向：ascending / descending（缺省未知）")

    @field_validator("polarization")
    @classmethod
    def _polarization_vocab(cls, v: str) -> str:
        v = (v or "").lower()
        if v not in ("vv", "vh", "hh", "hv"):
            raise ValueError(
                f"polarization 必须是 vv/vh/hh/hv 之一，got {v!r}")
        return v

    @field_validator("acquisition_date")
    @classmethod
    def _date_shape(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v or ""):
            raise ValueError(
                f"acquisition_date 必须是 YYYY-MM-DD，got {v!r}")
        return v

    @field_validator("incidence_angle_deg")
    @classmethod
    def _incidence_range(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not (0.0 < float(v) < 90.0):
            raise ValueError(
                f"incidence_angle_deg 必须在 (0, 90) 开区间，got {v!r}")
        return float(v)

    @field_validator("orbit_direction")
    @classmethod
    def _orbit_vocab(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = (v or "").lower()
        if v not in ("ascending", "descending"):
            raise ValueError(
                f"orbit_direction 必须是 ascending/descending，got {v!r}")
        return v


# ── 规模守卫 + 时序统计 ───────────────────────────────────────────────

SAR_PRODUCTS = ("mean", "std", "min", "max", "range")
SAR_SCALE_LIMIT_T = 24
SAR_SCALE_LIMIT_PIXELS = 4096 * 4096

SAR_HONESTY_META = (
    "无斑点滤波、无辐射定标——假定输入已几何校正并对齐；"
    "时序统计为逐像元描述性统计（无趋势检验）"
)


def _check_stack_scale(stack: np.ndarray) -> None:
    if stack.ndim != 3:
        raise ValueError(
            f"SAR 栈必须是 (T, H, W) 三维，got ndim={stack.ndim}")
    t, h, w = stack.shape
    pixels = int(h) * int(w)
    estimated_bytes = int(t) * pixels * 8
    if t > SAR_SCALE_LIMIT_T or pixels > SAR_SCALE_LIMIT_PIXELS:
        raise ResourceScaleMismatch(
            f"SAR 时序栈规模超限：T={t}（≤{SAR_SCALE_LIMIT_T}），"
            f"H×W={h}×{w}={pixels}（≤{SAR_SCALE_LIMIT_PIXELS}）",
            estimated=f"{estimated_bytes / 1e6:.1f} MB float64 "
                      f"({t}×{h}×{w}×8B)",
            limit=f"T≤{SAR_SCALE_LIMIT_T}, H·W≤{SAR_SCALE_LIMIT_PIXELS}",
            correction_hint="按时间或空间分块（瓦片/年份）后分批统计",
        )


def temporal_stack_statistics(
    stack: np.ndarray,
    *,
    product: str = "mean",
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """SAR 时序栈的逐像元统计（时间维聚合）。

    Args:
        stack: (T, H, W)。nodata 值与 NaN/Inf 像元逐切片剔除——某像元
            在部分切片无效时，统计在**其余有效切片**上计算（诚实披露
            per-pixel 有效切片数不足的像元）。
        product: mean / std（总体标准差 ddof=0）/ min / max / range。
        nodata: 标量哨兵值（如 0 dB 之外约定的 -9999）。

    Returns:
        dict: array（全切片无效的像元 → NaN）+ product + meta（诚实
        边界 + 每像元有效切片数摘要）。
    """
    product_key = (product or "mean").lower()
    if product_key not in SAR_PRODUCTS:
        raise ValueError(
            f"unsupported SAR product '{product}'; valid: {list(SAR_PRODUCTS)}")

    arr = np.asarray(stack, dtype=float)
    _check_stack_scale(arr)

    valid = np.isfinite(arr)
    if nodata is not None:
        valid &= arr != float(nodata)

    filled = np.where(valid, arr, np.nan)
    # MINOR-7（数值评审）：全无效切片的 nan-statistics 会经 warnings 模块
    # 发 RuntimeWarning（errstate 管不住）——显式压制；无效计数已在 meta
    # 诚实披露，警告是重复噪声。
    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", RuntimeWarning)
        with np.errstate(invalid="ignore"):
            if product_key == "mean":
                out = np.nanmean(filled, axis=0)
            elif product_key == "std":
                out = np.nanstd(filled, axis=0)   # 总体标准差（ddof=0），文档化
            elif product_key == "min":
                out = np.nanmin(filled, axis=0)
            elif product_key == "max":
                out = np.nanmax(filled, axis=0)
            else:  # range
                out = np.nanmax(filled, axis=0) - np.nanmin(filled, axis=0)
    out = np.asarray(out, dtype=float)

    counts = np.sum(valid, axis=0)
    total_slices = int(arr.shape[0])
    complete = counts == total_slices

    meta: Dict[str, object] = {
        "product": product_key,
        "time_slices": total_slices,
        "pixels_all_slices_valid": int(np.sum(complete)),
        "pixels_partially_valid": int(np.sum((counts > 0) & ~complete)),
        "pixels_no_valid_slice": int(np.sum(counts == 0)),
        "std_convention": "population (ddof=0)" if product_key == "std" else "",
        "disclosure": SAR_HONESTY_META,
    }
    return {"array": out, "product": product_key, "meta": meta}


# ── 极化比值 / 对数比值变化 ──────────────────────────────────────────

def vh_ratio(
    vv_arr: np.ndarray, vh_arr: np.ndarray, *, nodata: Optional[np.ndarray] = None
) -> Dict[str, object]:
    """VV/VH 极化比（结构对比代理；VH=0 → NaN）。

    C1（科学评审修复）：本函数计算的是**线性功率/强度域**的比值
    ``vv/vh``。dB 域（对数域）输入的等价对比度量是差值 VV−VH，与本
    实现完全不同 —— 此前 "dB 域输入结果是 dB 差" 的声明是错误的
    （负 dB 值相除会产生无意义的符号比值）。线性功率非负：负值输入
    抛 UnsupportedMethod（提示先做线性定标或改用 log_ratio_change）。
    """
    vv = np.asarray(vv_arr, dtype=float)
    vh = np.asarray(vh_arr, dtype=float)
    finite = np.isfinite(vv) & np.isfinite(vh)
    if nodata is not None:
        finite &= ~np.equal(nodata, True)
    if finite.any() and ((vv[finite] < 0) | (vh[finite] < 0)).any():
        from app.lib.gis.scientific_errors import UnsupportedMethod

        raise UnsupportedMethod(
            "vh_ratio 需要线性功率/强度域输入（非负）；检测到负值 —— "
            "输入疑似 dB 对数域。请先做线性定标，或改用 "
            "temporal_log_ratio_change（dB 差语义）",
            correction_hint="linear-power calibration first, or use log-ratio")
    result = ratio_change(
        vv_arr, vh_arr, method="ratio", nodata=nodata)
    result["meta"]["disclosure"] = (
        "VV/VH 极化比（线性功率域比值 vv/vh）；dB 域输入请改用 "
        "log-ratio（VV−VH）；无辐射定标假定下仅作结构对比代理")
    result["meta"]["formula"] = "vv / vh (linear power)"
    return result


def temporal_log_ratio_change(
    a: np.ndarray,
    b: np.ndarray,
    *,
    nodata: Optional[np.ndarray] = None,
    t1_date: Optional[str] = None,
    t2_date: Optional[str] = None,
) -> Dict[str, object]:
    """SAR 双时相对数比值变化（raster_change.ratio_change 的域门面）。

    log(a) − log(b)：对数域对称（增强=衰减镜像），双期后向散射比较的
    惯用量（Bruzzone 类方法的经典预量化步）。
    """
    return ratio_change(
        a, b, method="log_ratio", nodata=nodata,
        t1_date=t1_date, t2_date=t2_date)


def acquisition_comparability(metas: List[SARAcquisitionMeta]) -> Dict[str, object]:
    """两景获取元数据的可比较性检查（入射角差/轨道混搭 → 警告，不拒绝）。"""
    warnings: List[str] = []
    if len(metas) < 2:
        return {"comparable": True, "warnings": warnings}
    angles = [m.incidence_angle_deg for m in metas if m.incidence_angle_deg is not None]
    if len(angles) == len(metas) and max(angles) - min(angles) > 5.0:
        warnings.append(
            f"入射角差 {max(angles) - min(angles):.1f}° > 5°——后向散射"
            "比值混入入射角效应")
    orbits = {m.orbit_direction for m in metas if m.orbit_direction}
    if len(orbits) > 1:
        warnings.append(f"轨道方向混搭 {sorted(orbits)}——几何/极化响应不可直接比")
    pols = {m.polarization for m in metas}
    if len(pols) > 1:
        warnings.append(f"极化混搭 {sorted(pols)}——不是同极化时序")
    return {"comparable": not warnings, "warnings": warnings}
