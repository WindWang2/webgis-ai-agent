"""
Unit tests for Geospatial Data Fabric Services:
- SpatialCatalogService
- DatasetFingerprintService
- MaterializationService
- DataFabricHealthCheck
- SSRF Policy in DataFabricSecurity
"""
import pytest
from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
)
from app.services.data_fabric.spatial_catalog import SpatialCatalogService
from app.services.data_fabric.fingerprint import DatasetFingerprintService
from app.services.data_fabric.materialization_service import MaterializationService
from app.services.data_fabric.health import DataFabricHealthCheck
from app.services.data_fabric.connection_manager import (
    GenericDataSourceAdapter,
)


@pytest.mark.asyncio
async def test_spatial_catalog_service():
    catalog = SpatialCatalogService()
    desc1 = DatasetDescriptor(
        id="usgs_earthquakes",
        title="USGS Seismic Events",
        description="Global earthquake dataset",
        source_type="geojson",
        geometry_type="Point",
        srs="EPSG:4326",
        bbox=[-180.0, -90.0, 180.0, 90.0],
    )
    desc2 = DatasetDescriptor(
        id="beijing_landuse",
        title="Beijing Municipal Land Use",
        description="Urban zoning and land use planning",
        source_type="postgis",
        geometry_type="MultiPolygon",
        srs="EPSG:3857",
        bbox=[115.7, 39.4, 117.4, 41.0],
    )

    catalog.register_dataset(desc1, tags=["seismic", "global"], profile_id="usgs_p1")
    catalog.register_dataset(desc2, tags=["urban", "china"], profile_id="pg_beijing")

    assert len(catalog.list_datasets()) == 2
    assert catalog.get_dataset("usgs_earthquakes").title == "USGS Seismic Events"

    # Search keyword
    res = catalog.search(query="earthquake")
    assert res["total"] == 1
    assert res["items"][0]["id"] == "usgs_earthquakes"

    # Search bbox
    res_bbox = catalog.search(bbox=[116.0, 39.8, 116.5, 40.2])
    assert res_bbox["total"] == 2  # Both intersect (desc1 is global)

    # Search CRS
    res_crs = catalog.search(crs="3857")
    assert res_crs["total"] == 1
    assert res_crs["items"][0]["id"] == "beijing_landuse"

    # Search source_type
    res_st = catalog.search(source_type="postgis")
    assert res_st["total"] == 1
    assert res_st["items"][0]["id"] == "beijing_landuse"

    # Unregister
    assert catalog.unregister_dataset("usgs_earthquakes") is True
    assert catalog.get_dataset("usgs_earthquakes") is None


def test_dataset_fingerprint_service():
    fp_service = DatasetFingerprintService()
    desc = DatasetDescriptor(
        id="test_layer",
        source_type="geojson",
        geometry_type="Point",
        srs="EPSG:4326",
        bbox=[0.0, 0.0, 10.0, 10.0],
        feature_count=100,
        fields=[{"name": "id", "type": "int"}, {"name": "val", "type": "string"}],
    )
    desc_fp = fp_service.calculate_descriptor_fingerprint(desc)
    assert isinstance(desc_fp, str)
    assert len(desc_fp) == 64  # SHA256 hex string length

    features = [{"type": "Feature", "properties": {"id": 1, "val": "A"}}]
    data_fp = fp_service.calculate_data_fingerprint(features)
    assert isinstance(data_fp, str)

    combined_fp = fp_service.calculate_combined_fingerprint(desc, features)
    assert fp_service.verify_fingerprint(desc, combined_fp, features) is True
    assert fp_service.verify_fingerprint(desc, "invalid_hash", features) is False


@pytest.mark.asyncio
async def test_materialization_service():
    mat_service = MaterializationService()
    profile = ConnectionProfile(id="test_mat_p1", source_type="geojson")
    adapter = GenericDataSourceAdapter(profile)

    spec = QuerySpec(limit=5, bbox=[116.0, 39.8, 117.0, 40.2])
    q_res = mat_service.execute_query(adapter, "test_mat_p1", spec)
    assert isinstance(q_res, QueryResult)
    assert len(q_res.features) > 0

    mat_res = await mat_service.materialize_dataset(
        adapter=adapter,
        dataset_id="test_mat_p1",
        query_spec=spec,
        session_id="unit_test_session",
        layer_name="Test Materialized Layer",
    )
    assert mat_res["status"] == "success"
    assert mat_res["ref_id"].startswith("ref:data-fabric-")
    assert mat_res["layer_name"] == "Test Materialized Layer"
    assert "fingerprint" in mat_res


def test_data_fabric_health_and_ssrf():
    health_checker = DataFabricHealthCheck()
    profile = ConnectionProfile(
        id="safe_profile",
        source_type="wfs",
        url="https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/World_Cities/FeatureServer/0",
    )
    h_res = health_checker.check_connection_profile(profile)
    assert h_res.status == "healthy"

    # SSRF Attack tests
    bad_urls = [
        "http://127.0.0.1/admin",
        "http://localhost:8080/internal",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/db",
    ]
    for url in bad_urls:
        bad_profile = ConnectionProfile(id="bad_p", source_type="wfs", url=url)
        bad_h = health_checker.check_connection_profile(bad_profile)
        assert bad_h.status == "unreachable"
        assert "SSRF" in bad_h.message
