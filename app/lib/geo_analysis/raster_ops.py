from rasterstats import zonal_stats

def zonal_statistics(polygons_geojson, raster_path, stats=['mean', 'sum', 'max', 'min']):
    """
    Compute zonal statistics for polygons against a raster.
    """
    return zonal_stats(polygons_geojson, raster_path, stats=stats)
