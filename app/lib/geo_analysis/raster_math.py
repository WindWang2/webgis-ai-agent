"""Raster math operations: reclassify, calculator, resample."""
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window
from rasterio.warp import reproject, calculate_default_transform

# ADR-0052: 窗口写入循环现在（a）在窗口边界检查取消，（b）写临时文件再原子
# os.replace —— 取消/崩溃不再留下半个 GeoTIFF（规范 §12 raster window / §23）。
from app.services.jobs.artifacts import atomic_output
from app.services.jobs.cancellation import checkpoint


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

# OOM/disk guard for resample output grids: a unit-confusion request
# (e.g. target_resolution=1 meaning 1 m on a 3°×3° EPSG:4326 source warped
# to EPSG:3857) would create a ~334k×334k grid — ~111 billion pixels,
# ~400 GiB uncompressed — and hang or OOM the worker. Mirrors the
# _MAX_GRID_CELLS guard pattern in density.py.
MAX_OUTPUT_PIXELS = 250_000_000   # ≈1 GiB float32, single band
MAX_OUTPUT_DIMENSION = 100_000    # per side, catches extreme aspect ratios
MAX_OUTPUT_UPSCALE_RATIO = 10_000  # out px / in px, catches unit confusion on small inputs

# Pixel budgets used to suggest coarser target_resolution values in errors.
_SUGGESTION_BUDGETS = (1_000_000, 10_000_000, MAX_OUTPUT_PIXELS)

# Windowed processing: raster math iterates a fixed window grid so memory is
# O(window) — even when the source file has a single giant block — instead of
# O(full raster) with several full-size temporaries.
_WINDOW_SIZE = 512



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


from app.lib.geo_analysis.raster_guard import RasterResourceGuard


def _guard_output_grid(
    width: int,
    height: int,
    src_pixels: int,
    target_resolution: float,
    bands: int = 1,
    dtype: str = "float32",
) -> None:
    """Reject resample outputs that exceed the resource budget."""
    bytes_per_pixel = np.dtype(dtype).itemsize
    RasterResourceGuard.check_grid(
        width=width,
        height=height,
        bytes_per_pixel=bytes_per_pixel,
        num_bands=bands,
    )


def _nodata_valid_mask(arr: np.ndarray, nodata) -> np.ndarray:
    """Boolean mask of *valid* (non-nodata) pixels.

    Handles the NaN-nodata case correctly: ``arr != NaN`` is all-True
    (``NaN != NaN``), so a naive value comparison would treat every pixel as
    valid. Mirrors reclassify's ``_valid_mask``. (R-F03)

    Float arrays also exclude *undeclared* NaN pixels — where the declared
    nodata is a scalar (or None) but the data still contains NaN — so a
    ``where(A > 0, A, 0)``-style expression cannot turn an undeclared NaN into
    a valid-looking 0 (review B). Integer arrays cannot hold NaN.
    """
    if nodata is None:
        base = np.ones(arr.shape, dtype=bool)
    elif isinstance(nodata, float) and np.isnan(nodata):
        base = ~np.isnan(arr)
    else:
        base = arr != nodata
    if np.issubdtype(arr.dtype, np.floating):
        base = base & ~np.isnan(arr)
    return base


