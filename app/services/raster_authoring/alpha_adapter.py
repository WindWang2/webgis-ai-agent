"""Shared straight-alpha premultiplied reprojection and RGBA/internal-mask adapter.

Used by:
- G48 georeferencing warper & preview renderer (`app/services/georeferencing/warp.py`)
- Live XYZ raster tile renderer (`app/services/raster_tile_service.py`)
- Snapshot & print PNG/PDF window renderer (`app/services/raster_authoring/render.py`, `publication_window.py`)

Keeping premultiplied alpha bilinear weighting and mask handling in one module ensures
live tiles, print exports, and downloaded GeoTIFFs share identical zero-black-halo semantics.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence, Tuple

import numpy as np
from rasterio.enums import ColorInterp, MaskFlags, Resampling
from rasterio.warp import reproject as rio_reproject


def normalize_alpha_band(alpha_raw: np.ndarray) -> np.ndarray:
    """Normalize an alpha or mask ndarray of uint8/uint16/int/float dtype to float32 in [0.0, 1.0]."""
    arr = np.asarray(alpha_raw)
    if np.issubdtype(arr.dtype, np.floating):
        return np.clip(np.nan_to_num(arr.astype(np.float32), nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    if np.issubdtype(arr.dtype, np.bool_):
        return arr.astype(np.float32)
    if np.issubdtype(arr.dtype, np.integer):
        max_val = float(np.iinfo(arr.dtype).max)
        if max_val <= 0.0:
            return np.zeros(arr.shape, dtype=np.float32)
        return np.clip(arr.astype(np.float32) / max_val, 0.0, 1.0)
    return np.clip(arr.astype(np.float32) / 255.0, 0.0, 1.0)


def alpha_to_uint8(alpha_raw: np.ndarray) -> np.ndarray:
    """Scale any numeric alpha or mask band cleanly to uint8 [0, 255]."""
    if np.asarray(alpha_raw).dtype == np.uint8:
        return np.asarray(alpha_raw, dtype=np.uint8)
    return np.clip(np.rint(normalize_alpha_band(alpha_raw) * 255.0), 0, 255).astype(np.uint8)


def detect_alpha_or_mask(src: Any) -> Tuple[bool, Optional[int], bool]:
    """Inspect an open Rasterio dataset for a dedicated alpha band or non-trivial internal mask.

    Returns:
        ``(has_alpha_semantics, alpha_band_1based, has_internal_mask)``
    """
    ci = list(getattr(src, "colorinterp", ()) or ())
    alpha_band: Optional[int] = None
    for idx, interp in enumerate(ci, start=1):
        if interp == ColorInterp.alpha:
            alpha_band = idx
            break
    if alpha_band is not None:
        return True, alpha_band, False

    try:
        # rasterio >= 1.3 renamed `mask_flags` -> `mask_flag_enums`; reading the
        # removed attribute raised AttributeError that the broad except below
        # swallowed, silently disabling internal-mask detection on rasterio 1.5+.
        mflags = getattr(src, "mask_flag_enums", None)
        if mflags is None:
            mflags = getattr(src, "mask_flags", None)  # rasterio < 1.3 fallback
        if mflags:
            first = set(mflags[0])
            if (MaskFlags.per_dataset in first or MaskFlags.alpha in first) and MaskFlags.all_valid not in first:
                return True, None, True
    except Exception:  # noqa: BLE001
        pass
    return False, None, False


def reproject_straight_alpha(
    src_color: np.ndarray,
    src_alpha: Optional[np.ndarray],
    *,
    src_transform: Any,
    src_crs: Any,
    dst_transform: Any,
    dst_crs: Any,
    dst_height: int,
    dst_width: int,
    resampling: str = "bilinear",
    out_dtype: str = "uint8",
) -> Tuple[np.ndarray, np.ndarray]:
    """Reproject `(C, H, W)` color bands and `[0..1]` float32 alpha cleanly with zero black halos.

    - ``nearest``: preserves exact discrete values and alpha coverage mask cleanly.
    - ``bilinear`` / ``cubic``: premultiplied straight-alpha weighted reprojection:
      warps `c * w` and `w` via floating-point bilinear kernel (`dst_nodata=0.0`),
      then normalizes `dst_c / dst_w` wherever `dst_w > 1e-6`.
    Returns:
        ``(dst_color (C, dst_height, dst_width), dst_alpha_u8 (dst_height, dst_width))``
    """
    c_count = int(src_color.shape[0])
    if src_alpha is None:
        w = np.ones((src_color.shape[1], src_color.shape[2]), dtype=np.float32)
    else:
        w = np.clip(src_alpha.astype(np.float32), 0.0, 1.0)

    if resampling == "nearest":
        alpha_u8_src = np.clip(np.rint(w * 255.0), 0, 255).astype(np.uint8)
        dst_alpha_u8 = np.zeros((dst_height, dst_width), dtype=np.uint8)
        rio_reproject(
            source=alpha_u8_src,
            destination=dst_alpha_u8,
            src_transform=src_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.nearest,
            src_nodata=None,
            dst_nodata=0,
        )
        np_dtype = np.uint16 if out_dtype == "uint16" else np.uint8
        dst_color = np.zeros((c_count, dst_height, dst_width), dtype=np_dtype)
        for idx in range(c_count):
            rio_reproject(
                source=np.ascontiguousarray(src_color[idx], dtype=np_dtype),
                destination=dst_color[idx],
                src_transform=src_transform,
                src_crs=src_crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.nearest,
                src_nodata=None,
                dst_nodata=None,
            )
            dst_color[idx][dst_alpha_u8 == 0] = 0
        return dst_color, dst_alpha_u8

    color_resample = Resampling.cubic if resampling == "cubic" else Resampling.bilinear
    dst_w = np.full((dst_height, dst_width), -1.0, dtype=np.float32)
    rio_reproject(
        source=w,
        destination=dst_w,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=color_resample,
        src_nodata=None,
        dst_nodata=-1.0,
    )
    dst_w = np.clip(dst_w, 0.0, 1.0)
    dst_alpha_u8 = np.clip(np.rint(dst_w * 255.0), 0, 255).astype(np.uint8)
    valid = dst_w > 1e-6

    np_dtype = np.uint16 if out_dtype == "uint16" else np.uint8
    max_val = 65535.0 if out_dtype == "uint16" else 255.0
    dst_color = np.zeros((c_count, dst_height, dst_width), dtype=np_dtype)
    for idx in range(c_count):
        premul_src = src_color[idx].astype(np.float32) * w
        dst_premul = np.full((dst_height, dst_width), -1.0, dtype=np.float32)
        rio_reproject(
            source=premul_src,
            destination=dst_premul,
            src_transform=src_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=color_resample,
            src_nodata=None,
            dst_nodata=-1.0,
        )
        dst_premul = np.maximum(dst_premul, 0.0)
        out_band = np.zeros((dst_height, dst_width), dtype=np.float32)
        out_band[valid] = np.clip(dst_premul[valid] / dst_w[valid], 0.0, max_val)
        band_int = np.rint(out_band).astype(np_dtype)
        band_int[dst_alpha_u8 == 0] = 0
        dst_color[idx] = band_int

    return dst_color, dst_alpha_u8


def render_alpha_aware_window(
    src: Any,
    *,
    color_indexes: Tuple[int, ...],
    alpha_band: Optional[int],
    has_internal_mask: bool,
    src_crs: Any,
    dst_transform: Any,
    dst_crs: Any,
    out_h: int,
    out_w: int,
    resampling: str,
    stretch: Optional[Sequence[Tuple[float, float]]] = None,
    colormap: Optional[str] = None,
    opacity: float = 1.0,
    win: Optional[Any] = None,
    win_out_shape: Optional[Tuple[int, int]] = None,
) -> Tuple[np.ndarray, bool]:
    """Render windowed RGBA `(out_h, out_w, 4) uint8` for a dataset with native alpha or TIFF mask.

    Returns ``(rgba, all_nodata)``.
    """
    read_kw: dict[str, Any] = {}
    if win is not None:
        read_kw["window"] = win
    if win_out_shape is not None:
        read_kw["out_shape"] = (len(color_indexes), int(win_out_shape[0]), int(win_out_shape[1]))
    win_data = src.read(indexes=list(color_indexes), **read_kw)

    mask_kw: dict[str, Any] = {}
    if win is not None:
        mask_kw["window"] = win
    if win_out_shape is not None:
        mask_kw["out_shape"] = (int(win_out_shape[0]), int(win_out_shape[1]))

    if alpha_band is not None:
        alpha_raw = src.read(alpha_band, **mask_kw)
        src_alpha = normalize_alpha_band(alpha_raw)
    elif has_internal_mask:
        mask_raw = src.read_masks(1, **mask_kw)
        src_alpha = normalize_alpha_band(mask_raw)
    else:
        src_alpha = np.ones((win_data.shape[1], win_data.shape[2]), dtype=np.float32)

    # Declared nodata wins over alpha coverage: a pixel whose color bands carry
    # the dataset's nodata value renders transparent even when its alpha band
    # is opaque (mirrors src_nodata handling of the non-alpha reprojection path).
    nodata_val = getattr(src, "nodata", None)
    if nodata_val is not None:
        try:
            nodata_hit = np.any(win_data == nodata_val, axis=0)
        except TypeError:  # non-numeric nodata vs band dtype mismatch
            nodata_hit = None
        if nodata_hit is not None and bool(nodata_hit.any()):
            src_alpha = np.where(nodata_hit, np.float32(0.0), src_alpha)

    if win is not None:
        from affine import Affine as _Affine

        dec_h, dec_w = int(win_data.shape[1]), int(win_data.shape[2])
        win_transform = src.window_transform(win) * _Affine.scale(
            float(win.width) / float(max(dec_w, 1)),
            float(win.height) / float(max(dec_h, 1)),
        )
    else:
        win_transform = src.transform

    dtype_name = str(src.dtypes[0]) if src.dtypes else "uint8"
    if dtype_name == "uint8":
        dst_color, dst_alpha_u8 = reproject_straight_alpha(
            win_data.astype(np.uint8),
            src_alpha,
            src_transform=win_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_height=out_h,
            dst_width=out_w,
            resampling=resampling,
            out_dtype="uint8",
        )
        valid_u8 = dst_alpha_u8 > 0
        all_nodata = not bool(valid_u8.any())
        # Respect explicit custom stretch if it differs from [0, 255]; otherwise keep faithful uint8.
        use_stretch = False
        if stretch is not None and len(stretch) == len(color_indexes):
            use_stretch = any(abs(lo - 0.0) > 1e-6 or abs(hi - 255.0) > 1e-6 for lo, hi in stretch)
        if use_stretch and stretch is not None:
            from app.services.raster_tile_service import _normalize_channel

            proc = np.zeros_like(dst_color, dtype=np.uint8)
            for idx in range(len(color_indexes)):
                lo, hi = stretch[idx]
                proc[idx] = np.where(valid_u8, _normalize_channel(dst_color[idx], valid_u8, float(lo), float(hi)), 0)
            dst_color = proc
    else:
        # uint16 (or other scalar) with internal mask: reproject float numerator/weight then stretch.
        # NaN/Inf in float sources must never enter the warp: NaN * 0 = NaN smears
        # through premultiplied bilinear weights and poisons the stretch min/max,
        # turning every valid pixel pure black. Transparent (alpha=0) pixels are
        # zeroed here — their color is never observed downstream.
        vals_f = np.nan_to_num(
            win_data.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0
        )
        dst_w = np.zeros((out_h, out_w), dtype=np.float32)
        rio_resample = {
            "nearest": Resampling.nearest,
            "cubic": Resampling.cubic,
        }.get(resampling, Resampling.bilinear)
        rio_reproject(
            source=src_alpha,
            destination=dst_w,
            src_transform=win_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=rio_resample,
            src_nodata=None,
            dst_nodata=0.0,
        )
        dst_alpha_u8 = np.clip(np.rint(np.clip(dst_w, 0.0, 1.0) * 255.0), 0, 255).astype(np.uint8)
        valid_u8 = dst_alpha_u8 > 0
        all_nodata = not bool(valid_u8.any())
        dst_f = np.zeros((len(color_indexes), out_h, out_w), dtype=np.float32)
        for idx in range(len(color_indexes)):
            if resampling == "nearest":
                rio_reproject(
                    source=vals_f[idx],
                    destination=dst_f[idx],
                    src_transform=win_transform,
                    src_crs=src_crs,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    resampling=rio_resample,
                    src_nodata=None,
                    dst_nodata=0.0,
                )
            else:
                premul = vals_f[idx] * src_alpha
                dst_pre = np.zeros((out_h, out_w), dtype=np.float32)
                rio_reproject(
                    source=premul,
                    destination=dst_pre,
                    src_transform=win_transform,
                    src_crs=src_crs,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    resampling=rio_resample,
                    src_nodata=None,
                    dst_nodata=0.0,
                )
                dst_f[idx][valid_u8] = dst_pre[valid_u8] / dst_w[valid_u8]

        from app.services.raster_tile_service import _normalize_channel

        dst_color = np.zeros((len(color_indexes), out_h, out_w), dtype=np.uint8)
        for idx in range(len(color_indexes)):
            lo, hi = stretch[idx] if (stretch and idx < len(stretch)) else (float(np.min(dst_f[idx][valid_u8])) if valid_u8.any() else 0.0, float(np.max(dst_f[idx][valid_u8])) if valid_u8.any() else 1.0)
            dst_color[idx] = np.where(valid_u8, _normalize_channel(dst_f[idx], valid_u8, float(lo), float(hi)), 0)

    if len(color_indexes) >= 3:
        rgb = np.dstack([dst_color[0], dst_color[1], dst_color[2]])
    else:
        gray = dst_color[0]
        if colormap:
            from app.services.raster_authoring.palettes import colormap_lut

            lut = colormap_lut(colormap, 256)
            rgb = lut[gray] if lut is not None else np.dstack([gray, gray, gray])
        else:
            rgb = np.dstack([gray, gray, gray])

    if opacity < 1.0:
        dst_alpha_u8 = np.rint(dst_alpha_u8.astype(np.float32) * float(opacity)).astype(np.uint8)
    rgba = np.dstack([rgb, dst_alpha_u8])
    return rgba, all_nodata
