"""Raster math operations: reclassify, calculator, resample."""
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject, calculate_default_transform


# ─── Shared GDAL environment ────────────────────────────────────


@contextmanager
def rasterio_env():
    """Shared GDAL env for all raster reads/writes.

    Disables read-dir-on-open (avoids scanning adjacent files), sets a short
    HTTP timeout, and forbids retries — so a hanging remote source fails fast
    instead of blocking the worker. Consolidates the 4 identical
    ``rasterio.Env(...)`` blocks previously inlined in ``SpatialAnalyzer``'s
    raster methods (ADR-0037 Win 2).
    """
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="TRUE",
        GDAL_HTTP_TIMEOUT=5,
        GDAL_HTTP_MAX_RETRY=0,
    ):
        yield


RESAMPLING_MAP = {
    "nearest": Resampling.nearest,
    "bilinear": Resampling.bilinear,
    "cubic": Resampling.cubic,
    "mode": Resampling.mode,
    "average": Resampling.average,
    "lanczos": Resampling.lanczos,
    "med": Resampling.med,
    "min": Resampling.min,
    "max": Resampling.max,
    "sum": Resampling.sum,
    "q1": Resampling.q1,
    "q3": Resampling.q3,
}



# ─── Shared helpers ──────────────────────────────────────────────


def _gtiff_profile(src_profile: dict, nodata: Optional[float] = None, count: int = 1) -> dict:
    """Build a compressed, tiled GTiff write profile from a source raster profile."""
    profile = src_profile.copy()
    profile.update({
        "driver": "GTiff",
        "compress": "lzw",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "count": count,
    })
    if nodata is not None:
        profile["nodata"] = nodata
    return profile


def _suffix_output_path(raster_path: str, suffix: str) -> str:
    """Append a suffix before the file extension, avoiding collisions."""
    p = Path(raster_path)
    return str(p.parent / f"{p.stem}{suffix}")


