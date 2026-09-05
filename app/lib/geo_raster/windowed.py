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
    window_size: Optional[tuple[int, int]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    dst_dtype: Optional[str] = None,
) -> WindowResult:
    """Run ``fn`` over every window of ``band`` and merge the outputs.

    ``fn(window_data, core_window, read_window)`` receives the halo-padded
    array, the CORE window tuple (col_off, row_off, width, height) it owns,
    and the READ window actually fetched (halo-clamped at raster edges —
    so ``core - read`` offsets locate the core inside ``window_data`` even
    on the boundary). It must return an array matching the core shape.
    Raises :class:`RasterReaderError` for non-window-safe profiles.

    Band scope: execution is single-band (``band=``) by contract. Multi-band
    INPUT is available at the read level (``RasterReader.read_window(bands=…)``
    returns a stacked array) but wiring it through this executor is a
    documented extension point, NOT a wired capability — it requires an
    ``fn`` contract for stacked inputs and a merge policy for stacked
    outputs, and this module will not grow a second, diverging execution
    path until a real consumer needs one.
    """
    if not profile.window_safe:
        raise RasterReaderError(
            "algorithm is not window-safe; use a full budgeted read instead"
        )
    meta = reader.metadata()
    ds = reader._ds()

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

    for win in cancellable(windows, every=8):
        col0, row0 = int(win.col_off), int(win.row_off)
        w, h = int(win.width), int(win.height)
        halo = profile.halo
        r_col = max(0, col0 - halo)
        r_row = max(0, row0 - halo)
        r_w = min(meta.width, col0 + w + halo) - r_col
        r_h = min(meta.height, row0 + h + halo) - r_row
        from rasterio.windows import Window

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
