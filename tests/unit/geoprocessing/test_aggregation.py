import pytest
import geopandas as gpd
from app.lib.geoprocessing.aggregation import generate_fishnet, aggregate_points_to_polygons
from shapely.geometry import Polygon, box, Point

def test_generate_fishnet_square():
    bounds = [0, 0, 10, 10]
    cell_size = 5
    result = generate_fishnet(bounds, cell_size, type='square')
    
    assert result.success is True
    assert len(result.data['features']) == 4
    # Check first cell geometry
    first_geom = result.data['features'][0]['geometry']
    assert first_geom['type'] == 'Polygon'
    # 5x5 cell at (0,0)
    coords = first_geom['coordinates'][0]
    # Standard GeoJSON polygon: [ [x1,y1], [x2,y2], ... [x1,y1] ]
    # box(0, 0, 5, 5) -> [[5.0, 0.0], [5.0, 5.0], [0.0, 5.0], [0.0, 0.0], [5.0, 0.0]] or similar
    # We just check if corner points are present
    flat_coords = [list(c) for c in coords]
    assert [0.0, 0.0] in flat_coords
    assert [5.0, 0.0] in flat_coords
    assert [5.0, 5.0] in flat_coords
    assert [0.0, 5.0] in flat_coords

def test_generate_fishnet_hexagon():
    bounds = [0, 0, 10, 10]
    cell_size = 2
    result = generate_fishnet(bounds, cell_size, type='hexagon')
    
    assert result.success is True
    assert len(result.data['features']) > 0
    
    # Check if we have polygons with 6 vertices (7 including closure)
    first_geom = result.data['features'][0]['geometry']
    assert first_geom['type'] == 'Polygon'
    coords = first_geom['coordinates'][0]
    # Hexagon has 6 sides, so 7 points in GeoJSON
    assert len(coords) == 7

def test_aggregate_points_to_polygons():
    # Create 4 polygons (2x2 grid)
    polygons = [
        box(0, 0, 5, 5), box(5, 0, 10, 5),
        box(0, 5, 5, 10), box(5, 5, 10, 10)
    ]
    poly_gdf = gpd.GeoDataFrame({'geometry': polygons})
    polygons_geojson = poly_gdf.__geo_interface__
    
    # Create points:
    # 3 points in first poly (0,0 to 5,5)
    # 1 point in second poly (5,0 to 10,5)
    points = [
        Point(1, 1), Point(2, 2), Point(3, 3), # in poly 0
        Point(6, 1),                            # in poly 1
        Point(1, 6)                             # in poly 2
    ]
    # Add values for sum/mean
    point_gdf = gpd.GeoDataFrame({'geometry': points, 'val': [10, 20, 30, 40, 50]})
    points_geojson = point_gdf.__geo_interface__
    
    result = aggregate_points_to_polygons(
        points_geojson, 
        polygons_geojson, 
        stats=['count', 'sum', 'mean'], 
        value_field='val'
    )
    
    assert result.success is True
    data = gpd.GeoDataFrame.from_features(result.data['features'])
    
    # Check poly 0 (first)
    # It should have count=3, sum=60, mean=20
    # Note: sjoin order might vary, but we can check values
    poly0_row = data.iloc[0]
    assert poly0_row['count'] == 3
    assert poly0_row['sum'] == 60
    assert poly0_row['mean'] == 20
    
    # Check poly 3 (no points)
    poly3_row = data.iloc[3]
    assert poly3_row['count'] == 0
