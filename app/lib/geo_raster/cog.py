"""COG readiness — writer, validator, and range-read probe.

Everything delegates to rasterio/GDAL (no TIFF format is reimplemented
here):

* :func:`write_cog` — copy any readable raster to a Cloud-Optimized GTiff:
  tiled + INTERNAL block order with overviews, DEFLATE compression, nodata
  preserved. Uses GDAL's ``COG`` driver when available and falls back to
  the classic GTiff + build_overviews recipe.
* :func:`validate_cog` — structural validation (tiled, overviews present,
  readable band/block layout) returning a report dict; ``ok=False`` items
  name exactly what is missing.
* :func:`range_read_probe` — the practical COG test: read ONE small window
  (a real HTTP-style range read through GDAL) and confirm it returns the
  requested shape without reading the file wholesale (wall-clock bounded,
  advisory).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CogWriteError(ValueError):
    """Structured COG write/reject error."""


def write_cog(
    source_uri: str,
    out_path: str | Path,
    *,
    overviews: Optional[list[int]] = None,
    compress: str = "DEFLATE",
    blocksize: int = 512,
) -> Path:
    """Write ``source_uri`` as a COG at ``out_path`` (parent dirs created).

    Preserves dtype/bands/nodata/CRS/transform; adds internal overviews
    (2× ladder by default). Raises :class:`CogWriteError` on unreadable
    sources or a failed write — never leaves a truncated file behind.
    """
    import rasterio
    from rasterio.shutil import copy as rio_copy

    from app.lib.geo_analysis.raster_math import rasterio_env

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Per-process unique tmp name: concurrent writers to one target must not
    # collide on a fixed ".tmp" suffix (review finding #12).
    import uuid as _uuid

    tmp = out.with_suffix(f"{out.suffix}.{_uuid.uuid4().hex[:8]}.tmp")
    try:
      with rasterio_env():
        with rasterio.open(source_uri) as src:
            if overviews is None:
                overviews = []
                dim = max(src.width, src.height)
                f = 2
                while dim // f >= 256:
                    overviews.append(f)
                    f *= 2
            try:
                # GDAL ≥3.1 COG driver: overviews + tiling in one pass.
                cog_profile = {
                    "driver": "COG", "compress": compress,
                    "blocksize": blocksize,
                    "overview_resampling": "nearest",
                }
                rio_copy(src, tmp.as_posix(), **cog_profile)
            except Exception:
                # Classic recipe: tiled GTiff + explicit overviews.
                gtiff_profile = src.profile.copy()
                gtiff_profile.update(
                    driver="GTiff", tiled=True, blockxsize=blocksize,
                    blockysize=blocksize, compress=compress,
                )
                rio_copy(src, tmp.as_posix(), **gtiff_profile)
                if overviews:
                    with rasterio.open(tmp.as_posix(), "r+") as ds:
                        ds.build_overviews(
                            overviews, rasterio.enums.Resampling.nearest
                        )
        tmp.replace(out)
        return out
    except Exception as e:  # noqa: BLE001 — structured + cleanup
        tmp.unlink(missing_ok=True)
        raise CogWriteError(f"COG write failed for {source_uri!r}: {e}") from e


def validate_cog(uri: str) -> dict[str, Any]:
    """Structural COG validation report (ok / missing items)."""
    import rasterio

    report: dict[str, Any] = {"uri": uri, "ok": False, "issues": []}
    try:
        with rasterio.open(uri) as ds:
            report["driver"] = ds.driver
            if not getattr(ds, "is_tiled", False):
                report["issues"].append("not_tiled")
            ovs = ds.overviews(1) if ds.count else []
            report["overviews"] = list(ovs)
            if not ovs:
                report["issues"].append("no_overviews")
            report["block_shape"] = list(ds.block_shapes[0]) if ds.block_shapes else []
            report["compressor"] = (ds.profile or {}).get("compress") or "none"
            report["size"] = [ds.width, ds.height]
            report["bands"] = ds.count
    except Exception as e:  # noqa: BLE001
        report["issues"].append(f"unopenable:{e}")
        return report
    report["ok"] = not report["issues"]
    return report


def range_read_probe(
    uri: str, *, window: tuple[int, int, int, int] = (0, 0, 64, 64)
) -> dict[str, Any]:
    """Read one small window through GDAL — the practical COG range-read
    test (a COG answers this from the first block without scanning the
    file). Advisory: returns shape/elapsed; failures return ok=False."""
    import rasterio
    from rasterio.windows import Window

    col, row, w, h = window
    t0 = time.monotonic()
    try:
        with rasterio.open(uri) as ds:
            arr = ds.read(1, window=Window(col, row, w, h))
        return {
            "ok": arr.shape == (h, w),
            "shape": list(arr.shape),
            "elapsed_ms": round((time.monotonic() - t0) * 1000, 2),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
