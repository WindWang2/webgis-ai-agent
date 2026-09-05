"""Raster math operations: reclassify, calculator, resample."""
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window
from rasterio.warp import reproject, calculate_default_transform

# ADR-0052: 窗口写入循环现在（a）在窗口边界检查取消，（b）写临时文件再原子
# os.replace —— 取消/崩溃不再留下半个 GeoTIFF（规范 §12 raster window / §23）。
from app.lib.artifacts import atomic_output
from app.lib.cancellation import checkpoint
from app.lib.geo_analysis.raster_grid import (
    RasterAlignmentError,
    RasterGridProfile,
    aligned_reader,
    decide_alignment,
    iter_bounded_windows,
    window_side_from_budget,
)
from app.lib.geo_analysis.raster_windowed import (
    WindowedRasterWriter,
    build_output_profile,
)


# ─── Shared GDAL environment ────────────────────────────────────


def rasterio_env():
    """Shared GDAL env for all raster reads/writes (兼容导入路径)。

    Runtime V5 起 env 的规范归属是 ``app.lib.geo_raster.env``（env 是运行时
    属性——任何栅格 open 路径都必须持有它，而不是某个 reader 的实现细节）；
    本函数仅委托保留原导入路径（SpatialAnalyzer/STAC client 等既有调用方）。
    Knobs 语义（ADR-0037 Win 2 + ADR-0089：GDAL_CACHEMAX 上限 + 单线程 warp）
    见 canonical 模块 docstring。
    """
    from app.lib.geo_raster.env import rasterio_env as _canonical

    return _canonical()


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

# Windowed processing: windows are derived from the raster processing memory
# budget (RASTER_PROCESSING_MEMORY_MB, see raster_grid.window_side_from_budget)
# — memory is O(window) even when the source file has a single giant block —
# instead of O(full raster) with several full-size temporaries.
_WINDOW_SIZE = 512  # legacy constant: budget 推导的缺省在 256MB 下与 2048 重合；
# 保留只为 reclassify 的窗口语义兼容，计算器/指数/变化检测已走预算推导窗口。



# ─── Shared helpers ──────────────────────────────────────────────


