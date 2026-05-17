import pytest
import geopandas as gpd
from app.lib.geo_processor.core import safe_parse, to_utm_gdf

def test_safe_parse():
    # Test string input
    assert safe_parse('{"type": "Point", "coordinates": [0, 0]}') == {"type": "Point", "coordinates": [0, 0]}
    # Test dict input
    assert safe_parse({"type": "Point", "coordinates": [0, 0]}) == {"type": "Point", "coordinates": [0, 0]}
    # Test invalid input
    assert safe_parse("invalid") is None
    assert safe_parse(None) is None

def test_to_utm_gdf():
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", 
            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}, 
            "properties": {"name": "Beijing"}
        }]
    }
    gdf, utm_crs = to_utm_gdf(geojson)
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert utm_crs.startswith("EPSG:326") # Northern hemisphere, zone 50 (approx)
    assert gdf.crs == utm_crs
    assert len(gdf) == 1
    assert gdf.iloc[0]["name"] == "Beijing"
