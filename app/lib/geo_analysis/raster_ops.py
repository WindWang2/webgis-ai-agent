import json
import logging
import math
import os
from typing import Optional, Union
import numpy as np
import rasterio

from app.utils.coord_transform import transform_geojson

logger = logging.getLogger(__name__)


def _has_inf_coords(obj) -> bool:
    """Return True if any leaf coordinate in a GeoJSON is non-finite.

    pyproj silently returns (inf, inf) for out-of-bounds reprojections
    (e.g. EPSG:3857 metres misread as EPSG:4326 degrees) without raising.
    Those infinities survive into rasterstats and produce hole/zero stats
    that look plausible. Catch them here to fail loudly (GIS-23 / #682).
    """
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
                    if isinstance(c, float) and not math.isfinite(c):
                        return True
            else:
                for c in v:
                    if _walk(c):
                        return True
        return False

    return _walk(obj)


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
        # #708: honor both GeoJSON crs member forms — the dict form
        # ({"crs": {"properties": {"name": ...}}}) and the string shorthand
        # ("crs": "EPSG:…") that extract_declared_crs (the contract authority)
        # accepts; the old dict-only read raised AttributeError on the string
        # form.
        from app.lib.geo_processor.core import extract_declared_crs
        src_crs = extract_declared_crs(geojson_obj) or "EPSG:4326"
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

    return _windowed_zonal_stats(polygons_input, raster_path, stats=stats)


def _windowed_zonal_stats(
    polygons_geojson: dict, raster_path: str, *, stats: list[str]
) -> list[dict]:
    """Zonal statistics through the V4 raster runtime (bounded memory).

    Replaces the rasterstats library hand-off, whose internals materialize
    whole-band reads for every zone. Each zone reads only its own pixel
    window (bbox → window, clamped to the raster), masks by the polygon,
    and computes the requested statistics in numpy. Semantics preserved
    from the rasterstats contract: stats keys exactly as requested
    (min/max/mean/sum/count), nodata excluded, ``all_touched=False``-style
    cell-center membership.
    """
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds
    from shapely.geometry import shape

    from app.lib.geo_raster import RasterSource

    source = RasterSource.from_path(raster_path)
    reader = source.reader()
    try:
        meta = reader.metadata()
        if meta.crs is None:
            raise ValueError(
                f"raster {raster_path!r} has no CRS; refusing zonal stats"
            )
        # rasterstats accepted FeatureCollections, bare Features, AND raw
        # geometries — the temporal engine's _scene_aoi emits a bare
        # Feature. Normalize all three shapes (parity, not a behavior cut).
        if isinstance(polygons_geojson, dict):
            if isinstance(polygons_geojson.get("features"), list):
                feat_list = polygons_geojson["features"]
            elif polygons_geojson.get("type") == "Feature":
                feat_list = [polygons_geojson]
            elif polygons_geojson.get("geometry"):
                feat_list = [{"type": "Feature", "geometry": polygons_geojson["geometry"], "properties": {}}]
            elif polygons_geojson.get("type") in ("Polygon", "MultiPolygon"):
                feat_list = [{"type": "Feature", "geometry": polygons_geojson, "properties": {}}]
            else:
                feat_list = []
        else:
            feat_list = []
        out: list[dict] = []
        for f in feat_list:
            geom = (f or {}).get("geometry")
            if not geom:
                out.append({k: None for k in stats})
                continue
            try:
                poly = shape(geom)
            except Exception as e:  # noqa: BLE001 — per-zone honest skip
                logger.warning("zonal: unparseable zone geometry skipped: %s", e)
                out.append({k: None for k in stats})
                continue
            minx, miny, maxx, maxy = poly.bounds
            # Disjoint zones: rasterio's Window.intersection RAISES on empty
            # overlap — clamp explicitly so an off-raster zone is a per-zone
            # null row (rasterstats parity), never a whole-call crash.
            import rasterio as _rio

            win = from_bounds(minx, miny, maxx, maxy, reader.dataset.transform)
            win = win.round_offsets().round_shape()
            full = _rio.windows.Window(0, 0, meta.width, meta.height)
            if not _rio.windows.intersect([win, full]):
                out.append({k: None for k in stats})
                continue
            win = win.intersection(full)
            arr = reader.read_window(
                (int(win.col_off), int(win.row_off), int(win.width), int(win.height))
            ).astype(np.float64, copy=False)
            mask = geometry_mask(
                [geom], out_shape=arr.shape,
                transform=reader.window_transform(win), invert=True,
            )
            vals = arr[mask]
            if meta.nodata is not None:
                vals = vals[vals != meta.nodata]
            vals = vals[np.isfinite(vals)]
            row: dict = {}
            for key in stats:
                if key == "count":
                    row["count"] = int(vals.size)
                elif vals.size == 0:
                    row[key] = None
                elif key == "min":
                    row["min"] = float(vals.min())
                elif key == "max":
                    row["max"] = float(vals.max())
                elif key == "mean":
                    row["mean"] = float(vals.mean())
                elif key == "sum":
                    row["sum"] = float(vals.sum())
                elif key == "std":
                    row["std"] = float(vals.std())
                else:
                    logger.warning(
                        "zonal: unsupported stat %r returned as None "
                        "(supported: min/max/mean/sum/std/count)", key,
                    )
                    row[key] = None
            if "count" not in stats and vals.size == 0:
                row = {k: None for k in stats}
            out.append(row)
        return out
    finally:
        reader.close()

