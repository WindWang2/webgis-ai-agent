"""Direct 6-parameter affine georeferencing warp, preview renderer, and verified COG builder (R19-R22, R33, R39)."""
from __future__ import annotations

import io
import math
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from pyproj import CRS
from rasterio.enums import ColorInterp, Resampling

from app.lib.geo_raster.cog import CogWriteError, range_read_probe, to_cog, validate_cog
from app.lib.geo_raster.env import open_raster, rasterio_env
from app.schemas.georeferencing_schema import (
    MAX_OUTPUT_PIXELS,
    MAX_PREVIEW_SIDE,
    MAX_UNCOMPRESSED_OUTPUT_BYTES,
    AffineCoefficients,
    GeorefDomainError,
    GeorefErrorCode,
    GeorefSourceRecord,
)
from app.services.georeferencing.crs import ring_to_wgs84_geojson
from app.services.georeferencing.refs import (
    restore_blob_to_path,
    verify_file_sha256,
)
from app.services.georeferencing.store import georef_session_dir
from app.services.raster_authoring.alpha_adapter import reproject_straight_alpha

_MAX_OUTPUT_SIDE = 32_768
_STRIP_HEIGHT = 512


@dataclass(frozen=True)
class OutputGridSpec:
    """Preflighted destination grid derived strictly from frozen 6 affine parameters."""

    src_transform: Affine
    dst_transform: Affine
    out_width: int
    out_height: int
    resolution_m: float
    bounds_target_crs: List[float]
    footprint_wgs84: Dict[str, Any]
    bounds_wgs84: List[float]
    out_bands: int
    out_dtype: str
    estimated_pixels: int
    estimated_bytes: int


def affine_from_coefficients(coeffs: AffineCoefficients) -> Affine:
    """Build Rasterio ``Affine(a, b, c, d, e, f)`` mapping pixel `(col, row)` -> `(map_x, map_y)`.

    Convention: `(0, 0)` is top-left corner of top-left pixel; center is `(col+0.5, row+0.5)`.
    NEVER delegates GCP re-fitting to GDAL.
    """
    return Affine(
        float(coeffs.a1),
        float(coeffs.a2),
        float(coeffs.a0),
        float(coeffs.b1),
        float(coeffs.b2),
        float(coeffs.b0),
    )


def _output_layout_for_source(source: GeorefSourceRecord) -> Tuple[int, str, str]:
    """Determine GeoTIFF band count, numpy/rasterio dtype, and nodata policy."""
    if source.dtype == "uint16":
        return 1, "uint16", "internal_mask_tif"
    if source.bands in (3, 4):
        return 4, "uint8", "band4_alpha"
    return 2, "uint8", "band2_alpha"