def _validate_scheme(scheme: list[dict]) -> None:
    """Validate reclassify scheme items. Raises ValueError on invalid input."""
    if not scheme:
        raise ValueError("scheme must contain at least one rule")
    for i, rule in enumerate(scheme):
        if "value" not in rule:
            raise ValueError(f"scheme[{i}] missing required 'value' key")
        if "min" not in rule and "max" not in rule:
            raise ValueError(f"scheme[{i}] must have 'min' and/or 'max'")
        if "min" in rule and "max" in rule and rule["min"] > rule["max"]:
            raise ValueError(f"scheme[{i}] 'min' ({rule['min']}) cannot be greater than 'max' ({rule['max']})")


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
    out_path = _suffix_output_path(raster_path, "_reclassified.tif")

    with rasterio.open(raster_path) as src:
        data = src.read(1)
        src_nodata = src.nodata if src.nodata is not None else src.profile.get("nodata")

        if nodata is not None:
            out_nodata = nodata
        elif src_nodata is not None:
            out_nodata = src_nodata
        else:
            out_nodata = 0

        profile = _gtiff_profile(src.profile, nodata=out_nodata, count=1)

        scheme_values = [r.get("value", 0) for r in scheme]
        out_dtype = np.result_type(*scheme_values, out_nodata) if scheme_values else np.result_type(out_nodata)

        profile["dtype"] = out_dtype
        out_data = np.full_like(data, fill_value=out_nodata, dtype=out_dtype)

        assigned = np.zeros(data.shape, dtype=bool)
        if src_nodata is not None:
            assigned[data == src_nodata] = True

        for rule in scheme:
            rmin = rule.get("min", -float("inf"))
            rmax = rule.get("max", float("inf"))
            rval = rule["value"]
            rule_mask = (data >= rmin) & (data <= rmax)
            if src_nodata is not None:
                rule_mask = rule_mask & (data != src_nodata)

            match_mask = rule_mask & (~assigned)
            out_data[match_mask] = rval
            assigned[match_mask] = True

        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(out_data, 1)

    if isinstance(out_nodata, float) and np.isnan(out_nodata):
        valid_mask = ~np.isnan(out_data)
    else:
        valid_mask = (out_data != out_nodata)

    unique_vals = np.unique(out_data[valid_mask])
    label_map = {rule["value"]: rule.get("label", str(rule["value"])) for rule in scheme}

    if np.issubdtype(out_data.dtype, np.integer):
        unique_value_list = [int(v) for v in unique_vals]
    else:
        unique_value_list = [float(v) for v in unique_vals]

    stats = {
        "output_path": out_path,
        "pixel_count": int(valid_mask.sum()),
        "unique_values": unique_value_list,
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
        nodata_a = src_a.nodata if src_a.nodata is not None else src_a.profile.get("nodata")

        if raster_b:
            with rasterio.open(raster_b) as src_b:
                nodata_b = src_b.nodata if src_b.nodata is not None else src_b.profile.get("nodata", nodata_a)
                if (src_b.crs != src_a.crs) or (src_b.transform != src_a.transform) or (src_b.shape != src_a.shape):
                    fill_b = nodata_b if nodata_b is not None else 0
                    data_b = np.full(src_a.shape, fill_value=fill_b, dtype=src_b.dtypes[0])
                    gcps_b, gcps_crs_b = src_b.gcps if src_b.gcps else (None, None)
                    reproject_kwargs = {
                        "source": rasterio.band(src_b, 1),
                        "destination": data_b,
                        "dst_transform": src_a.transform,
                        "dst_crs": src_a.crs,
                        "resampling": Resampling.nearest,
                        "src_nodata": nodata_b,
                        "dst_nodata": fill_b,
                    }
                    if gcps_b:
                        reproject_kwargs["gcps"] = gcps_b
                        reproject_kwargs["gcps_crs"] = gcps_crs_b
                        reproject_kwargs["src_crs"] = gcps_crs_b or src_b.crs
                    else:
                        reproject_kwargs["src_transform"] = src_b.transform
                        reproject_kwargs["src_crs"] = src_b.crs

                    reproject(**reproject_kwargs)
                else:
                    data_b = src_b.read(1)
        else:
            const_val = constant if constant is not None else 0
            data_b = np.full_like(data_a, fill_value=const_val, dtype=data_a.dtype)
            nodata_b = nodata_a

        if nodata is None:
            out_nodata = nodata_a if nodata_a is not None else 0
        else:
            out_nodata = nodata

        if nodata_a is not None:
            mask_a = (data_a != nodata_a)
        else:
            mask_a = np.ones(src_a.shape, dtype=bool)

        if raster_b and nodata_b is not None:
            mask_b = (data_b != nodata_b)
        else:
            mask_b = np.ones(src_a.shape, dtype=bool)

        mask = mask_a & mask_b

        valid_a = np.where(mask, data_a, 0)
        valid_b = np.where(mask, data_b, 0)
        result = ne.evaluate(expression, local_dict={"A": valid_a, "B": valid_b})

        result = np.where(np.isfinite(result), result, out_nodata)
        result = np.where(mask, result, out_nodata)

        profile = _gtiff_profile(src_a.profile, nodata=out_nodata, count=1)
        profile["dtype"] = result.dtype

        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(result, 1)

    if isinstance(out_nodata, float) and np.isnan(out_nodata):
        valid = result[~np.isnan(result)]
    else:
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
    res_key = resampling.lower()
    if res_key not in RESAMPLING_MAP:
        raise ValueError(f"Unsupported resampling method: '{resampling}'. Valid options: {list(RESAMPLING_MAP.keys())}")
    resampling_method = RESAMPLING_MAP[res_key]

    out_path = _suffix_output_path(raster_path, "_resampled.tif")

    with rasterio.open(raster_path) as src:
        gcps, gcps_crs = src.gcps if src.gcps else (None, None)
        src_crs = gcps_crs or src.crs
        dst_crs = target_crs if target_crs else src_crs

        if gcps:
            transform, width, height = calculate_default_transform(
                src_crs, dst_crs, src.width, src.height, gcps=gcps, resolution=target_resolution
            )
        else:
            transform, width, height = calculate_default_transform(
                src_crs, dst_crs, src.width, src.height, *src.bounds, resolution=target_resolution
            )

        profile = _gtiff_profile(src.profile, count=src.count)
        profile.update({
            "crs": dst_crs,
            "transform": transform,
            "width": width,
            "height": height,
        })
        src_nodata = src.nodata if src.nodata is not None else src.profile.get("nodata")
        dst_nodata = profile.get("nodata")

        with rasterio.open(out_path, "w", **profile) as dst:
            for i in range(1, src.count + 1):
                reproject_kwargs = {
                    "source": rasterio.band(src, i),
                    "destination": rasterio.band(dst, i),
                    "src_crs": src_crs,
                    "dst_transform": transform,
                    "dst_crs": dst_crs,
                    "resampling": resampling_method,
                    "src_nodata": src_nodata,
                    "dst_nodata": dst_nodata,
                }
                if gcps:
                    reproject_kwargs["gcps"] = gcps
                    reproject_kwargs["gcps_crs"] = gcps_crs
                else:
                    reproject_kwargs["src_transform"] = src.transform

                reproject(**reproject_kwargs)

    return {
        "output_path": out_path,
        "target_crs": str(dst_crs),
        "target_resolution": target_resolution,
        "new_shape": [height, width],
        "resampling": resampling,
    }

