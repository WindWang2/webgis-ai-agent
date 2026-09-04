"""
Unit tests for Data Fabric REST routes, manager service, and MapSpec integration
"""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal, Base, Engine
from app.schemas.data_fabric_schema import DataFabricHealth, QueryResult
from app.services.mapspec.lifecycle_engine import mapspec_lifecycle_engine, UpsertLayerIntent
from app.services.mapspec_source import is_data_fabric_entry


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=Engine)
    # Postgres（CI）上 init_db 按 audit #839 跳过 create_all（schema 归
    # Alembic），测试自管 schema 须显式建表 —— 与 test_618 / test_project_domain
    # 的 drop_all + create_all 惯例一致。
    Base.metadata.create_all(bind=Engine)
    # The create route persists owner_id = JWT sub into data_sources.owner_id,
    # which FK-references users(id). Postgres (CI) enforces the FK; sqlite
    # (local) does not — seed the caller's row so both environments agree.
    from app.models.db_model import User
    from datetime import datetime, timezone

    with SessionLocal() as db:
        if db.get(User, "df-user") is None:
            db.add(User(
                id="df-user",
                username="df",
                email="df-user@example.com",
                password_hash="scrypt$16384$8$1$00$00",
                role="editor",
                is_active=True,
                token_version=0,
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()


def test_data_fabric_rest_routes():
    client = TestClient(app)

    # create/probe/sync now require authentication (anonymous callers used to
    # create GLOBAL sources and trigger outbound probe/sync requests).
    from app.core.auth import create_access_token
    auth_token = create_access_token({"sub": "df-user", "username": "df", "role": "editor"})
    auth_headers = {"Authorization": f"Bearer {auth_token}"}

    # 1. List sources (read path stays anonymous-optional)
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
    }

    with patch("app.services.data_fabric.manager.DataFabricManager.probe_profile") as mock_probe, \
         patch("app.services.data_fabric.manager.DataFabricManager.sync_catalog") as mock_sync:
        mock_probe.return_value = DataFabricHealth(status="healthy", message="OK", latency_ms=12.5)
        mock_sync.return_value = []

        create_res = client.post("/api/v1/data-fabric/sources", json=create_payload, headers=auth_headers)
        assert create_res.status_code == 200
        create_data = create_res.json()
        assert create_data["success"] is True
        source_id = create_data["data_source"]["id"]
        assert source_id.startswith("ds_")

    # 3. Get single data source (owned by df-user -> authenticate)
    get_res = client.get(f"/api/v1/data-fabric/sources/{source_id}", headers=auth_headers)
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "Test OGC API Source"

    # 4. Probe data source health
    with patch("app.services.data_fabric.manager.DataFabricManager.probe_profile") as mock_probe:
        mock_probe.return_value = DataFabricHealth(status="healthy", message="OK", latency_ms=10.0)
        probe_res = client.post(f"/api/v1/data-fabric/sources/{source_id}/probe", headers=auth_headers)
        assert probe_res.status_code == 200
        assert probe_res.json()["status"] == "healthy"

    # 5. Sync catalog
    with patch("app.services.data_fabric.manager.DataFabricManager.sync_catalog") as mock_sync:
        mock_sync.return_value = []
        sync_res = client.post(f"/api/v1/data-fabric/sources/{source_id}/sync", headers=auth_headers)
        assert sync_res.status_code == 200
        assert "synced_count" in sync_res.json()

    # 6. List Spatial Catalog
    cat_res = client.get("/api/v1/data-fabric/catalog")
    assert cat_res.status_code == 200
    cat_data = cat_res.json()
    assert "total" in cat_data

    # 7. Materialize catalog item / query to session ref_id
    with SessionLocal() as db:
        from app.models.data_fabric import CatalogItemModel
        # Insert a fresh catalog row. Use db.add (not merge) — merge on a new
        # object with the data_source relationship set was resetting source_id to
        # None during autoflush, tripping the NOT NULL constraint. source_id is
        # set explicitly, so the relationship assignment is redundant here.
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
        db.add(item)
        db.commit()

    # V2（ADR-0094 §8）：REST 物化走 MaterializationService 单管线 —— seam
    # 从 query_catalog_item_async 换成 get_adapter（ref 前缀统一 data-fabric）。
    class _FakeAdapter:
        def query(self, dataset_id, spec):
            return QueryResult(
                dataset_id=f"cat_{source_id}_default",
                features=[{"type": "Feature", "geometry": {"type": "Point", "coordinates": [100.0, 0.0]}, "properties": {"name": "Sample"}}],
                total_count=1,
            )

    with patch("app.services.data_fabric.manager.DataFabricManager.get_adapter", staticmethod(lambda profile: _FakeAdapter())):
        mat_payload = {
            "session_id": "test_session_12345",
            "catalog_item_id": f"cat_{source_id}_default",
            "query_spec": {"limit": 5},
        }
        mat_res = client.post("/api/v1/data-fabric/materialize", json=mat_payload, headers=auth_headers)
        assert mat_res.status_code == 200, mat_res.text
        mat_data = mat_res.json()
        assert mat_data["success"] is True
        assert mat_data["ref_id"].startswith("ref:data-fabric-"), (
            "V2 统一 ref 前缀（此前 REST df / 工具 data-fabric 双前缀）"
        )
        assert mat_data["feature_count"] == 1

    # 8. Delete source
    del_res = client.delete(f"/api/v1/data-fabric/sources/{source_id}", headers=auth_headers)
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


# ─── #565 review: post-close ORM serialization (real SessionLocal) ─────────
# The offloaded workers use their own SessionLocal() (expire_on_commit=True);
# managers that COMMIT inside the worker leave returned ORM rows expired, so
# the route must serialize them into plain dicts INSIDE the worker. Reading
# them after the worker session closes raised DetachedInstanceError, which the
# routes' catch-all misreported as HTTP 400 ON SUCCESS. These tests run the
# REAL manager flow (real create/sync code + real commit) against the real
# SQLite DB — only the network adapter seam (get_adapter) is faked.


class _FakeSyncAdapter:
    """Adapter stand-in for the network seam: the manager's real
    probe/capabilities/list_datasets/describe flow runs without HTTP."""

    def health(self):
        return DataFabricHealth(status="healthy", message="OK", latency_ms=1.0)

    def capabilities(self):
        return ["catalog"]

    def list_datasets(self):
        return [{"id": "roads", "title": "Roads"}]

    def describe(self, dataset_id: str):
        from app.schemas.data_fabric_schema import DatasetDescriptor
        return DatasetDescriptor(
            id=dataset_id,
            title="Roads",
            source_type="ogc_api",
            geometry_type="LineString",
        )


def _df_auth_headers():
    from app.core.auth import create_access_token
    token = create_access_token({"sub": "df-user", "username": "df", "role": "editor"})
    return {"Authorization": f"Bearer {token}"}


def test_create_data_source_2xx_with_real_commit():
    """create_data_source commits inside the worker (create + auto catalog
    sync) — the response must still be 200 with the row serialized inside the
    worker, not a 400 from DetachedInstanceError on success."""
    client = TestClient(app)
    headers = _df_auth_headers()
    payload = {
        "name": "Real-Commit Source",
        "source_type": "ogc_api",
        "endpoint_url": "https://example.com/ogc/collections",
        "options": {},
    }
    with patch(
        "app.services.data_fabric.manager.DataFabricManager.get_adapter",
        staticmethod(lambda profile: _FakeSyncAdapter()),
    ):
        res = client.post("/api/v1/data-fabric/sources", json=payload, headers=headers)

    assert res.status_code == 200, f"expected 200, got {res.status_code}: {res.text}"
    body = res.json()
    assert body["success"] is True
    assert body["data_source"]["id"].startswith("ds_")
    assert body["data_source"]["status"] == "healthy"

    # The row AND its auto-synced catalog item were really committed by the
    # worker (real SessionLocal + real manager flow, only the adapter faked).
    from app.models.data_fabric import CatalogItemModel, DataSourceModel

    with SessionLocal() as db:
        row = db.get(DataSourceModel, body["data_source"]["id"])
        assert row is not None, "create worker did not commit the source row"
        assert row.status == "healthy"
        assert (
            db.query(CatalogItemModel)
            .filter(CatalogItemModel.source_id == row.id)
            .count()
        ) >= 1, "auto catalog sync did not commit an item"


def test_sync_data_source_catalog_2xx_with_existing_dataset():
    """sync_catalog commits before returning — the route must serialize the
    items inside the worker (post-close reads previously → 400 on success)."""
    client = TestClient(app)
    headers = _df_auth_headers()
    payload = {
        "name": "Re-Sync Source",
        "source_type": "ogc_api",
        "endpoint_url": "https://example.com/ogc/collections",
        "options": {},
    }
    with patch(
        "app.services.data_fabric.manager.DataFabricManager.get_adapter",
        staticmethod(lambda profile: _FakeSyncAdapter()),
    ):
        create_res = client.post("/api/v1/data-fabric/sources", json=payload, headers=headers)
        assert create_res.status_code == 200, create_res.text
        source_id = create_res.json()["data_source"]["id"]

        # Re-sync over the auto-synced catalog: ≥1 existing dataset, real commit.
        sync_res = client.post(f"/api/v1/data-fabric/sources/{source_id}/sync", headers=headers)

    assert sync_res.status_code == 200, (
        f"expected 200, got {sync_res.status_code}: {sync_res.text}"
    )
    body = sync_res.json()
    assert body["success"] is True
    assert body["synced_count"] >= 1
    assert body["items"][0]["id"].startswith("cat_")


# ─── #767: unsupported source types rejected loudly; demo sources labeled ───


def test_create_data_source_unsupported_type_is_4xx_767():
    """#767: an unregistered source type (csv) must be rejected with a typed
    4xx BEFORE any probe/persist — previously the probe/capabilities swallow
    persisted an 'unreachable' row and returned success."""
    client = TestClient(app)
    headers = _df_auth_headers()
    res = client.post(
        "/api/v1/data-fabric/sources",
        json={
            "name": "CSV Source",
            "source_type": "csv",
            "endpoint_url": "https://example.com/data.csv",
            "options": {},
        },
        headers=headers,
    )
    assert res.status_code == 400
    body = res.json()
    assert body["success"] is False
    assert body["error_type"] == "UNSUPPORTED_SOURCE"
    assert "supported" in body["details"]

    # Nothing was persisted.
    from app.models.data_fabric import DataSourceModel

    with SessionLocal() as db:
        assert (
            db.query(DataSourceModel)
            .filter(DataSourceModel.name == "CSV Source")
            .count()
        ) == 0


def test_create_data_source_geojson_rejected_767():
    """#767: source_type='geojson' no longer routes to the demo adapter that
    fabricates synthetic Beijing features for a real remote URL."""
    client = TestClient(app)
    headers = _df_auth_headers()
    res = client.post(
        "/api/v1/data-fabric/sources",
        json={
            "name": "Remote GeoJSON",
            "source_type": "geojson",
            "endpoint_url": "https://example.org/parks.geojson",
            "options": {},
        },
        headers=headers,
    )
    assert res.status_code == 400
    assert res.json()["error_type"] == "UNSUPPORTED_SOURCE"


def test_create_data_source_demo_labeled_767():
    """#767: when the demo adapter IS used (explicit 'generic'), the create
    response labels it is_demo so synthetic data is never mistaken for remote
    data."""
    client = TestClient(app)
    headers = _df_auth_headers()
    res = client.post(
        "/api/v1/data-fabric/sources",
        json={
            "name": "Demo Sample Source",
            "source_type": "generic",
            "endpoint_url": "https://example.com/anything",
            "options": {},
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["data_source"]["is_demo"] is True
