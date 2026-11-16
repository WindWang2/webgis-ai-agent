"""G20 渲染投影：规范表达 → 真实瓦片参数 / 有界单幅快照。"""
from __future__ import annotations

from typing import Any, Dict

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
    from affine import Affine
    from PIL import Image
    from rasterio.crs import CRS
    from rasterio.enums import Resampling

    from app.services.raster_authoring.alpha_adapter import (
        alpha_to_uint8,
        detect_alpha_or_mask,
        normalize_alpha_band,
        reproject_straight_alpha,
    )

    style = render_style(spec)
    stretch = style.stretch or ()
    # 与瓦片同一重采样语义（分类栅格 nearest、连续量 bilinear/cubic），
    # 避免导出位图与屏幕表达不一致。
    resampling = {
        "nearest": Resampling.nearest,
        "bilinear": Resampling.bilinear,
        "cubic": Resampling.cubic,
    }[style.resampling]
    with rasterio.open(source.path) as src:
        scale = min(1.0, MAX_RASTER_SNAPSHOT_SIDE / float(max(src.width, src.height)))
        out_h = max(1, int(round(src.height * scale)))
        out_w = max(1, int(round(src.width * scale)))
        _has_alpha_sem, _alpha_band, _has_internal_mask = detect_alpha_or_mask(src)
        use_premul_downscale = (
            scale < 1.0
            and resampling != Resampling.nearest
            and _has_alpha_sem
            and (int(src.width) * int(src.height) <= 16_777_216)
        )
        if use_premul_downscale:
            full_data = src.read(indexes=list(style.bands), masked=True)
            full_arr = np.ma.getdata(full_data)
            full_mask = np.ma.getmaskarray(full_data)
            if _alpha_band is not None:
                full_alpha_f = normalize_alpha_band(src.read(_alpha_band))
            else:
                full_alpha_f = normalize_alpha_band(src.read_masks(1))
            src_w, src_h = int(src.width), int(src.height)
            data = None
            _src_alpha_u8 = None
        else:
            data = src.read(
                indexes=list(style.bands), out_shape=(len(style.bands), out_h, out_w),
                masked=True, resampling=resampling,
            )
            arr = np.ma.getdata(data)
            mask = np.ma.getmaskarray(data)
            _src_alpha_u8 = None
            if _alpha_band is not None:
                _src_alpha_u8 = alpha_to_uint8(src.read(_alpha_band, out_shape=(out_h, out_w), resampling=resampling))
            elif _has_internal_mask:
                _src_alpha_u8 = alpha_to_uint8(src.read_masks(1, out_shape=(out_h, out_w), resampling=resampling))

    if use_premul_downscale:
        per_channel_valid = []
        norm_channels = []
        for i in range(len(style.bands)):
            band_arr = full_arr[i]
            valid = np.isfinite(band_arr) & ~full_mask[i]
            lo, hi = stretch[i]
            norm_channels.append(_normalize(band_arr, valid, lo, hi))
            per_channel_valid.append(valid)
        common_valid = np.logical_and.reduce(per_channel_valid)
        eff_alpha = np.where(common_valid, full_alpha_f, np.float32(0.0)).astype(np.float32)
        src_stack = np.stack(norm_channels[:3] if len(norm_channels) >= 3 else norm_channels[:1], axis=0)
        pixel_crs = CRS.from_epsg(3857)
        dst_stack, alpha = reproject_straight_alpha(
            src_stack,
            eff_alpha,
            src_transform=Affine.identity(),
            src_crs=pixel_crs,
            dst_transform=Affine.scale(float(src_w) / float(out_w), float(src_h) / float(out_h)),
            dst_crs=pixel_crs,
            dst_height=out_h,
            dst_width=out_w,
            resampling=style.resampling,
            out_dtype="uint8",
        )
        all_nodata = not bool((alpha > 0).any())
        if len(norm_channels) >= 3:
            rgb = np.dstack([dst_stack[0], dst_stack[1], dst_stack[2]])
        else:
            gray = dst_stack[0]
            lut = colormap_lut(style.colormap or "gray", 256)
            if lut is None:
                raise RasterExpressionError(
                    "UNKNOWN_COLORMAP", f"colormap {style.colormap!r} has no LUT",
                    status_code=502,
                )
            rgb = lut[gray]
    else:
        channels = []
        per_channel_valid = []
        for i in range(len(style.bands)):
            band_arr = arr[i]
            valid = np.isfinite(band_arr) & ~mask[i]
            lo, hi = stretch[i]
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
        if _src_alpha_u8 is not None:
            all_nodata = all_nodata or not bool((_src_alpha_u8[common_valid] > 0).any() if common_valid.any() else False)
            alpha = np.where(common_valid, _src_alpha_u8, 0).astype(np.uint8)
        else:
            alpha = np.where(common_valid, 255, 0).astype(np.uint8)
    if style.opacity < 1.0:
        alpha = np.rint(alpha.astype(np.float32) * style.opacity).astype(np.uint8)
    rgba = np.dstack([rgb, alpha])
    lo_all = min(p[0] for p in stretch)
    hi_all = max(p[1] for p in stretch)
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
