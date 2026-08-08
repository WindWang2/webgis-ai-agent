"""
Unit tests for Data Fabric REST routes, manager service, and MapSpec integration
"""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal, init_db, Base, Engine
from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec, DataFabricHealth, QueryResult, DatasetDescriptor
from app.services.data_fabric.manager import data_fabric_manager
from app.services.mapspec.lifecycle_engine import mapspec_lifecycle_engine, UpsertLayerIntent
from app.services.mapspec_source import is_data_fabric_entry


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=Engine)
    init_db()


def test_data_fabric_rest_routes():
    client = TestClient(app)

    # 1. List sources
    res = client.get("/api/v1/data-fabric/sources")
    assert res.status_code == 200
    data = res.json()
    assert "sources" in data

    # 2. Register Data Source (with mocked adapter probe & sync)
    create_payload = {
        "name": "Test OGC API Source",
        "source_type": "ogc_api",
        "endpoint_url": "https://example.com/ogc/collections",
        "options": {},
        "allow_private": False,
    }

    with patch("app.services.data_fabric.manager.DataFabricManager.probe_profile") as mock_probe, \
         patch("app.services.data_fabric.manager.DataFabricManager.sync_catalog") as mock_sync:
        mock_probe.return_value = DataFabricHealth(status="healthy", message="OK", latency_ms=12.5)
        mock_sync.return_value = []

        create_res = client.post("/api/v1/data-fabric/sources", json=create_payload)
        assert create_res.status_code == 200
        create_data = create_res.json()
        assert create_data["success"] is True
        source_id = create_data["data_source"]["id"]
        assert source_id.startswith("ds_")

    # 3. Get single data source
    get_res = client.get(f"/api/v1/data-fabric/sources/{source_id}")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "Test OGC API Source"

    # 4. Probe data source health
    with patch("app.services.data_fabric.manager.DataFabricManager.probe_profile") as mock_probe:
        mock_probe.return_value = DataFabricHealth(status="healthy", message="OK", latency_ms=10.0)
        probe_res = client.post(f"/api/v1/data-fabric/sources/{source_id}/probe")
        assert probe_res.status_code == 200
        assert probe_res.json()["status"] == "healthy"

    # 5. Sync catalog
    with patch("app.services.data_fabric.manager.DataFabricManager.sync_catalog") as mock_sync:
        mock_sync.return_value = []
        sync_res = client.post(f"/api/v1/data-fabric/sources/{source_id}/sync")
        assert sync_res.status_code == 200
        assert "synced_count" in sync_res.json()

    # 6. List Spatial Catalog
    cat_res = client.get("/api/v1/data-fabric/catalog")
    assert cat_res.status_code == 200
    cat_data = cat_res.json()
    assert "total" in cat_data

    # 7. Materialize catalog item / query to session ref_id
    with SessionLocal() as db:
        from app.models.data_fabric import CatalogItemModel, DataSourceModel
        ds = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
        item = CatalogItemModel(
            id=f"cat_{source_id}_default",
            source_id=source_id,
            name="default_layer",
            title="Default Layer",
            geometry_type="Point",
            feature_type="vector",
            crs="EPSG:4326",
            bbox_json=[-180.0, -90.0, 180.0, 90.0],
            tags_json=["ogc_api"],
            descriptor_json={"id": "default_layer", "title": "Default Layer", "source_type": "ogc_api"},
        )
        if ds:
            item.data_source = ds
        db.merge(item)
        db.commit()

    with patch("app.services.data_fabric.manager.DataFabricManager.query_catalog_item") as mock_q:
        mock_q.return_value = QueryResult(
            dataset_id=f"cat_{source_id}_default",
            features=[{"type": "Feature", "geometry": {"type": "Point", "coordinates": [100.0, 0.0]}, "properties": {"name": "Sample"}}],
            total_count=1,
        )

        mat_payload = {
            "session_id": "test_session_12345",
            "catalog_item_id": f"cat_{source_id}_default",
            "query_spec": {"limit": 5},
        }
        mat_res = client.post("/api/v1/data-fabric/materialize", json=mat_payload)
        assert mat_res.status_code == 200
        mat_data = mat_res.json()
        assert mat_data["success"] is True
        assert mat_data["ref_id"].startswith("ref:df-")
        assert mat_data["feature_count"] == 1

    # 8. Delete source
    del_res = client.delete(f"/api/v1/data-fabric/sources/{source_id}")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True


@pytest.mark.asyncio
async def test_mapspec_lifecycle_engine_data_fabric_source():
    session_id = "test_mapspec_df_session"

    df_layer = {
        "id": "layer_df_01",
        "name": "DataFabric Water Layer",
        "type": "fill",
        "source": "source_df_01",
        "paint": {"fill-color": "#0080ff", "fill-opacity": 0.7},
    }
    df_source_data = {
        "type": "data_fabric",
        "catalog_item_id": "cat_ds_test_water",
        "source_id": "ds_test",
        "lazy": True,
        "ref_id": "ref:df-water12345",
        "bbox": [-122.5, 37.7, -122.3, 37.9],
    }

    intent = UpsertLayerIntent(layer=df_layer, source_data=df_source_data)
    result = await mapspec_lifecycle_engine.apply_mutation(session_id, intent)

    assert result.is_error is False
    assert result.mapspec is not None
    assert "source_df_01" in result.mapspec["sources"]

    source_entry = result.mapspec["sources"]["source_df_01"]
    assert is_data_fabric_entry(source_entry) is True
    assert source_entry.get("type") == "data_fabric"
    assert source_entry.get("ref_id") == "ref:df-water12345"
