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


def overview_ladder(width: int, height: int) -> list[int]:
    """The canonical overview factor ladder: 2× steps while
    ``short_side // f >= 256`` (round-1 review MINOR — small rasters
    legitimately have NO overviews; the ladder is empty for them).

    Single definition shared by the writer (:func:`write_cog`) and the
    validator (:func:`validate_cog`) so a converter output never fails its
    own structural check just for being small.
    """
    dim = max(1, min(int(width), int(height)))
    factors: list[int] = []
    f = 2
    while dim // f >= 256:
        factors.append(f)
        f *= 2
    return factors


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
    (the :func:`overview_ladder` 2× ladder by default — empty for small
    sources). Raises :class:`CogWriteError` on unreadable sources or a
    failed write — never leaves a truncated file behind.
    """
    import rasterio
    from rasterio.shutil import copy as rio_copy

    from app.lib.geo_raster.env import rasterio_env

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
                overviews = overview_ladder(src.width, src.height)
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
    """Structural COG validation report (ok / missing items).

    Overview/tile requirements follow the same :func:`overview_ladder` rule
    the writer uses (round-1 review MINOR): a raster whose short side cannot
    yield a >=256px second level — or whose COG layout is a single
    full-coverage block (GDAL's own choice for images ≤ blocksize, where an
    overview can never be cheaper than the one-block read) — legitimately
    has no overviews and is NOT flagged ``no_overviews``/``not_tiled``.
    """
    import rasterio

    report: dict[str, Any] = {"uri": uri, "ok": False, "issues": []}
    try:
        with rasterio.open(uri) as ds:
            report["driver"] = ds.driver
            ovs = ds.overviews(1) if ds.count else []
            report["overviews"] = list(ovs)
            block = ds.block_shapes[0] if ds.block_shapes else None
            full_coverage_block = bool(
                block) and block[0] >= ds.height and block[1] >= ds.width
            if not getattr(ds, "is_tiled", False) and not full_coverage_block:
                report["issues"].append("not_tiled")
            if not ovs and overview_ladder(ds.width, ds.height) \
                    and not full_coverage_block:
                report["issues"].append("no_overviews")
            report["block_shape"] = list(block) if block else []
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


# ── Ingest seam (Wave 6, audit 05 §7.4): the writer/validator above had
#    ZERO production callers. These two entry points give upload/ingest and
#    tool surfaces an honest opt-in conversion path. Honest caller note:
#    app/services/data_parser.parse_raster and app/services/upload.py are
#    NOT wired here (other waves own them) — today the callers are the
#    ``convert_raster_to_cog`` tool (app/tools/raster_tools_cog.py) and any
#    service that opts in explicitly. Nothing converts silently.

def to_cog(
    src_path: str | Path,
    dst_dir: str | Path,
    *,
    compress: str = "DEFLATE",
    blocksize: int = 512,
) -> Path:
    """Convert any readable raster to a validated COG under ``dst_dir``.

    Always writes a NEW file (``<dst_dir>/<stem>.tif`` — an existing file
    is replaced atomically by ``write_cog``), then structurally validates
    the RESULT: a conversion that does not yield tiled + overviewed output
    raises :class:`CogWriteError` instead of pretending success. Missing
    source → :class:`CogWriteError` (typed, tool-facing).
    """
    src = Path(src_path)
    if not src.is_file():
        raise CogWriteError(f"source raster not found: {src}")
    out_dir = Path(dst_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{src.stem}.tif"
    write_cog(str(src), out, compress=compress, blocksize=blocksize)
    report = validate_cog(str(out))
    if not report.get("ok"):
        raise CogWriteError(
            f"COG conversion of {src} produced a non-conforming file "
            f"({out}): issues={report.get('issues')}"
        )
    return out


def ensure_cog(path: str | Path, out_dir: str | Path) -> Path:
    """Idempotent ingest utility: return the path if it is ALREADY a
    structurally valid COG, otherwise convert it via :func:`to_cog`.

    The structural check is the same advisory one the reader uses (tiled +
    overviews). Missing file → :class:`CogWriteError`. No conversion is
    attempted in place — the original file is never mutated.
    """
    p = Path(path)
    if not p.is_file():
        raise CogWriteError(f"raster not found: {p}")
    if validate_cog(str(p)).get("ok"):
        return p
    return to_cog(p, out_dir)
