import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box, Polygon
from app.lib.geoprocessing.interface import GeoAnalysisResult

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
        # Area / 50000 = new_cell_size^2
        new_cell_size = np.sqrt((width * height) / 50000)
        warning = f"Warning: Grid too dense ({int(estimated_cells)} cells). Cell size adjusted from {cell_size} to {new_cell_size:.4f}."
        cell_size = new_cell_size

    if type == 'square':
        # Use np.arange but ensure we cover the bounds
        cols = list(np.arange(xmin, xmax, cell_size))
        rows = list(np.arange(ymin, ymax, cell_size))
        
        polygons = []
        for x in cols:
            for y in rows:
                polygons.append(box(x, y, x + cell_size, y + cell_size))
        
        grid = gpd.GeoDataFrame({'geometry': polygons})
        return GeoAnalysisResult(
            success=True,
            data=grid.__geo_interface__,
            summary=f"Generated {len(polygons)} square cells. {warning}".strip()
        )
    
    elif type == 'hexagon':
        # cell_size is distance between parallel sides (flat-to-flat)
        R = cell_size / np.sqrt(3)
        dx = cell_size
        dy = 1.5 * R
        
        polygons = []
        # Add buffer to ensure coverage
        cols = np.arange(xmin - dx, xmax + dx, dx)
        rows = np.arange(ymin - dy, ymax + dy, dy)
        
        for j, y in enumerate(rows):
            for x in cols:
                x_offset = (j % 2) * (dx / 2)
                cx = x + x_offset
                
                # Create hexagon vertices (pointy-topped)
                # Vertices are at 30, 90, 150, 210, 270, 330 degrees for flat-to-flat = cell_size
                # Wait, pointy-topped means vertices at 30, 90... no.
                # Pointy-topped: vertices at 30, 90, 150... or 0, 60, 120...
                # If vertices at 0, 60, 120, 180, 240, 300:
                # - Width (point-to-point) = 2R
                # - Height (flat-to-flat) = sqrt(3)R
                # This matches pointy-topped.
                angles = np.radians([0, 60, 120, 180, 240, 300, 0])
                hex_coords = [
                    (cx + R * np.cos(a), y + R * np.sin(a))
                    for a in angles
                ]
                polygons.append(Polygon(hex_coords))
        
        grid = gpd.GeoDataFrame({'geometry': polygons})
        # Optional: Clip to bounds if we want strictness, but usually fishnet just covers it
        return GeoAnalysisResult(
            success=True,
            data=grid.__geo_interface__,
            summary=f"Generated {len(polygons)} hexagonal cells. {warning}".strip()
        )
    
    return GeoAnalysisResult(success=False, data=None, summary="Not implemented")

def aggregate_points_to_polygons(points_geojson, polygons_geojson, stats=['count'], value_field=None):
    """
    Aggregate points to polygons using spatial join.
    Supports stats: count, sum, mean, max, min.
    """
    try:
        points = gpd.GeoDataFrame.from_features(points_geojson['features'])
        polygons = gpd.GeoDataFrame.from_features(polygons_geojson['features'])
        
        # Ensure CRS is set (assume same if not provided)
        if points.crs is None:
            points.set_crs(epsg=4326, inplace=True)
        if polygons.crs is None:
            polygons.set_crs(epsg=4326, inplace=True)

        # Spatial Join: points to polygons
        # index_right will contain the index of the polygon containing the point
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
            # Fallback to count
            res = joined.groupby('index_right').size().rename('count')
            results.append(res)
            
        combined_stats = pd.concat(results, axis=1)
        
        # Join back to original polygons to ensure all polygons are present
        final_gdf = polygons.join(combined_stats)
        
        # Fill NaNs where appropriate
        if 'count' in final_gdf.columns:
            final_gdf['count'] = final_gdf['count'].fillna(0).astype(int)
        if 'sum' in final_gdf.columns:
            final_gdf['sum'] = final_gdf['sum'].fillna(0)
            
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