def compute_output_grid(
    source: GeorefSourceRecord,
    coeffs: AffineCoefficients,
    target_crs: str,
    target_resolution_m: Optional[float] = None,
) -> OutputGridSpec:
    """Compute bounded destination north-up grid from transformed corners & check budgets BEFORE allocation."""
    src_t = affine_from_coefficients(coeffs)
    w, h = float(source.width), float(source.height)
    corners_px = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
    corners_map = [src_t @ (cx, cy) for cx, cy in corners_px]
    xs = [p[0] for p in corners_map]
    ys = [p[1] for p in corners_map]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)

    if target_resolution_m is not None:
        if not math.isfinite(target_resolution_m) or target_resolution_m <= 0.0:
            raise GeorefDomainError(
                GeorefErrorCode.OUTPUT_BUDGET_EXCEEDED,
                f"target_resolution_m must be finite and > 0 (got {target_resolution_m}).",
                status_code=422,
            )
        res = float(target_resolution_m)
    else:
        sx = coeffs.px_scale_x
        sy = coeffs.px_scale_y
        if max(sx, sy) > 0 and abs(sx - sy) / max(sx, sy) < 0.05:
            res = float((sx + sy) * 0.5)
        else:
            res = float(math.sqrt(max(abs(coeffs.determinant), 1e-18)))
    res = max(res, 1e-6)

    raw_w = math.ceil((maxx - minx) / res)
    raw_h = math.ceil((maxy - miny) / res)
    if raw_w <= 0 or raw_h <= 0:
        raise GeorefDomainError(
            GeorefErrorCode.OUTPUT_BUDGET_EXCEEDED,
            "Degenerate zero-sized target extent from affine transform.",
            status_code=422,
        )

    out_bands, out_dtype, _nodata_mode = _output_layout_for_source(source)
    bytes_per_px = out_bands * (2 if out_dtype == "uint16" else 1) + (1 if out_dtype == "uint16" else 0)
    est_pixels = int(raw_w) * int(raw_h)
    est_bytes = int(est_pixels) * int(bytes_per_px)

    if (
        raw_w > _MAX_OUTPUT_SIDE
        or raw_h > _MAX_OUTPUT_SIDE
        or est_pixels > MAX_OUTPUT_PIXELS
        or est_bytes > MAX_UNCOMPRESSED_OUTPUT_BYTES
    ):
        suggested_res = max(
            math.sqrt((maxx - minx) * (maxy - miny) / max(1.0, float(MAX_OUTPUT_PIXELS) * 0.85)),
            (maxx - minx) / float(_MAX_OUTPUT_SIDE),
            (maxy - miny) / float(_MAX_OUTPUT_SIDE),
        )
        raise GeorefDomainError(
            GeorefErrorCode.OUTPUT_BUDGET_EXCEEDED,
            (
                f"Target output grid {raw_w}x{raw_h} ({est_pixels:,} px, ~{est_bytes / (1024 * 1024):.1f} MiB) "
                f"exceeds budget ({MAX_OUTPUT_PIXELS:,} px, {MAX_UNCOMPRESSED_OUTPUT_BYTES // (1024 * 1024)} MiB, max side {_MAX_OUTPUT_SIDE}). "
                f"Increase target_resolution_m to >= {suggested_res:.3f} m/px."
            ),
            status_code=422,
            details={
                "out_width": raw_w,
                "out_height": raw_h,
                "estimated_pixels": est_pixels,
                "estimated_bytes": est_bytes,
                "max_pixels": MAX_OUTPUT_PIXELS,
                "max_bytes": MAX_UNCOMPRESSED_OUTPUT_BYTES,
                "suggested_resolution_m": round(suggested_res, 4),
            },
        )

    dst_t = Affine(res, 0.0, minx, 0.0, -res, maxy)
    footprint_wgs84, bounds_wgs84 = ring_to_wgs84_geojson(corners_map, target_crs)

    return OutputGridSpec(
        src_transform=src_t,
        dst_transform=dst_t,
        out_width=int(raw_w),
        out_height=int(raw_h),
        resolution_m=res,
        bounds_target_crs=[float(minx), float(miny), float(maxx), float(maxy)],
        footprint_wgs84=footprint_wgs84,
        bounds_wgs84=bounds_wgs84,
        out_bands=out_bands,
        out_dtype=out_dtype,
        estimated_pixels=est_pixels,
        estimated_bytes=est_bytes,
    )


def _load_verified_source_arrays(source: GeorefSourceRecord) -> Tuple[np.ndarray, np.ndarray]:
    """Verify normalized SHA256 digest and return `(color_bands (C, H, W), alpha (H, W) float32)`."""
    src_path = Path(source.normalized_path)
    if not src_path.is_file():
        restored = restore_blob_to_path(source.normalized_sha256, src_path)
        if not restored:
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED,
                f"Source file '{source.source_id}' is missing and could not be restored from BlobStore.",
                status_code=422,
            )
    try:
        verify_file_sha256(src_path, source.normalized_sha256)
    except Exception as exc:  # noqa: BLE001
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED,
            f"Source file integrity digest check failed before read: {exc}",
            status_code=409,
        ) from exc

    if source.dtype == "uint16":
        with rasterio_env(), open_raster(src_path) as ds:
            arr = ds.read(1).astype(np.uint16)
        alpha = np.ones((arr.shape[0], arr.shape[1]), dtype=np.float32)
        return arr[np.newaxis, :, :], alpha

    with Image.open(src_path) as img:
        img.load()
        arr = np.asarray(img)
    if arr.ndim == 2:
        color = arr[np.newaxis, :, :].astype(np.uint8)
        alpha = np.ones((arr.shape[0], arr.shape[1]), dtype=np.float32)
    elif arr.shape[2] == 2:
        color = arr[:, :, 0][np.newaxis, :, :].astype(np.uint8)
        alpha = arr[:, :, 1].astype(np.float32) / 255.0
    elif arr.shape[2] == 3:
        color = np.transpose(arr[:, :, :3], (2, 0, 1)).astype(np.uint8)
        alpha = np.ones((arr.shape[0], arr.shape[1]), dtype=np.float32)
    else:
        color = np.transpose(arr[:, :, :3], (2, 0, 1)).astype(np.uint8)
        alpha = arr[:, :, 3].astype(np.float32) / 255.0
    return color, alpha


