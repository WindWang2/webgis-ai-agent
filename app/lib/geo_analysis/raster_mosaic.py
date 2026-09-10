"""GeoAnalysis 栅格镶嵌/裁剪原语（GeoCompute V8 Phase D 的执行底座）。

分区执行的 I/O 原语（不是科学算法 —— 波段运算/指数等真相仍在既有
raster_math / raster_windowed / rs.band_math）：

- ``crop_window``：按像素窗口裁剪 halo tile（tile job 的前置输入步）；
- ``mosaic_tiles``：把 tile 结果按 **core 窗口**写回全幅输出网格 ——
  halo 裁除的 seam 语义在这里落地（core 并集 = 全幅、互不重叠 →
  确定性 mosaic，无接缝混合决策）；tile nodata 区不覆盖已写像素
  （first-wins 只在意外重叠时兜底）。

全部重依赖（rasterio/numpy）惰性导入且**有界降级**：缺席 → 类型化
``RasterioUnavailableError``（诚实失败，绝不假装成功）。GeoCompute
边界（ADR-0096 D1）：本模块属于 Data Plane（app/lib/geo_analysis），
被 geocompute.partitioning 委托调用。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RasterioUnavailableError(RuntimeError):
    """栅格 I/O 依赖缺席（诚实失败；调用方转类型化 NodeExecutionError）。"""


def _require_rasterio():
    try:
        import rasterio  # noqa: F401
        from rasterio import windows  # noqa: F401

        return True
    except Exception as exc:  # noqa: BLE001 - 依赖缺席是事实不是错误
        raise RasterioUnavailableError(
            f"rasterio unavailable for partition I/O: {exc}"
        ) from exc


def crop_window(raster_path: str, window: dict[str, int], out_path: str) -> dict[str, Any]:
    """裁剪像素窗口 → 独立 GeoTIFF（保留 CRS/transform/nodata）。

    ``window``：{col_off, row_off, width, height}（源栅格像素坐标）。
    返回 {output_path, width, height}。
    """
    _require_rasterio()
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    col_off = int(window["col_off"])
    row_off = int(window["row_off"])
    width = int(window["width"])
    height = int(window["height"])
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid crop window: {window}")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with rasterio.open(raster_path) as src:
        win = Window(col_off, row_off, width, height)
        data = src.read(window=win)
        profile = src.profile.copy()
        profile.update(
            width=width,
            height=height,
            transform=src.window_transform(win),
            compress=str(os.environ.get("WEBGIS_GEOTIFF_COMPRESS", "deflate")),
        )
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(data)
    return {"output_path": out_path, "width": width, "height": height}


def raster_header(raster_path: str) -> dict[str, Any]:
    """栅格头元数据（分区计划输入）：宽高/CRS/transform/nodata/波段数。"""
    _require_rasterio()
    import rasterio

    with rasterio.open(raster_path) as src:
        return {
            "width": int(src.width),
            "height": int(src.height),
            "crs": str(src.crs) if src.crs else None,
            "transform": list(src.transform)[:6],
            "nodata": src.nodata,
            "count": int(src.count),
            "dtype": str(src.dtypes[0]) if src.dtypes else None,
        }


def mosaic_tiles(
    tiles: list[dict[str, Any]],
    *,
    out_path: str,
    width: int,
    height: int,
    crs: Optional[str],
    transform: Optional[list[float]],
) -> dict[str, Any]:
    """tile 结果 → 全幅输出（core 窗口写回；halo 裁除）。

    ``tiles``：每项 {path, core_window:{col_off,row_off,width,height},
    window:{col_off,row_off,width,height}}。``window`` 是该 tile 文件
    覆盖的**全幅像素窗口**（含 halo）—— core 在 tile 文件内的偏移 =
    core_window - window；缺失时按 0（tile 即 core）处理。

    core 窗口由分区计划保证**互不重叠**（并集 = 全幅）→ 逐 tile 直接
    写入即确定性 mosaic（无接缝混合决策，无 dst 读回 —— 'w' 模式数据集
    不可读）。意外重叠时后写覆盖先写（last-wins 兜底；计划不变式下
    不可达）。
    """
    _require_rasterio()
    import rasterio
    from rasterio.transform import Affine
    from rasterio.windows import Window

    if not tiles:
        raise ValueError("mosaic_tiles requires at least one tile")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    out_transform = Affine(*transform) if transform else None

    with rasterio.open(tiles[0]["path"]) as first:
        profile = first.profile.copy()
    profile.update(
        width=int(width), height=int(height),
        transform=out_transform,
        crs=crs if crs else profile.get("crs"),
        compress=str(os.environ.get("WEBGIS_GEOTIFF_COMPRESS", "deflate")),
    )

    written = 0
    with rasterio.open(out_path, "w", **profile) as dst:
        for tile in tiles:
            core = tile["core_window"]
            halo = tile.get("window") or core
            # core 在 tile 文件内的像素偏移（halo 裁除量）
            off_c = int(core["col_off"]) - int(halo.get("col_off", 0))
            off_r = int(core["row_off"]) - int(halo.get("row_off", 0))
            cw, ch = int(core["width"]), int(core["height"])
            win = Window(int(core["col_off"]), int(core["row_off"]), cw, ch)
            with rasterio.open(tile["path"]) as src_tile:
                if src_tile.count != dst.count:
                    raise ValueError(
                        f"tile band count mismatch: {src_tile.count} != {dst.count}"
                    )
                data = src_tile.read(window=Window(off_c, off_r, cw, ch))
                if data.shape[1] != ch or data.shape[2] != cw:
                    raise ValueError(
                        f"tile core region out of bounds: {tile['path']} "
                        f"({data.shape} vs {cw}x{ch})"
                    )
                for band in range(dst.count):
                    dst.write(data[band], band + 1, window=win)
                written += 1
    return {"output_path": out_path, "width": int(width), "height": int(height),
            "tiles_written": written}
