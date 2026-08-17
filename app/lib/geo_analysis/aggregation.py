import logging
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box, Polygon
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_processor.core import to_utm_gdf, gdf_from_features
# ADR-0052: 协作式取消检查点。cancellable() 在 chunk 边界读一次 contextvar，
# 未绑定 token 时开销为零；用户取消后长循环立即抛 OperationCancelled 退出，
# 真正释放 CPU 而不是只改 UI 状态。
from app.services.jobs.cancellation import cancellable

logger = logging.getLogger(__name__)

def generate_fishnet(bounds: tuple[float, float, float, float], cell_size: float, type: str = 'square') -> GeoAnalysisResult:
    """Generate a square or hexagonal grid over specified bounds.

    GIS contract (V-F01): ``bounds`` are WGS84 degrees and ``cell_size`` is in
    **metres** (matching the ``fishnet_grid`` tool description). The grid is
    built in a projected metric CRS (UTM, or polar stereographic beyond 84°)
    and reprojected back to EPSG:4326, so a 500 m request yields ~500 m cells
    on the ground. Previously the metre value was applied directly in degree
    space, producing a single polygon spanning hundreds of degrees. Includes
    OOM protection (50 000-cell estimate).
    """
    if cell_size <= 0:
        return GeoAnalysisResult(
            False, None, f"cell_size must be positive (metres), got {cell_size}",
            error_type="ValueError",
        )

    xmin, ymin, xmax, ymax = bounds

    # Pick a metric CRS for the (WGS84) bounds: UTM, or polar stereographic.
    abs_max_lat = max(abs(float(ymin)), abs(float(ymax)))
    if abs_max_lat > 84.0:
        metric_crs = "EPSG:3413" if (float(ymin) + float(ymax)) / 2 >= 0 else "EPSG:3031"
    else:
        try:
            metric_crs = str(
                gpd.GeoDataFrame(geometry=[box(xmin, ymin, xmax, ymax)], crs="EPSG:4326")
                .estimate_utm_crs()
            )
        except Exception:
            clon = (float(xmin) + float(xmax)) / 2
            lon = (clon + 180.0) % 360.0 - 180.0
            zone = max(1, min(60, int((lon + 180) / 6) + 1))
            metric_crs = f"EPSG:{32600 if (float(ymin)+float(ymax))/2 >= 0 else 32700}{zone}"

    # Project the bounds to the metric CRS and build the grid there.
    bounds_m = (
        gpd.GeoDataFrame(geometry=[box(xmin, ymin, xmax, ymax)], crs="EPSG:4326")
        .to_crs(metric_crs)
    )
    mxmin, mymin, mxmax, mymax = (float(v) for v in bounds_m.total_bounds)
    m_width = mxmax - mxmin
    m_height = mymax - mymin

    estimated_cells = (m_width / cell_size) * (m_height / cell_size)
    warning = ""
    if estimated_cells > 50000:
        new_cell_size = float(np.sqrt((m_width * m_height) / 50000))
        warning = (
            f"Warning: Grid too dense ({int(estimated_cells)} cells). "
            f"Cell size adjusted from {cell_size}m to {new_cell_size:.4f}m."
        )
        cell_size = new_cell_size

    metric_polys = []
    if type == 'square':
        cols = np.arange(mxmin, mxmax, cell_size)
        rows = np.arange(mymin, mymax, cell_size)
        # Vectorized cell grid, x-major order preserved (for each x, all y).
        xs = np.repeat(cols, len(rows))
        ys = np.tile(rows, len(cols))
        metric_polys = [box(x, y, x + cell_size, y + cell_size) for x, y in zip(xs, ys)]

    elif type == 'hexagon':
        R = cell_size / np.sqrt(3)
        dx = cell_size
        dy = 1.5 * R

        cols = np.arange(mxmin - dx, mxmax + dx, dx)
        rows = np.arange(mymin - dy, mymax + dy, dy)
        nrows = len(rows)

        # GIS-P2-1: the lattice above is pointy-top (dx=√3·R flat-to-flat,
        # dy=1.5·R interleaved rows) — vertices must sit at 30°+k·60° so each
        # hexagon's width is √3·R and height 2R. The old 0°+k·60° (flat-top)
        # vertices made every cell overlap its neighbors on BOTH axes
        # (0.134·cell vertical, 0.155·cell horizontal) — not a tessellation.
        angles = np.radians([30, 90, 150, 210, 270, 330, 30])
        cos_a = np.cos(angles)
        sin_a = np.sin(angles)

        offsets = (np.arange(nrows) % 2) * (dx / 2)
        cx = cols[None, :] + offsets[:, None]
        cy = np.broadcast_to(rows[:, None], (nrows, len(cols)))

        vx = cx[:, :, None] + R * cos_a[None, None, :]
        vy = cy[:, :, None] + R * sin_a[None, None, :]
        vertices = np.stack([vx, vy], axis=-1).reshape(-1, 7, 2)
        metric_polys = [Polygon(v) for v in vertices]
    else:
        return GeoAnalysisResult(success=False, data=None, summary=f"Unsupported type: {type}")

    # Reproject the metric grid back to WGS84 for the response.
    grid = gpd.GeoDataFrame({'geometry': metric_polys}, crs=metric_crs).to_crs("EPSG:4326")

    return GeoAnalysisResult(
        success=True,
        data=grid.__geo_interface__,
        summary=f"Generated {len(metric_polys)} {type} cells. {warning}".strip()
    )

