"""RasterChunkDescriptor — serializable chunk identity for the window runtime
(Wave 6, audit 05 §7.1).

Windows used to exist only as transient ``rasterio.windows.Window`` generator
values: nothing a scheduler, a cache, or a progress hook could name. This
module adds the missing identity layer **without** a second window runtime
(``iter_bounded_windows`` stays the single partition authority — V4's red
line):

* :class:`RasterChunkDescriptor` — a serializable window + grid identity
  (``chunk_id`` = sha256 over the canonical identity JSON, truncated 16 hex;
  ``source_fingerprint`` = the V5 bounded content fingerprint
  ``raster_content_fingerprint_v5`` — reused, not a 4th fingerprint scheme).
* :func:`iter_chunk_descriptors` — descriptor-yielding variant of the
  bounded-window iteration (same ``window_side_from_budget`` + native-block
  preference; zero behavior change for existing bare-``Window`` callers).
* :func:`chunk_digest` — per-chunk content digest: sha256 seeded with the
  grid identity (the same seed the V3 writer uses,
  ``raster_windowed.py`` §32) over the chunk's ACTUAL bytes — the audit's
  requirement for a cache-sound chunk fingerprint (the corner-block scheme
  is documented-blind to mid-raster edits and must NOT key caches).
* :class:`ChunkCacheBackend` — opt-in per-chunk array cache on the
  ``artifact_cache`` chassis (see ``app/lib/artifact_cache.get_chunk`` /
  ``publish_chunk``); wired into ``execute_windowed`` as ``chunk_cache=…``.
* :func:`raster_runtime_capabilities` — honest capability disclosure for
  tools (``{"cog", "zarr", "chunk_cache"}``).

The serial red line is untouched: windows are still processed strictly
sequentially (``GDAL_NUM_THREADS=1``, ``env.py``); descriptors name units of
work, they do not parallelize them.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any, Iterator, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: (col_off, row_off, width, height) — the tuple form used across the runtime.
WindowTuple = Tuple[int, int, int, int]

#: Digest length for chunk ids (16 hex = repo-wide key convention).
_CHUNK_ID_HEX = 16


def _grid_identity_dict(grid: Any) -> dict:
    """Bounded grid identity projection (header-only fields; no pixels)."""
    if hasattr(grid, "to_dict"):
        raw = grid.to_dict()
    else:  # already a projection dict
        raw = dict(grid)
    return {
        "width": int(raw.get("width", 0)),
        "height": int(raw.get("height", 0)),
        "crs": raw.get("crs"),
        "transform": [float(v) for v in (raw.get("transform") or ())][:6],
        "dtype": raw.get("dtype") or "",
        "nodata": raw.get("nodata"),
        "band_count": int(raw.get("band_count", 1)),
    }


def make_chunk_id(identity: dict) -> str:
    """Deterministic chunk id: sha256(canonical identity JSON)[:16]."""
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:_CHUNK_ID_HEX]


@dataclass(frozen=True)
class RasterChunkDescriptor:
    """One unit of windowed work — serializable, cache-keyable, hashable.

    ``source_fingerprint`` is the V5 bounded content fingerprint of the
    SOURCE (identity, not a cache-invalidation key — that role belongs to
    the mtime+size identity in ``artifact_cache.make_chunk_cache_key`` and
    to :func:`chunk_digest` for chunk bytes).
    """

    chunk_id: str
    source_fingerprint: str
    #: :class:`RasterGridProfile` (alignment authority) — projected via to_dict.
    grid: Any
    window: WindowTuple
    dtype: str
    byte_size: int
    band_indexes: Tuple[int, ...]
    source_uri: str = ""

    # ── projections ──────────────────────────────────────────────────
    @property
    def window_dict(self) -> dict:
        col_off, row_off, width, height = self.window
        return {
            "col_off": int(col_off),
            "row_off": int(row_off),
            "width": int(width),
            "height": int(height),
        }

    def _identity_dict(self) -> dict:
        return {
            "source_fingerprint": self.source_fingerprint,
            "grid": _grid_identity_dict(self.grid),
            "window": [int(v) for v in self.window],
            "dtype": self.dtype,
            "band_indexes": [int(b) for b in self.band_indexes],
        }

    def canonical_json(self) -> str:
        """Canonical JSON of the identity (cache-key derivation input)."""
        return json.dumps(
            self._identity_dict(),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def to_dict(self) -> dict:
        """Schema-friendly projection (grid as its dict projection)."""
        grid = (
            self.grid.to_dict()
            if hasattr(self.grid, "to_dict")
            else dict(self.grid)
        )
        return {
            "chunk_id": self.chunk_id,
            "source_fingerprint": self.source_fingerprint,
            "grid": grid,
            "window": self.window_dict,
            "dtype": self.dtype,
            "byte_size": int(self.byte_size),
            "band_indexes": [int(b) for b in self.band_indexes],
            "source_uri": self.source_uri,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RasterChunkDescriptor":
        """Roundtrip reconstruction. The grid comes back as its bounded dict
        projection (a :class:`RasterGridProfile` is recoverable from it —
        same fields — but the descriptor never re-derives grid truth)."""
        from app.lib.geo_analysis.raster_grid import RasterGridProfile

        g = data["grid"]
        if isinstance(g, RasterGridProfile):
            grid: Any = g
        else:
            grid = RasterGridProfile(
                width=int(g["width"]),
                height=int(g["height"]),
                crs=g.get("crs"),
                transform=tuple(float(v) for v in (g.get("transform") or ()))[:6],
                dtype=g.get("dtype") or "",
                nodata=g.get("nodata"),
                band_count=int(g.get("band_count", 1)),
                bounds=(
                    tuple(float(v) for v in g["bounds"])
                    if g.get("bounds") else None
                ),
            )
        win = data["window"]
        if isinstance(win, dict):
            window: WindowTuple = (
                int(win["col_off"]), int(win["row_off"]),
                int(win["width"]), int(win["height"]),
            )
        else:
            window = tuple(int(v) for v in win)[:4]  # type: ignore[assignment]
        return cls(
            chunk_id=str(data["chunk_id"]),
            source_fingerprint=str(data.get("source_fingerprint", "")),
            grid=grid,
            window=window,
            dtype=str(data.get("dtype", "")),
            byte_size=int(data.get("byte_size", 0)),
            band_indexes=tuple(int(b) for b in data.get("band_indexes", (1,))),
            source_uri=str(data.get("source_uri", "")),
        )


def build_chunk_descriptor_from_grid(
    grid: Any,
    window: WindowTuple,
    *,
    dtype: str,
    band_indexes: Sequence[int] = (1,),
    source_fingerprint: str = "",
    source_uri: str = "",
    identity_extra: str = "",
) -> RasterChunkDescriptor:
    """Assemble a descriptor from a grid profile + window (no reader needed).

    ``identity_extra`` participates in the chunk id only (not the dict body)
    so two writers on the same grid can never mint colliding ids.
    """
    col_off, row_off, width, height = (int(v) for v in window)
    bands = tuple(int(b) for b in band_indexes) or (1,)
    itemsize = max(1, np.dtype(dtype or "uint8").itemsize)
    byte_size = width * height * itemsize * len(bands)
    identity = {
        "source_fingerprint": source_fingerprint,
        "grid": _grid_identity_dict(grid),
        "window": [col_off, row_off, width, height],
        "dtype": str(dtype),
        "band_indexes": list(bands),
        "extra": identity_extra,
    }
    return RasterChunkDescriptor(
        chunk_id=make_chunk_id(identity),
        source_fingerprint=source_fingerprint,
        grid=grid,
        window=(col_off, row_off, width, height),
        dtype=str(dtype),
        byte_size=byte_size,
        band_indexes=bands,
        source_uri=source_uri,
    )


def build_chunk_descriptor(
    reader: Any,
    window: WindowTuple,
    *,
    band: int = 1,
    bands: Optional[Sequence[int]] = None,
    identity_extra: str = "",
) -> RasterChunkDescriptor:
    """Descriptor for one window of a reader's source (header-only IO).

    The source fingerprint is the reader's cached V5 content fingerprint
    (``RasterMetadata.fingerprint[:16]`` — computed once per metadata
    resolution, never re-read here). ``identity_extra`` participates in the
    chunk id only (see :func:`build_chunk_descriptor_from_grid`).
    """
    meta = reader.metadata()
    grid = getattr(meta, "grid_profile", None)
    if grid is None:
        from app.lib.geo_analysis.raster_grid import RasterGridProfile

        grid = RasterGridProfile.from_dataset(
            reader._ds(), source_path=reader.uri
        )
    band_indexes: Tuple[int, ...] = (
        tuple(int(b) for b in bands) if bands else (int(band),)
    )
    return build_chunk_descriptor_from_grid(
        grid,
        window,
        dtype=meta.dtype,
        band_indexes=band_indexes,
        source_fingerprint=(meta.fingerprint or "")[:16],
        source_uri=getattr(reader, "uri", ""),
        identity_extra=identity_extra,
    )


def fn_fingerprint(fn: Any) -> str:
    """Bounded, process-stable function identity (round-1 review MINOR).

    ``sha256(qualname + code object bytes)[:16]`` — same algorithm on the
    same source → same digest in every process (no memory addresses, no
    pickled closures). Callables without a ``__code__`` (functools.partial,
    C builtins) degrade to their qualname digest. This feeds the chunk
    CACHE operation namespace (``ChunkCacheBackend.operation``) so editing
    a window fn invalidates cached chunks — it is NOT part of
    :class:`RasterChunkDescriptor` (descriptor identity stays
    data-defined).

    round-2 review MINOR: ``inspect.unwrap`` first — ``functools.wraps``
    decorators otherwise poison the digest with the WRAPPER's bytecode
    while keeping the wrapped fn's qualname (same algorithm, different
    digest per decorating module ⇒ cache keys that never reproduce across
    call sites). Residual blindness (documented, accepted): the digest
    still cannot see closure cell contents or mutated defaults — editing
    only those does not invalidate cached chunks.
    """
    fn = inspect.unwrap(fn)
    qualname = str(
        getattr(fn, "__qualname__", None) or getattr(fn, "__name__", "") or ""
    )
    h = hashlib.sha256()
    h.update(qualname.encode("utf-8", "replace"))
    code = getattr(fn, "__code__", None)
    if code is not None:
        h.update(bytes(getattr(code, "co_code", b"") or b""))
    return h.hexdigest()[:_CHUNK_ID_HEX]


def iter_chunk_descriptors(
    reader: Any,
    *,
    band: int = 1,
    bands: Optional[Sequence[int]] = None,
    window_size: Optional[Tuple[int, int]] = None,
    window_side: Optional[int] = None,
    identity_extra: str = "",
) -> Iterator[RasterChunkDescriptor]:
    """Descriptor-yielding variant of the bounded window partition.

    Reuses ``window_side_from_budget`` + the native-block preference of
    :func:`raster_grid.iter_bounded_windows` verbatim (that function remains
    the single partition authority — this is a projection over it, not a
    second iterator). Existing bare-``Window`` callers are untouched.
    ``identity_extra`` passes through to the chunk id (execute_windowed
    mints its descriptors with ``out_dtype=…`` — round-1 review MINOR).
    """
    from app.lib.geo_analysis.raster_grid import iter_bounded_windows

    meta = reader.metadata()
    ds = reader._ds()
    if window_side is None:
        window_side = window_size[0] if window_size else None
    for win in iter_bounded_windows(
        meta.width, meta.height, window_side=window_side, src=ds
    ):
        yield build_chunk_descriptor(
            reader,
            (int(win.col_off), int(win.row_off), int(win.width), int(win.height)),
            band=band,
            bands=bands,
            identity_extra=identity_extra,
        )


def chunk_digest(grid: Any, arr: np.ndarray) -> str:
    """Per-chunk content digest: sha256(grid identity seed + chunk bytes).

    The seed mirrors the V3 writer's streaming digest seed
    (``raster_windowed.py`` §32: same content on the same grid → same
    digest, path/mtime-independent) scoped to ONE chunk. This hashes the
    chunk's actual bytes — sound as a cache-payload validator, unlike the
    corner-block dataset fingerprint.
    """
    h = hashlib.sha256()
    h.update(
        repr(
            (
                grid.width, grid.height, grid.crs,
                tuple(round(float(v), 12) for v in grid.transform),
                grid.dtype, grid.nodata, grid.band_count,
                str(arr.dtype),
            )
        ).encode()
    )
    h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()


class ChunkCacheBackend:
    """Opt-in per-chunk array cache over the artifact-cache chassis.

    Scope honesty (audit 05 §5): chunks are stored under
    ``data/artifacts/chunks/<key>.npy`` keyed by
    ``sha256(source mtime+size identity, chunk descriptor canonical JSON,
    operation, ARTIFACT_VERSION_NS)``. This is safe ONLY for element-wise
    windowed ops — ``execute_windowed`` refuses to combine it with halo or
    global-stat profiles (window_safe alone is not enough: a halo read is
    fine content-wise but the runtime conservatively refuses anything that
    is not purely per-core-computable). Chunks persist across cancelled
    runs, which is exactly the resume story.
    """

    def __init__(self, source_path: str, operation: str) -> None:
        self.source_path = str(source_path)
        self.operation = str(operation)

    def key_for(
        self, descriptor: RasterChunkDescriptor, *, operation: Optional[str] = None
    ) -> str:
        from app.lib.artifact_cache import make_chunk_cache_key

        return make_chunk_cache_key(
            self.source_path,
            json.loads(descriptor.canonical_json()),
            # chunk_id 参与缓存命名空间：canonical_json 刻意不含
            # identity_extra（如 out_dtype），chunk_id 是含它在内的完整身份
            # —— 缓存键绝不把两个不同身份的输出混为一谈（round-1 review）。
            f"{operation if operation is not None else self.operation}"
            f"|cid:{descriptor.chunk_id}",
        )

    def load(
        self,
        key: str,
        *,
        expected_shape: Optional[Tuple[int, int]] = None,
        expected_dtype: Optional[str] = None,
    ) -> Optional[np.ndarray]:
        """Cached array for ``key`` (None = honest miss on ANY failure)."""
        from app.lib.artifact_cache import get_chunk

        path = get_chunk(key)
        if path is None:
            return None
        try:
            arr = np.load(path)
        except Exception:  # noqa: BLE001 — corrupt entry → miss, never raise
            logger.warning("[chunk_cache] unreadable chunk %s", key, exc_info=True)
            return None
        if expected_shape is not None and tuple(arr.shape) != tuple(expected_shape):
            return None
        if expected_dtype is not None and str(arr.dtype) != str(np.dtype(expected_dtype)):
            return None
        return arr

    def store(self, key: str, arr: np.ndarray) -> Optional[str]:
        """Persist one chunk array (atomic publish; best-effort — a failed
        store degrades to a cache miss on the next run, never an error)."""
        from app.lib.artifact_cache import publish_chunk

        buf = io_bytes_npy(arr)
        try:
            return publish_chunk(key, buf, source_path=self.source_path)
        except Exception:  # noqa: BLE001 — cache is additive, never fatal
            logger.warning("[chunk_cache] publish failed for %s", key, exc_info=True)
            return None


def io_bytes_npy(arr: np.ndarray) -> bytes:
    """Serialize one (bounded) chunk array to .npy bytes."""
    import io

    bio = io.BytesIO()
    np.save(bio, np.ascontiguousarray(arr), allow_pickle=False)
    return bio.getvalue()


def raster_runtime_capabilities() -> dict[str, bool]:
    """Honest capability disclosure for tools (never a fake success).

    ``cog`` — the COG writer/validator exist unconditionally (GDAL COG
    driver with a classic-GTiff fallback). ``zarr`` — real per-call import
    probe (:func:`app.lib.geo_raster.zarr.zarr_available`); zarr is NOT a
    repo dependency, so False is an expected, honest state. ``chunk_cache``
    — the chunk cache chassis is always importable (stdlib only).
    """
    from app.lib.geo_raster.zarr import zarr_available

    return {
        "cog": True,
        "zarr": zarr_available(),
        "chunk_cache": True,
    }
