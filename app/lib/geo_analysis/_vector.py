"""Shared NumPy vectorization helpers for the geo_analysis package."""
import numpy as np


def extract_centroids(gdf) -> np.ndarray:
    """Extract the (x, y) centroid of every geometry as an (n, 2) float array.

    Vectorized replacement for the legacy per-geometry comprehension
    ``np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])``:
    one C-level GeoSeries centroid pass instead of n Python attribute calls.
    Results are identical — for Point geometries the centroid is the point
    itself, so ``centroid.x`` == ``g.x``.
    """
    return np.column_stack((gdf.geometry.centroid.x.values, gdf.geometry.centroid.y.values))