def _compute_dtype(arr: np.ndarray) -> np.ndarray:
    """Return ``arr`` promoted to float64 when it is integer, else unchanged.

    numexpr evaluates integer expressions in int32 and silently wraps on
    overflow — e.g. ``uint16 * uint16`` with values >~46340 yields large
    negative numbers written as valid data (R-F01). Promoting integer inputs
    to float64 before evaluation makes arithmetic overflow impossible for any
    realistic raster value range. Float inputs are left as-is (float32 overflow
    to inf is caught downstream by the ``np.isfinite`` guard).
    """
    if np.issubdtype(arr.dtype, np.integer):
        return arr.astype(np.float64)
    return arr


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
        src_nodata = src.nodata if src.nodata is not None else src.profile.get("nodata")

        if nodata is not None:
            out_nodata = nodata
        elif src_nodata is not None:
            out_nodata = src_nodata
        else:
            out_nodata = 0

        scheme_values = [r.get("value", 0) for r in scheme]
        out_dtype = np.result_type(*scheme_values, out_nodata) if scheme_values else np.result_type(out_nodata)

        profile = _gtiff_profile(src.profile, nodata=out_nodata, count=1)
        profile["dtype"] = out_dtype

        # Windowed: fixed grid so memory stays O(_WINDOW_SIZE²) even for
        # single-block sources; stats accumulate across windows and are
        # re-uniqued at the end (identical to the full-array np.unique).
        pixel_count = 0
        per_window_uniques: list[np.ndarray] = []

        def _valid_mask(arr: np.ndarray) -> np.ndarray:
            if isinstance(out_nodata, float) and np.isnan(out_nodata):
                return ~np.isnan(arr)
            return arr != out_nodata

        with atomic_output(out_path) as _tmp_out, rasterio.open(_tmp_out, "w", **profile) as dst:
            for row0 in range(0, src.height, _WINDOW_SIZE):
                for col0 in range(0, src.width, _WINDOW_SIZE):
                    checkpoint()
                    win = Window(
                        col0, row0,
                        min(_WINDOW_SIZE, src.width - col0),
                        min(_WINDOW_SIZE, src.height - row0),
                    )
                    data = src.read(1, window=win)
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

                    dst.write(out_data, 1, window=win)
                    valid = out_data[_valid_mask(out_data)]
                    pixel_count += int(valid.size)
                    if valid.size:
                        per_window_uniques.append(np.unique(valid))

    if per_window_uniques:
        unique_vals = np.unique(np.concatenate(per_window_uniques))
    else:
        unique_vals = np.array([], dtype=out_dtype)

    label_map = {rule["value"]: rule.get("label", str(rule["value"])) for rule in scheme}

    if np.issubdtype(out_dtype, np.integer):
        unique_value_list = [int(v) for v in unique_vals]
    else:
        unique_value_list = [float(v) for v in unique_vals]

    stats = {
        "output_path": out_path,
        "pixel_count": pixel_count,
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
        nodata_a = src_a.nodata if src_a.nodata is not None else src_a.profile.get("nodata")
        RasterResourceGuard.check_grid(
            width=src_a.width,
            height=src_a.height,
            bytes_per_pixel=np.dtype(src_a.dtypes[0]).itemsize,
            num_bands=1,
            input_pixels=src_a.width * src_a.height,
            bounds=src_a.bounds,
        )

        if nodata is None:
            out_nodata = nodata_a if nodata_a is not None else 0
        else:
            out_nodata = nodata

        # B 输入准备：对齐栅格（同 CRS/transform/shape）走窗口化路径；不对齐
        # 的 B 需整幅重投影 —— 工具说明已建议先 resample 对齐，保留原实现。
        src_b = None
        data_b_full: Optional[np.ndarray] = None
        aligned = False
        try:
            if raster_b:
                src_b = rasterio.open(raster_b)
                nodata_b = src_b.nodata if src_b.nodata is not None else src_b.profile.get("nodata", nodata_a)
                aligned = (
                    src_b.crs == src_a.crs
                    and src_b.transform == src_a.transform
                    and src_b.shape == src_a.shape
                )
                if not aligned:
                    # Guard raster B's own footprint before the full-band
                    # reproject: the A-grid guard above does not see B, so a
                    # huge B paired with a small A would otherwise pass and the
                    # reproject reads all of B. (R-F04)
                    RasterResourceGuard.check_grid(
                        width=src_b.width,
                        height=src_b.height,
                        bytes_per_pixel=np.dtype(src_b.dtypes[0]).itemsize,
                        num_bands=1,
                        input_pixels=src_a.width * src_a.height,
                        bounds=src_b.bounds,
                    )
                    fill_b = nodata_b if nodata_b is not None else 0
                    data_b_full = np.full(src_a.shape, fill_value=fill_b, dtype=src_b.dtypes[0])
                    gcps_b, gcps_crs_b = src_b.gcps if src_b.gcps else (None, None)
                    reproject_kwargs = {
                        "source": rasterio.band(src_b, 1),
                        "destination": data_b_full,
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
                const_val = constant if constant is not None else 0
                nodata_b = nodata_a

            def _get_b_window(win: Window, data_a_win: np.ndarray) -> np.ndarray:
                if src_b is not None and aligned:
                    return src_b.read(1, window=win)
                if src_b is not None:
                    return data_b_full[win.toslices()]
                return np.full_like(data_a_win, fill_value=const_val, dtype=data_a_win.dtype)

            def _compute_window(data_a_win: np.ndarray, data_b_win: np.ndarray) -> np.ndarray:
                mask_a = _nodata_valid_mask(data_a_win, nodata_a)
                mask_b = (
                    _nodata_valid_mask(data_b_win, nodata_b)
                    if (raster_b and nodata_b is not None)
                    else np.ones(data_a_win.shape, dtype=bool)
                )

                mask = mask_a & mask_b

                valid_a = np.where(mask, data_a_win, 0)
                valid_b = np.where(mask, data_b_win, 0)
                # Promote integer rasters to float64 before numexpr: integer
                # arithmetic wraps on overflow (uint16*uint16 -> negative
                # garbage), and the NaN-nodata mask above is now correct so
                # genuine nodata pixels are zeroed before evaluation. (R-F01)
                valid_a = _compute_dtype(valid_a)
                valid_b = _compute_dtype(valid_b)
                result = ne.evaluate(expression, local_dict={"A": valid_a, "B": valid_b})

                result = np.where(np.isfinite(result), result, out_nodata)
                return np.where(mask, result, out_nodata)

            def _accumulate(res: np.ndarray) -> None:
                nonlocal min_v, max_v, total, count
                if isinstance(out_nodata, float) and np.isnan(out_nodata):
                    valid = res[~np.isnan(res)]
                else:
                    valid = res[res != out_nodata]
                count += int(valid.size)
                if valid.size:
                    total += float(valid.sum())
                    vmin, vmax = float(valid.min()), float(valid.max())
                    min_v = vmin if min_v is None else min(min_v, vmin)
                    max_v = vmax if max_v is None else max(max_v, vmax)

            # 窗口化：固定 512×512 网格，内存 O(window)（对齐/常数路径）。
            # 首个窗口先算 dtype，再建 profile 写文件。
            windows = [
                Window(
                    col0, row0,
                    min(_WINDOW_SIZE, src_a.width - col0),
                    min(_WINDOW_SIZE, src_a.height - row0),
                )
                for row0 in range(0, src_a.height, _WINDOW_SIZE)
                for col0 in range(0, src_a.width, _WINDOW_SIZE)
            ]

            min_v: Optional[float] = None
            max_v: Optional[float] = None
            total = 0.0
            count = 0

            first_win = windows[0]
            data_a0 = src_a.read(1, window=first_win)
            result0 = _compute_window(data_a0, _get_b_window(first_win, data_a0))

            profile = _gtiff_profile(src_a.profile, nodata=out_nodata, count=1)
            profile["dtype"] = result0.dtype

            with atomic_output(out_path) as _tmp_out, rasterio.open(_tmp_out, "w", **profile) as dst:
                dst.write(result0, 1, window=first_win)
                _accumulate(result0)
                for win in windows[1:]:
                    checkpoint()
                    data_a_win = src_a.read(1, window=win)
                    res = _compute_window(data_a_win, _get_b_window(win, data_a_win))
                    dst.write(res, 1, window=win)
                    _accumulate(res)
        finally:
            if src_b is not None:
                src_b.close()

    stats = {
        "output_path": out_path,
        "expression": expression,
        "min": float(min_v) if count > 0 else 0.0,
        "max": float(max_v) if count > 0 else 0.0,
        "mean": float(total / count) if count > 0 else 0.0,
        "pixel_count": count,
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

    Raises:
        ValueError: if target_resolution is not positive, or if the output
            grid would exceed the resource guard (see _guard_output_grid).
    """
    res_key = resampling.lower()
    if res_key not in RESAMPLING_MAP:
        raise ValueError(f"Unsupported resampling method: '{resampling}'. Valid options: {list(RESAMPLING_MAP.keys())}")
    resampling_method = RESAMPLING_MAP[res_key]

    if target_resolution <= 0:
        raise ValueError(f"target_resolution must be positive, got {target_resolution}")

    out_path = _suffix_output_path(raster_path, "_resampled.tif")

    # Artifact cache (ADR-0048): resample is the most expensive file-producing
    # op and fully deterministic. A cache hit returns the stored GeoTIFF path
    # without recomputing the warp. The compute closure runs only on a miss;
    # concurrent misses are still singleflighted at the tool_cache layer.
    from app.lib.artifact_cache import make_artifact_key, publish_artifact

    params = {
        "target_resolution": target_resolution,
        "target_crs": target_crs,
        "resampling": resampling,
    }
    cache_key = make_artifact_key(raster_path, "resample", params)

    def _compute() -> str:
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

            _guard_output_grid(
                width, height, src.width * src.height, target_resolution,
                bands=src.count, dtype=src.dtypes[0],
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

            return out_path

    final_path = publish_artifact(cache_key, raster_path, _compute)

    # Read shape back from the (possibly cached) output for the response.
    with rasterio.open(final_path) as out:
        cached_height, cached_width = out.height, out.width
        cached_crs = str(out.crs)

    return {
        "output_path": final_path,
        "target_crs": cached_crs,
        "target_resolution": target_resolution,
        "new_shape": [cached_height, cached_width],
        "resampling": resampling,
    }

