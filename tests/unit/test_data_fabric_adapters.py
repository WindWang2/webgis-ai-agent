"""
Unit Tests for All 10 Data Fabric Adapters Interface Contracts
(PostGIS, OGC API Features, WFS, WMS/WMTS, ArcGIS REST, STAC, GeoParquet, FlatGeobuf, PMTiles, S3)
"""
import pytest
from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters import (
    PostGISAdapter,
    OGCApiFeaturesAdapter,
    WFSAdapter,
    WMSWMTSAdapter,
    ArcGISRESTAdapter,
    STACAdapter,
    GeoParquetAdapter,
    FlatGeobufAdapter,
    PMTilesAdapter,
    S3ObjectStorageSeam,
)


def test_postgis_adapter_interface():
    profile = ConnectionProfile(
        source_type="postgis",
        endpoint_url="postgresql://user:pass@localhost:5432/gisdb",
        name="test_postgis",
    )
    adapter = PostGISAdapter(profile)
    assert isinstance(adapter.capabilities(), list)
    desc = adapter.describe("public.cities")
    assert desc.id == "public.cities"


def test_ogc_api_adapter_interface():
    profile = ConnectionProfile(
        source_type="ogc_api",
        endpoint_url="https://demo.ldproxy.net/dusseldorf",
        name="test_ogc",
    )
    adapter = OGCApiFeaturesAdapter(profile)
    assert isinstance(adapter.capabilities(), list)


def test_wfs_adapter_interface():
    profile = ConnectionProfile(
        source_type="wfs",
        endpoint_url="https://demo.mapserver.org/cgi-bin/wfs",
        name="test_wfs",
    )
    adapter = WFSAdapter(profile)
    assert isinstance(adapter.capabilities(), list)


def test_wms_wmts_adapter_interface():
    profile = ConnectionProfile(
        source_type="wms",
        endpoint_url="https://demo.mapserver.org/cgi-bin/wms",
        name="test_wms",
    )
    adapter = WMSWMTSAdapter(profile)
    assert isinstance(adapter.capabilities(), list)


def test_arcgis_adapter_interface():
    profile = ConnectionProfile(
        source_type="arcgis",
        endpoint_url="https://services.arcgis.com/World_Cities/FeatureServer/0",
        name="test_arcgis",
    )
    adapter = ArcGISRESTAdapter(profile)
    assert isinstance(adapter.capabilities(), list)


def test_stac_adapter_interface():
    profile = ConnectionProfile(
        source_type="stac",
        endpoint_url="https://earth-search.aws.element84.com/v1",
        name="test_stac",
    )
    adapter = STACAdapter(profile)
    assert isinstance(adapter.capabilities(), list)


def test_geoparquet_adapter_interface():
    profile = ConnectionProfile(
        source_type="geoparquet",
        endpoint_url="s3://mybucket/data.parquet",
        name="test_parquet",
    )
    adapter = GeoParquetAdapter(profile)
    assert adapter.probe() is True
    desc = adapter.describe("data.parquet")
    assert desc.geometry_type == "MultiPolygon"


def test_flatgeobuf_adapter_interface():
    profile = ConnectionProfile(
        source_type="flatgeobuf",
        endpoint_url="https://flatgeobuf.org/test.fgb",
        name="test_fgb",
    )
    adapter = FlatGeobufAdapter(profile)
    assert adapter.probe() is True


def test_pmtiles_adapter_interface():
    profile = ConnectionProfile(
        source_type="pmtiles",
        endpoint_url="https://pmtiles.io/test.pmtiles",
        name="test_pmtiles",
    )
    adapter = PMTilesAdapter(profile)
    assert adapter.probe() is True
    desc = adapter.describe("pmtiles_layer")
    assert desc.feature_type == "tile"


def test_s3_storage_seam_interface():
    profile = ConnectionProfile(
        source_type="s3",
        endpoint_url="s3://spatial-bucket/layer.geojson",
        name="test_s3",
    )
    adapter = S3ObjectStorageSeam(profile)
    assert adapter.probe() is True
    health = adapter.health()
    assert health.reachable is True
