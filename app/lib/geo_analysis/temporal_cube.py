"""Temporal Cube（science-v5 W8）—— SAR/光学通用时空立方体适配层。

定位（01-architecture.md D6）：

- **统一时间轴**：栈（T,H,W）+ 秒制时间戳 + 可选质量掩膜——SAR 与光学
  用同一容器；语义差异（单位/噪声/云）由适配器标注，不散落算法里；
- **诚实缺口**：缺时间片/无效像元**计数披露**，绝不静默插值填充——
  填充决策留给下游（phenology 有界 gap-fill，另行计数）；
- 无项目专属类别（不含任何地理硬编码）；纯数据容器 + 校验；
- 资源有界：T ≤ ``CUBE_MAX_SLICES``（类型化拒绝，先拒绝不 OOM）。

错误语义：规模超限 → ``ResourceScaleMismatch``；形状/时间轴非法 →
``DegenerateData``（均为 ValueError 子类）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.lib.gis.scientific_errors import (
    DegenerateData,
    ResourceScaleMismatch,
)

CUBE_MAX_SLICES = 512                    # 时间片硬顶（先拒绝不 OOM）


@dataclass
class TemporalCube:
    """时空立方体（第 0 轴 = 时间序；时间升序）。"""

    stack: np.ndarray                    # (T, H, W) float，无效处 = NaN
    times_sec: np.ndarray                # (T,) 秒制，严格非降
    nodata: Optional[float] = None
    quality: Optional[np.ndarray] = None  # (T, H, W) [0,1]；None = 未提供
    label: str = "temporal_cube"         # 语义标注（sar/optical/...）
    disclosures: list = field(default_factory=list)

    # ── 构造后派生量（惰性语义以属性方法暴露）─────────────────────────

    @property
    def shape(self):
        return self.stack.shape

    @property
    def n_slices(self) -> int:
        return int(self.stack.shape[0])

    def valid_mask(self) -> np.ndarray:
        """有效像元掩膜 (T,H,W)：有限且非 nodata 且 quality>0。"""
        valid = np.isfinite(self.stack)
        if self.quality is not None:
            valid &= self.quality > 0.0
        return valid

    def gap_lengths(self) -> np.ndarray:
        """逐像元最长连续无效 run（0 = 全有效），(H, W)。"""
        invalid = ~self.valid_mask()
        t = self.n_slices
        if not invalid.any():
            return np.zeros(self.stack.shape[1:], dtype=int)
        run = np.zeros(self.stack.shape[1:], dtype=int)
        best = np.zeros(self.stack.shape[1:], dtype=int)
        for i in range(t):
            run = np.where(invalid[i], run + 1, 0)
            best = np.maximum(best, run)
        return best

    def slice_spacing_report(self) -> dict:
        """时间片间距报告：唯一间距、疑似缺失切片数（中位距 1.5× 判据）。"""
        t = np.asarray(self.times_sec, dtype=float)
        if t.size < 2:
            return {"n_slices": int(t.size), "unique_gaps": [], "missing_slices": 0}
        diffs = np.diff(t)
        uniq = np.unique(np.round(diffs, 6))
        median = float(np.median(diffs))
        missing = int(np.sum(diffs > 1.5 * median)) if median > 0 else 0
        return {
            "n_slices": int(t.size),
            "unique_gaps": [float(g) for g in uniq[:32]],   # 有界
            "median_gap_sec": round(median, 3),
            "missing_slices": missing,
        }

    def climatology(self) -> dict:
        """全期逐像元气候态（nan-aware；std ddof=1，n<2 → NaN）。"""
        v = np.where(self.valid_mask(), self.stack, np.nan)
        with np.errstate(invalid="ignore"):
            mean = np.nanmean(v, axis=0)
            n = np.sum(np.isfinite(v), axis=0)
            std = np.nanstd(v, axis=0, ddof=1)
        std = np.where(n >= 2, std, np.nan)
        return {"mean": mean, "std": std, "n_valid": n.astype(int)}


def build_cube(
    stack,
    times_sec,
    *,
    nodata: Optional[float] = None,
    quality=None,
    label: str = "temporal_cube",
) -> TemporalCube:
    """构造时空立方体（全部校验集中于此；不静默修正时间轴）。"""
    arr = np.asarray(stack, dtype=float)
    if arr.ndim != 3:
        raise DegenerateData(
            f"时空立方体需要 (T, H, W) 三维栈，got shape {arr.shape}")
    t = np.asarray(times_sec, dtype=float)
    if t.ndim != 1 or len(t) != arr.shape[0]:
        raise DegenerateData(
            f"时间戳须为与栈第 0 轴等长的一维数组（{arr.shape[0]}），got {len(t)}")
    if len(t) == 0:
        raise DegenerateData("时空立方体不允许空时间轴")
    if not np.isfinite(t).all():
        raise DegenerateData("时间戳含非有限值（须为 epoch/相对秒）")
    if len(t) > CUBE_MAX_SLICES:
        raise ResourceScaleMismatch(
            f"时间片 {len(t)} 超过上限 {CUBE_MAX_SLICES}",
            estimated=f"{len(t)} slices", limit=f"≤{CUBE_MAX_SLICES}",
            correction_hint="按时间窗切片或降采样时间轴")
    if arr.shape[0] > 1 and np.any(np.diff(t) < 0):
        raise DegenerateData(
            "时间轴非升序——请先排序（不静默重排：时间序即数据语义）")
    disclosures: list = []
    if arr.shape[0] > 1 and np.any(np.diff(t) == 0):
        n_dup = int(np.sum(np.diff(t) == 0))
        disclosures.append(
            f"{n_dup} 对重复时间戳（非降序允许；统计语义按重复切片处理）")
    q = None
    if quality is not None:
        q = np.asarray(quality, dtype=float)
        if q.shape != arr.shape:
            raise DegenerateData(
                f"质量掩膜形状 {q.shape} 与栈 {arr.shape} 不一致")
        if np.isfinite(q).any() and (np.nanmin(q) < 0 or np.nanmax(q) > 1):
            raise DegenerateData("质量掩膜须在 [0, 1]（可用度语义）")
        disclosures.append("quality 掩膜已启用（quality=0 切片视为无效）")
    values = arr.copy()
    invalid = ~np.isfinite(values)
    if nodata is not None:
        invalid |= values == float(nodata)
        disclosures.append(f"nodata={nodata} 像元置 NaN（计数见 gap 报告）")
    values[invalid] = np.nan
    n_invalid = int(invalid.sum())
    if n_invalid:
        disclosures.append(
            f"{n_invalid} 个无效像元-切片（NaN；占 {n_invalid / values.size:.2%}）")
    return TemporalCube(
        stack=values, times_sec=t, nodata=nodata, quality=q,
        label=str(label)[:32], disclosures=disclosures)


def from_sar_stack(stack, times_sec, *, nodata: Optional[float] = None,
                   quality=None) -> TemporalCube:
    """SAR 时序栈适配（后向散射强度/分贝制由调用方声明并保持一致）。"""
    cube = build_cube(stack, times_sec, nodata=nodata, quality=quality,
                      label="sar")
    cube.disclosures.insert(0,
        "SAR 适配：单位一致性（强度/分贝）由调用方保证——混合单位栈不做"
        "统计（comparability 语义与 sar_temporal 一致）")
    return cube


def from_optical_stack(stack, times_sec, *, nodata: Optional[float] = None,
                       cloud_mask=None) -> TemporalCube:
    """光学时序栈适配；``cloud_mask`` (T,H,W) 0/1（1 = 可用）入 quality。"""
    quality = None
    if cloud_mask is not None:
        quality = np.asarray(cloud_mask, dtype=float).clip(0.0, 1.0)
    cube = build_cube(stack, times_sec, nodata=nodata, quality=quality,
                      label="optical")
    if cloud_mask is not None:
        cube.disclosures.insert(0,
            "光学适配：cloud_mask 已入 quality（1=可用；可与 cloud_qc_basic "
            "输出对接——本模块不做云判识，只消费掩膜）")
    return cube
