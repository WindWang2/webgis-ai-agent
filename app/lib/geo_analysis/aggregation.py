import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box, Polygon, mapping
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_processor.core import to_utm_gdf

def generate_fishnet(bounds, cell_size, type='square'):
    """
    Generate a square or hexagonal grid over specified bounds.
    Includes OOM protection.
    """
    xmin, ymin, xmax, ymax = bounds
    
    # OOM Protection: Cap at 50,000 cells
    width = xmax - xmin
    height = ymax - ymin
    
    # Square cell area: cell_size * cell_size
    # We use a simple estimate for now
    estimated_cells = (width / cell_size) * (height / cell_size)
    
    warning = ""
    if estimated_cells > 50000:
        # Calculate a safe cell_size
        new_cell_size = np.sqrt((width * height) / 50000)
        warning = f"Warning: Grid too dense ({int(estimated_cells)} cells). Cell size adjusted from {cell_size} to {new_cell_size:.4f}."
        cell_size = new_cell_size

    polygons = []
    if type == 'square':
        cols = list(np.arange(xmin, xmax, cell_size))
        rows = list(np.arange(ymin, ymax, cell_size))
        
        for x in cols:
            for y in rows:
                polygons.append(box(x, y, x + cell_size, y + cell_size))
        
    elif type == 'hexagon':
        R = cell_size / np.sqrt(3)
        dx = cell_size
        dy = 1.5 * R
        
        cols = np.arange(xmin - dx, xmax + dx, dx)
        rows = np.arange(ymin - dy, ymax + dy, dy)
        
        for j, y in enumerate(rows):
            for x in cols:
                x_offset = (j % 2) * (dx / 2)
                cx = x + x_offset
                angles = np.radians([0, 60, 120, 180, 240, 300, 0])
                hex_coords = [
                    (cx + R * np.cos(a), y + R * np.sin(a))
                    for a in angles
                ]
                polygons.append(Polygon(hex_coords))
    else:
        return GeoAnalysisResult(success=False, data=None, summary=f"Unsupported type: {type}")

    grid = gpd.GeoDataFrame({'geometry': polygons}, crs="EPSG:4326")
    
    return GeoAnalysisResult(
        success=True,
        data=grid.__geo_interface__,
        summary=f"Generated {len(polygons)} {type} cells. {warning}".strip()
    )

def spatial_aggregate(points_geojson, polygons_geojson, stats=['count', 'sum', 'mean'], value_field=None):
    """
    Aggregate points to polygons using spatial join.
    Supports stats: count, sum, mean, max, min.
    """
    try:
        # Use geo_processor for pre-processing (alignment)
        res_points = to_utm_gdf(points_geojson)
        res_polys = to_utm_gdf(polygons_geojson)
        
        if not res_points or not res_polys:
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
        
        # Convert back to 4326 for output
        final_gdf = final_gdf.to_crs("EPSG:4326")
            
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