def _gtiff_profile(src_profile: dict, nodata: Optional[float] = None, count: int = 1) -> dict:
    """Build a compressed, tiled GTiff write profile from a source raster profile.

    P7：委托共享 ``build_output_profile``（driver/tiled/compression/count/
    nodata 统一设置一次），源 profile 只贡献网格字段（crs/transform/宽高）
    与缺省 nodata（未显式覆盖时继承 —— resample 等保持源 nodata 声明）。
    """
    effective_nodata = nodata if nodata is not None else src_profile.get("nodata")
    profile = build_output_profile(
        width=int(src_profile.get("width", 0)),
        height=int(src_profile.get("height", 0)),
        count=count,
        dtype=str(src_profile.get("dtype") or "float32"),
        crs=src_profile.get("crs"),
        transform=src_profile.get("transform"),
        nodata=effective_nodata,
    )
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
    resampling: Optional[str] = None,
) -> dict:
    """Pixel-wise raster math.

    Args:
        raster_a: Primary raster path (the REFERENCE grid — B is aligned to it).
        raster_b: Optional secondary raster path. If None, `constant` is used.
        expression: Numexpr-compatible expression using A (raster_a) and B (raster_b).
            Examples: "A + B", "A * 2", "(A - B) / (A + B)", "where(A > 0, A, 0)".
        constant: Scalar value used when raster_b is None.
        nodata: Output nodata value.
        resampling: B→A 对齐重采样方法。缺省 bilinear（连续量）；输入是
            分类栅格（土地覆盖等）时必须传 "nearest"——bilinear 会混合出
            不存在的类别（§10）。

    Returns:
        dict with output_path, stats, alignment decision, quality evidence,
        descriptor and content_fingerprint.

    Runtime V3 (ADR-0089): execution is ``alignment decision → windowed read
    A → windowed aligned read B (WarpedVRT, never a whole-raster in-RAM
    reproject) → nodata mask → expression → windowed atomic write``. A is the
    reference grid by contract; a B with no footprint overlap raises
    ``RasterAlignmentError`` instead of producing an empty garbage raster.
    """
    import numexpr as ne

    out_path = _suffix_output_path(raster_a, "_calc.tif")

    with rasterio.open(raster_a) as src_a:
        grid_a = RasterGridProfile.from_dataset(src_a, raster_a)
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

        window_side = window_side_from_budget()

        # B 输入准备（P3/P6）：对齐裁决先行。aligned → 原文件窗口读；
        # needs_resample/needs_reproject → WarpedVRT 虚拟对齐（窗口读即对齐
        # 读，零整幅重投影、零临时栅格）；incompatible → 结构化拒绝。
        decision = None
        b_ctx = None
        src_b_reader = None
        eff_nodata_b: Optional[float] = None
        if raster_b:
            src_b_raw = rasterio.open(raster_b)
            try:
                grid_b = RasterGridProfile.from_dataset(src_b_raw, raster_b)
                if resampling is not None and resampling not in RESAMPLING_MAP:
                    raise ValueError(
                        f"Unsupported resampling method: '{resampling}'. "
                        f"Valid options: {list(RESAMPLING_MAP.keys())}"
                    )
                decision = decide_alignment(grid_a, grid_b, resampling=resampling)
                if decision.incompatible:
                    raise RasterAlignmentError(
                        f"raster B cannot be aligned to A: {decision.reason}",
                        decision,
                    )
                # Guard raster B's own footprint before any warped reads: a
                # huge B paired with a small A must still fit the resource
                # budget (R-F04).
                RasterResourceGuard.check_grid(
                    width=src_b_raw.width,
                    height=src_b_raw.height,
                    bytes_per_pixel=np.dtype(src_b_raw.dtypes[0]).itemsize,
                    num_bands=1,
                    input_pixels=grid_b.width * grid_b.height,
                    bounds=src_b_raw.bounds,
                )
            finally:
                src_b_raw.close()
            b_cm = aligned_reader(raster_b, decision)
            src_b_reader, eff_nodata_b = b_cm.__enter__()
            b_ctx = b_cm  # 仅在成功进入后才交给 finally 清理
        else:
            const_val = constant if constant is not None else 0

        try:
            def _get_b_window(win: Window, data_a_win: np.ndarray) -> np.ndarray:
                if src_b_reader is not None:
                    return src_b_reader.read(1, window=win)
                return np.full_like(data_a_win, fill_value=const_val, dtype=data_a_win.dtype)

            def _mask_b_window(data_b_win: np.ndarray) -> np.ndarray:
                if src_b_reader is not None:
                    nd = eff_nodata_b if eff_nodata_b is not None else src_b_reader.nodata
                    if nd is not None:
                        return _nodata_valid_mask(data_b_win, nd)
                    return np.ones(data_b_win.shape, dtype=bool)
                return np.ones(data_b_win.shape, dtype=bool)

            def _compute_window(data_a_win: np.ndarray, data_b_win: np.ndarray, mask_b: np.ndarray) -> np.ndarray:
                mask_a = _nodata_valid_mask(data_a_win, nodata_a)
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

            # 窗口化：预算推导的窗口网格，内存 O(window)。首个窗口先算
            # dtype，再建 profile 写文件。统计/证据/摘要由 writer 在写循环
            # 内顺路累计（§36：零二次扫描）。
            windows = list(
                iter_bounded_windows(
                    src_a.width, src_a.height, window_side=window_side, src=src_a
                )
            )

            first_win = windows[0]
            data_a0 = src_a.read(1, window=first_win)
            b0 = _get_b_window(first_win, data_a0)
            result0 = _compute_window(data_a0, b0, _mask_b_window(b0))

            profile = build_output_profile(
                width=src_a.width,
                height=src_a.height,
                count=1,
                dtype=result0.dtype,
                crs=src_a.crs,
                transform=src_a.transform,
                nodata=out_nodata,
            )

            with WindowedRasterWriter(
                out_path, profile=profile, grid=grid_a, window_side=window_side,
            ) as writer:
                writer.write(first_win, result0)
                for win in windows[1:]:
                    checkpoint()
                    data_a_win = src_a.read(1, window=win)
                    b_win = _get_b_window(win, data_a_win)
                    res = _compute_window(data_a_win, b_win, _mask_b_window(b_win))
                    writer.write(win, res)
            finalized = writer.finalize()
        finally:
            if b_ctx is not None:
                b_ctx.__exit__(None, None, None)

    # 质量证据（§35，有界）：对齐事实 + 输入/输出网格 + 有效/nodata 计数。
    evidence: dict = {
        "algorithm": "raster_calculator",
        "parameters": {"expression": expression[:64], "constant": constant},
        "input_width": grid_a.width,
        "input_height": grid_a.height,
        "input_crs": grid_a.crs,
        "output_width": grid_a.width,
        "output_height": grid_a.height,
        "output_crs": grid_a.crs,
        "valid_pixel_count": finalized["stats"]["valid_pixel_count"],
        "nodata_pixel_count": finalized["stats"]["nodata_pixel_count"],
    }
    if decision is not None:
        evidence["alignment"] = decision.to_dict()
        if decision.other_bounds and decision.reference_bounds:
            ob, rb = decision.other_bounds, decision.reference_bounds
            evidence["cropped"] = bool(
                ob[0] < rb[0] or ob[1] < rb[1] or ob[2] > rb[2] or ob[3] > rb[3]
            )

    stats_dict = {
        "output_path": out_path,
        "expression": expression,
        "min": finalized["stats"]["min"] or 0.0,
        "max": finalized["stats"]["max"] or 0.0,
        "mean": finalized["stats"]["mean"] or 0.0,
        "pixel_count": finalized["stats"]["valid_pixel_count"] or 0,
        "descriptor": finalized["descriptor"].to_dict(),
        "content_fingerprint": finalized["content_fingerprint"],
        "quality_evidence": evidence,
    }
    if decision is not None:
        stats_dict["alignment"] = decision.to_dict()
    return stats_dict


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

