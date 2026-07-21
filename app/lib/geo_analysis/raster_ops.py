from rasterstats import zonal_stats

def zonal_statistics(
    polygons_geojson: dict | str,
    raster_path: str,
    stats: list[str] = None,
) -> list[dict]:
    """Compute zonal statistics for polygons against a raster."""
    if stats is None:
        stats = ['mean', 'sum', 'max', 'min']
    return zonal_stats(polygons_geojson, raster_path, stats=stats)
