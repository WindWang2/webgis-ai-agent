"""Zarr foundation (Wave 6, audit 05 §7.5) — probe-gated, honest degrade.

Zarr is NOT a repository dependency (requirements.txt / pyproject.toml carry
no zarr; ADR-0096 §non-goals recorded the deliberate deferral; the skills
policy at ``app/tools/skills.py`` requires admin review for heavy deps).
This module therefore copies the sanctioned optional-carrier pattern of
``app/services/data_fabric/vector_carrier.py`` verbatim in shape:

* lazy ``import zarr`` — probe per call (:func:`zarr_available`), import at
  use (:func:`_require_zarr`);
* :class:`ZarrUnavailable` — typed error, ``code="ZARR_UNAVAILABLE"``,
  ``correction_hint="pip install zarr"``;
* NO fake success — every entry point either really works on a real store
  or raises a typed error. Degraded (zarr-less) environments get the typed
  error from every entry, and :func:`raster_runtime_capabilities` reports
  ``zarr: False``.

Surface:

* :func:`open_zarr_array` — open a directory/array store read-only.
* :func:`zarr_chunk_descriptors` — map a store's zarr chunks to
  :class:`RasterChunkDescriptor`s. The grid comes from the array's
  ``attrs`` (``crs`` / ``transform`` / optional ``nodata`` — exactly what
  :func:`write_zarr_cube` writes); a store without georeferencing attrs
  raises :class:`ZarrGridError` instead of guessing a grid.
* :func:`write_zarr_cube` — temporal stack materialization: one zarr array
  ``(time, y, x)`` with per-time chunk descriptors, time axis in attrs.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

#: attrs keys carrying georeferencing (written by write_zarr_cube, required
#: by zarr_chunk_descriptors).
_ATTR_CRS = "crs"
_ATTR_TRANSFORM = "transform"
_ATTR_TIMES = "times"
_ATTR_NODATA = "nodata"


class ZarrUnavailable(RuntimeError):
    """zarr 导入可用才启用；不可用时一切入口抛本类型化错误（诚实降级，
    绝不假装 —— vector_carrier 同族纪律）。"""

    code = "ZARR_UNAVAILABLE"
    correction_hint = "pip install zarr"

    def __init__(self, message: str = "zarr is not installed in this environment"):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict:
        return {
            "success": False,
            "code": self.code,
            "message": self.message,
            "correction_hint": self.correction_hint,
        }


class ZarrStoreError(ValueError):
    """Store-level rejection: missing/unreadable store, shape mismatches."""

    code = "ZARR_STORE_UNREADABLE"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
        self.code = ZarrStoreError.code


class ZarrGridError(ValueError):
    """The array's attrs lack the georeferencing the grid contract needs —
    an honest typed refusal, never a guessed CRS/transform."""

    code = "ZARR_GRID_METADATA_MISSING"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
        self.code = ZarrGridError.code


def zarr_available() -> bool:
    """Honest per-call probe (module version churn → re-check, no cache)."""
    try:
        import zarr  # noqa: F401
    except Exception:  # noqa: BLE001 — any import failure = unavailable
        return False
    return True


def _require_zarr() -> Any:
    """Import-at-use; ImportError → typed ZarrUnavailable (never raw)."""
    try:
        import zarr
    except Exception as e:  # noqa: BLE001
        raise ZarrUnavailable() from e
    return zarr


def open_zarr_array(store_path: str | Path) -> Any:
    """Open a zarr array store read-only (typed errors on every failure)."""
    zarr = _require_zarr()
    p = Path(store_path)
    if not p.exists():
        raise ZarrStoreError(f"zarr store not found: {p}")
    try:
        if hasattr(zarr, "open_array"):
            return zarr.open_array(store=str(p), mode="r")
        return zarr.open(str(p), mode="r")  # zarr < 3 fallback
    except ZarrStoreError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ZarrStoreError(f"cannot open zarr store {str(p)!r}: {e}") from e


def _store_fingerprint(store_path: str | Path, arr: Any) -> str:
    """Bounded store identity for descriptors: sha256(path, shape, dtype,
    chunk geometry)[:16] — structural, zero pixel IO (a zarr store's
    identity IS its metadata; there is no corner-block to sample)."""
    h = hashlib.sha256()
    h.update(
        repr(
            (
                str(Path(store_path).resolve()),
                tuple(int(v) for v in arr.shape),
                str(arr.dtype),
                tuple(int(v) for v in arr.chunks),
            )
        ).encode()
    )
    return h.hexdigest()[:16]


def _grid_identity_dict_local(grid: Any) -> dict:
    """Bounded grid identity comparison key (widths/crs/transform/dtype)."""
    from app.lib.geo_raster.chunk import _grid_identity_dict

    raw = _grid_identity_dict(grid)
    # nodata/band_count 参与立方体一致性判定过于苛刻（时间源 nodata 声明
    # 可能不同）；网格契约的一致性 = 几何 + dtype。
    return {k: raw[k] for k in ("width", "height", "crs", "transform", "dtype")}


def _grid_from_attrs(arr: Any, what: str) -> Any:
    """RasterGridProfile from array attrs — typed refusal when absent."""
    from app.lib.geo_analysis.raster_grid import RasterGridProfile

    attrs = dict(getattr(arr, "attrs", {}) or {})
    crs = attrs.get(_ATTR_CRS)
    transform = attrs.get(_ATTR_TRANSFORM)
    if not crs or not transform:
        raise ZarrGridError(
            f"{what} attrs lack georeferencing "
            f"(need '{_ATTR_CRS}' and '{_ATTR_TRANSFORM}'; got "
            f"{sorted(attrs)}); the grid contract refuses to guess"
        )
    transform = tuple(float(v) for v in transform)[:6]
    if len(transform) != 6:
        raise ZarrGridError(
            f"{what} attr '{_ATTR_TRANSFORM}' must carry 6 affine "
            f"coefficients, got {len(transform)}"
        )
    return RasterGridProfile(
        width=int(arr.shape[-1]),
        height=int(arr.shape[-2]),
        crs=str(crs),
        transform=transform,
        dtype=str(arr.dtype),
        nodata=(
            float(attrs[_ATTR_NODATA]) if attrs.get(_ATTR_NODATA) is not None else None
        ),
        band_count=1,
    )


def zarr_chunk_descriptors(store_path: str | Path) -> List[Any]:
    """Map a store's zarr chunks to RasterChunkDescriptors.

    2D arrays map their (y, x) chunk grid directly. 3D ``(t, y, x)`` cubes
    expose time slices as ``band_indexes=(t+1,)`` on otherwise-identical
    spatial windows. ``source_fingerprint`` is the structural store
    fingerprint (see :func:`_store_fingerprint`); ``source_uri`` is empty
    (the store is the source — round-tripping into ``write_zarr_cube``
    requires an explicit ``read_chunk`` loader).
    """
    from app.lib.geo_raster.chunk import build_chunk_descriptor_from_grid

    arr = open_zarr_array(store_path)
    if arr.ndim not in (2, 3):
        raise ZarrGridError(
            f"expected a 2D array or 3D (time, y, x) cube, got ndim={arr.ndim}"
        )
    grid = _grid_from_attrs(arr, f"zarr store {store_path}")
    fingerprint = _store_fingerprint(store_path, arr)
    t_n = int(arr.shape[0]) if arr.ndim == 3 else 1
    chunk_t = int(arr.chunks[0]) if arr.ndim == 3 else 1
    chunk_y = int(arr.chunks[-2])
    chunk_x = int(arr.chunks[-1])
    height = int(arr.shape[-2])
    width = int(arr.shape[-1])

    descriptors: List[Any] = []
    for t0 in range(0, t_n, chunk_t):
        for y0 in range(0, height, chunk_y):
            for x0 in range(0, width, chunk_x):
                descriptors.append(
                    build_chunk_descriptor_from_grid(
                        grid,
                        (x0, y0, min(chunk_x, width - x0), min(chunk_y, height - y0)),
                        dtype=str(arr.dtype),
                        band_indexes=(t0 + 1,),
                        source_fingerprint=fingerprint,
                        source_uri=str(store_path),
                        identity_extra=f"zarr:t={t0}",
                    )
                )
    return descriptors


def write_zarr_cube(
    descriptors: Sequence[Sequence[Any]],
    times: Sequence[str],
    out_store: str | Path,
    *,
    read_chunk: Optional[Callable[[Any, str], Any]] = None,
    overwrite: bool = True,
) -> Path:
    """Materialize a temporal cube ``(time, y, x)`` from chunk descriptors.

    ``descriptors[t]`` is the spatial chunk list for time step ``t`` (all
    on ONE common grid — mismatches are a typed error, never a silent
    resample); ``times[t]`` is its ISO-8601 label, persisted in attrs
    together with the georeferencing so :func:`zarr_chunk_descriptors` can
    round-trip the store.

    Data source per chunk: ``read_chunk(descriptor, time)`` when given,
    else the descriptor's ``source_uri`` is opened through
    :class:`RasterReader` and its window read at
    ``band_indexes[0]`` (the sanctioned bounded read path).
    """
    from app.lib.geo_raster.reader import RasterReader

    zarr = _require_zarr()
    if len(times) != len(descriptors):
        raise ZarrStoreError(
            f"times ({len(times)}) and descriptor groups "
            f"({len(descriptors)}) must have equal length"
        )
    if not times:
        raise ZarrStoreError("refusing to write an empty cube")
    flat = [d for group in descriptors for d in group]
    if not flat:
        raise ZarrStoreError("descriptor groups are empty")

    grid = flat[0].grid
    dtype = str(flat[0].dtype)
    for d in flat:
        if d.dtype != dtype:
            raise ZarrStoreError(
                f"mixed chunk dtypes in cube: {dtype} vs {d.dtype}"
            )
        if _grid_identity_dict_local(d.grid) != _grid_identity_dict_local(grid):
            raise ZarrStoreError(
                f"chunk {d.chunk_id} is on a different grid than the cube "
                "reference grid; temporal cubes require one common grid "
                "(align sources first — no silent resample)"
            )

    height = int(grid.height)
    width = int(grid.width)
    # Uniform zarr chunk geometry from the first descriptor's window
    # (variable edge windows still write fine — chunks cap the geometry).
    w0 = descriptors[0][0]
    chunk_y = max(1, min(int(w0.window[3]), height))
    chunk_x = max(1, min(int(w0.window[2]), width))

    store_str = str(out_store)
    kwargs = dict(
        shape=(len(times), height, width),
        chunks=(1, chunk_y, chunk_x),
        dtype=dtype,
        overwrite=overwrite,
    )
    try:
        if hasattr(zarr, "create_array"):
            arr = zarr.create_array(store=store_str, **kwargs)
        else:  # zarr < 3 fallback
            arr = zarr.open_array(store=store_str, mode="w", **kwargs)
    except Exception as e:  # noqa: BLE001
        raise ZarrStoreError(f"cannot create zarr store {store_str!r}: {e}") from e

    arr.attrs[_ATTR_CRS] = grid.crs
    arr.attrs[_ATTR_TRANSFORM] = [float(v) for v in grid.transform]
    arr.attrs[_ATTR_NODATA] = grid.nodata
    arr.attrs[_ATTR_TIMES] = [str(t) for t in times]
    arr.attrs["chunk_grid"] = {
        "chunk_y": chunk_y,
        "chunk_x": chunk_x,
        "layout": "raster_chunk_descriptor_v1",
    }

    for t, time_label in enumerate(times):
        for d in descriptors[t]:
            if read_chunk is not None:
                data = read_chunk(d, str(time_label))
            else:
                if not d.source_uri:
                    raise ZarrStoreError(
                        f"chunk {d.chunk_id} has no source_uri and no "
                        "read_chunk loader was provided"
                    )
                reader = RasterReader.open(d.source_uri)
                try:
                    data = reader.read_window(d.window, band=int(d.band_indexes[0]))
                finally:
                    reader.close()
            col, row, cw, ch = (int(v) for v in d.window)
            data = np.asarray(data)
            if data.shape != (ch, cw):
                raise ZarrStoreError(
                    f"chunk {d.chunk_id} loader returned {data.shape}, "
                    f"expected {(ch, cw)}"
                )
            arr[t, row:row + ch, col:col + cw] = data
    return Path(out_store)
