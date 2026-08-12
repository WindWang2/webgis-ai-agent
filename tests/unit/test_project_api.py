"""
Unit & Integration tests for Project Workspace & Workflow APIs
"""
import pytest
from fastapi.testclient import TestClient
from app.core.database import Base, Engine
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=Engine)
    yield


def test_create_and_get_project():
    res = client.post("/api/v1/projects", json={
        "name": "Haidian GIS Analysis",
        "description": "Spatial buffer and overlay analysis",
    })
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Haidian GIS Analysis"
    proj_id = data["id"]

    # Get Project
    get_res = client.get(f"/api/v1/projects/{proj_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == proj_id

    # List Projects
    list_res = client.get("/api/v1/projects")
    assert list_res.status_code == 200
    assert any(p["id"] == proj_id for p in list_res.json()["items"])


def test_attach_and_detach_dataset():
    # Create Project
    p_res = client.post("/api/v1/projects", json={"name": "Chaoyang Urban Project"})
    proj_id = p_res.json()["id"]

    # Attach Dataset
    attach_res = client.post(f"/api/v1/projects/{proj_id}/datasets", json={
        "name": "Chaoyang_Buildings",
        "source_type": "upload",
        "source_ref": "upload_123",
        "crs": "EPSG:4326"
    })
    assert attach_res.status_code == 200
    dataset = attach_res.json()
    assert dataset["name"] == "Chaoyang_Buildings"
    ds_id = dataset["id"]

    # List Datasets
    ds_list = client.get(f"/api/v1/projects/{proj_id}/datasets")
    assert ds_list.status_code == 200
    assert len(ds_list.json()["items"]) == 1

    # Detach Dataset
    detach_res = client.delete(f"/api/v1/projects/{proj_id}/datasets/{ds_id}")
    assert detach_res.status_code == 200

    # Verify List Empty
    ds_list_after = client.get(f"/api/v1/projects/{proj_id}/datasets")
    assert len(ds_list_after.json()["items"]) == 0


def test_spatial_quality_and_repair_api():
    p_res = client.post("/api/v1/projects", json={"name": "Quality Audit Project"})
    proj_id = p_res.json()["id"]

    # Invalid geometry GeoJSON
    bad_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]]
                },
                "properties": {"name": "valid_poly"}
            }
        ]
    }

    # Audit Quality API
    audit_res = client.post(f"/api/v1/projects/{proj_id}/quality-audit", json={"geojson": bad_geojson})
    assert audit_res.status_code == 200
    audit_data = audit_res.json()
    assert "overall_status" in audit_data

    # Repair API
    repair_res = client.post(f"/api/v1/projects/{proj_id}/repair", json={
        "geojson": bad_geojson,
        "operations": ["make_valid", "remove_empty"]
    })
    assert repair_res.status_code == 200
    # Fetch-on-Demand: the repaired geometry is trimmed out of the inline
    # response (kept under the 50k tool_result cap); only a preview + count remain.
    repair_body = repair_res.json()
    assert "repaired_geojson_preview" in repair_body
    assert "feature_count" in repair_body
