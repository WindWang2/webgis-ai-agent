import pytest
import os
import numpy as np
import rasterio
from rasterio.transform import from_origin
from app.lib.geo_analysis.raster_ops import zonal_statistics

def test_zonal_statistics_basic(tmp_path):
    # Create a dummy raster
    raster_path = str(tmp_path / "test_raster.tif")
    data = np.ones((10, 10), dtype=np.float32)
    data[0:5, 0:5] = 2.0
    
    transform = from_origin(0, 10, 1, 1)
    with rasterio.open(
        raster_path, 'w', driver='GTiff',
        height=10, width=10, count=1, dtype=np.float32,
        crs='+proj=latlong', transform=transform
    ) as dst:
        dst.write(data, 1)

    # GeoJSON polygon covering the 2.0 area
    polygons = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 10], [5, 10], [5, 5], [0, 5], [0, 10]]]
            },
            "properties": {"id": 1}
        }]
    }

    stats = zonal_statistics(polygons, raster_path, stats=['mean', 'sum'])
    assert stats[0]['mean'] == 2.0
    assert stats[0]['sum'] == 50.0 # 5x5 = 25 pixels * 2.0 = 50.0

from app.lib.geo_analysis.interpolation import idw_interpolation

def test_idw_interpolation_h3():
    # Simple points: (lat, lon, value)
    points = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120.0, 30.0]}, "properties": {"val": 10}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120.1, 30.1]}, "properties": {"val": 20}}
        ]
    }
    
    # Interpolate to H3 grid at resolution 8
    result = idw_interpolation(points, value_field="val", resolution=8)
    
    assert len(result) > 0
    assert "h3_index" in result[0]
    assert "value" in result[0]
    # Check if values are within range
    values = [r['value'] for r in result]
    assert min(values) >= 10 and max(values) <= 20
