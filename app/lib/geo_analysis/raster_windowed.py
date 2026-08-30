"""Windowed Raster Execution Core —— 共享窗口化执行底座（Runtime V3）。

把「open → iterate windows → read → compute → write → close」从
raster_calculator / reclassify / calculate_ndvi 各自的循环收编为一个
共享原语层，**不是**新执行 runtime（ToolRegistry 仍是唯一工具执行运行时）：

- ``build_output_profile()`` —— 统一输出 GTiff profile（driver/tiled/
  compression/dtype/crs/transform/nodata），算法不得各自 copy profile
  后漏字段（P7）；
- ``WindowStats`` —— 窗口内累计的统计与证据（valid/nodata 计数、
  min/max/sum），处理完成后**不再**重新 open/read 全图只为出报告（§36）；
- ``WindowedRasterWriter`` —— 原子写（tmp → finalize → replace）、窗口写
  入、边写边累计**内容摘要**（content digest，零额外 IO）、有界 overview
  金字塔、以及**写者已知**的 ``RasterArtifactDescriptor``（不重开文件，
  P10）；
- ``windowed_band_index()`` —— 统一光谱指数窗口化执行（NDVI/NDWI/NBR/EVI
  共用；公式 truth 在 ``app.services.rs.band_math.INDEX_FORMULAS``，此处
  只是执行底座，不复制数学）。

资源治理：窗口内存 O(window)（边长由 ``raster_grid.window_side_from_budget``
从 ``RASTER_PROCESSING_MEMORY_MB`` 推导）；窗口循环默认串行；GDAL 线程数
在 ``rasterio_env()`` 内钉为 1（§42 禁无界并行）。
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window

from app.lib.artifacts import atomic_output
from app.lib.cancellation import checkpoint
from app.lib.geo_analysis.raster_grid import (
    RasterGridProfile,
    iter_bounded_windows,
    window_side_from_budget,
)

logger = logging.getLogger(__name__)

# ── 统一输出 profile（P7）───────────────────────────────────────────


def build_output_profile(
    *,
    width: int,
    height: int,
    count: int = 1,
    dtype: str = "float32",
    crs=None,
    transform=None,
    nodata: Optional[float] = None,
) -> dict:
    """所有栅格算法共用的输出 GTiff profile。

    tiled(256) + LZW：瓦片服务 / 产物检视 / range read 友好（ADR-0089）。
    每个字段都在这里设一次，算法不得再 copy 后各自补漏。
    """
    profile: Dict[str, Any] = {
        "driver": "GTiff",
        "width": int(width),
        "height": int(height),
        "count": int(count),
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "compress": "lzw",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    return profile


# overview 金字塔的有界策略：只为“瓦片服务降采样读确实会走 overview”的
# 尺寸建（最长边 > OVERVIEW_MIN_SIDE），因子逐级 ÷2 且保证顶层仍有
# ≥256 像元；连续量 average、分类量 nearest。
OVERVIEW_MIN_SIDE = 512
_OVERVIEW_BASE = 256
_OVERVIEW_MAX_FACTORS = 4


def overview_factors(width: int, height: int) -> List[int]:
    factors: List[int] = []
    short_side = min(width, height)
    for i in range(1, _OVERVIEW_MAX_FACTORS + 1):
        f = 2 ** i
        if short_side // f >= _OVERVIEW_BASE:
            factors.append(f)
    return factors


# ── 窗口统计 / 证据累计 ─────────────────────────────────────────────


def valid_mask_for(arr: np.ndarray, nodata) -> np.ndarray:
    """有效像元掩膜（NaN-nodata / 未声明 NaN 都正确剔除）。

    与 raster_math._nodata_valid_mask 同语义；本模块自持一份避免
    执行底座反向依赖具体算法模块。
    """
    if nodata is None:
        base = np.ones(arr.shape, dtype=bool)
    elif isinstance(nodata, float) and np.isnan(nodata):
        base = ~np.isnan(arr)
    else:
        base = arr != nodata
    if np.issubdtype(arr.dtype, np.floating):
        base = base & ~np.isnan(arr) & np.isfinite(arr)
    return base


class WindowStats:
    """跨窗口累计的有界统计（写循环内顺路累计，§36：零二次扫描）。"""

    __slots__ = ("valid", "nodata", "total", "min_v", "max_v", "sum_v")

    def __init__(self) -> None:
        self.valid = 0
        self.nodata = 0
        self.total = 0
        self.min_v: Optional[float] = None
        self.max_v: Optional[float] = None
        self.sum_v = 0.0

    def update(self, arr: np.ndarray, nodata) -> None:
        valid = valid_mask_for(arr, nodata)
        self.total += int(arr.size)
        self.nodata += int(arr.size - int(valid.sum()))
        n = int(valid.sum())
        if n == 0:
            return
        self.valid += n
        vals = arr[valid].astype(np.float64, copy=False)
        vmin = float(vals.min())
        vmax = float(vals.max())
        self.sum_v += float(vals.sum())
        self.min_v = vmin if self.min_v is None else min(self.min_v, vmin)
        self.max_v = vmax if self.max_v is None else max(self.max_v, vmax)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid_pixel_count": self.valid,
            "nodata_pixel_count": self.nodata,
            "total_pixel_count": self.total,
            "min": self.min_v,
            "max": self.max_v,
            "mean": (self.sum_v / self.valid) if self.valid else None,
        }


# ── 窗口化写者（原子 + 摘要 + descriptor + overview）────────────────


class WindowedRasterWriter:
    """窗口化原子写者。

    enter：``atomic_output`` 临时文件 + rasterio 写数据集；
    每窗口 ``write()``：写窗口 + 累计统计 + 内容摘要（sha256，按窗口
    字节流顺序）；
    exit（成功）：有界 overview → close → 原子 replace；
    exit（异常/取消）：关闭并清理临时文件，绝不留下“看似有效的半个栅格”。

    ``finalize()`` 返回写者已知事实（stats / descriptor / content_fingerprint
    / output_path）—— 不重开输出文件（P10 descriptor V2）。
    """

    def __init__(
        self,
        out_path: str,
        *,
        profile: dict,
        grid: RasterGridProfile,
        overview_resampling: str = "average",
        window_side: Optional[int] = None,
    ) -> None:
        self.out_path = out_path
        self.profile = profile
        self.grid = grid
        self.overview_resampling = Resampling[overview_resampling]
        self.window_side = window_side or window_side_from_budget()
        self.stats = WindowStats()
        self._digest = hashlib.sha256()
        self._seeded = False
        self._finalized: Optional[Dict[str, Any]] = None
        self._tmp_ctx = None
        self._dst: Optional[rasterio.io.DatasetWriter] = None

    # – 生命周期 –
    def __enter__(self) -> "WindowedRasterWriter":
        self._tmp_ctx = atomic_output(self.out_path)
        tmp_path = self._tmp_ctx.__enter__()
        self._dst = rasterio.open(tmp_path, "w", **self.profile)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._dst is not None:
            if exc_type is None and self._finalized is None:
                self._close_with_overviews()
            else:
                self._dst.close()
        if self._tmp_ctx is not None:
            self._tmp_ctx.__exit__(exc_type, exc, tb)

    def _close_with_overviews(self) -> None:
        assert self._dst is not None
        try:
            factors = overview_factors(self.grid.width, self.grid.height)
            if factors and max(self.grid.width, self.grid.height) > OVERVIEW_MIN_SIDE:
                self._dst.build_overviews(factors, resampling=self.overview_resampling)
        except Exception as e:  # noqa: BLE001 — overview 是增值，失败不阻断产物
            logger.warning("[raster_windowed] overview build skipped: %s", e)
        finally:
            self._dst.close()

    # – 写入 –
    def write(
        self,
        win: Window,
        arr: np.ndarray,
        band: int = 1,
        stats_arr: Optional[np.ndarray] = None,
    ) -> None:
        """写一个窗口并顺路累计统计/摘要。调用方负责 checkpoint()。

        ``stats_arr``：统计/证据用的真值数组（缺省 = 写入数组）。写入数组
        是落盘 dtype（如 float32），但算法真值可能在更高精度（float64）——
        统计按真值累计（与既有 NDVI 契约一致：mean 描述 float64 真值，
        不是 float32 舍入后的字节）。
        """
        assert self._dst is not None
        self._dst.write(arr, band, window=win)
        if not self._seeded:
            # 摘要种子 = 网格身份（同内容不同路径/mtime → 同摘要；§32）。
            self._digest.update(
                repr(
                    (
                        self.grid.width, self.grid.height, self.grid.crs,
                        tuple(round(v, 12) for v in self.grid.transform),
                        self.grid.dtype, self.grid.nodata, self.grid.band_count,
                        self.profile.get("count", 1), str(arr.dtype),
                    )
                ).encode()
            )
            self._seeded = True
        self._digest.update(np.ascontiguousarray(arr).tobytes())
        self.stats.update(stats_arr if stats_arr is not None else arr, self.profile.get("nodata"))

    # – 产物 –
    def finalize(self) -> Dict[str, Any]:
        """写者已知事实（零重开）。在 writer 退出前调用。"""
        from app.schemas.raster_spec import RasterArtifactDescriptor, RasterBandInfo

        vmin, vmax = self.stats.min_v, self.stats.max_v
        descriptor = RasterArtifactDescriptor(
            file_path=self.out_path,
            width=self.grid.width,
            height=self.grid.height,
            crs=self.grid.crs,
            bounds=(
                list(self.grid.bounds) if self.grid.bounds else None
            ),
            dtype=str(self.profile.get("dtype") or self.grid.dtype),
            nodata=self.profile.get("nodata"),
            band_count=int(self.profile.get("count", 1)),
            bands=[
                RasterBandInfo(index=1, dtype=str(self.profile.get("dtype")), vmin=vmin, vmax=vmax)
            ] * int(self.profile.get("count", 1)),
            has_overviews=bool(
                overview_factors(self.grid.width, self.grid.height)
                and max(self.grid.width, self.grid.height) > OVERVIEW_MIN_SIDE
            ),
            transform=[float(v) for v in self.grid.transform],
            resolution_x=self.grid.resolution_x,
            resolution_y=self.grid.resolution_y,
            driver="GTiff",
        )
        self._finalized = {
            "output_path": self.out_path,
            "stats": self.stats.to_dict(),
            "descriptor": descriptor,
            "content_fingerprint": self._digest.hexdigest(),
        }
        return self._finalized


# ── 共享光谱指数窗口化执行（P4）─────────────────────────────────────

# 指数 → 波段角色契约（truth：app/services/rs/band_math.INDEX_FORMULAS）。
# 每种指数在此明确 required bands / 输出语义，供波段探测与契约测试。
INDEX_BAND_ROLES: Dict[str, Tuple[str, ...]] = {
    "ndvi": ("red", "nir"),
    "ndwi": ("green", "nir"),
    "nbr": ("nir", "swir1"),
    "evi": ("blue", "red", "nir"),
}
INDEX_VALID_RANGE: Dict[str, Tuple[float, float]] = {
    "ndvi": (-1.0, 1.0),
    "ndwi": (-1.0, 1.0),
    "nbr": (-1.0, 1.0),
    # EVI 物理上可达 ~[-1, 2.5]（公式系数 2.5）。
    "evi": (-1.0, 2.5),
}

# DN→反射率判定的有界扫描边长（与 inspect/stats 同数量级；EVI 的全局
# max 判定必须一次做完，不能逐窗口抖动）。
_DN_SCAN_MAX_SIDE = 512


def _resolve_band_map(index_type: str, band_map: Dict[str, int]) -> Tuple[str, ...]:
    idx = index_type.lower()
    roles = INDEX_BAND_ROLES.get(idx)
    if roles is None:
        raise ValueError(
            f"unsupported index type '{index_type}'; valid: {sorted(INDEX_BAND_ROLES)}"
        )
    missing = [r for r in roles if not isinstance(band_map.get(r), int) or band_map[r] < 1]
    if missing:
        raise ValueError(
            f"index '{idx}' requires band role(s) {roles}; missing/unset: {missing}. "
            "Specify band indices explicitly."
        )
    return roles


def _decimated_band_max(src, band: int, max_side: int = _DN_SCAN_MAX_SIDE) -> float:
    """有界（≤512 边）降采样读，返回该波段有限值最大者（EVI DN 判定）。"""
    scale = min(1.0, max_side / float(max(src.width, src.height)))
    out_shape = (1, max(1, int(round(src.height * scale))), max(1, int(round(src.width * scale))))
    arr = src.read(band, out_shape=out_shape, masked=True)
    data = np.ma.getdata(arr)
    mask = np.ma.getmaskarray(arr)
    valid = np.isfinite(data) & ~mask
    if not valid.any():
        return float("nan")
    return float(data[valid].max())


def windowed_band_index(
    in_path: str,
    index_type: str,
    *,
    band_map: Dict[str, int],
    out_path: Optional[str] = None,
    window_side: Optional[int] = None,
) -> Dict[str, Any]:
    """窗口化计算单数据集光谱指数（NDVI/NDWI/NBR/EVI 共用底座）。

    数学 truth 是 ``app.services.rs.band_math.compute_index_array`` 的公式
    lambdas（含零分母 → NaN 的 golden 语义）；本函数提供窗口化执行：
    逐窗口读各波段 → 联合 nodata/NaN 掩膜 → 公式 → 写 float32（nodata
    -9999，与既有 NDVI 产物头/字节契约一致，#537）。

    EVI 的 DN→反射率归一是全局判定：循环前做一次有界（≤512 边）降采样
    扫描，之后所有窗口统一应用，避免逐窗口判定抖动。
    """
    from app.services.rs.band_math import INDEX_FORMULAS

    idx = index_type.lower()
    roles = _resolve_band_map(idx, band_map)
    # 公式 lambda 按位置取参；INDEX_BAND_ROLES 的角色顺序与
    # INDEX_FORMULAS 的 bands_needed 顺序一一对应（构造时保证）。
    bands_needed, formula = INDEX_FORMULAS[idx]
    if len(bands_needed) != len(roles):  # 防御：契约漂移早失败
        raise ValueError(
            f"index '{idx}' role/formula band order mismatch: "
            f"{roles} vs {bands_needed}"
        )

    if out_path is None:
        base, ext = os.path.splitext(in_path)
        out_path = f"{base}_{idx}.tif"

    with rasterio.open(in_path) as src:
        grid = RasterGridProfile.from_dataset(src, in_path)
        band_idx = {role: band_map[role] for role in roles}

        from app.lib.geo_analysis.raster_guard import RasterResourceGuard

        # 窗口化执行下内存是 O(window)（预算推导）——这里的字节记账是**输出
        # 磁盘足迹**（float32 单波段），不是旧整幅实现的全网格工作集。
        RasterResourceGuard.check_grid(
            width=grid.width,
            height=grid.height,
            bytes_per_pixel=4,
            num_bands=1,
            input_pixels=grid.width * grid.height,
        )

        # EVI DN 判定：一次有界全局扫描。
        dn_scale = 1.0
        if idx == "evi":
            band_maxes = [
                _decimated_band_max(src, band_idx[r]) for r in roles
            ]
            finite_maxes = [m for m in band_maxes if math.isfinite(m)]
            if finite_maxes and max(finite_maxes) > 1.5:
                dn_scale = 1.0 / 10000.0

        nodata_in = grid.nodata
        profile = build_output_profile(
            width=grid.width,
            height=grid.height,
            count=1,
            dtype="float32",
            crs=src.crs,
            transform=src.transform,
            nodata=-9999.0,
        )

        with WindowedRasterWriter(
            out_path, profile=profile, grid=grid,
            window_side=window_side,
        ) as writer:
            for win in iter_bounded_windows(
                grid.width, grid.height, window_side=window_side, src=src
            ):
                checkpoint()
                arrays: Dict[str, np.ndarray] = {}
                invalid = np.zeros(
                    (int(win.height), int(win.width)), dtype=bool
                )
                for role in roles:
                    a = src.read(band_idx[role], window=win).astype(np.float64)
                    invalid |= ~valid_mask_for(a, nodata_in)
                    arrays[role] = np.where(invalid, np.nan, a * dn_scale)
                result = formula(*[arrays[role] for role in roles])
                result = np.where(invalid, np.nan, result)
                out = np.where(
                    np.isfinite(result), result, -9999.0
                ).astype(np.float32)
                # 统计按 float64 真值累计（NaN=无效），落盘按 float32 字节
                # —— 与既有 NDVI 统计契约（#537）逐位一致。
                writer.write(win, out, stats_arr=result)

        return {
            "success": True,
            "output_path": out_path,
            "index_type": idx,
            "band_map": band_idx,
            "algorithm": f"windowed_index:{idx}",
            **writer.finalize(),
        }
