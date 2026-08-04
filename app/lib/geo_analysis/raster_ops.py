import json
import os
from typing import Optional, Union
import rasterio
from rasterstats import zonal_stats

from app.utils.coord_transform import transform_geojson


def zonal_statistics(
    polygons_geojson: Union[dict, str],
    raster_path: str,
    stats: Optional[list[str]] = None,
) -> list[dict]:
    """Compute zonal statistics for polygons against a raster."""
    if stats is None:
        stats = ['mean', 'sum', 'max', 'min']

    with rasterio.open(raster_path) as src:
        raster_crs = src.crs

    geojson_obj: Union[dict, None] = None
    if isinstance(polygons_geojson, str):
        if os.path.exists(polygons_geojson):
            with open(polygons_geojson, "r", encoding="utf-8") as f:
                geojson_obj = json.load(f)
        elif polygons_geojson.strip().startswith("{"):
            geojson_obj = json.loads(polygons_geojson)
    elif isinstance(polygons_geojson, dict):
        geojson_obj = polygons_geojson

    if geojson_obj is not None and raster_crs is not None:
        src_crs = geojson_obj.get("crs", {}).get("properties", {}).get("name", "EPSG:4326")
        target_crs_str = str(raster_crs)
        try:
            reprojected_geojson = transform_geojson(geojson_obj, from_crs=src_crs, to_crs=target_crs_str)
            polygons_input = reprojected_geojson
        except Exception:
            polygons_input = geojson_obj
    else:
        polygons_input = polygons_geojson

    return zonal_stats(polygons_input, raster_path, stats=stats)

