"""Shared NumPy vectorization helpers for the geo_analysis package."""
import logging

import numpy as np

logger = logging.getLogger(__name__)


def extract_centroids(gdf) -> np.ndarray:
    """Extract the (x, y) centroid of every geometry as an (n, 2) float array.

    Vectorized replacement for the legacy per-geometry comprehension
    ``np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])``:
    one C-level GeoSeries centroid pass instead of n Python attribute calls.
    Results are identical — for Point geometries the centroid is the point
    itself, so ``centroid.x`` == ``g.x``.

    Contract: ``gdf`` must be in a *projected* (metric) CRS. Centroids of a
    geographic CRS are mathematically defined but meaningless for any metric
    use (distance/area/interpolation). All current callers feed the output of
    ``to_utm_gdf``; this helper warns if handed a geographic CRS so a future
    caller does not silently get degree-space coordinates.
    """
    try:
        if gdf.crs is not None and not gdf.crs.is_projected:
            logger.warning(
                "extract_centroids: gdf CRS %s is geographic; centroids are in "
                "degree space, not metric. Project first for distance/area work.",
                gdf.crs,
            )
    except Exception:  # crs access must never break centroid extraction
        pass
    return np.column_stack((gdf.geometry.centroid.x.values, gdf.geometry.centroid.y.values))
