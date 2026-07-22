"""Raster math operations: reclassify, calculator, resample."""
import enum
import os
from typing import Optional

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject, calculate_default_transform

from app.utils.path import validate_data_path


class ResamplingMethod(enum.Enum):
    """Supported raster resampling methods."""
    NEAREST = Resampling.nearest
    BILINEAR = Resampling.bilinear
    CUBIC = Resampling.cubic
    MODE = Resampling.mode
    AVERAGE = Resampling.average


# ─── Shared helpers ──────────────────────────────────────────────


def _gtiff_profile(src_profile: dict, nodata: Optional[float] = None) -> dict:
    """Build a compressed, tiled GTiff write profile from a source raster profile."""
    profile = src_profile.copy()
    profile.update({
        "driver": "GTiff",
        "compress": "lzw",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    })
    if nodata is not None:
        profile["nodata"] = nodata
    return profile


def _suffix_output_path(raster_path: str, suffix: str) -> str:
    """Append a suffix before the .tif extension, avoiding collisions."""
    if raster_path.endswith(".tif"):
        out_path = raster_path[:-4] + suffix
    else:
        out_path = raster_path + suffix
    # Guard against path.replace producing the same string (e.g., already suffixed)
    if out_path == raster_path:
        out_path = raster_path + suffix
    return out_path


def _validate_scheme(scheme: list[dict]) -> None:
    """Validate reclassify scheme items. Raises ValueError on invalid input."""
    if not scheme:
        raise ValueError("scheme must contain at least one rule")
    for i, rule in enumerate(scheme):
        if "value" not in rule:
            raise ValueError(f"scheme[{i}] missing required 'value' key")
        if "min" not in rule and "max" not in rule:
            raise ValueError(f"scheme[{i}] must have 'min' and/or 'max'")


# ─── Operations ──────────────────────────────────────────────────


def reclassify(
    raster_path: str,
    scheme: list[dict],
    nodata: Optional[float] = None,
) -> dict:
    """Reclassify raster pixel values into categories.

    Args:
        raster_path: Path to input raster (validated by caller).
        scheme: List of {min, max, value, label?} dicts. Applied in order;
            first match wins. Unmatched pixels become nodata.
        nodata: Output nodata value (default: input raster's nodata or 0).

    Returns:
        dict with output_path, stats, and metadata.
    """
    _validate_scheme(scheme)
    scheme = sorted(scheme, key=lambda s: s.get("min", -float("inf")))
    out_path = _suffix_output_path(raster_path, "_reclassified.tif")

    with rasterio.open(raster_path) as src:
        data = src.read(1)
        profile = _gtiff_profile(src.profile, nodata)
        out_nodata = nodata if nodata is not None else (profile.get("nodata", 0))
        out_data = np.full_like(data, fill_value=out_nodata, dtype=profile.get("dtype", data.dtype))

        for rule in scheme:
            rmin = rule.get("min", -float("inf"))
            rmax = rule.get("max", float("inf"))
            rval = rule["value"]
            mask = (data >= rmin) & (data <= rmax) & (data != profile.get("nodata"))
            out_data[mask] = rval

        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(out_data, 1)

    unique_vals = np.unique(out_data[out_data != out_nodata])
    label_map = {rule["value"]: rule.get("label", str(rule["value"])) for rule in scheme}
    stats = {
        "output_path": out_path,
        "pixel_count": int((out_data != out_nodata).sum()),
        "unique_values": [int(v) for v in unique_vals],
        "labels": {str(k): v for k, v in label_map.items() if k in unique_vals},
    }
    return stats


