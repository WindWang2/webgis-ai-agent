"""F14: CRS conversion failure must not silently produce mismatched GeoJSON."""
import pytest
from pathlib import Path
from unittest.mock import patch
from app.services.data_parser import parse_vector, ParseError


def test_crs_conversion_failure_raises_parse_error(tmp_path):
    """When to_crs() fails, parse_vector must raise instead of writing unconverted data."""
    import geopandas as gpd
    from shapely.geometry import Point

    # Create a valid GeoDataFrame with a CRS
    gdf = gpd.GeoDataFrame(
        {"name": ["test"]},
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )

    # Write it as a valid file for read_file to parse
    test_file = tmp_path / "test.geojson"
    gdf.to_file(test_file, driver="GeoJSON")

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Mock to_crs to raise an exception
    with patch.object(gpd.GeoDataFrame, 'to_crs', side_effect=Exception("CRS transform failed")):
        with pytest.raises(ParseError, match="坐标转换失败"):
            parse_vector(test_file, out_dir, "test-upload-id")