def spatial_aggregate(
    points_geojson: dict | str,
    polygons_geojson: dict | str,
    stats: list[str] | None = None,
    value_field: str | None = None,
    output_crs: str | None = "EPSG:4326",
) -> GeoAnalysisResult:
    """
    Aggregate points to polygons using spatial join.
    Supports stats: count, sum, mean, max, min.
    
    Args:
        output_crs: Output CRS (default EPSG:4326). Set to None to keep input CRS.
    """
    if stats is None:
        stats = ['count', 'sum', 'mean']
    try:
        # Use geo_processor for pre-processing (alignment)
        res_points = to_utm_gdf(points_geojson)
        res_polys = to_utm_gdf(polygons_geojson)

        # GIS-13: to_utm_gdf returns (None, None) on failure but a non-empty
        # tuple is always truthy, so `if not res_*` never fired. See network.py.
        if res_points is None or res_points[0] is None or res_polys is None or res_polys[0] is None:
            return GeoAnalysisResult(False, None, "Invalid input GeoJSON")
            
        points, utm_crs = res_points
        polygons, poly_crs = res_polys
        
        # Ensure same CRS
        if utm_crs != poly_crs:
            polygons = polygons.to_crs(utm_crs)

        # Spatial Join
        joined = gpd.sjoin(points, polygons, how='inner', predicate='within')
        
        results = []
        for stat in stats:
            if stat == 'count':
                res = joined.groupby('index_right').size().rename('count')
                results.append(res)
            elif value_field and value_field in points.columns:
                if stat in ['sum', 'mean', 'max', 'min']:
                    res = joined.groupby('index_right')[value_field].agg(stat).rename(stat)
                    results.append(res)
        
        if not results:
            res = joined.groupby('index_right').size().rename('count')
            results.append(res)
            
        combined_stats = pd.concat(results, axis=1)
        final_gdf = polygons.join(combined_stats)
        
        # Fill NaNs
        if 'count' in final_gdf.columns:
            final_gdf['count'] = final_gdf['count'].fillna(0).astype(int)
        for s in ['sum', 'mean', 'max', 'min']:
            if s in final_gdf.columns:
                final_gdf[s] = final_gdf[s].fillna(0)
        
        # Convert to output CRS (audit: warn when silently reprojecting)
        if output_crs and str(final_gdf.crs) != str(output_crs):
            logger.warning(
                "spatial_aggregate: input CRS %s differs from output_crs %s; reprojecting.",
                final_gdf.crs, output_crs,
            )
            final_gdf = final_gdf.to_crs(output_crs)
            
        summary = f"Successfully aggregated points to {len(polygons)} polygons."
        if value_field:
            summary += f" Used field '{value_field}' for {', '.join([s for s in stats if s != 'count'])}."

        return GeoAnalysisResult(
            success=True,
            data=final_gdf.__geo_interface__,
            summary=summary
        )
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Aggregation failed: {str(e)}"
        )