def raster_calculator(
    raster_a: str,
    raster_b: Optional[str] = None,
    expression: str = "A + B",
    constant: Optional[float] = None,
    nodata: Optional[float] = None,
) -> dict:
    """Pixel-wise raster math.

    Args:
        raster_a: Primary raster path.
        raster_b: Optional secondary raster path. If None, `constant` is used.
        expression: Numexpr-compatible expression using A (raster_a) and B (raster_b).
            Examples: "A + B", "A * 2", "(A - B) / (A + B)", "where(A > 0, A, 0)".
        constant: Scalar value used when raster_b is None.
        nodata: Output nodata value.

    Returns:
        dict with output_path, stats, and metadata.
    """
    import numexpr as ne

    out_path = _suffix_output_path(raster_a, "_calc.tif")

    with rasterio.open(raster_a) as src_a:
        data_a = src_a.read(1)
        profile = _gtiff_profile(src_a.profile)
        nodata_a = profile.get("nodata")

        if raster_b:
            with rasterio.open(raster_b) as src_b:
                data_b = src_b.read(1).astype(data_a.dtype)
                if src_b.shape != src_a.shape:
                    data_b = np.broadcast_to(data_b, src_a.shape).copy()
                nodata_b = src_b.profile.get("nodata", nodata_a)
        else:
            data_b = np.full_like(data_a, fill_value=constant if constant is not None else 0, dtype=data_a.dtype)
            nodata_b = nodata_a

        # Use each raster's own nodata for masking (fix: was using nodata_a for both)
        mask = (data_a != nodata_a) & (data_b != nodata_b)
        if nodata is None:
            out_nodata = nodata_a if nodata_a is not None else 0
        else:
            out_nodata = nodata

        valid_a = np.where(mask, data_a, 0)
        valid_b = np.where(mask, data_b, 0)
        result = ne.evaluate(expression, local_dict={"A": valid_a, "B": valid_b})
        result = np.where(mask, result, out_nodata)
        result = result.astype(data_a.dtype)

        profile.update({"nodata": out_nodata})
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(result, 1)

    valid = result[result != out_nodata]
    stats = {
        "output_path": out_path,
        "expression": expression,
        "min": float(valid.min()) if valid.size > 0 else 0.0,
        "max": float(valid.max()) if valid.size > 0 else 0.0,
        "mean": float(valid.mean()) if valid.size > 0 else 0.0,
        "pixel_count": int(valid.size),
    }
    return stats


def resample_raster(
    raster_path: str,
    target_resolution: float,
    target_crs: Optional[str] = None,
    resampling: str = "bilinear",
) -> dict:
    """Resample raster to a new resolution and/or CRS.

    Args:
        raster_path: Path to input raster.
        target_resolution: Target pixel size in meters (for projected CRS) or degrees (for geographic).
        target_crs: Optional target CRS (e.g., "EPSG:3857"). If None, keeps source CRS.
        resampling: Resampling method: bilinear, cubic, nearest, mode, average.

    Returns:
        dict with output_path, new_shape, new_transform, and metadata.
    """
    _RESAMPLING_METHODS = {
        ResamplingMethod.NEAREST: Resampling.nearest,
        ResamplingMethod.BILINEAR: Resampling.bilinear,
        ResamplingMethod.CUBIC: Resampling.cubic,
        ResamplingMethod.MODE: Resampling.mode,
        ResamplingMethod.AVERAGE: Resampling.average,
    }
    method = ResamplingMethod(resampling.lower())
    resampling_method = _RESAMPLING_METHODS[method]

    out_path = _suffix_output_path(raster_path, "_resampled.tif")

    with rasterio.open(raster_path) as src:
        dst_crs = target_crs if target_crs else src.crs
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds, resolution=target_resolution
        )
        profile = _gtiff_profile(src.profile)
        profile.update({
            "crs": dst_crs,
            "transform": transform,
            "width": width,
            "height": height,
        })

        with rasterio.open(out_path, "w", **profile) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=resampling_method,
                )

    return {
        "output_path": out_path,
        "target_crs": str(dst_crs),
        "target_resolution": target_resolution,
        "new_shape": [height, width],
        "resampling": resampling,
    }
