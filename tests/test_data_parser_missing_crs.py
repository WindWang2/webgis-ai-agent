"""Shapefile / GPKG without a CRS must not be silently labeled EPSG:4326."""
import pytest

from app.services.data_parser import parse_vector, ParseError


def test_shapefile_zip_without_prj_is_rejected(tmp_path):
    """A projected shapefile missing .prj is not WGS84. Labeling it EPSG:4326
    places coordinates like (500000, 4500000) on the map as lon/lat."""
    import zipfile

    import geopandas as gpd
    from shapely.geometry import Point

    gdf = gpd.GeoDataFrame(
        {"name": ["utm"]},
        geometry=[Point(500000.0, 4500000.0)],
        crs=None,
    )
    shp_dir = tmp_path / "shp_src"
    shp_dir.mkdir()
    gdf.to_file(shp_dir / "utm.shp", engine="pyogrio")

    zip_path = tmp_path / "utm.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for ext in [".shp", ".dbf", ".shx"]:
            p = shp_dir / f"utm{ext}"
            if p.exists():
                zf.write(p, f"utm{ext}")

    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ParseError, match="CRS|坐标参考|prj"):
        parse_vector(zip_path, out, "missing-crs")


def test_geojson_without_crs_remains_wgs84(tmp_path):
    """RFC 7946: GeoJSON without a CRS is lon/lat WGS84."""
    import geopandas as gpd
    from shapely.geometry import Point

    gdf = gpd.GeoDataFrame(
        {"name": ["ok"]},
        geometry=[Point(116.4, 39.9)],
        crs=None,
    )
    src = tmp_path / "points.geojson"
    gdf.to_file(src, driver="GeoJSON")
    out = tmp_path / "out"
    out.mkdir()
    result = parse_vector(src, out, "geojson-default")
    assert result["crs"] == "EPSG:4326"
    assert result["feature_count"] == 1