def generate_warped_preview(
    *,
    session_id: str,
    plan_id: str,
    source: GeorefSourceRecord,
    coeffs: AffineCoefficients,
    target_crs: str,
    resampling: str,
    target_resolution_m: Optional[float],
) -> Tuple[Path, OutputGridSpec]:
    """Generate a downsampled transparent RGBA PNG preview matching the affine model (R19-R22)."""
    grid = compute_output_grid(source, coeffs, target_crs, target_resolution_m)
    src_color, src_alpha = _load_verified_source_arrays(source)

    # Preview renders on the north-up grid in WGS84 / target extent scaled <= MAX_PREVIEW_SIDE.
    scale = min(1.0, float(MAX_PREVIEW_SIDE) / float(max(grid.out_width, grid.out_height)))
    prev_w = max(1, int(round(grid.out_width * scale)))
    prev_h = max(1, int(round(grid.out_height * scale)))

    # Project source into target CRS preview grid cleanly (uses EPSG:target_crs directly).
    minx, miny, maxx, maxy = grid.bounds_target_crs
    prev_res_x = (maxx - minx) / float(prev_w)
    prev_res_y = (maxy - miny) / float(prev_h)
    prev_dst_t = Affine(prev_res_x, 0.0, minx, 0.0, -prev_res_y, maxy)
    crs = CRS.from_user_input(target_crs)

    if source.dtype == "uint16":
        lo = float(np.min(src_color[0]))
        hi = float(np.max(src_color[0]))
        if hi > lo:
            scaled_u8 = np.clip(np.rint((src_color[0].astype(np.float64) - lo) / (hi - lo) * 255.0), 0, 255).astype(np.uint8)
        else:
            scaled_u8 = np.zeros_like(src_color[0], dtype=np.uint8)
        work_color = scaled_u8[np.newaxis, :, :]
    else:
        work_color = src_color

    dst_color, dst_alpha = reproject_straight_alpha(
        work_color,
        src_alpha,
        src_transform=grid.src_transform,
        src_crs=crs,
        dst_transform=prev_dst_t,
        dst_crs=crs,
        dst_height=prev_h,
        dst_width=prev_w,
        resampling=resampling,
        out_dtype="uint8",
    )
    if dst_color.shape[0] >= 3:
        rgba = np.dstack([dst_color[0], dst_color[1], dst_color[2], dst_alpha])
    else:
        rgba = np.dstack([dst_color[0], dst_color[0], dst_color[0], dst_alpha])

    preview_dir = georef_session_dir(session_id) / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / f"{plan_id}_preview.png"
    tmp = preview_path.with_name(f".{preview_path.name}.tmp-{uuid.uuid4().hex[:8]}")
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", compress_level=4)
    try:
        tmp.write_bytes(buf.getvalue())
        os.replace(tmp, preview_path)
    finally:
        tmp.unlink(missing_ok=True)
    return preview_path, grid


class WarpCancelledError(Exception):
    """Raised cleanly between chunks when job cancellation is signalled."""


