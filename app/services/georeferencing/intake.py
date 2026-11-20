"""Bounded image intake, EXIF canonicalization, TIFF inspection, and BlobStore persistence (R01-R08)."""
from __future__ import annotations

import io
import os
import struct
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageOps

from app.lib.geo_raster.env import open_raster, rasterio_env
from app.schemas.georeferencing_schema import (
    MAX_DECODE_PIXELS,
    MAX_DECODE_SIDE,
    MAX_UPLOAD_BYTES,
    ExistingGeorefMetadata,
    GeorefDomainError,
    GeorefErrorCode,
    GeorefSourceRecord,
)
from app.services.georeferencing.refs import (
    compute_sha256,
    pin_blob_bytes,
    pin_blob_from_path,
)
from app.services.georeferencing.store import georef_session_dir, save_source

_TIFF_MAGICS = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

_ORIENTATION_NAMES = {
    1: "none",
    2: "flip_horizontal",
    3: "rotate_180",
    4: "flip_vertical",
    5: "transpose_main",
    6: "rotate_90_cw",
    7: "transpose_anti",
    8: "rotate_270_cw",
}


def _apply_exif_orientation_2d(arr: np.ndarray, orientation: int) -> np.ndarray:
    """Apply EXIF orientation 1..8 consistently to a 2D ndarray (matching ImageOps.exif_transpose)."""
    if orientation == 2:
        return np.ascontiguousarray(np.fliplr(arr))
    if orientation == 3:
        return np.ascontiguousarray(np.rot90(arr, k=2))
    if orientation == 4:
        return np.ascontiguousarray(np.flipud(arr))
    if orientation == 5:
        return np.ascontiguousarray(arr.T)
    if orientation == 6:
        return np.ascontiguousarray(np.rot90(arr, k=3))
    if orientation == 7:
        return np.ascontiguousarray(arr[::-1, ::-1].T)
    if orientation == 8:
        return np.ascontiguousarray(np.rot90(arr, k=1))
    return arr


def _detect_magic_format(head: bytes) -> str:
    if head.startswith(_PNG_MAGIC):
        return "PNG"
    if head.startswith(_JPEG_MAGIC):
        return "JPEG"
    if head[:4] in _TIFF_MAGICS:
        return "TIFF"
    # Detect common rejected text/SVG/PDF/GIF payloads for clear diagnostics.
    stripped = head.lstrip()
    if stripped.startswith(b"%PDF-"):
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
            "PDF documents are not supported; upload static PNG, JPEG, or single-page TIFF.",
            status_code=415,
        )
    if stripped.startswith((b"<svg", b"<?xml", b"<VRTDataset")):
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
            "SVG/XML/GDAL VRT payloads are rejected for security and raster intake rules.",
            status_code=415,
        )
    if head[:6] in (b"GIF87a", b"GIF89a"):
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
            "Animated or GIF images are not supported; upload static PNG, JPEG, or single-page TIFF.",
            status_code=415,
        )
    raise GeorefDomainError(
        GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
        "Unsupported file signature; only PNG, JPEG, and single-page TIFF images are allowed.",
        status_code=415,
    )


