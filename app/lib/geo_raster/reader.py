"""RasterReader — the only sanctioned way raster datasets get opened.

Wraps a ``rasterio.DatasetReader`` with:

* :meth:`metadata` — typed identity (crs/bbox/shape/dtype/bands/nodata/
  transform + content fingerprint) computed once and cached.
* :meth:`read_window` / :meth:`read_band` / :meth:`read_mask` /
  :meth:`read_overview` — bounded reads. A whole-array read exists ONLY as
  :meth:`read_full` and refuses arrays larger than the memory budget
  unless the caller passes ``budget_ok=True`` explicitly (the guard the
  old ``dataset.read()`` habit never had).
* GDAL runtime knobs honour the app settings (RASTER_PROCESSING_MEMORY_MB,
  RASTER_GDAL_CACHE_MAX_MB) via rasterio's environment.

The reader never mutates the source and never guesses a CRS: an unreadable
or non-raster dataset raises :class:`RasterReaderError`.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

#: Default whole-array read ceiling (cells × dtype bytes). Full reads above
#: this raise unless the caller proves a budget (see read_full).
DEFAULT_FULL_READ_BUDGET_BYTES = 512 * 1024 * 1024  # 512 MiB


class RasterReaderError(ValueError):
    """Structured reader rejection (unopenable dataset, oversized read, …)."""


@dataclass
class RasterMetadata:
    """Typed raster identity — everything a tool or artifact record needs."""

    uri: str
    width: int
    height: int
    count: int
    dtype: str
    crs: Optional[str]
    bbox: Optional[list[float]]
    transform: Optional[list[float]]  # GDAL order: [px, rot, ox, rot, py, oy]
    nodata: Optional[float]
    overviews: int = 0
    is_tiled: bool = False
    is_cog: bool = False
    fingerprint: str = ""
    #: The V3 grid identity this metadata derives from (alignment decisions
    #: consume RasterGridProfile — never a re-derived copy).
    grid_profile: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "width": self.width,
            "height": self.height,
            "shape": [self.height, self.width],
            "bands": self.count,
            "dtype": self.dtype,
            "crs": self.crs,
            "bbox": self.bbox,
            "transform": self.transform,
            "nodata": self.nodata,
            "overviews": self.overviews,
            "is_tiled": self.is_tiled,
            "is_cog": self.is_cog,
            "fingerprint": self.fingerprint,
        }


@dataclass
class RasterReader:
    """Bounded window reader over one dataset."""

    uri: str
    _dataset: Any = field(default=None, repr=False)
    _meta: Optional[RasterMetadata] = field(default=None, repr=False)
    _env: Any = field(default=None, repr=False)

    # ── lifecycle ────────────────────────────────────────────────────
    @classmethod
    def open(cls, uri: str) -> "RasterReader":
        # Delegate to the shared V3 env (GDAL_HTTP_TIMEOUT/RETRY/READDIR +
        # GDAL_CACHEMAX from RASTER_GDAL_CACHE_MAX_MB + GDAL_NUM_THREADS=1).
        # The env is HELD for the reader lifetime: knobs at open() only would
        # leave every later read in a default env (cache back to GDAL's
        # ~5%-of-RAM default, no HTTP hardening) — review finding #3.
        from app.lib.geo_analysis.raster_math import rasterio_env

        env_cm = rasterio_env()
        env_cm.__enter__()
        try:
            import rasterio

            ds = rasterio.open(uri)
        except Exception as e:
            env_cm.__exit__(None, None, None)
            raise RasterReaderError(f"cannot open raster {uri!r}: {e}") from e
        reader = cls(uri=uri, _dataset=ds)
        reader._env = env_cm
        return reader

    @property
    def closed(self) -> bool:
        return self._dataset is None or self._dataset.closed

    def close(self) -> None:
        if self._dataset is not None and not self._dataset.closed:
            self._dataset.close()
        self._dataset = None
        if self._env is not None:
            try:
                self._env.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 — env teardown is best-effort
                pass
            self._env = None

    def __enter__(self) -> "RasterReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _ds(self) -> Any:
        if self.closed:
            raise RasterReaderError(f"reader for {self.uri!r} is closed")
        return self._dataset

    @property
    def dataset(self) -> Any:
        """The open rasterio dataset (public seam for window transforms and
        block layouts — prefer read_window for pixels)."""
        return self._ds()

    def window_transform(self, window: Any) -> Any:
        """Affine transform for a window (public — consumers must not reach
        into the dataset for it)."""
        return self._ds().window_transform(window)

    # ── identity ─────────────────────────────────────────────────────
    def metadata(self) -> RasterMetadata:
        if self._meta is not None:
            return self._meta
        ds = self._ds()
        # Grid identity derives from the V3 RasterGridProfile (header-only,
        # zero pixel IO) so V4 metadata can never drift from the sanctioned
        # grid identity used by alignment decisions (audit tension #1).
        from app.lib.geo_analysis.raster_grid import RasterGridProfile

        grid = RasterGridProfile.from_dataset(ds, source_path=self.uri)
        try:
            overviews = len(ds.overviews(1)) if ds.count else 0
        except Exception:  # noqa: BLE001
            overviews = 0
        self._meta = RasterMetadata(
            uri=self.uri,
            width=grid.width,
            height=grid.height,
            count=grid.band_count,
            dtype=grid.dtype or "unknown",
            crs=grid.crs,
            bbox=(
                [grid.bounds[0], grid.bounds[1], grid.bounds[2], grid.bounds[3]]
                if grid.bounds else None
            ),
            transform=list(grid.transform) if grid.transform else None,
            nodata=grid.nodata,
            overviews=overviews,
            is_tiled=bool(getattr(ds, "is_tiled", False)),
            is_cog=self.cog_structure_ok(ds),
            fingerprint=self._fingerprint(ds),
        )
        self._meta.grid_profile = grid
        return self._meta

    @staticmethod
    def _fingerprint(ds: Any) -> str:
        """Cheap content identity: structure + downsampled min/max digest.

        A full-pixel sha256 costs an O(pixels) read — exactly what V4
        removes from hot paths. The structural digest (shape/dtype/crs/
        transform/nodata + first/last blocks' bytes) detects rewrite with
        bounded cost, mirroring the analysis_reuse raster fingerprint
        philosophy. CAVEAT: it is deliberately blind to mid-raster edits
        (corner blocks only) — identity, NOT a cache-invalidation key.
        """
        h = hashlib.sha256()
        h.update(
            f"{ds.width}x{ds.height}x{ds.count}|{ds.dtypes}|{ds.crs}|"
            f"{ds.transform}|{ds.nodata}".encode()
        )
        try:
            from rasterio.windows import Window

            w = min(256, ds.width)
            hgt = min(64, ds.height)
            block = ds.read(1, window=Window(0, 0, w, hgt))
            h.update(np.ascontiguousarray(block).tobytes())
            tail = ds.read(
                1, window=Window(max(0, ds.width - w), max(0, ds.height - hgt), w, hgt)
            )
            h.update(np.ascontiguousarray(tail).tobytes())
        except Exception:  # noqa: BLE001 — digest is best-effort
            h.update(b"|unreadable")
        return h.hexdigest()[:32]

    @staticmethod
    def cog_structure_ok(uri_or_ds: Any) -> bool:
        """Structural COG readiness: tiled + overviews present (advisory).
        Accepts an ALREADY-OPEN dataset to avoid a second open per metadata
        resolution (open-count discipline matters to the temporal engine's
        IO budget)."""
        try:
            ds = uri_or_ds
            if isinstance(uri_or_ds, str):
                import rasterio

                with rasterio.open(uri_or_ds) as opened:
                    ds = opened
            if not getattr(ds, "is_tiled", False):
                return False
            return len(ds.overviews(1)) > 0
        except Exception:  # noqa: BLE001
            return False

    # ── bounded reads ────────────────────────────────────────────────
    def read_window(
        self,
        window: tuple[int, int, int, int],
        band: int = 1,
    ) -> np.ndarray:
        """Read ``(col_off, row_off, width, height)`` from one band at FULL
        resolution. Overview reads have their own entry (read_overview) —
        mixing overview decimation into window offsets produced broken
        bounds semantics (review finding #4), so the combination is gone."""
        from rasterio.windows import Window

        ds = self._ds()
        col, row, w, h = window
        if w <= 0 or h <= 0:
            raise RasterReaderError(f"window must be positive, got {window!r}")
        if col < 0 or row < 0 or col + w > ds.width or row + h > ds.height:
            raise RasterReaderError(
                f"window {window!r} outside raster {ds.width}x{ds.height}"
            )
        return ds.read(band, window=Window(col, row, w, h))

    def read_band(self, band: int = 1) -> np.ndarray:
        """Whole-band read via the budget guard (see read_full)."""
        ds = self._ds()
        return self._budgeted_read(lambda: ds.read(band))

    def read_mask(self, window: tuple[int, int, int, int] | None = None) -> np.ndarray:
        ds = self._ds()
        if window is None:
            return self._budgeted_read(lambda: ds.read_masks(1))
        from rasterio.windows import Window

        col, row, w, h = window
        return ds.read_masks(1, window=Window(col, row, w, h))

    def read_overview(self, band: int = 1, level: int = -1) -> np.ndarray:
        """Read a whole decimated overview level (-1 = coarsest). Raises for
        out-of-range levels instead of silently clamping."""
        ds = self._ds()
        ovs = ds.overviews(band) if ds.count else []
        if not ovs:
            raise RasterReaderError("dataset has no overviews")
        idx = len(ovs) - 1 if level == -1 else level
        if not (0 <= idx < len(ovs)):
            raise RasterReaderError(
                f"overview level {level} out of range (0..{len(ovs) - 1})"
            )
        factor = int(ovs[idx])
        out_h = max(1, ds.height // factor)
        out_w = max(1, ds.width // factor)
        return self._budgeted_read(
            lambda: ds.read(band, out_shape=(out_h, out_w))
        )

    def read_full(self, *, budget_ok: bool = False) -> np.ndarray:
        """Whole-array read — refuses above the budget unless proven."""
        ds = self._ds()
        return self._budgeted_read(lambda: ds.read(), budget_ok=budget_ok)

    def _budgeted_read(self, fn: Any, *, budget_ok: bool = False) -> np.ndarray:
        m = self.metadata()
        est = int(m.width) * int(m.height) * max(1, int(m.count)) * np.dtype(
            m.dtype
        ).itemsize if m.dtype != "unknown" else 0
        if (
            not budget_ok
            and est > DEFAULT_FULL_READ_BUDGET_BYTES
        ):
            raise RasterReaderError(
                f"full read of {self.uri!r} would take ~{est / 1e6:.0f} MB "
                f"(budget {DEFAULT_FULL_READ_BUDGET_BYTES // 1e6:.0f} MB); use "
                "read_window/execute_windowed or pass budget_ok=True"
            )
        return fn()
