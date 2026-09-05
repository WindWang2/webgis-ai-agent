"""Unified bounded content fingerprint (Runtime V5).

One digest scheme for raster content identity, shared by the reader plane
(``RasterReader._fingerprint`` → ``RasterMetadata.fingerprint``) and any
new consumer via :func:`content_fingerprint` /
:func:`raster_content_fingerprint_v5`.

Digest composition (in order):

1. structural identity string — ``width x height x count | dtypes | crs |
   transform | nodata``;
2. sampled block bytes — band 1 corner samples, first (top-left) and last
   (bottom-right) block at minimum.

Block budget: EXACTLY two windowed reads of at most 256×64 pixels each
(~128 KiB float32 worst case). Never a whole-dataset read — a full-pixel
sha256 costs O(pixels) and is exactly what the V4/V5 runtime removes from
hot paths. Consequence (documented, not hidden): the digest is blind to
mid-raster edits between the two corner samples — it is an identity for
alignment/reuse bookkeeping, NOT a cache-invalidation key for adversarial
in-place rewrites.

Truncation: :func:`content_fingerprint` returns ``sha256[:16]`` (V5
canonical). ``RasterReader._fingerprint`` keeps its historical
``sha256[:32]`` of the SAME digest (value-compatible: the 16-char value is
a strict prefix of the 32-char one — cross-entry-point comparison via
``startswith``).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

#: Corner-sample budget per read (col × row), band 1 — documented block
#: budget; raising it raises the fingerprint's IO cost linearly.
_SAMPLE_COLS = 256
_SAMPLE_ROWS = 64


def content_digest(dataset: Any) -> str:
    """Full sha256 hexdigest over structural identity + corner block bytes.

    ``dataset`` is an OPEN rasterio dataset (header + two bounded windowed
    reads; no whole-dataset read). Unreadable pixel payload degrades to a
    structural-only digest (``|unreadable`` salt) instead of raising —
    identity is best-effort, the caller decides the policy.
    """
    h = hashlib.sha256()
    h.update(
        f"{dataset.width}x{dataset.height}x{dataset.count}|{dataset.dtypes}|{dataset.crs}|"
        f"{dataset.transform}|{dataset.nodata}".encode()
    )
    try:
        from rasterio.windows import Window

        w = min(_SAMPLE_COLS, dataset.width)
        hgt = min(_SAMPLE_ROWS, dataset.height)
        block = dataset.read(1, window=Window(0, 0, w, hgt))
        h.update(np.ascontiguousarray(block).tobytes())
        tail = dataset.read(
            1,
            window=Window(
                max(0, dataset.width - w), max(0, dataset.height - hgt), w, hgt
            ),
        )
        h.update(np.ascontiguousarray(tail).tobytes())
    except Exception:  # noqa: BLE001 — digest is best-effort
        h.update(b"|unreadable")
    return h.hexdigest()


def content_fingerprint(dataset: Any) -> str:
    """Bounded content fingerprint, ``sha256[:16]`` (V5 canonical format).

    Same digest as :func:`content_digest`; see the module docstring for
    composition, block budget, and the truncation-compatibility contract
    with ``RasterReader._fingerprint``.
    """
    return content_digest(dataset)[:16]


def raster_content_fingerprint_v5(path_or_reader: Any) -> str:
    """Convenience entry: bounded content fingerprint from a path or reader.

    Accepts a filesystem path (opened through :class:`RasterReader` so the
    canonical hardened env is held for the two sample reads, then closed)
    or an already-open :class:`RasterReader` (uses its live dataset; the
    caller owns the lifecycle). Raises :class:`RasterReaderError` for an
    unopenable path or a closed reader — never returns a fabricated value.
    """
    from app.lib.geo_raster.reader import RasterReader

    if isinstance(path_or_reader, RasterReader):
        return content_fingerprint(path_or_reader._ds())
    reader = RasterReader.open(str(Path(path_or_reader)))
    try:
        return content_fingerprint(reader._ds())
    finally:
        reader.close()
