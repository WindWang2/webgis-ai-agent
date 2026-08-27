"""Raster Data Plane 基础契约（C5，ADR-0075）。

目标：**样式改动 ≠ 重新计算**。当前栅格有两条渲染路径互不相通——瓦片流
（raster_tile_service，单波段恒灰度、cmap_name 死参数）与 colormap 烘焙
PNG（raster_cartography_converter，换色=重跑转换=新 PNG + 新 imageRef）。
本模块定义两个契约对象：

- ``RasterArtifactDescriptor`` —— 栅格工件注册期描述子：band schema / dtype /
  nodata / per-band stats / CRS / bounds / overview 有无。登记时一次算好
  （调用方负责 to_thread），消费方（瓦片路由、图例、Agent 感知）零 IO 读取。
- ``RasterStyleSpec`` —— 样式契约：band 组合 / stretch / colormap / 透明度 /
  重采样。存 MapSpec 图层 paint 侧（``paint.raster_style``），数据平面按
  (数据, 样式) 二元组缓存瓦片——换样式只换缓存键，绝不重跑遥感计算。

Zero Big Data in Context：两者都是有界元数据，可安全进 LLM 上下文。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RasterBandInfo(BaseModel):
    """单波段结构化描述。"""

    index: int  # 1-based
    dtype: str = ""
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    description: str = ""


class RasterArtifactDescriptor(BaseModel):
    """栅格工件描述子（登记期一次计算；消费期零 IO）。"""

    file_path: str
    width: int = 0
    height: int = 0
    crs: Optional[str] = None
    bounds: Optional[List[float]] = None  # [minx, miny, maxx, maxy]（源 CRS）
    dtype: str = ""
    nodata: Optional[float] = None
    band_count: int = 0
    bands: List[RasterBandInfo] = Field(default_factory=list)
    has_overviews: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


def inspect_raster_artifact(file_path: str) -> Optional[RasterArtifactDescriptor]:
    """读取栅格头信息 + 有界统计（重 IO——调用方必须 asyncio.to_thread）。

    统计与瓦片服务同策略：降采样到最长边 2048，跳过 nodata/非有限值。
    返回 None（文件缺失/非栅格）由调用方决定降级语义。
    """
    try:
        import numpy as np
        import rasterio
        from rasterio.enums import Resampling

        with rasterio.open(file_path) as src:
            band_infos: List[RasterBandInfo] = []
            scale = min(1.0, 2048.0 / float(max(src.width, src.height)))
            out_shape = (
                src.count,
                max(1, int(round(src.height * scale))),
                max(1, int(round(src.width * scale))),
            )
            data = src.read(out_shape=out_shape, masked=True, resampling=Resampling.average)
            arr = np.ma.getdata(data)
            mask = np.ma.getmaskarray(data)
            for b in range(src.count):
                valid = np.isfinite(arr[b]) & ~mask[b]
                if valid.any():
                    vmin = float(arr[b][valid].min())
                    vmax = float(arr[b][valid].max())
                else:
                    vmin = vmax = None
                band_infos.append(
                    RasterBandInfo(
                        index=b + 1,
                        dtype=str(src.dtypes[b]),
                        vmin=vmin,
                        vmax=vmax,
                        description=(src.descriptions[b] or "") if src.descriptions else "",
                    )
                )
            return RasterArtifactDescriptor(
                file_path=file_path,
                width=src.width,
                height=src.height,
                crs=str(src.crs) if src.crs else None,
                bounds=list(src.bounds) if src.bounds else None,
                dtype=str(src.dtypes[0]) if src.dtypes else "",
                nodata=(float(src.nodata) if src.nodata is not None else None),
                band_count=src.count,
                bands=band_infos,
                has_overviews=bool(src.overviews(1)),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[raster_spec] inspect failed for %s: %s", file_path, e)
        return None


class RasterStyleSpec(BaseModel):
    """栅格样式契约（MapSpec paint 侧；样式 ≠ 数据）。

    - ``bands``：1-based 波段组合（RGB 三元组或单波段）；缺省 = 前 min(3, n)。
    - ``stretch``：(min, max) 覆盖数据集全局 stretch；缺省 = 描述子统计。
    - ``colormap``：单波段着色名（matplotlib 合法名，如 viridis/rdylgn）；
      三波段组合忽略。缺省 = 灰度。
    - ``opacity`` / ``resampling``：渲染旋钮。
    """

    bands: Optional[List[int]] = None
    stretch: Optional[Tuple[float, float]] = None
    colormap: Optional[str] = None
    opacity: float = Field(1.0, ge=0.0, le=1.0)
    resampling: str = "bilinear"

    def normalized_bands(self, band_count: int) -> Tuple[int, ...]:
        """校验并归一波段索引（1-based，≤3 个，越界剔除，空则默认前 N）。"""
        if not self.bands:
            n = min(3, max(1, band_count))
            return tuple(range(1, n + 1))
        picked = tuple(
            b for b in self.bands if isinstance(b, int) and 1 <= b <= band_count
        )[:3]
        if not picked:
            n = min(3, max(1, band_count))
            return tuple(range(1, n + 1))
        return picked

    def cache_key(self) -> str:
        """样式缓存键（数据平面 (数据, 样式) 二元组缓存键的样式侧）。"""
        return ",".join(
            str(x)
            for x in (
                self.bands or (),
                self.stretch or (),
                self.colormap or "",
                self.opacity,
                self.resampling,
            )
        )


_COLORMAP_LUT_CACHE: Dict[str, Any] = {}


def colormap_rgb_lut(colormap: str, size: int = 256):
    """colormap 名 → (size, 3) uint8 LUT（进程内缓存）。

    matplotlib colormap 归一化采样；未知名返回 None（调用方回退灰度）。
    """
    import numpy as np

    cache_key = f"{colormap}:{size}"
    if cache_key in _COLORMAP_LUT_CACHE:
        return _COLORMAP_LUT_CACHE[cache_key]
    try:
        # matplotlib ≥3.9 移除了 cm.get_cmap；注册表访问是现行 API
        from matplotlib import colormaps

        cmap = colormaps[colormap]
        lut = (cmap(np.linspace(0.0, 1.0, size))[:, :3] * 255).astype(np.uint8)
    except Exception:  # noqa: BLE001
        return None
    if len(_COLORMAP_LUT_CACHE) > 64:
        _COLORMAP_LUT_CACHE.clear()
    _COLORMAP_LUT_CACHE[cache_key] = lut
    return lut


def apply_colormap_u8(gray: "np.ndarray", colormap: str) -> Optional["np.ndarray"]:
    """把归一化 uint8 灰度经 LUT 映射为 (..., 3) RGB。未知 colormap → None。"""

    lut = colormap_rgb_lut(colormap)
    if lut is None:
        return None
    return lut[gray]
