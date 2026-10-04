"""审查 G1/G2：不可信上传不得被 GDAL 按内容嗅探成 VRT（任意本地文件读取 / SSRF）
或在 KML 中解析 DTD 外部实体。"""
import zipfile

import numpy as np
import pytest

from app.services.data_parser import ParseError, parse_raster, parse_vector


@pytest.fixture
def secret_csv(tmp_path):
    p = tmp_path / "secret" / "other_user.csv"
    p.parent.mkdir()
    p.write_text("name,lon,lat\nTOPSECRET,1,2\n", encoding="utf-8")
    return p


def _out(tmp_path, name="out"):
    d = tmp_path / name
    d.mkdir()
    return d


def test_ogr_vrt_disguised_as_geojson_is_rejected(tmp_path, secret_csv):
    evil = tmp_path / "evil.geojson"
    evil.write_text(
        '<OGRVRTDataSource><OGRVRTLayer name="other_user">'
        f'<SrcDataSource relativeToVRT="0">{secret_csv}</SrcDataSource>'
        '<GeometryType>wkbPoint</GeometryType><LayerSRS>EPSG:4326</LayerSRS>'
        '<GeometryField encoding="PointFromColumns" x="lon" y="lat"/>'
        "</OGRVRTLayer></OGRVRTDataSource>",
        encoding="utf-8",
    )
    out = _out(tmp_path)
    with pytest.raises(ParseError):
        parse_vector(evil, out, "x")
    assert not (out / "original.geojson").exists()


def test_ogr_vrt_disguised_as_json_with_leading_ws_is_rejected(tmp_path, secret_csv):
    evil = tmp_path / "evil.json"
    evil.write_text(
        f"\n  <OGRVRTDataSource><OGRVRTLayer name='other_user'><SrcDataSource>{secret_csv}"
        "</SrcDataSource></OGRVRTLayer></OGRVRTDataSource>",
        encoding="utf-8",
    )
    with pytest.raises(ParseError):
        parse_vector(evil, _out(tmp_path), "x")


def test_vrt_disguised_as_gpkg_and_shp_rejected(tmp_path, secret_csv):
    body = (
        f"<OGRVRTDataSource><OGRVRTLayer name='other_user'><SrcDataSource>{secret_csv}"
        "</SrcDataSource></OGRVRTLayer></OGRVRTDataSource>"
    )
    for name in ("evil.gpkg", "evil.shp"):
        p = tmp_path / name
        p.write_text(body, encoding="utf-8")
        with pytest.raises(ParseError):
            parse_vector(p, _out(tmp_path, name + "_out"), "x")


def test_zip_with_vrt_member_rejected(tmp_path, secret_csv):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.shp", b"\x00\x00\x27\x0a" + b"\x00" * 96)
        zf.writestr("a.dbf", b"\x03")
        zf.writestr("b.vrt", f"<OGRVRTDataSource><OGRVRTLayer name='other_user'><SrcDataSource>{secret_csv}</SrcDataSource></OGRVRTLayer></OGRVRTDataSource>")
    with pytest.raises(ParseError, match="不允许"):
        parse_vector(z, _out(tmp_path), "x")


def test_kml_with_doctype_entity_rejected(tmp_path):
    kml = tmp_path / "xxe.kml"
    kml.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/hostname">]>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>'
        "<name>&x;</name><Point><coordinates>1,2</coordinates></Point>"
        "</Placemark></Document></kml>",
        encoding="utf-8",
    )
    with pytest.raises(ParseError, match="DOCTYPE"):
        parse_vector(kml, _out(tmp_path), "x")


def test_plain_kml_still_parses(tmp_path):
    kml = tmp_path / "ok.kml"
    kml.write_text(
        '<?xml version="1.0"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>'
        "<name>p</name><Point><coordinates>116.4,39.9</coordinates></Point>"
        "</Placemark></Document></kml>",
        encoding="utf-8",
    )
    meta = parse_vector(kml, _out(tmp_path), "x")
    assert meta["feature_count"] == 1


def test_gdal_vrt_disguised_as_tif_is_rejected(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin

    victim = tmp_path / "victim.tif"
    with rasterio.open(
        victim, "w", driver="GTiff", width=4, height=4, count=1, dtype="uint8",
        crs="EPSG:4326", transform=from_origin(0, 4, 1, 1),
    ) as dst:
        dst.write(np.full((1, 4, 4), 42, dtype="uint8"))
    evil = tmp_path / "evil.tif"
    evil.write_text(
        '<VRTDataset rasterXSize="4" rasterYSize="4"><SRS>EPSG:4326</SRS>'
        '<GeoTransform>0,1,0,4,0,-1</GeoTransform><VRTRasterBand dataType="Byte" band="1">'
        f'<SimpleSource><SourceFilename relativeToVRT="0">{victim}</SourceFilename>'
        "<SourceBand>1</SourceBand></SimpleSource></VRTRasterBand></VRTDataset>",
        encoding="utf-8",
    )
    out = _out(tmp_path)
    with pytest.raises(ParseError):
        parse_raster(evil, out, "x")
    assert not (out / "original.tif").exists()
    # 真 GeoTIFF 仍可解析
    meta = parse_raster(victim, _out(tmp_path, "ok"), "y")
    assert meta["band_count"] == 1
