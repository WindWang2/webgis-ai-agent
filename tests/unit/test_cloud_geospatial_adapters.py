"""
Unit tests for Cloud-Native Geospatial Data Fabric Adapters:
1. STAC Adapter (generalized collections/items beyond Sentinel)
2. GeoParquet Adapter (column projection, bbox selective read)
3. FlatGeobuf Adapter (spatial index / bbox selective read)
4. PMTiles Adapter (tile source registration, metadata bounds, no full GeoJSON conversion)
5. S3 / MinIO Object Storage Seam (s3:// URI support, secret reference security)
"""
import pytest
from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters import (
    STACAdapter,
    GeoParquetAdapter,
    FlatGeobufAdapter,
    PMTilesAdapter,
    S3StorageSeam,
)
from tests.unit.test_data_fabric_contract import verify_adapter_contract


def test_stac_adapter_contract_and_generalization():
    """Verify STAC adapter contract and multi-collection generalization beyond Sentinel."""
    profile = ConnectionProfile(provider_type="stac", endpoint="")
    adapter = STACAdapter(profile)

    # 1. Standard contract compliance
    verify_adapter_contract(adapter, "landsat-8-c2-l2")

    # 2. Generalization beyond Sentinel (Landsat 8, Copernicus DEM)
    datasets = adapter.list_datasets()
    dataset_ids = [d["id"] for d in datasets]
    assert "landsat-8-c2-l2" in dataset_ids
    assert "cop-dem-30m" in dataset_ids

    # 3. Spatial & datetime pushdown query
    q_spec = QuerySpec(
        limit=5,
        bbox=[116.0, 39.5, 117.0, 40.5],
        datetime_range=["2023-01-01T00:00:00Z", "2023-12-31T23:59:59Z"],
    )
    res = adapter.query("landsat-8-c2-l2", q_spec)
    assert res.dataset_id == "landsat-8-c2-l2"
    assert isinstance(res.features, list)
    assert len(res.features) > 0
    assert res.metadata["pushdown_bbox"] is True


def test_geoparquet_adapter_column_projection_and_bbox():
    """Verify GeoParquet adapter column projection and selective bbox read."""
    profile = ConnectionProfile(provider_type="geoparquet", endpoint="")
    adapter = GeoParquetAdapter(profile)

    # 1. Standard contract compliance
    verify_adapter_contract(adapter, "us_states_geoparquet")

    # 2. Describe schema
    desc = adapter.describe("us_states_geoparquet")
    assert desc.geometry_type in ["Polygon", "MultiPolygon"]
    assert "state_name" in desc.schema_fields

    # 3. Selective Column Projection + BBOX Read
    q_spec = QuerySpec(
        limit=2,
        columns=["state_name", "population"],
        bbox=[-125.0, 30.0, -110.0, 45.0],
    )
    res = adapter.query("us_states_geoparquet", q_spec)
    assert res.dataset_id == "us_states_geoparquet"
    assert res.metadata["column_projection"] is True
    assert res.metadata["pushdown_bbox"] is True

    # Ensure unrequested columns are not in properties
    for feat in res.features:
        props = feat["properties"]
        assert "state_name" in props
        assert "population" in props
        assert "area_sqkm" not in props  # Column projection respected


def test_flatgeobuf_adapter_spatial_index():
    """Verify FlatGeobuf adapter spatial index search and fast binary scan."""
    profile = ConnectionProfile(provider_type="flatgeobuf", endpoint="")
    adapter = FlatGeobufAdapter(profile)

    # 1. Standard contract compliance
    verify_adapter_contract(adapter, "beijing_subway_stations")

    # 2. Header description
    desc = adapter.describe("beijing_subway_stations")
    assert desc.geometry_type == "Point"
    assert desc.metadata.get("spatial_index") == "packed_rtree"

    # 3. Spatial bbox query
    q_spec = QuerySpec(
        limit=10,
        bbox=[116.3, 39.85, 116.5, 39.95],
    )
    res = adapter.query("beijing_subway_stations", q_spec)
    assert res.metadata["spatial_index_used"] is True
    assert len(res.features) > 0


def test_pmtiles_adapter_no_full_geojson_conversion():
    """Verify PMTiles adapter tile source registration, metadata bounds, and zero full GeoJSON conversion."""
    profile = ConnectionProfile(provider_type="pmtiles", endpoint="")
    adapter = PMTilesAdapter(profile)

    # 1. Standard contract compliance
    verify_adapter_contract(adapter, "world_basemap_vector")

    # 2. Descriptor inspection (127-byte header metadata)
    desc = adapter.describe("world_basemap_vector")
    assert desc.data_type == "tile"
    assert desc.geometry_type == "TilePyramid"
    assert desc.metadata["no_full_geojson_conversion"] is True

    # 3. Preview without full GeoJSON conversion
    preview = adapter.preview("world_basemap_vector", limit=4)
    assert preview["schema"]["full_geojson_conversion"] is False
    assert len(preview["features"]) == 4

    # 4. Query tile coordinates without full GeoJSON conversion
    q_spec = QuerySpec(tile_coords={"z": 2, "x": 1, "y": 1})
    res = adapter.query("world_basemap_vector", q_spec)
    assert res.data["full_geojson_conversion"] is False
    assert res.data["tile_coord"] == {"z": 2, "x": 1, "y": 1}


def test_s3_storage_seam_security_and_s3_uri():
    """Verify S3 Storage Seam s3:// URI support, secret reference security, and bounded memory streams."""
    profile = ConnectionProfile(
        provider_type="s3",
        endpoint="s3://geo-data-bucket/remote_sensing/sentinel2_beijing.parquet",
        credentials={"access_key": "AKIA1234567890", "secret_key": "SuperSecretKeyDoNotLog"},
        options={"region": "us-west-2"},
    )
    adapter = S3StorageSeam(profile)

    # 1. Standard contract compliance
    verify_adapter_contract(adapter, "s3://geo-data-bucket/remote_sensing/sentinel2_beijing.parquet")

    # 2. Secret reference security (no secret logging in profile representation)
    sanitized = adapter.sanitized_profile
    assert sanitized["credentials"] == "********"

    # 3. s3:// URI resolution
    bucket, key = adapter.parse_s3_uri("s3://geo-data-bucket/vectors/county_boundaries.fgb")
    assert bucket == "geo-data-bucket"
    assert key == "vectors/county_boundaries.fgb"

    # 4. Bounded memory stream query
    q_spec = QuerySpec(limit=64)
    res = adapter.query("s3://geo-data-bucket/vectors/county_boundaries.fgb", q_spec)
    assert res.data["secret_sanitization"] is True
    assert res.metadata["bounded_memory_stream"] is True
