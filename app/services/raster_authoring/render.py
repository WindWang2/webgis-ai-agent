"""G20 渲染投影：规范表达 → 真实瓦片参数 / 有界单幅快照。"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np

from app.schemas.raster_spec import MAX_RASTER_SNAPSHOT_SIDE, RasterExpressionError
from app.services.raster_authoring.palettes import colormap_lut
from app.services.raster_authoring.source import RasterSource
from app.services.raster_tile_service import RasterRenderStyle, RasterTileError

#: 规范表达 → 数据面渲染参数（唯一投影，不复制渲染器）。
def render_style(spec: Any) -> RasterRenderStyle:
    bands = tuple(int(b) for b in spec.bands)
    stretch = tuple(spec.channel_stretch(i) for i in range(len(bands)))
    return RasterRenderStyle(
        bands=bands,
        stretch=stretch,
        colormap=spec.colormap,
        opacity=float(spec.opacity),
        resampling=str(spec.resampling),
        style_id=str(spec.style_id),
        data_revision=str(spec.data_revision),
    )


def render_tile(path: str, z: int, x: int, y: int, spec: Any) -> bytes:
    """调用既有窗口/overview/全局归一渲染器，只注入已提交样式。"""
    from app.services.raster_tile_service import render_raster_tile

    try:
        return render_raster_tile(path, z, x, y, 256, None, None, render_style(spec))
    except RasterTileError as exc:
        raise RasterExpressionError(
            exc.code if exc.code in (
                "BAND_OUT_OF_RANGE", "UNKNOWN_COLORMAP", "BAND_COUNT_UNSUPPORTED",
                "STRETCH_CHANNEL_MISMATCH", "OPACITY_OUT_OF_RANGE",
                "UNSUPPORTED_RESAMPLING",
            ) else "RENDER_FAILED",
            exc.message,
            status_code=502,
        ) from exc


def _normalize(arr: np.ndarray, valid: np.ndarray, lo: float, hi: float) -> np.ndarray:
    out = np.zeros(arr.shape, dtype=np.uint8)
    if hi > lo:
        out[valid] = np.rint(
            np.clip((arr[valid].astype(np.float64) - lo) / (hi - lo), 0.0, 1.0) * 255.0
        ).astype(np.uint8)
    return out


def render_snapshot(source: RasterSource, spec: Any) -> Dict[str, Any]:
    """有界单幅快照（源网格，≤2048 边），与瓦片共用规范样式/色带/mask 规则。

    返回 ``{"image": PIL.Image, "width","height","all_nodata","constant",
    "truncated","native_width","native_height"}``。绝不把提高 DPI 说成增加
    真实遥感细节。
    """
    import rasterio
    from PIL import Image
    from rasterio.enums import Resampling

    style = render_style(spec)
    with rasterio.open(source.path) as src:
        scale = min(1.0, MAX_RASTER_SNAPSHOT_SIDE / float(max(src.width, src.height)))
        out_h = max(1, int(round(src.height * scale)))
        out_w = max(1, int(round(src.width * scale)))
        data = src.read(
            indexes=list(style.bands), out_shape=(len(style.bands), out_h, out_w),
            masked=True, resampling=Resampling.nearest,
        )
        arr = np.ma.getdata(data)
        mask = np.ma.getmaskarray(data)

    channels = []
    per_channel_valid = []
    for i in range(len(style.bands)):
        band_arr = arr[i]
        valid = np.isfinite(band_arr) & ~mask[i]
        lo, hi = style.stretch[i]
        channels.append(_normalize(band_arr, valid, lo, hi))
        per_channel_valid.append(valid)
    # 与瓦片/probe 同规则：所有请求通道都有效才绘制，避免黑色填充值冒充实测值。
    common_valid = np.logical_and.reduce(per_channel_valid)
    all_nodata = not bool(common_valid.any())
    if len(channels) >= 3:
        rgb = np.dstack(channels[:3])
    else:
        gray = channels[0]
        lut = colormap_lut(style.colormap or "gray", 256)
        if lut is None:
            raise RasterExpressionError(
                "UNKNOWN_COLORMAP", f"colormap {style.colormap!r} has no LUT",
                status_code=502,
            )
        rgb = lut[gray]
    alpha = np.where(common_valid, 255, 0).astype(np.uint8)
    if style.opacity < 1.0:
        alpha = np.rint(alpha.astype(np.float32) * style.opacity).astype(np.uint8)
    rgba = np.dstack([rgb, alpha])
    lo_all = min(p[0] for p in style.stretch)
    hi_all = max(p[1] for p in style.stretch)
    return {
        "image": Image.fromarray(rgba, "RGBA"),
        "width": out_w,
        "height": out_h,
        "native_width": source.descriptor.width,
        "native_height": source.descriptor.height,
        "all_nodata": all_nodata,
        "constant": bool(hi_all <= lo_all),
        "truncated": scale < 1.0,
        "grid": "source",
    }
