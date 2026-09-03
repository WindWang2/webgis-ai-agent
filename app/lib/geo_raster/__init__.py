"""Raster Runtime V4 — unified RasterSource → RasterReader → WindowedExecution.

Every raster tool historically opened GeoTIFFs its own way: scattered CRS
assumptions, nodata folklore, full-array ``dataset.read()`` on arbitrarily
large rasters, and no shared windowed execution. This package is the single
runtime surface for raster work (ADR: reproducible professional GIS runtime,
raster axis):

* :class:`RasterSource` — a *descriptor* (no I/O at construction): where the
  raster lives (local path / session ref / project artifact / COG / remote),
  plus lazily-resolved identity (crs, bbox, shape, dtype, bands, nodata,
  transform, content fingerprint).
* :class:`RasterReader` — the only sanctioned open path. Metadata plus
  window/band/mask/overview reads; NEVER ``read()`` of the whole array
  unless the caller explicitly proves the budget.
* :func:`execute_windowed` — block-wise execution with halo, merge,
  progress, cooperative cancellation, and a memory budget. Algorithms
  declare a :class:`AlgorithmProfile` (window-safe? halo? global stat?).
* :mod:`app.lib.geo_raster.cog` — COG writer + structural validator +
  range-read readiness probe, delegating to rasterio/GDAL (no TIFF
  reimplementation).

Consumers register outputs through the existing ArtifactRegistry — this
package adds no second artifact truth.
"""
from app.lib.geo_raster.source import (
    COGRasterSource,
    LocalFileRasterSource,
    RemoteRasterSource,
    ProjectArtifactRasterSource,
    RasterSource,
    RasterSourceError,
    SessionRefRasterSource,
)
from app.lib.geo_raster.reader import RasterMetadata, RasterReader, RasterReaderError
from app.lib.geo_raster.windowed import (
    AlgorithmProfile,
    WindowResult,
    execute_windowed,
)

__all__ = [
    "RasterSource",
    "RasterSourceError",
    "RasterReaderError",
    "LocalFileRasterSource",
    "SessionRefRasterSource",
    "ProjectArtifactRasterSource",
    "COGRasterSource",
    "RemoteRasterSource",
    "RasterReader",
    "RasterMetadata",
    "AlgorithmProfile",
    "WindowResult",
    "execute_windowed",
]
