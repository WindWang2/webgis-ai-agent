import h3
import numpy as np
from scipy.spatial import KDTree

def idw_interpolation(points_geojson, value_field, resolution=8, power=2):
    """
    Simple IDW interpolation to H3 grid.
    """
    coords = []
    values = []
    for feature in points_geojson['features']:
        lon, lat = feature['geometry']['coordinates']
        coords.append([lat, lon])
        values.append(feature['properties'][value_field])
    
    coords = np.array(coords)
    values = np.array(values)
    
    # Get bounding box to find relevant H3 cells
    min_lat, min_lon = np.min(coords, axis=0)
    max_lat, max_lon = np.max(coords, axis=0)
    
    # Simple strategy: get all cells in bounding box (or simplified buffered area)
    # For a real implementation, we might use h3.polyfill or similar if we had a boundary
    # Here we'll just use the points' cells and their neighbors as a proxy for the area
    target_cells = set()
    for lat, lon in coords:
        cell = h3.latlng_to_cell(lat, lon, resolution)
        target_cells.add(cell)
        target_cells.update(h3.grid_disk(cell, 2)) # Buffer a bit
    
    tree = KDTree(coords)
    results = []
    for cell in target_cells:
        c_lat, c_lon = h3.cell_to_latlng(cell)
        dist, idx = tree.query([c_lat, c_lon], k=min(5, len(coords)))
        
        # Handle cases with very small distance to avoid division by zero
        if np.any(dist < 1e-10):
            val = values[idx[dist < 1e-10][0]]
        else:
            weights = 1.0 / (dist ** power)
            val = np.sum(weights * values[idx]) / np.sum(weights)
            
        results.append({"h3_index": cell, "value": float(val)})
    
    return results
