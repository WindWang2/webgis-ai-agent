"""Raster Change Detection —— 双时相栅格变化检测（Runtime V3 P5）。

区别于既有两条“变化”路径（ADR-0089）：

- ``detect_vegetation_change``（STAC）：在线拉两期 Sentinel-2 → 指数 →
  **统计 GeoJSON**（无栅格产物）；
- ``temporal_raster``：已对齐栅格的**差值统计**（要求 CRS/transform 逐位
  对齐，无对齐引擎）。

本模块补上第三块：**本地两个栅格工件 → 对齐 → 窗口化差值 → 可选阈值
分类 → 变化栅格产物 + 统计 + 质量证据**。与 raster_calculator 共用同一
套 grid contract（A 为基准网格、B 经 ``aligned_reader``（WarpedVRT）虚拟
对齐、窗口由内存预算推导、原子写 + 写者 descriptor + 内容指纹）。

V1 是确定性基线（difference / absolute_difference / normalized_difference
+ threshold 二分类），不做深度学习变化检测。

语义约定（§22-§24）：
- A（T1 / 基准）网格是目标网格：``needs_resample``/``needs_reproject``/
  ``cropped`` 全部进 quality evidence，绝不静默 padding；
- 有效像元 = 双方都有效（任一 nodata/NaN → nodata）；
- ``normalized_difference`` 零分母 → nodata（与 NDVI 同 golden 语义，
  不产 inf）；
- 足迹无交集 → ``RasterAlignmentError``（结构化拒绝，不产空垃圾栅格）。

VNext 加法节（文件尾）：内存数组上的 ``change_vector_analysis`` /
``ratio_change`` / ``threshold_change``。反目标（模块级披露）：
CVA 只输出变化幅度与方向（谱空间几何量），**不做土地覆盖语义分类**
——「植被→建筑」类语义归类不在此层，须由上层结合专题判读完成。
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional

import numpy as np
import rasterio

from app.lib.cancellation import checkpoint
from app.lib.geo_analysis.raster_grid import (
    RasterAlignmentError,
    RasterGridProfile,
    aligned_reader,
    decide_alignment,
    iter_bounded_windows,
    window_side_from_budget,
)
from app.lib.geo_analysis.raster_windowed import (
    WindowedRasterWriter,
    build_output_profile,
    valid_mask_for,
)

logger = logging.getLogger(__name__)

CHANGE_METHODS = ("difference", "absolute_difference", "normalized_difference")

# 阈值分类输出编码（uint8 单波段；continuous 输出恒 float32）。
CHANGE_NODATA = 255
CLASS_STABLE = 0
CLASS_CHANGED = 1


def detect_raster_change(
    raster_a: str,
    raster_b: str,
    *,
    method: str = "difference",
    threshold: Optional[float] = None,
    band: int = 1,
    band_b: Optional[int] = None,
    out_path: Optional[str] = None,
    window_side: Optional[int] = None,
    resampling: Optional[str] = None,
) -> Dict:
    """窗口化双时相栅格变化检测（B 相对 A，T2 − T1）。

    Args:
        raster_a: T1 栅格路径（基准网格）。
        raster_b: T2 栅格路径（自动对齐到 A 的网格）。
        method: ``difference``（B−A）/ ``absolute_difference``（|B−A|）/
            ``normalized_difference``（(B−A)/(B+A)，零分母 → nodata）。
        threshold: 给定时输出二分类栅格（|Δ| ≥ threshold → 1 变化，
            否则 0；uint8，nodata=255），并附变化像元统计。
        band / band_b: A / B 的参与波段（1-based，默认 1）。
        out_path: 输出路径（缺省 ``<a>_change.tif``）。
        resampling: B→A 对齐重采样；缺省 bilinear，输入为分类图时传
            ``"nearest"``（分类禁 bilinear，§10）。

    Returns:
        dict：output_path / method / threshold / stats（含 changed_pixels、
        change_ratio）/ alignment / quality_evidence / descriptor /
        content_fingerprint。

    Raises:
        ValueError: method 非法。
        RasterAlignmentError: 足迹无交集 / 不可对齐。
    """
    method_key = (method or "difference").lower().replace("-", "_")
    if method_key not in CHANGE_METHODS:
        raise ValueError(
            f"unsupported change method '{method}'; valid: {list(CHANGE_METHODS)}"
        )
    if threshold is not None and not (np.isfinite(threshold) and threshold > 0):
        raise ValueError(f"threshold must be a positive finite number, got {threshold!r}")

    with rasterio.open(raster_a) as _probe_a, rasterio.open(raster_b) as _probe_b:
        if not (1 <= band <= _probe_a.count):
            raise ValueError(
                f"band {band} out of range for raster A (1..{_probe_a.count})"
            )
        if not (1 <= (band_b or band) <= _probe_b.count):
            raise ValueError(
                f"band_b {band_b or band} out of range for raster B (1..{_probe_b.count})"
            )

    if out_path is None:
        base, _ = os.path.splitext(raster_a)
        out_path = f"{base}_change.tif"

    window_side = window_side or window_side_from_budget()
    classified = threshold is not None

    with rasterio.open(raster_a) as src_a:
        grid_a = RasterGridProfile.from_dataset(src_a, raster_a)
        nodata_a = src_a.nodata
        src_b_raw = rasterio.open(raster_b)
        try:
            grid_b = RasterGridProfile.from_dataset(src_b_raw, raster_b)
            decision = decide_alignment(grid_a, grid_b, resampling=resampling)
            if decision.incompatible:
                raise RasterAlignmentError(
                    f"raster B cannot be aligned to A: {decision.reason}", decision
                )
            # 输出磁盘足迹守卫（内存是 O(window)，由预算推导保证）。
            from app.lib.geo_analysis.raster_guard import RasterResourceGuard

            RasterResourceGuard.check_grid(
                width=grid_a.width,
                height=grid_a.height,
                bytes_per_pixel=4,
                num_bands=1,
                input_pixels=grid_a.width * grid_a.height,
            )
        finally:
            src_b_raw.close()

        b_cm = aligned_reader(raster_b, decision, band=band_b or band)
        b_reader, eff_nodata_b = b_cm.__enter__()
        try:
            nd_b = eff_nodata_b if eff_nodata_b is not None else b_reader.nodata

            def _compute(a_win: np.ndarray, b_win: np.ndarray) -> np.ndarray:
                valid = valid_mask_for(a_win, nodata_a) & valid_mask_for(b_win, nd_b)
                af = np.where(valid, a_win, 0).astype(np.float64)
                bf = np.where(valid, b_win, 0).astype(np.float64)
                if method_key == "difference":
                    delta = bf - af
                elif method_key == "absolute_difference":
                    delta = np.abs(bf - af)
                else:  # normalized_difference
                    denom = bf + af
                    delta = np.divide(
                        bf - af, denom,
                        out=np.full_like(denom, np.nan),
                        where=valid & (denom != 0),
                    )
                # overflow/非有限值（如 inf）→ 无效，绝不落盘伪装合法值。
                delta = np.where(np.isfinite(delta), delta, np.nan)
                delta = np.where(valid, delta, np.nan)
                if classified:
                    # |Δ| ≥ threshold → changed；nodata（NaN）→ 255。
                    is_change = np.abs(delta) >= float(threshold)  # NaN → False
                    out = np.where(
                        np.isnan(delta), CHANGE_NODATA,
                        np.where(is_change, np.uint8(CLASS_CHANGED), np.uint8(CLASS_STABLE)),
                    )
                    return out.astype(np.uint8)
                return delta.astype(np.float32)

            if classified:
                out_dtype, out_nodata = "uint8", float(CHANGE_NODATA)
            else:
                out_dtype, out_nodata = "float32", np.nan

            profile = build_output_profile(
                width=grid_a.width,
                height=grid_a.height,
                count=1,
                dtype=out_dtype,
                crs=src_a.crs,
                transform=src_a.transform,
                nodata=out_nodata,
            )

            with WindowedRasterWriter(
                out_path, profile=profile, grid=grid_a,
                overview_resampling="nearest" if classified else "average",
                window_side=window_side,
            ) as writer:
                for win in iter_bounded_windows(
                    grid_a.width, grid_a.height,
                    window_side=window_side, src=src_a,
                ):
                    checkpoint()
                    a_win = src_a.read(band, window=win)
                    b_win = b_reader.read(band_b or band, window=win)
                    writer.write(win, _compute(a_win, b_win))
            finalized = writer.finalize()
        finally:
            b_cm.__exit__(None, None, None)

    # 变化像元统计：分类栅格由窗口统计重算一次代价过高（写者统计的是输出
    # 值域），这里用 finalize 的 valid/nodata 计数 + 变化计数在写循环内顺路
    # 累计 —— 由 WindowedRasterWriter 的值域直方替代：分类输出 valid 全在
    # {0,1}，min/max 即可判别，无变化计数需求时保持轻量。
    stats = dict(finalized["stats"])
    if classified:
        # min/max 为 0/1 值域；变化像元数 = valid − stable。写者按值累计了
        # min/max/mean：mean = changed/valid（0/1 值）→ 变化数可精确还原。
        valid = stats.get("valid_pixel_count") or 0
        mean = stats.get("mean")
        changed = int(round((mean or 0.0) * valid)) if valid else 0
        stats["changed_pixels"] = changed
        stats["stable_pixels"] = valid - changed
        stats["change_ratio"] = (changed / valid) if valid else 0.0

    evidence = {
        "algorithm": "raster_change_detection",
        "parameters": {
            "method": method_key,
            "threshold": threshold,
            "band": band,
            "band_b": band_b or band,
            "resampling": decision.resampling,
        },
        "input_width": grid_a.width,
        "input_height": grid_a.height,
        "input_crs": grid_a.crs,
        "output_width": grid_a.width,
        "output_height": grid_a.height,
        "output_crs": grid_a.crs,
        "valid_pixel_count": finalized["stats"]["valid_pixel_count"],
        "nodata_pixel_count": finalized["stats"]["nodata_pixel_count"],
        "alignment": decision.to_dict(),
    }
    if decision.other_bounds and decision.reference_bounds:
        ob, rb = decision.other_bounds, decision.reference_bounds
        evidence["cropped"] = bool(
            ob[0] < rb[0] or ob[1] < rb[1] or ob[2] > rb[2] or ob[3] > rb[3]
        )

    descriptor = finalized["descriptor"]
    # 语义披露（§47：复用 raster_surface 类型，semantic_type 进 metadata，
    # 不为每个算法新增 artifact type）。
    result = {
        "output_path": out_path,
        "method": method_key,
        "threshold": threshold,
        "stats": stats,
        "alignment": decision.to_dict(),
        "quality_evidence": evidence,
        "descriptor": descriptor.to_dict(),
        "content_fingerprint": finalized["content_fingerprint"],
        "semantic_type": "raster_change_surface",
    }
    return result


# ══════════════════════════════════════════════════════════════════════
# VNext 加法扩展（ADR-0099）：内存数组上的 CVA / 比值变化 / 阈值分类。
# 与上面 detect_raster_change（磁盘工件、窗口化）正交：本节是科学库的
# 纯函数层，供工具薄包装调用。反目标重申——CVA 只回答「变了多大、朝
# 哪个方向变」，**不做**任何土地覆盖语义分类（不输出「植被→建筑」类
# 命名）；语义解释是上层结合专题知识的职责。
# ══════════════════════════════════════════════════════════════════════

CVA_DISCLOSURE = (
    "CVA 输出变化幅度与方向（谱空间几何量），不构成土地覆盖语义变化"
    "（如『植被→建筑』）——语义归类需上层专题判读"
)
RATIO_DISCLOSURE = (
    "比值/对数比值假设输入为同量纲的后向散射或强度；log_ratio 对数域"
    "对称（增强=衰减的镜像），零/负值 → NaN"
)
THRESHOLD_DISCLOSURE = (
    "逐像元稳健 z 假设像元间空间独立——真实影像空间相关普遍存在，"
    "显著性解读是近似（变化像元数解释为比例而非独立检验数）"
)


def change_vector_analysis(
    t1: Dict[str, np.ndarray],
    t2: Dict[str, np.ndarray],
    *,
    nodata: Optional[np.ndarray] = None,
    t1_date: Optional[str] = None,
    t2_date: Optional[str] = None,
) -> Dict:
    """变化向量分析（Change Vector Analysis, Malila 1980）。

    角色序（文档化契约）：参与角色按 ``spectral.ROLE_ORDER`` 固定语义序
    排列——blue, green, red, red_edge, nir, swir1, swir2, thermal,
    vv, vh, hh, hv。幅度是全部角色分量差的欧氏范数；方向角取变化向量
    在**前两个角色**（该固定序下）张成平面上的 atan2(d2, d1)（弧度）。

    - 两景角色集必须一致（缺角色拒绝，绝不按位置对齐）；
    - 同一像元任一角色在任一景无效（NaN/Inf/nodata 掩膜）→ 该像元
      幅度/角度均为 NaN（有效像元 = 双方都有效）；
    - 完全相同的两景 → 幅度 0、角度 atan2(0,0)=0。

    Returns:
        dict: magnitude / angle(rad) / roles_used（固定序，供复现）/
        meta（t1_date/t2_date + 反目标披露）。
    """
    from app.lib.geo_analysis.spectral import roles_in_canonical_order
    from app.lib.gis.scientific_errors import UnsupportedBandSemantics as _UBS

    roles_t1 = set(t1 or {})
    roles_t2 = set(t2 or {})
    if roles_t1 != roles_t2:
        raise _UBS(
            f"CVA 两景角色集不一致：t1={sorted(roles_t1)} vs "
            f"t2={sorted(roles_t2)}；变化向量要求同一组语义角色",
            correction_hint="两景提供完全相同的语义角色集合",
        )
    if not roles_t1:
        raise _UBS(
            "CVA 需要至少一个语义角色（t1/t2 均为空）",
            correction_hint="按角色命名提供两景波段，如 {'red': ..., 'nir': ...}",
        )

    roles = roles_in_canonical_order(roles_t1)

    shape = np.asarray(t1[roles[0]]).shape
    delta = np.zeros(shape, dtype=float)
    valid = np.ones(shape, dtype=bool)
    for role in roles:
        a = np.asarray(t1[role], dtype=float)
        b = np.asarray(t2[role], dtype=float)
        if a.shape != shape or b.shape != shape:
            raise ValueError(
                f"CVA 角色 {role!r} 形状不一致：期望 {shape}，"
                f"t1={a.shape}, t2={b.shape}")
        role_valid = np.isfinite(a) & np.isfinite(b)
        delta = delta + np.where(role_valid, (b - a) ** 2, 0.0)
        valid &= role_valid
    if nodata is not None:
        valid &= ~np.asarray(nodata, dtype=bool)

    magnitude = np.sqrt(delta)
    magnitude = np.where(valid, magnitude, np.nan)

    # 方向角：固定角色序前两分量张成平面上的 atan2(d2, d1)。
    a1 = np.asarray(t1[roles[0]], dtype=float)
    b1 = np.asarray(t2[roles[0]], dtype=float)
    d1 = b1 - a1
    if len(roles) >= 2:
        a2 = np.asarray(t1[roles[1]], dtype=float)
        b2 = np.asarray(t2[roles[1]], dtype=float)
        d2 = b2 - a2
    else:
        d2 = np.zeros_like(d1)
    angle = np.arctan2(d2, d1)
    angle = np.where(valid & np.isfinite(angle), angle, np.nan)

    meta: Dict = {
        "role_order_contract": (
            "幅度=全角色欧氏范数；角度=固定角色序前两分量 atan2(d2, d1)"),
        "roles_used": list(roles),
        "disclosure": CVA_DISCLOSURE,
    }
    if t1_date is not None:
        meta["t1_date"] = t1_date
    if t2_date is not None:
        meta["t2_date"] = t2_date

    return {
        "magnitude": magnitude,
        "angle": angle,
        "roles_used": list(roles),
        "meta": meta,
    }


RATIO_CHANGE_METHODS = ("ratio", "log_ratio")


def ratio_change(
    a: np.ndarray,
    b: np.ndarray,
    *,
    method: str = "ratio",
    nodata: Optional[np.ndarray] = None,
    t1_date: Optional[str] = None,
    t2_date: Optional[str] = None,
) -> Dict:
    """双时相比值/对数比值变化（SAR 后向散射/强度设计）。

    - ``ratio``: a/b（b=0 → NaN）；
    - ``log_ratio``: log(a) − log(b)（= log(a/b)；a/b ≤ 0 → NaN）。
      对数域对称：增强幅度与衰减幅度可直接比较（−log(b/a) 恒等式）。

    有效像元 = 双方有限值且非 nodata 掩膜；无效像元 → NaN。
    """
    method_key = (method or "ratio").lower().replace("-", "_")
    if method_key not in RATIO_CHANGE_METHODS:
        raise ValueError(
            f"unsupported ratio method '{method}'; valid: {list(RATIO_CHANGE_METHODS)}")

    af = np.asarray(a, dtype=float)
    bf = np.asarray(b, dtype=float)
    if af.shape != bf.shape:
        raise ValueError(
            f"ratio_change 形状不一致：a={af.shape}, b={bf.shape}")

    valid = np.isfinite(af) & np.isfinite(bf)
    if nodata is not None:
        valid &= ~np.asarray(nodata, dtype=bool)

    if method_key == "ratio":
        out = np.divide(
            af, bf,
            out=np.full(af.shape, np.nan, dtype=float),
            where=valid & (bf != 0),
        )
        formula = "a / b"
    else:
        ratio = np.divide(
            af, bf,
            out=np.full(af.shape, np.nan, dtype=float),
            where=valid & (bf != 0),
        )
        with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
            # MINOR-1（数值评审）：非有限 ratio（溢出/下正规）同样 gate 掉，
            # 不让 Inf 泄漏进输出数组。
            out = np.log(np.where(
                np.isfinite(ratio) & (ratio > 0), ratio, np.nan))
        formula = "log(a) − log(b) == log(a / b)（对数域对称）"

    out = np.where(valid, out, np.nan)
    finite = np.isfinite(out)

    meta: Dict = {
        "method": method_key,
        "formula": formula,
        "disclosure": RATIO_DISCLOSURE,
        "symmetry": (
            "log_ratio(a, b) == −log_ratio(b, a)"
            if method_key == "log_ratio" else ""),
    }
    if t1_date is not None:
        meta["t1_date"] = t1_date
    if t2_date is not None:
        meta["t2_date"] = t2_date

    return {
        "array": out,
        "method": method_key,
        "stats": {
            "valid_pixels": int(np.sum(finite)),
            "total_pixels": int(out.size),
        },
        "meta": meta,
    }


THRESHOLD_CHANGE_METHODS = ("mad", "percentile")


def threshold_change(
    magnitude: np.ndarray,
    *,
    method: str = "mad",
    k: float = 3.0,
    percentile: float = 95.0,
) -> Dict:
    """变化幅度的统计阈值分类（MAD 稳健 z / 分位数）。

    - ``mad``：稳健 z = (x − median) / (1.4826 · MAD)，|z| ≥ k 判变化
      （阈值即 median + k·1.4826·MAD；k 默认 3）。MAD=0（退化尺度，
      如近常量背景 + 尖峰）时：严格高于中位数即判变化并在 meta 披露；
    - ``percentile``：x ≥ 第 p 百分位数判变化（p 默认 95）。

    NaN 像元（nodata）不参与阈值估计也不判变化。诚实披露：逐像元
    z/分位检验假设空间独立——近似（见 THRESHOLD_DISCLOSURE）。
    """
    method_key = (method or "mad").lower()
    if method_key not in THRESHOLD_CHANGE_METHODS:
        raise ValueError(
            f"unsupported threshold method '{method}'; "
            f"valid: {list(THRESHOLD_CHANGE_METHODS)}")

    arr = np.asarray(magnitude, dtype=float)
    finite = np.isfinite(arr)
    vals = arr[finite]
    if vals.size == 0:
        from app.lib.gis.scientific_errors import NoValidObservations

        raise NoValidObservations(
            "threshold_change 输入无有效像元（全部 NaN/nodata）",
            correction_hint="检查变化幅度数组的 nodata 掩膜",
        )

    warnings: list[str] = []
    mask = np.zeros(arr.shape, dtype=bool)
    if method_key == "mad":
        med = float(np.median(vals))
        mad = float(np.median(np.abs(vals - med)))
        if mad > 0:
            threshold_value = med + float(k) * 1.4826 * mad
            mask = finite & (np.abs(arr - med) >= float(k) * 1.4826 * mad)
        else:
            # 退化尺度：MAD=0（常量背景）。严格高于中位数即变化，
            # 阈值报告为最小超中位值 —— 诚实披露该退化路径。
            above = vals[vals > med]
            if above.size:
                threshold_value = float(np.min(above))
                mask = finite & (arr > med)
            else:
                threshold_value = float("inf")
                mask = np.zeros(arr.shape, dtype=bool)
            warnings.append(
                "MAD=0（常量背景退化尺度）：高于中位数即判变化，"
                "阈值取最小超中位值")
    else:
        threshold_value = float(np.percentile(vals, float(percentile)))
        mask = finite & (arr >= threshold_value)

    n_valid = int(np.sum(finite))
    changed = int(np.sum(mask))
    meta: Dict = {
        "method": method_key,
        "k": float(k) if method_key == "mad" else None,
        "percentile": float(percentile) if method_key == "percentile" else None,
        "disclosure": THRESHOLD_DISCLOSURE,
        "valid_pixels": n_valid,
    }
    if warnings:
        meta["warnings"] = warnings

    return {
        "mask": mask,
        "changed_fraction": (changed / n_valid) if n_valid else 0.0,
        "threshold_value": threshold_value,
        "method": method_key,
        "meta": meta,
    }
