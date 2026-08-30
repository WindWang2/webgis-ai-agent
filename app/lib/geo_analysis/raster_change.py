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
