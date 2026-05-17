import numpy as np
import pandas as pd
import geopandas as gpd
import h3
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

def h3_binning(geojson, resolution=None, stat_field=None, stat_method='count'):
    """
    Bin points into H3 hexagons.
    Supports stats: count, sum, mean.
    If resolution is None, it is automatically selected based on the extent.
    """
    try:
        import h3
        if isinstance(geojson, dict) and 'features' in geojson:
            gdf = gpd.GeoDataFrame.from_features(geojson['features'], crs="EPSG:4326")
        else:
            return GeoAnalysisResult(success=False, data=None, summary="Invalid geojson input.")
            
        if gdf.empty:
            return GeoAnalysisResult(success=False, data=None, summary="Empty geojson input.")
            
        # Automatic resolution selection
        if resolution is None:
            xmin, ymin, xmax, ymax = gdf.total_bounds
            width = xmax - xmin
            height = ymax - ymin
            max_dim = max(width, height)
            
            # Simple heuristic: map data extent to H3 resolution (0-15)
            if max_dim > 50: resolution = 1
            elif max_dim > 10: resolution = 3
            elif max_dim > 1: resolution = 5
            elif max_dim > 0.1: resolution = 7
            elif max_dim > 0.01: resolution = 9
            else: resolution = 11
            
        # Ensure point geometry
        if not all(geom.geom_type == 'Point' for geom in gdf.geometry):
            gdf['geometry'] = gdf.geometry.centroid
            
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")
            
        # Assign H3 index
        # gdf.geometry.y is lat, gdf.geometry.x is lng
        gdf['h3_index'] = gdf.apply(lambda row: h3.latlng_to_cell(row.geometry.y, row.geometry.x, resolution), axis=1)
        
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
        for h3_id in grouped['h3_index']:
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