def _validate_jpeg_app1_exif(raw: bytes) -> Optional[int]:
    """Strictly parse JPEG APP1 Exif header + Orientation tag; fail closed on corruption (R08)."""
    pos = 2
    n = len(raw)
    while pos + 4 <= n:
        if raw[pos] != 0xFF:
            pos += 1
            continue
        marker = raw[pos + 1]
        pos += 2
        if marker in (0xD8, 0xD9, 0x00) or (0xD0 <= marker <= 0xD7):
            continue
        if marker == 0xDA:  # Start of Scan -> image data follows
            break
        if pos + 2 > n:
            break
        seg_len = struct.unpack(">H", raw[pos : pos + 2])[0]
        if seg_len < 2 or pos + seg_len > n:
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_EXIF_FAILED,
                "JPEG EXIF/APP segment length is truncated or corrupt.",
                status_code=422,
            )
        seg_data = raw[pos + 2 : pos + seg_len]
        if marker == 0xE1 and seg_data.startswith(b"Exif\x00\x00"):
            tiff = seg_data[6:]
            if len(tiff) < 8:
                raise GeorefDomainError(
                    GeorefErrorCode.SOURCE_EXIF_FAILED,
                    "EXIF TIFF header is truncated.",
                    status_code=422,
                )
            bo = tiff[:2]
            if bo == b"II":
                endian = "<"
            elif bo == b"MM":
                endian = ">"
            else:
                raise GeorefDomainError(
                    GeorefErrorCode.SOURCE_EXIF_FAILED,
                    "Invalid EXIF byte-order marker.",
                    status_code=422,
                )
            magic = struct.unpack(f"{endian}H", tiff[2:4])[0]
            if magic != 42:
                raise GeorefDomainError(
                    GeorefErrorCode.SOURCE_EXIF_FAILED,
                    f"Corrupt EXIF TIFF magic number ({magic}).",
                    status_code=422,
                )
            ifd0_off = struct.unpack(f"{endian}I", tiff[4:8])[0]
            if ifd0_off < 8 or ifd0_off + 2 > len(tiff):
                raise GeorefDomainError(
                    GeorefErrorCode.SOURCE_EXIF_FAILED,
                    "EXIF IFD0 offset points outside EXIF segment.",
                    status_code=422,
                )
            entry_count = struct.unpack(f"{endian}H", tiff[ifd0_off : ifd0_off + 2])[0]
            table_end = ifd0_off + 2 + entry_count * 12
            if entry_count > 512 or table_end > len(tiff):
                raise GeorefDomainError(
                    GeorefErrorCode.SOURCE_EXIF_FAILED,
                    "EXIF IFD0 entry table is truncated or oversized.",
                    status_code=422,
                )
            for idx in range(entry_count):
                off = ifd0_off + 2 + idx * 12
                tag, field_type, count = struct.unpack(f"{endian}HHI", tiff[off : off + 8])
                if tag == 0x0112:
                    if field_type != 3 or count != 1:
                        raise GeorefDomainError(
                            GeorefErrorCode.SOURCE_EXIF_FAILED,
                            f"Invalid EXIF Orientation tag format (type={field_type}, count={count}).",
                            status_code=422,
                        )
                    val = struct.unpack(f"{endian}H", tiff[off + 8 : off + 10])[0]
                    if val not in _ORIENTATION_NAMES:
                        raise GeorefDomainError(
                            GeorefErrorCode.SOURCE_EXIF_FAILED,
                            f"Invalid EXIF Orientation value ({val}); expected 1..8.",
                            status_code=422,
                        )
                    return int(val)
        pos += seg_len
    return None


def _parse_orientation(
    img: Image.Image,
    raw: bytes,
    detected_format: str,
    *,
    ignore_exif: bool,
) -> int:
    """Strictly parse EXIF orientation; fail closed on malformed tags unless ignore_exif=True."""
    if ignore_exif:
        return 1
    try:
        if detected_format == "JPEG":
            explicit = _validate_jpeg_app1_exif(raw)
            if explicit is not None:
                return explicit
        exif = img.getexif()
        if exif and 0x0112 in exif:
            ori = int(exif[0x0112])
            if ori not in _ORIENTATION_NAMES:
                raise GeorefDomainError(
                    GeorefErrorCode.SOURCE_EXIF_FAILED,
                    f"Unsupported EXIF Orientation {ori}.",
                    status_code=422,
                )
            return ori
        return 1
    except GeorefDomainError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_EXIF_FAILED,
            f"EXIF orientation metadata could not be parsed cleanly: {exc}",
            status_code=422,
        ) from exc


