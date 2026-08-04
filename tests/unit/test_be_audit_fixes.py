"""Unit tests verifying all 4 Backend GIS audit fixes (BE-AUDIT-01 to BE-AUDIT-04)."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.core.auth import create_access_token
from app.models.db_model import CartographyTemplate
from app.core.database import SessionLocal, Base, Engine, AsyncSessionLocal

client = TestClient(app)

user1_token = create_access_token({"sub": "user_audit_1", "role": "viewer"})
user1_headers = {"Authorization": f"Bearer {user1_token}"}

user2_token = create_access_token({"sub": "user_audit_2", "role": "viewer"})
user2_headers = {"Authorization": f"Bearer {user2_token}"}


# ── BE-AUDIT-01: RemoteSensingService Import & Celery Task Verification ──────

def test_be_audit_01_remote_sensing_service_imports():
    """Verify RemoteSensingService can be imported from spatial_tasks, rs, and spectral_engine."""
    from app.services.spatial_tasks import RemoteSensingService as RSS_tasks
    from app.services.rs import RemoteSensingService as RSS_rs
    from app.services.rs.spectral_engine import RemoteSensingService as RSS_engine

    assert RSS_tasks is RSS_engine
    assert RSS_rs is RSS_engine


def test_be_audit_01_run_change_detection_uses_remote_sensing_service():
    """Verify run_change_detection instantiates RemoteSensingService without NameError."""
    from app.services.spatial_tasks import run_change_detection

    with patch("app.services.spatial_tasks.RemoteSensingService") as mock_rss, \
         patch.object(run_change_detection, "update_state") as mock_update_state:
        instance = MagicMock()
        mock_rss.return_value = instance

        # Mock compute_vegetation_index as async method
        async def mock_compute_vi(bbox, date_from, date_to, index_type):
            return {"stats": {"mean": 0.5}, "cloud_cover": 5.0}

        instance.compute_vegetation_index.side_effect = mock_compute_vi

        res = run_change_detection(
            bbox=[116.0, 39.0, 116.1, 39.1],
            t1_from="2023-01-01",
            t1_to="2023-01-10",
            t2_from="2023-06-01",
            t2_to="2023-06-10",
            index_type="ndvi",
            change_threshold=0.1,
        )
        assert res["success"] is True
        assert res["data"]["change"]["category"] == "no_change"


# ── BE-AUDIT-02: Template CRUD Authentication & Ownership Check ─────────────

async def _purge_user_templates():
    """Delete non-builtin templates via the async session (same pool as the routes).

    Avoids racing asyncpg connections under CI's real Postgres. Falls back to
    the sync session when no async driver is configured (local SQLite).
    """
    if AsyncSessionLocal is None:
        db = SessionLocal()
        try:
            db.query(CartographyTemplate).filter(CartographyTemplate.is_builtin == False).delete()
            db.commit()
        finally:
            db.close()
        return
    from sqlalchemy import delete as _delete
    async with AsyncSessionLocal() as db:
        await db.execute(_delete(CartographyTemplate).where(CartographyTemplate.is_builtin == False))
        await db.commit()


@pytest.fixture(autouse=True)
async def clean_user_templates():
    """Clean up non-builtin templates and clear dependency overrides before and after test."""
    app.dependency_overrides.clear()
    Base.metadata.create_all(Engine)
    await _purge_user_templates()
    yield
    app.dependency_overrides.clear()
    await _purge_user_templates()


def test_be_audit_02_template_creation_auth_and_deletion_ownership():
    """Verify POST /templates requires auth and DELETE /templates enforces ownership check."""
    # 1. Unauthenticated POST fails 401
    payload = {
        "name": "Audit Test Template",
        "kind": "symbology",
        "payload": {
            "mode": "single",
            "geometry": "Polygon",
            "style": {"fill_color": "#ff0000", "opacity": 1.0, "stroke_color": "#000000", "stroke_width": 1.0},
        },
    }
    res_unauth = client.post("/api/v1/templates", json=payload)
    assert res_unauth.status_code == 401

    # 2. Authenticated POST by User 1 succeeds
    res_create = client.post("/api/v1/templates", json=payload, headers=user1_headers)
    assert res_create.status_code == 201
    tmpl_data = res_create.json()
    assert tmpl_data["creator_id"] == "user_audit_1"
    tmpl_id = tmpl_data["id"]

    # 3. Delete by User 2 fails with 403 Forbidden
    res_del_user2 = client.delete(f"/api/v1/templates/{tmpl_id}", headers=user2_headers)
    assert res_del_user2.status_code == 403

    # 4. Delete by creator (User 1) succeeds with 200 OK
    res_del_user1 = client.delete(f"/api/v1/templates/{tmpl_id}", headers=user1_headers)
    assert res_del_user1.status_code == 200
    assert res_del_user1.json()["status"] == "deleted"


# ── BE-AUDIT-03: SpatialAnalysisEngine Parameter Keywords Verification ────────

def test_be_audit_03_spatial_analysis_engine_parameter_mapping():
    """Verify SpatialAnalysisEngine passes right_features for spatial_join and raster_path for zonal_stats."""
    from app.services.spatial_analyzer import spatial_analysis_engine, SpatialAnalyzer

    with patch.object(SpatialAnalyzer, "spatial_join") as mock_sjoin:
        mock_sjoin.return_value.to_llm_response.return_value = {"success": True, "type": "FeatureCollection"}
        
        spatial_analysis_engine.spatial_join(
            target_features={"type": "FeatureCollection", "features": []},
            join_features={"type": "FeatureCollection", "features": []},
            how="inner",
            predicate="intersects",
        )
        mock_sjoin.assert_called_once()
        _, kwargs = mock_sjoin.call_args
        assert "right_features" in kwargs
        assert kwargs["join_type"] == "inner"
        assert kwargs["predicate"] == "intersects"

    with patch.object(SpatialAnalyzer, "zonal_stats") as mock_zstats:
        mock_zstats.return_value.to_llm_response.return_value = {"success": True}

        spatial_analysis_engine.zonal_stats(
            raster_data="/data/test.tif",
            polygon_features={"type": "FeatureCollection", "features": []},
        )
        mock_zstats.assert_called_once()
        args, kwargs = mock_zstats.call_args
        assert args[0] == {"type": "FeatureCollection", "features": []}
        assert kwargs.get("raster_path") == "/data/test.tif"


# ── BE-AUDIT-04: Distance Matrix Maxsize Verification ───────────────────────

def test_be_audit_04_distance_matrix_maxsize_is_16():
    """Verify _distance_matrix_maxsize in app.lib.geo_analysis.statistics is set to 16."""
    from app.lib.geo_analysis.statistics import _distance_matrix_maxsize, get_distance_matrix_cache_info

    assert _distance_matrix_maxsize == 16
    cache_info = get_distance_matrix_cache_info()
    assert cache_info["maxsize"] == 16


# ── BE-AUDIT-06: Exception Sanitization Verification ─────────────────────────

def test_be_audit_06_templates_and_config_sanitized():
    """Verify raw exception strings are sanitized in templates and config routes."""
    from app.core.auth import get_current_user, get_current_user_with_version
    from app.api.routes import chat as chat_routes
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    chat_routes.registry = ToolRegistry()
    chat_routes.engine = ChatEngine(chat_routes.registry)
    app.dependency_overrides[get_current_user_with_version] = get_current_user

    try:
        admin_token = create_access_token({"sub": "admin_audit", "username": "admin", "role": "admin"})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Test SSRF validation failure in config route does not leak raw exception
        res = client.post("/api/v1/config/llm", json={"base_url": "http://127.0.0.1"}, headers=admin_headers)
        assert res.status_code == 400
        assert "127.0.0.1" not in res.json()["detail"]
        assert "base_url 校验失败" in res.json()["detail"]
    finally:
        chat_routes.engine = None
        chat_routes.registry = None


# ── BE-AUDIT-07: AsyncSession Dependency Verification ────────────────────────

def test_be_audit_07_templates_route_uses_async_session():
    """Verify templates route endpoints are coroutines and use AsyncSession via Depends(get_async_db)."""
    import inspect
    from app.api.routes.templates import list_templates, create_template, delete_template
    from app.core.database import get_async_db

    assert inspect.iscoroutinefunction(list_templates)
    assert inspect.iscoroutinefunction(create_template)
    assert inspect.iscoroutinefunction(delete_template)

    # Inspect parameter dependencies
    for func in (list_templates, create_template, delete_template):
        sig = inspect.signature(func)
        assert "db" in sig.parameters
        param = sig.parameters["db"]
        assert param.default.dependency == get_async_db


# ── BE-AUDIT-08: GeoJSON Geometry Structure Validation Verification ──────────

@pytest.mark.asyncio
async def test_be_audit_08_geojson_structure_validation_helper():
    """Verify validate_geojson_structure helper detects malformed GeoJSON and tool dispatch returns VALIDATION_ERROR."""
    from app.tools.registry import validate_geojson_structure, ToolRegistry, tool

    # 1. Valid GeoJSON structures pass cleanly
    valid_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                "properties": {"name": "Beijing"},
            }
        ],
    }
    validate_geojson_structure(valid_fc)  # should not raise

    # 2. Malformed FeatureCollection (missing features)
    with pytest.raises(ValueError, match="FeatureCollection 缺少必需的 'features'"):
        validate_geojson_structure({"type": "FeatureCollection"})

    # 3. Malformed Geometry (missing coordinates)
    with pytest.raises(ValueError, match="Geometry 'Point' 缺少必需的 'coordinates'"):
        validate_geojson_structure({"type": "Point"})

    # 4. Malformed coordinates (not a list)
    with pytest.raises(ValueError, match="coordinates' 字段必须为列表或元组"):
        validate_geojson_structure({"type": "Point", "coordinates": "invalid"})

    # 5. Integration check via ToolRegistry dispatch
    reg = ToolRegistry()

    @tool(reg, name="dummy_spatial", description="Dummy spatial tool")
    def dummy_spatial(data: dict) -> dict:
        return {"status": "ok"}

    res = await reg.dispatch("dummy_spatial", {"data": {"type": "FeatureCollection"}})
    assert res["success"] is False
    assert res["code"] == "VALIDATION_ERROR"
    assert "FeatureCollection" in res["message"]

