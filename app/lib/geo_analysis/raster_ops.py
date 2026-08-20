import json
import logging
import math
import os
from typing import Optional, Union
import rasterio
from rasterstats import zonal_stats

from app.utils.coord_transform import transform_geojson

logger = logging.getLogger(__name__)


def _has_inf_coords(obj) -> bool:
    """Return True if any leaf coordinate in a GeoJSON is non-finite.

    pyproj silently returns (inf, inf) for out-of-bounds reprojections
    (e.g. EPSG:3857 metres misread as EPSG:4326 degrees) without raising.
    Those infinities survive into rasterstats and produce hole/zero stats
    that look plausible. Catch them here to fail loudly (GIS-23 / #682).
    """
    try:
        import math as _math

        def _walk(v):
            if isinstance(v, dict):
                if "coordinates" in v:
                    if _walk(v["coordinates"]):
                        return True
                if "geometries" in v:
                    for g in v["geometries"] or []:
                        if _walk(g):
                            return True
                if "features" in v:
                    for f in v["features"] or []:
                        if _walk(f):
                            return True
                if "geometry" in v and isinstance(v["geometry"], dict):
                    if _walk(v["geometry"]):
                        return True
            elif isinstance(v, (list, tuple)):
                if v and isinstance(v[0], (int, float)) and not isinstance(v[0], bool):
                    # leaf coordinate tuple
                    for c in v[:2]:
                        if isinstance(c, float) and not _math.isfinite(c):
                            return True
                else:
                    for c in v:
                        if _walk(c):
                            return True
            return False

        return _walk(obj)
    except Exception:
        return False


def zonal_statistics(
    polygons_geojson: Union[dict, str],
    raster_path: str,
    stats: Optional[list[str]] = None,
) -> list[dict]:
    """Compute zonal statistics for polygons against a raster.

    Raises ValueError when polygon reprojection to the raster CRS fails — the
    previous behavior silently fed source-CRS polygons to a projected raster,
    producing plausible-looking zero statistics (GIS-23, deep-audit round 3).
    """
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
        except Exception as e:
            # GIS-23: do NOT silently fall back to source-CRS polygons — a
            # WGS84 polygon fed to a projected raster yields all-zero stats
            # that look like real "no data". Fail loudly with both CRS strings.
            logger.warning(
                "zonal_statistics reprojection failed (%s -> %s): %s",
                src_crs, target_crs_str, e,
            )
            raise ValueError(
                f"Polygon reprojection {src_crs} -> {target_crs_str} failed: {e}. "
                "Refusing to compute zonal stats with mismatched CRS."
            ) from e
        # GIS-682: pyproj returns (inf, inf) silently for out-of-bounds
        # reprojections without raising (e.g. EPSG:3857 metres misread as
        # EPSG:4326 degrees). Those infinities reach rasterstats as
        # hole/zero stats that look correct. Fail loudly when any
        # reprojected coordinate is non-finite so the GIS-23 guard fires.
        if _has_inf_coords(polygons_input):
            raise ValueError(
                f"Polygon reprojection {src_crs} -> {target_crs_str} produced "
                "non-finite coordinates (inf/nan) — input CRS likely mismatched. "
                "Refusing to compute zonal stats with mismatched CRS."
            )
    else:
        polygons_input = polygons_geojson

    return zonal_stats(polygons_input, raster_path, stats=stats)

