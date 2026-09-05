"""Shared GDAL environment — a RUNTIME property, not a reader detail.

``rasterio_env()`` is the canonical home of the GDAL knob set (moved here
from ``app.lib.geo_analysis.raster_math``, which now delegates). Every path
that opens a raster — RasterReader, tile streaming, temporal engines, COG
write, STAC assets — must HOLD this env for the lifetime of the open
dataset. Setting knobs only at ``open()`` (or only in one reader class)
leaves every other open path running in a default env: GDAL block cache
back to the ~5%-of-RAM default, no HTTP timeout, unbounded warp threads.
That is why this module exists standalone: the env is a property of the
runtime any raster open happens in, not an implementation detail of one
reader (Runtime V5 convergence, audit tension #2).

Knobs (ADR-0037 Win 2 + ADR-0089 resource governance):

* ``GDAL_DISABLE_READDIR_ON_OPEN=TRUE`` — no scanning of adjacent files.
* ``GDAL_HTTP_TIMEOUT=5`` / ``GDAL_HTTP_MAX_RETRY=0`` — a hanging remote
  source fails fast instead of blocking the worker.
* ``GDAL_CACHEMAX`` — block cache capped by RASTER_GDAL_CACHE_MAX_MB
  (default 64 MB).
* ``GDAL_NUM_THREADS=1`` — raster windows are processed sequentially by
  design (§42: no unbounded parallel windows); extra GDAL threads only
  amplify peak memory.
"""
from contextlib import contextmanager


@contextmanager
def rasterio_env():
    """Shared GDAL env for all raster reads/writes (see module docstring)."""
    try:
        from app.core.config import settings

        cache_max_mb = settings.RASTER_GDAL_CACHE_MAX_MB
    except Exception:  # noqa: BLE001 — 配置缺席按保守默认
        cache_max_mb = 64
    import rasterio

    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="TRUE",
        GDAL_HTTP_TIMEOUT=5,
        GDAL_HTTP_MAX_RETRY=0,
        GDAL_CACHEMAX=int(cache_max_mb),
        GDAL_NUM_THREADS=1,
    ):
        yield