def run_direct_affine_warp(
    *,
    source: GeorefSourceRecord,
    coeffs: AffineCoefficients,
    target_crs: str,
    resampling: str,
    target_resolution_m: Optional[float],
    generate_cog: bool,
    out_path: Path,
    check_cancelled: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[str, Any]:
    """Execute direct 6-param affine reprojection in cancellable strips and publish atomically."""
    grid = compute_output_grid(source, coeffs, target_crs, target_resolution_m)

    def _guard_cancel() -> None:
        if check_cancelled is not None and check_cancelled():
            raise WarpCancelledError("Georeference job cancelled")

    _guard_cancel()
    if progress_cb:
        progress_cb(5, 100, "verifying_source")

    # Verify normalized file digest BEFORE load & copy into job temp staging.
    src_color, src_alpha = _load_verified_source_arrays(source)
    # Re-verify file digest on disk immediately after load to catch mid-read file swap (R29).
    try:
        verify_file_sha256(Path(source.normalized_path), source.normalized_sha256)
    except Exception as exc:  # noqa: BLE001
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED,
            f"Source digest changed during load (concurrent swap detected): {exc}",
            status_code=409,
        ) from exc

    _guard_cancel()
    crs = CRS.from_user_input(target_crs)
    crs_wkt = crs.to_wkt()
    out_bands, out_dtype, nodata_mode = _output_layout_for_source(source)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_path.parent, prefix=".g48-stage-") as td:
        stage_dir = Path(td)
        raw_tif = stage_dir / "warped_raw.tif"
        profile: Dict[str, Any] = {
            "driver": "GTiff",
            "width": grid.out_width,
            "height": grid.out_height,
            "count": out_bands,
            "dtype": out_dtype,
            "crs": crs_wkt,
            "transform": grid.dst_transform,
            "nodata": None,
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
            "compress": "DEFLATE",
        }
        if out_bands == 4:
            profile["photometric"] = "RGB"

        with rasterio_env(), rasterio.Env(GDAL_TIFF_INTERNAL_MASK="YES"), rasterio.open(raw_tif, "w", **profile) as dst:
            if out_bands == 4:
                dst.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
            elif out_bands == 2:
                dst.colorinterp = (ColorInterp.gray, ColorInterp.alpha)
            else:
                dst.colorinterp = (ColorInterp.gray,)

            dst.update_tags(
                G48_GEOREF_MODEL="affine_2d_direct_6p",
                G48_SOURCE_SHA256=source.normalized_sha256,
                G48_RESAMPLING=resampling,
            )

            total_rows = grid.out_height
            for y0 in range(0, total_rows, _STRIP_HEIGHT):
                _guard_cancel()
                y1 = min(total_rows, y0 + _STRIP_HEIGHT)
                strip_h = y1 - y0
                strip_dst_t = grid.dst_transform @ Affine.translation(0, y0)
                win = rasterio.windows.Window(0, y0, grid.out_width, strip_h)

                dst_color, dst_alpha = reproject_straight_alpha(
                    src_color,
                    src_alpha,
                    src_transform=grid.src_transform,
                    src_crs=crs,
                    dst_transform=strip_dst_t,
                    dst_crs=crs,
                    dst_height=strip_h,
                    dst_width=grid.out_width,
                    resampling=resampling,
                    out_dtype=out_dtype,
                )

                if out_bands == 4:
                    out_stack = np.vstack([dst_color[:3], dst_alpha[np.newaxis, :, :]])
                    dst.write(out_stack, window=win)
                elif out_bands == 2:
                    out_stack = np.vstack([dst_color[:1], dst_alpha[np.newaxis, :, :]])
                    dst.write(out_stack, window=win)
                else:
                    dst.write(dst_color[0], 1, window=win)
                    mask_u8 = np.where(dst_alpha > 0, 255, 0).astype(np.uint8)
                    dst.write_mask(mask_u8, window=win)

                pct = 10 + int(70.0 * (y1 / float(max(total_rows, 1))))
                if progress_cb:
                    progress_cb(pct, 100, "warping")

        _guard_cancel()
        final_candidate = raw_tif
        cog_verified = False
        cog_checks: Dict[str, Any] = {}

        if generate_cog:
            if progress_cb:
                progress_cb(85, 100, "converting_cog")
            cog_dir = stage_dir / "cog"
            cog_dir.mkdir(parents=True, exist_ok=True)
            try:
                cog_path = to_cog(raw_tif, cog_dir, compress="DEFLATE", blocksize=512)
            except CogWriteError as exc:
                raise GeorefDomainError(
                    GeorefErrorCode.JOB_FAILED,
                    f"COG conversion failed: {exc}",
                    status_code=500,
                ) from exc
            _guard_cancel()
            v_report = validate_cog(str(cog_path))
            probe_w = min(64, grid.out_width)
            probe_h = min(64, grid.out_height)
            r_report = range_read_probe(str(cog_path), window=(0, 0, probe_w, probe_h))
            cog_verified = bool(v_report.get("ok") and r_report.get("ok"))
            cog_checks = {"validate": v_report, "range_probe": r_report}
            if not cog_verified:
                raise GeorefDomainError(
                    GeorefErrorCode.JOB_FAILED,
                    f"Generated COG failed structural validation or range-read probe: {cog_checks}",
                    status_code=500,
                    details=cog_checks,
                )
            final_candidate = cog_path

        # Cancel fence right before final atomic publish (R26).
        _guard_cancel()
        if progress_cb:
            progress_cb(95, 100, "publishing")

        # Re-verify source digest right before final replace as extra defense against swap (R29).
        try:
            verify_file_sha256(Path(source.normalized_path), source.normalized_sha256)
        except Exception as exc:  # noqa: BLE001
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED,
                f"Source file changed prior to final publish: {exc}",
                status_code=409,
            ) from exc

        # Atomic stage -> final path on same filesystem.
        publish_tmp = out_path.with_name(f".{out_path.name}.pub-{uuid.uuid4().hex[:8]}")
        try:
            os.replace(final_candidate, publish_tmp)
            _guard_cancel()
            os.replace(publish_tmp, out_path)
        finally:
            publish_tmp.unlink(missing_ok=True)

    return {
        "grid": grid,
        "bands": out_bands,
        "dtype": out_dtype,
        "has_alpha": out_bands in (2, 4),
        "nodata_mode": nodata_mode,
        "cog_requested": bool(generate_cog),
        "cog_verified": cog_verified,
        "cog_checks": cog_checks,
        "byte_size": out_path.stat().st_size,
    }