# Alias for backward compatibility with plan
aggregate_points_to_polygons = spatial_aggregate

def h3_binning(geojson: dict | str, resolution: int | None = None, stat_field: str | None = None, stat_method: str = 'count') -> GeoAnalysisResult:
    """
    Bin points into H3 hexagons.
    Supports stats: count, sum, mean.
    If resolution is None, it is automatically selected based on the extent.
    """
    try:
        import h3
        import math
        if isinstance(geojson, dict) and 'features' in geojson:
            # GIS-599: honor a declared `crs` member instead of hardcoding
            # EPSG:4326 — a declared projected input (e.g. EPSG:3857 metres)
            # would otherwise be fed to latitude/longitude H3 lookups.
            gdf = gdf_from_features(geojson, "h3_binning")
        else:
            return GeoAnalysisResult(success=False, data=None, summary="Invalid geojson input.")
            
        if gdf.empty:
            return GeoAnalysisResult(success=False, data=None, summary="Empty geojson input.")
            
        # Automatic resolution selection (审计：从度数阈值改为米制阈值)
        if resolution is None:
            xmin, ymin, xmax, ymax = gdf.total_bounds
            # 用 centroid 纬度近似转换度数为米（仍受纬度偏差影响，但比纯度数更直观）
            lat = (ymin + ymax) / 2
            m_per_deg_lat = 111_320
            m_per_deg_lon = 111_320 * math.cos(math.radians(lat))
            max_dim_m = max(
                (xmax - xmin) * m_per_deg_lon,
                (ymax - ymin) * m_per_deg_lat,
            )
            # 阈值对应原度数阈值的等价米数（赤道近似）
            if max_dim_m > 5_566_000:  # > ~50°
                resolution = 1
            elif max_dim_m > 1_113_000:  # > ~10°
                resolution = 3
            elif max_dim_m > 111_300:  # > ~1°
                resolution = 5
            elif max_dim_m > 11_130:  # > ~0.1°
                resolution = 7
            elif max_dim_m > 1_113:  # > ~0.01°
                resolution = 9
            else:
                resolution = 11
            
        # Ensure point geometry
        if not all(geom.geom_type == 'Point' for geom in gdf.geometry):
            gdf['geometry'] = gdf.geometry.centroid
            
        # Assign H3 index (向量化：避免 O(n) apply lambda)
        lats = gdf.geometry.y.values
        lngs = gdf.geometry.x.values
        gdf['h3_index'] = [h3.latlng_to_cell(lat, lng, resolution) for lat, lng in zip(lats, lngs)]
        
        # Group by H3 index
        if stat_method == 'count':
            grouped = gdf.groupby('h3_index').size().rename('count').reset_index()
        elif stat_field and stat_field in gdf.columns:
            if stat_method in ['sum', 'mean']:
                grouped = gdf.groupby('h3_index')[stat_field].agg(stat_method).rename(stat_method).reset_index()
            else:
                grouped = gdf.groupby('h3_index').size().rename('count').reset_index()
                stat_method = 'count'
        else:
            grouped = gdf.groupby('h3_index').size().rename('count').reset_index()
            stat_method = 'count'
            
        # Create Polygons from H3 indices
        polygons = []
        for h3_id in cancellable(grouped['h3_index'], every=512):
            # cell_to_boundary returns ((lat, lng), ...)
            boundary = h3.cell_to_boundary(h3_id)
            # shapely expects (lng, lat)
            coords = [(lng, lat) for lat, lng in boundary]
            polygons.append(Polygon(coords))
            
        hex_gdf = gpd.GeoDataFrame(grouped, geometry=polygons, crs="EPSG:4326")
        
        summary = f"Binned {len(gdf)} points into {len(hex_gdf)} hexagons at resolution {resolution}."
        
        return GeoAnalysisResult(
            success=True,
            data=hex_gdf.__geo_interface__,
            summary=summary
        )
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"H3 binning failed: {str(e)}"
        )

