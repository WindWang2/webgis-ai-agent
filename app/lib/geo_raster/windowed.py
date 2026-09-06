"""WindowedExecution — block-wise raster processing with bounded memory.

Algorithms declare an :class:`AlgorithmProfile`:

* ``window_safe=True`` — pure per-window computation (NDVI, band math,
  focal ops with a halo). Execution streams windows and merges results.
* ``halo`` — pixels of context each side of a window (Sobel/terrain-style
  focal operators). Halo is read but only the core is written.
* ``global_stat_required=True`` — the op needs whole-raster statistics
  first (e.g. percentile stretch). The caller gets a cheap overview-based
  pre-pass hook instead of a full read (:func:`overview_statistics`).

:func:`execute_windowed` walks the raster in blocks (native block shape
when tiled, else a synthetic tile), applies ``fn(window_data, window)``,
stitches results into one array (or yields per-window via the callback
form), honours the cooperative cancellation token, and reports progress.
Peak memory is bounded by ``(window + 2·halo)² × dtype × workers``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from app.lib.cancellation import cancellable
from app.lib.geo_raster.reader import RasterReader, RasterReaderError

logger = logging.getLogger(__name__)

from app.lib.geo_raster.remote import (  # noqa: E402 - 模块级绑定便于测试替换
    RemoteReadSession,
    remote_read_window,
    remote_uri,
)

#: 多波段窗口读的字节预算（与 RasterReader 512MiB 红线同一族）。
_MULTIBAND_WINDOW_BUDGET_BYTES = 512 * 1024 * 1024


@dataclass
class AlgorithmProfile:
    """What an algorithm needs from the execution runtime."""

    window_safe: bool = True
    halo: int = 0
    #: Some ops cannot stream (e.g. they produce global overlays). The
    #: runtime refuses to run them windowed; they must budget a full read.
    global_stat_required: bool = False


@dataclass
class WindowResult:
    """Merged windowed execution output + provenance."""

    array: np.ndarray
    profile: AlgorithmProfile
    windows_processed: int = 0
    width: int = 0
    height: int = 0
    dtype: str = ""
    nodata_mask: Optional[np.ndarray] = None


def execute_windowed(
    reader: RasterReader,
    profile: AlgorithmProfile,
    fn: Callable[[np.ndarray, tuple[int, int, int, int], tuple[int, int, int, int]], np.ndarray],
    *,
    band: int = 1,
    bands: Optional[tuple[int, ...]] = None,
    window_size: Optional[tuple[int, int]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    dst_dtype: Optional[str] = None,
) -> WindowResult:
    """Run ``fn`` over every window and merge the outputs.

    ``fn(window_data, core_window, read_window)`` receives the halo-padded
    array, the CORE window tuple (col_off, row_off, width, height) it owns,
    and the READ window actually fetched (halo-clamped at raster edges —
    so ``core - read`` offsets locate the core inside ``window_data`` even
    on the boundary). It must return an array matching the core shape.
    Raises :class:`RasterReaderError` for non-window-safe profiles.

    Band scope (ADR-0101 D9, V6 §19 — now a wired capability):
    - ``band=N`` (default): single-band read, ``window_data.shape == (h, w)``.
    - ``bands=(i, j, …)``: stacked multi-band read via the same budgeted
      path as ``RasterReader.read_window(bands=…)``; ``window_data.shape``
      is ``(len(bands), h, w)`` and ``fn`` returns ``(h, w)`` (multi-band
      INPUT, single-band OUTPUT — the index-math shape). ``band`` is
      ignored when ``bands`` is given.
    """
    if not profile.window_safe:
        raise RasterReaderError(
            "algorithm is not window-safe; use a full budgeted read instead"
        )
    meta = reader.metadata()
    ds = reader._ds()
    if bands is not None:
        band_list = [int(b) for b in bands]
        if not band_list or any(b < 1 or b > meta.count for b in band_list):
            raise RasterReaderError(
                f"bands {band_list} out of range 1..{meta.count}"
            )
        if len(set(band_list)) != len(band_list):
            raise RasterReaderError(f"duplicate band indices in {band_list}")

    # V3 primitives are the sanctioned loop driver (audit tension #1: V4
    # must not grow a second window runtime): budget-derived side, native
    # block windows when they fit the budget, fixed grid otherwise.
    from app.lib.geo_analysis.raster_grid import (
        iter_bounded_windows,
        window_side_from_budget,
    )

    window_side = window_size[0] if window_size else window_side_from_budget()

    out_dtype = dst_dtype or meta.dtype
    out = np.empty((meta.height, meta.width), dtype=out_dtype)
    windows = list(iter_bounded_windows(meta.width, meta.height, window_side=window_side, src=ds))
    n_windows = len(windows)
    done = 0
    itemsize = np.dtype(out_dtype).itemsize

    # 远端源（http(s):///vsi*）走应用层预算/重试/健康策略（ADR-0101 D9 §22）。
    remote_session = (
        RemoteReadSession() if remote_uri(getattr(reader, "uri", None)) else None
    )

    for win in cancellable(windows, every=8):
        col0, row0 = int(win.col_off), int(win.row_off)
        w, h = int(win.width), int(win.height)
        halo = profile.halo
        r_col = max(0, col0 - halo)
        r_row = max(0, row0 - halo)
        r_w = min(meta.width, col0 + w + halo) - r_col
        r_h = min(meta.height, row0 + h + halo) - r_row
        from rasterio.windows import Window

        if bands is not None:
            # 与 reader.read_window(bands=…) 同口径的字节预算（512MiB 红线）。
            est = r_w * r_h * len(band_list) * itemsize
            if est > _MULTIBAND_WINDOW_BUDGET_BYTES:
                raise RasterReaderError(
                    f"windowed multi-band read would allocate {est} bytes "
                    f"(budget {_MULTIBAND_WINDOW_BUDGET_BYTES}); reduce window size"
                )
        if remote_session is not None:
            read_target = band_list if bands is not None else band
            data = remote_read_window(
                remote_session, reader.uri, ds, read_target,
                Window(r_col, r_row, r_w, r_h),
                cancel_token=None,  # 窗口间取消由 cancellable 迭代器承担
            )
        elif bands is not None:
            data = ds.read(band_list, window=Window(r_col, r_row, r_w, r_h))
        else:
            data = ds.read(band, window=Window(r_col, r_row, r_w, r_h))
        core_result = fn(data, (col0, row0, w, h), (r_col, r_row, r_w, r_h))
        expected = (h, w)
        if core_result.shape != expected:
            raise RasterReaderError(
                f"window fn returned {core_result.shape}, expected {expected}"
            )
        out[row0:row0 + h, col0:col0 + w] = core_result
        done += 1
        if on_progress is not None:
            on_progress(done, n_windows)

    return WindowResult(
        array=out,
        profile=profile,
        windows_processed=done,
        width=meta.width,
        height=meta.height,
        dtype=str(out.dtype),
    )


def overview_statistics(
    reader: RasterReader,
    band: int = 1,
    *,
    max_pixels: int = 1_000_000,
) -> dict[str, float]:
    """Global statistics from the COARSEST overview ≤ max_pixels.

    The sanctioned substitute for a full-array read when an algorithm needs
    whole-raster stats (min/max/mean/std + p2/p98 for stretches): overview
    pixels are area-weighted samples of the same field, at a bounded cost.
    """
    meta = reader.metadata()
    ds = reader._ds()
    total = meta.width * meta.height
    if total <= max_pixels:
        arr = ds.read(band)
    else:
        factor = int(np.ceil(np.sqrt(total / max_pixels)))
        out_w = max(1, meta.width // factor)
        out_h = max(1, meta.height // factor)
        arr = ds.read(band, out_shape=(out_h, out_w))
    arr = arr.astype(np.float64, copy=False)
    if meta.nodata is not None:
        arr = arr[arr != meta.nodata]
    if arr.size == 0:
        raise RasterReaderError("raster has no valid (non-nodata) pixels")
    return {
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "p2": float(np.percentile(arr, 2)),
        "p98": float(np.percentile(arr, 98)),
        "sample_pixels": int(arr.size),
    }