def _inspect_existing_tiff_georef(tmp_tiff: Path) -> Tuple[ExistingGeorefMetadata, int, str, int, int]:
    """Validate TIFF driver/subdatasets and inspect any embedded CRS/transform/GCPs."""
    with rasterio_env(), open_raster(tmp_tiff) as ds:
        if str(ds.driver) != "GTiff":
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
                f"Detected raster driver '{ds.driver}', expected 'GTiff'.",
                status_code=415,
            )
        if getattr(ds, "subdatasets", None):
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
                "Multi-subdataset TIFF files are not supported; upload a single-page TIFF.",
                status_code=415,
            )
        w = int(ds.width)
        h = int(ds.height)
        count = int(ds.count)
        dtypes = {str(d) for d in (ds.dtypes or ())}
        if len(dtypes) != 1:
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
                f"Mixed band data types {sorted(dtypes)} are not supported.",
                status_code=415,
            )
        dtype = next(iter(dtypes))
        if dtype == "uint16" and count != 1:
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
                f"uint16 TIFF is only supported for single-band grayscale images (got {count} bands).",
                status_code=415,
            )
        if dtype not in ("uint8", "uint16"):
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
                f"Unsupported raster dtype '{dtype}'; supported capability matrix: uint8 (1/3/4 bands) and uint16 grayscale TIFF.",
                status_code=415,
            )
        if count not in (1, 2, 3, 4):
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
                f"Unsupported band count {count}; expected 1 (gray), 2 (gray+alpha), 3 (RGB), or 4 (RGBA).",
                status_code=415,
            )

        warnings: List[str] = []
        has_crs = ds.crs is not None
        crs_str = ds.crs.to_string() if ds.crs is not None else None
        # Rasterio uses Affine.identity() (or identity-like top-down fallback) when un-georeferenced.
        t = ds.transform
        has_transform = bool(t is not None and not t.is_identity)
        gcps, _gcp_crs = ds.gcps
        has_gcps = bool(gcps and len(gcps) > 0)
        gcp_count = len(gcps) if gcps else 0

        if has_crs or has_transform:
            warnings.append(
                f"Input TIFF already contains georeferencing metadata (crs={crs_str or 'none'}, transform={t if has_transform else 'identity'}); original bytes remain untouched and the new GCP affine model will supersede it on export."
            )
        if has_gcps:
            warnings.append(
                f"Input TIFF embeds {gcp_count} internal GCP tag(s); existing tags are disclosed and will not be silently reused without explicit import."
            )

        meta = ExistingGeorefMetadata(
            has_existing_crs=has_crs,
            existing_crs=crs_str,
            has_existing_transform=has_transform,
            has_embedded_gcps=has_gcps,
            embedded_gcp_count=gcp_count,
            warnings=warnings,
        )
        return meta, count, dtype, w, h


