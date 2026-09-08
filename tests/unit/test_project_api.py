"""
Unit & Integration tests for Project Workspace & Workflow APIs
"""
import pytest
from fastapi.testclient import TestClient
from app.core.database import Base, Engine
from app.main import app

client = TestClient(app)


def _auth_headers():
    """Project write endpoints require authentication (state-changing routes
    must not be anonymous: anonymous creates would yield ownerless projects
    writable by everyone)."""
    from app.core.auth import create_access_token
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": "proj-owner", "username": "proj-owner", "role": "viewer"})}


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=Engine)
    # CI 跑真 Postgres：projects.owner_id 有外键约束，必须先落 users 行，
    # 否则令牌里的 "proj-owner" 违反 projects_owner_id_fkey（SQLite 不查所以本地过）。
    # merge 保证幂等，重复进 fixture 不会撞唯一主键。
    from app.core.database import SessionLocal
    from app.models.db_model import User
    with SessionLocal() as db:
        db.merge(User(id="proj-owner", username="proj-owner", email="proj-owner@example.com",
                      password_hash="not-a-real-hash", role="viewer", is_active=True))
        db.commit()
    yield


def test_create_and_get_project():
    res = client.post("/api/v1/projects", json={
        "name": "Haidian GIS Analysis",
        "description": "Spatial buffer and overlay analysis",
    }, headers=_auth_headers())
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Haidian GIS Analysis"
    proj_id = data["id"]

    # Get Project（owned 项目仅 owner 可见）
    get_res = client.get(f"/api/v1/projects/{proj_id}", headers=_auth_headers())
    assert get_res.status_code == 200
    assert get_res.json()["id"] == proj_id

    # List Projects —— 分页形状 {items, total, ...}
    list_res = client.get("/api/v1/projects", headers=_auth_headers())
    assert list_res.status_code == 200
    assert any(p["id"] == proj_id for p in list_res.json()["items"])


def test_attach_and_detach_dataset():
    # Create Project
    p_res = client.post("/api/v1/projects", json={"name": "Chaoyang Urban Project"},
                        headers=_auth_headers())
    proj_id = p_res.json()["id"]

    # Attach Dataset
    attach_res = client.post(f"/api/v1/projects/{proj_id}/datasets", json={
        "name": "Chaoyang_Buildings",
        "source_type": "upload",
        "source_ref": "upload_123",
        "crs": "EPSG:4326"
    }, headers=_auth_headers())
    assert attach_res.status_code == 200
    dataset = attach_res.json()
    assert dataset["name"] == "Chaoyang_Buildings"
    ds_id = dataset["id"]

    # List Datasets —— 分页形状 {items, total, ...}
    ds_list = client.get(f"/api/v1/projects/{proj_id}/datasets",
                         headers=_auth_headers())
    assert ds_list.status_code == 200
    assert len(ds_list.json()["items"]) == 1

    # Detach Dataset
    detach_res = client.delete(f"/api/v1/projects/{proj_id}/datasets/{ds_id}",
                               headers=_auth_headers())
    assert detach_res.status_code == 200

    # Verify List Empty
    ds_list_after = client.get(f"/api/v1/projects/{proj_id}/datasets",
                               headers=_auth_headers())
    assert len(ds_list_after.json()["items"]) == 0


def test_spatial_quality_and_repair_api():
    p_res = client.post("/api/v1/projects", json={"name": "Quality Audit Project"},
                        headers=_auth_headers())
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
    audit_res = client.post(f"/api/v1/projects/{proj_id}/quality-audit",
                            json={"geojson": bad_geojson},
                            headers=_auth_headers())
    assert audit_res.status_code == 200
    audit_data = audit_res.json()
    assert "overall_status" in audit_data

    # Repair API
    repair_res = client.post(f"/api/v1/projects/{proj_id}/repair", json={
        "geojson": bad_geojson,
        "operations": ["make_valid", "remove_empty"]
    }, headers=_auth_headers())
    assert repair_res.status_code == 200
    # Fetch-on-Demand: the repaired geometry is trimmed out of the inline
    # response (kept under the 50k tool_result cap); only a preview + count remain.
    repair_body = repair_res.json()
    assert "repaired_geojson_preview" in repair_body
    assert "feature_count" in repair_body


def test_repair_session_ownership_guard():
    """review round-1 SEC CRITICAL-1: ``POST /projects/{id}/repair`` 的
    session_id 是跨租户写原语（execute_repair → session store）。守卫契约：

    - 匿名（无 Authorization）→ 401（写路径须认证）；
    - 外来 session（属于其他用户）→ 404（不泄露存在性，SEC-08 同款）；
    - 自己的 session → 200，修复正常落账。
    """
    from app.core.database import SessionLocal
    from app.models.db_model import Conversation, User

    p_res = client.post("/api/v1/projects", json={"name": "Repair Guard"},
                        headers=_auth_headers())
    assert p_res.status_code == 201
    proj_id = p_res.json()["id"]

    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [
                [[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]]},
            "properties": {"name": "poly"},
        }],
    }

    with SessionLocal() as db:
        db.merge(User(id="repair-stranger", username="repair-stranger",
                      email="repair-stranger@example.com",
                      password_hash="x", role="viewer", is_active=True))
        # merge = upsert：对共享 dev DB 幂等（重跑不撞 conversations.id 唯一约束）
        db.merge(Conversation(id="sess-repair-own", user_id="proj-owner"))
        db.merge(Conversation(id="sess-repair-foreign",
                              user_id="repair-stranger"))
        db.commit()

    # 匿名 → 401
    anon = client.post(f"/api/v1/projects/{proj_id}/repair", json={
        "geojson": geojson, "session_id": "sess-repair-own"})
    assert anon.status_code == 401

    # 外来 session → 404（存在性不泄露）
    foreign = client.post(f"/api/v1/projects/{proj_id}/repair", json={
        "geojson": geojson, "session_id": "sess-repair-foreign"},
        headers=_auth_headers())
    assert foreign.status_code == 404

    # 不存在的 session 同样 404（与无权不可区分）
    missing = client.post(f"/api/v1/projects/{proj_id}/repair", json={
        "geojson": geojson, "session_id": "sess-repair-does-not-exist"},
        headers=_auth_headers())
    assert missing.status_code == 404

    # 自己的 session → 200
    own = client.post(f"/api/v1/projects/{proj_id}/repair", json={
        "geojson": geojson, "session_id": "sess-repair-own"},
        headers=_auth_headers())
    assert own.status_code == 200
    assert own.json()["lineage_status"] in ("recorded", "skipped", "dataset_not_found")