def _check_pixel_observations(arr: np.ndarray, has_alpha: bool) -> List[str]:
    obs: List[str] = []
    if arr.ndim == 2:
        if arr.size > 0 and int(np.ptp(arr)) == 0:
            obs.append("uniform_constant_image")
    elif arr.ndim == 3:
        if has_alpha and arr.shape[2] in (2, 4):
            alpha = arr[:, :, -1]
            if int(np.max(alpha)) == 0:
                obs.append("fully_transparent_image")
            elif int(np.min(alpha)) < 255:
                obs.append("contains_partial_or_transparent_alpha")
            rgb = arr[:, :, :-1]
        else:
            rgb = arr
        if rgb.size > 0 and int(np.ptp(rgb)) == 0:
            obs.append("uniform_constant_color")
    return obs


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _ingest_source_bytes_sync(
    *,
    session_id: str,
    filename: str,
    raw_bytes: bytes,
    ignore_exif: bool = False,
) -> GeorefSourceRecord:
    """Validate, decode, EXIF-canonicalize, and immutably store an uploaded source raster.

    同步重实现 —— Pillow 解码 / GDAL 检查 / SHA256 / 落盘全部在调用线程执行；
    async 入口 ``ingest_source_bytes`` 用 ``asyncio.to_thread`` 包裹，绝不在
    事件循环上做大图解码（issue #2193）。
    """
    if not raw_bytes:
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_DECODE_FAILED,
            "Uploaded file is empty.",
            status_code=400,
        )
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_TOO_LARGE,
            f"Upload size ({len(raw_bytes)} bytes) exceeds {MAX_UPLOAD_BYTES} bytes limit.",
            status_code=413,
            details={"byte_size": len(raw_bytes), "max_bytes": MAX_UPLOAD_BYTES},
        )

    detected_format = _detect_magic_format(raw_bytes[:64])
    orig_sha = compute_sha256(raw_bytes)
    observations: List[str] = []
    existing_georef = ExistingGeorefMetadata()

    prev_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_DECODE_PIXELS
    try:
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                real_fmt = (img.format or "").upper()
                if real_fmt != detected_format:
                    raise GeorefDomainError(
                        GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
                        f"File header ({detected_format}) disagrees with decoded format ({real_fmt}).",
                        status_code=415,
                    )
                if getattr(img, "is_animated", False) or getattr(img, "n_frames", 1) > 1:
                    raise GeorefDomainError(
                        GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
                        f"Multi-frame/multi-page {real_fmt} ({getattr(img, 'n_frames', 1)} frames) is not supported; provide a single-page image.",
                        status_code=415,
                    )
                w0, h0 = img.size
                if w0 <= 0 or h0 <= 0:
                    raise GeorefDomainError(
                        GeorefErrorCode.SOURCE_DECODE_FAILED,
                        "Image has zero width or height.",
                        status_code=400,
                    )
                if w0 * h0 > MAX_DECODE_PIXELS or max(w0, h0) > MAX_DECODE_SIDE:
                    raise GeorefDomainError(
                        GeorefErrorCode.SOURCE_TOO_LARGE,
                        f"Image dimensions {w0}x{h0} ({w0 * h0} px) exceed decode limits ({MAX_DECODE_PIXELS} px, max side {MAX_DECODE_SIDE}).",
                        status_code=413,
                        details={"width": w0, "height": h0, "max_pixels": MAX_DECODE_PIXELS, "max_side": MAX_DECODE_SIDE},
                    )
                orientation = _parse_orientation(img, raw_bytes, detected_format, ignore_exif=ignore_exif)
                applied_transform = "ignored_by_user" if ignore_exif else _ORIENTATION_NAMES.get(orientation, "none")
                raw_tiff_ori = 1

                if detected_format == "TIFF":
                    try:
                        tiff_exif = img.getexif()
                        if tiff_exif and 0x0112 in tiff_exif:
                            raw_tiff_ori = int(tiff_exif[0x0112])
                    except Exception:
                        raw_tiff_ori = 1
                    # Inspect via GDAL/Rasterio for existing CRS/transform/GCPs + uint16 check.
                    with tempfile.TemporaryDirectory(prefix="g48-tiff-check-") as td:
                        tmp_tif = Path(td) / "input.tif"
                        tmp_tif.write_bytes(raw_bytes)
                        existing_georef, bands, dtype_str, rw, rh = _inspect_existing_tiff_georef(tmp_tif)
                        # Pillow TiffImagePlugin swaps img.size when TIFF orientation tag is 5..8.
                        expected_pillow_wh = (rh, rw) if raw_tiff_ori in (5, 6, 7, 8) else (rw, rh)
                        if expected_pillow_wh != (w0, h0):
                            raise GeorefDomainError(
                                GeorefErrorCode.SOURCE_DECODE_FAILED,
                                f"TIFF dimension mismatch between Pillow ({w0}x{h0}) and GDAL ({rw}x{rh}).",
                                status_code=422,
                            )
                else:
                    dtype_str = "uint8"

                # Force full decode cleanly to validate corrupt streams.
                try:
                    img.load()
                except Exception as exc:  # noqa: BLE001
                    raise GeorefDomainError(
                        GeorefErrorCode.SOURCE_DECODE_FAILED,
                        f"Failed to decode {detected_format} pixel stream: {exc}",
                        status_code=422,
                    ) from exc

                work_img: Image.Image = img
                if detected_format == "TIFF" and ignore_exif and raw_tiff_ori != 1 and dtype_str == "uint8":
                    # Pillow TiffImagePlugin auto-applies TIFF Orientation on decode; reload raw pixels via GDAL.
                    with tempfile.TemporaryDirectory(prefix="g48-tiff-raw-") as td:
                        tmp_tif = Path(td) / "input.tif"
                        tmp_tif.write_bytes(raw_bytes)
                        with rasterio_env(), open_raster(tmp_tif) as ds:
                            raw_arr = ds.read()
                    if raw_arr.shape[0] == 1:
                        work_img = Image.fromarray(raw_arr[0].astype(np.uint8), mode="L")
                    elif raw_arr.shape[0] in (2, 3, 4):
                        hwc = np.moveaxis(raw_arr, 0, -1).astype(np.uint8)
                        raw_mode = {2: "LA", 3: "RGB", 4: "RGBA"}[raw_arr.shape[0]]
                        work_img = Image.fromarray(hwc, mode=raw_mode)
                elif orientation != 1 and not ignore_exif and not (detected_format == "TIFF" and dtype_str == "uint16"):
                    try:
                        work_img = ImageOps.exif_transpose(work_img)
                    except Exception as exc:  # noqa: BLE001
                        raise GeorefDomainError(
                            GeorefErrorCode.SOURCE_EXIF_FAILED,
                            f"EXIF orientation transpose failed: {exc}",
                            status_code=422,
                        ) from exc
                if ignore_exif:
                    observations.append("exif_orientation_ignored_by_user")

                # Canonicalize pixel layout cleanly without falsifying uint16 gray.
                session_dir = georef_session_dir(session_id)
                source_id = f"src_{uuid.uuid4().hex[:16]}"
                sources_dir = session_dir / "sources"

                if detected_format == "TIFF" and dtype_str == "uint16":
                    with tempfile.TemporaryDirectory(prefix="g48-u16-") as td:
                        tmp_tif = Path(td) / "input.tif"
                        tmp_tif.write_bytes(raw_bytes)
                        with rasterio_env(), open_raster(tmp_tif) as ds:
                            band_arr = ds.read(1)
                    if not ignore_exif:
                        band_arr = _apply_exif_orientation_2d(band_arr, orientation)
                    norm_h, norm_w = band_arr.shape
                    # Save canonical un-georeferenced lossless uint16 TIFF without old geo tags.
                    norm_path = sources_dir / f"{source_id}_norm.tif"
                    sources_dir.mkdir(parents=True, exist_ok=True)
                    tmp_norm = norm_path.with_name(f".{norm_path.name}.tmp-{uuid.uuid4().hex[:8]}")
                    try:
                        import rasterio

                        with rasterio_env(), rasterio.open(
                            tmp_norm,
                            "w",
                            driver="GTiff",
                            width=norm_w,
                            height=norm_h,
                            count=1,
                            dtype="uint16",
                            compress="DEFLATE",
                        ) as dst:
                            dst.write(band_arr, 1)
                        os.replace(tmp_norm, norm_path)
                    finally:
                        tmp_norm.unlink(missing_ok=True)
                    norm_bytes = norm_path.read_bytes()
                    # Also render a 8-bit PNG viewer preview cleanly at exact same dimensions.
                    lo = float(np.min(band_arr))
                    hi = float(np.max(band_arr))
                    if hi > lo:
                        scaled = np.clip(np.rint((band_arr.astype(np.float64) - lo) / (hi - lo) * 255.0), 0, 255).astype(np.uint8)
                    else:
                        scaled = np.zeros_like(band_arr, dtype=np.uint8)
                    viewer_buf = io.BytesIO()
                    Image.fromarray(scaled, mode="L").save(viewer_buf, format="PNG")
                    _atomic_write_bytes(sources_dir / f"{source_id}_viewer.png", viewer_buf.getvalue())
                    bands = 1
                    color_mode = "uint16_gray"
                    has_alpha = False
                    dtype_label = "uint16"
                    observations.extend(_check_pixel_observations(band_arr, False))
                    observations.append("uint16_grayscale_tiff")
                else:
                    mode = work_img.mode
                    if mode == "P":
                        if "transparency" in work_img.info:
                            work_img = work_img.convert("RGBA")
                        else:
                            work_img = work_img.convert("RGB")
                        observations.append("palette_converted")
                    elif mode == "PA":
                        work_img = work_img.convert("RGBA")
                        observations.append("palette_alpha_converted")
                    elif mode == "CMYK":
                        work_img = work_img.convert("RGB")
                        observations.append("cmyk_converted_to_rgb")
                    elif mode == "1":
                        work_img = work_img.convert("L")
                        observations.append("1bit_converted_to_gray")
                    elif mode not in ("L", "LA", "RGB", "RGBA"):
                        raise GeorefDomainError(
                            GeorefErrorCode.SOURCE_UNSUPPORTED_FORMAT,
                            f"Unsupported image color mode '{mode}'; expected grayscale, RGB, or RGBA.",
                            status_code=415,
                        )

                    norm_w, norm_h = work_img.size
                    if work_img.mode == "L":
                        bands, color_mode, has_alpha = 1, "grayscale", False
                    elif work_img.mode == "LA":
                        bands, color_mode, has_alpha = 2, "grayscale_alpha", True
                    elif work_img.mode == "RGB":
                        bands, color_mode, has_alpha = 3, "rgb", False
                    else:
                        bands, color_mode, has_alpha = 4, "rgba", True
                    dtype_label = "uint8"
                    observations.extend(_check_pixel_observations(np.asarray(work_img), has_alpha))

                    # Materialize normalized pixel-exact lossless PNG (strips EXIF once already applied).
                    out_buf = io.BytesIO()
                    work_img.save(out_buf, format="PNG", compress_level=6)
                    norm_bytes = out_buf.getvalue()
                    norm_path = sources_dir / f"{source_id}_norm.png"
                    _atomic_write_bytes(norm_path, norm_bytes)
                    _atomic_write_bytes(sources_dir / f"{source_id}_viewer.png", norm_bytes)
        except Image.DecompressionBombError as exc:
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_TOO_LARGE,
                f"Decompression bomb guard rejected image: {exc}",
                status_code=413,
            ) from exc
        except GeorefDomainError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GeorefDomainError(
                GeorefErrorCode.SOURCE_DECODE_FAILED,
                f"Unable to open/decode uploaded image: {exc}",
                status_code=422,
            ) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = prev_limit

    norm_sha = compute_sha256(norm_bytes)
    orig_key = pin_blob_bytes(orig_sha, session_id, raw_bytes)
    norm_key = pin_blob_from_path(norm_sha, session_id, norm_path)

    record = GeorefSourceRecord(
        source_id=source_id,
        session_id=session_id,
        filename=os.path.basename(filename or "uploaded_image")[:200],
        detected_format=detected_format,
        width=int(norm_w),
        height=int(norm_h),
        bands=int(bands),
        dtype=dtype_label,
        color_mode=color_mode,
        has_alpha=has_alpha,
        exif_orientation=int(orientation),
        exif_applied_transform=applied_transform,
        exif_overridden=bool(ignore_exif),
        original_blob_key=orig_key,
        normalized_blob_key=norm_key,
        normalized_path=str(norm_path),
        original_sha256=orig_sha,
        normalized_sha256=norm_sha,
        byte_size=len(raw_bytes),
        observations=sorted(set(observations)),
        existing_georef=existing_georef,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    save_source(record)
    return record


async def ingest_source_bytes(
    *,
    session_id: str,
    filename: str,
    raw_bytes: bytes,
    ignore_exif: bool = False,
) -> GeorefSourceRecord:
    """Async facade for :func:`_ingest_source_bytes_sync` (issue #2193).

    ≤32M px 的 Pillow 解码、GDAL 临时 TIFF 检查、EXIF 转置、PNG/TIFF 编码、
    SHA256 与 BlobStore 落盘全部卸载到工作线程，事件循环零阻塞。
    """
    import asyncio

    return await asyncio.to_thread(
        _ingest_source_bytes_sync,
        session_id=session_id,
        filename=filename,
        raw_bytes=raw_bytes,
        ignore_exif=ignore_exif,
    )
