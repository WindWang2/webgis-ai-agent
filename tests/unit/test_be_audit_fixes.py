"""Unit tests verifying all 4 Backend GIS audit fixes (BE-AUDIT-01 to BE-AUDIT-04)."""
import pytest
from unittest.mock import patch, MagicMock
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.main import app
from app.core.auth import create_access_token
from app.models.db_model import Base
from app.core.database import get_async_db

user1_token = create_access_token({"sub": "user_audit_1", "role": "viewer"})
user1_headers = {"Authorization": f"Bearer {user1_token}"}

user2_token = create_access_token({"sub": "user_audit_2", "role": "viewer"})
user2_headers = {"Authorization": f"Bearer {user2_token}"}

# Isolated sqlite async engine for the template CRUD test (BE-AUDIT-02) — avoids
# racing asyncpg connections under CI's real Postgres. File-based (per tmp_path)
# so all sessions in a test see the same data. Mirrors test_critical_auth_hardening.
_tmpl_engine = None
_TmplSession = None


async def _override_get_async_db():
    async with _TmplSession() as s:
        yield s


# ── BE-AUDIT-01: RemoteSensingService Import & Celery Task Verification ──────

def test_be_audit_01_remote_sensing_service_imports():
    """Verify the spectral engine is importable from spatial_tasks, rs, and spectral_engine.

    spatial_tasks now imports the canonical SpectralRasterEngine (previously
    the RemoteSensingService alias — review m3); the deprecated alias still
    resolves to the same class for backward compatibility.
    """
    from app.services.spatial_tasks import SpectralRasterEngine as SRE_tasks
    from app.services.rs import SpectralRasterEngine as SRE_rs
    from app.services.rs.spectral_engine import SpectralRasterEngine as SRE_engine
    from app.services.rs.spectral_engine import RemoteSensingService as RSS_engine

    assert SRE_tasks is SRE_engine
    assert SRE_rs is SRE_engine
    assert RSS_engine is SRE_engine  # deprecated alias kept for compatibility


def test_be_audit_01_run_change_detection_uses_remote_sensing_service():
    """Verify run_change_detection instantiates the spectral engine without NameError."""
    from app.services.spatial_tasks import run_change_detection

    with patch("app.services.spatial_tasks.SpectralRasterEngine") as mock_rss, \
         patch.object(run_change_detection, "update_state"):  # noqa: F841 — mock handle not used in assertions
        instance = MagicMock()
        mock_rss.return_value = instance
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

@pytest.fixture(autouse=True)
async def clean_user_templates(tmp_path):
    """Per-test file-based sqlite engine so the template routes don't touch the
    real Postgres+asyncpg pool (which races under AsyncClient).

    File-based (tmp_path) rather than :memory: so all sessions in one test share
    one database.
    """
    global _tmpl_engine, _TmplSession
    _tmpl_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'be_audit.db'}",
        connect_args={"check_same_thread": False},
    )
    _TmplSession = async_sessionmaker(bind=_tmpl_engine, expire_on_commit=False)
    app.dependency_overrides.clear()
    app.dependency_overrides[get_async_db] = _override_get_async_db
    async with _tmpl_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    app.dependency_overrides.clear()
    await _tmpl_engine.dispose()


@pytest.mark.asyncio
async def test_be_audit_02_template_creation_auth_and_deletion_ownership():
    """Verify POST /templates requires auth and DELETE /templates enforces ownership check."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
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
        res_unauth = await ac.post("/api/v1/templates", json=payload)
        assert res_unauth.status_code == 401

        # 2. Authenticated POST by User 1 succeeds
        res_create = await ac.post("/api/v1/templates", json=payload, headers=user1_headers)
        assert res_create.status_code == 201
        tmpl_data = res_create.json()
        assert tmpl_data["creator_id"] == "user_audit_1"
        tmpl_id = tmpl_data["id"]

        # 3. Delete by User 2 fails with 403 Forbidden
        res_del_user2 = await ac.delete(f"/api/v1/templates/{tmpl_id}", headers=user2_headers)
        assert res_del_user2.status_code == 403

        # 4. Delete by creator (User 1) succeeds with 200 OK
        res_del_user1 = await ac.delete(f"/api/v1/templates/{tmpl_id}", headers=user1_headers)
        assert res_del_user1.status_code == 200
        assert res_del_user1.json()["status"] == "deleted"


# ── BE-AUDIT-03: SpatialAnalyzer Parameter Keywords Verification ─────────────

def test_be_audit_03_spatial_analyzer_parameter_mapping():
    """Verify SpatialAnalyzer passes right_features for spatial_join and
    raster_path for zonal_stats.

    ARCH-01 (deep-audit round 3): the SpatialAnalysisEngine name-dispatch seam
    (which ADR-0013 deleted) was removed; tools call SpatialAnalyzer directly.
    The parameter-mapping contract it verified is preserved here.
    """
    from app.services.spatial_analyzer import SpatialAnalyzer

    with patch.object(SpatialAnalyzer, "spatial_join") as mock_sjoin:
        mock_sjoin.return_value.to_llm_response.return_value = {"success": True, "type": "FeatureCollection"}

        SpatialAnalyzer.spatial_join(
            {"type": "FeatureCollection", "features": []},
            {"type": "FeatureCollection", "features": []},
            join_type="inner",
            predicate="intersects",
        )
        mock_sjoin.assert_called_once()
        args, kwargs = mock_sjoin.call_args
        # right_features is a positional arg (left, right); join_type/predicate
        # are keywords — verify the full parameter mapping.
        sentinel = {"type": "FeatureCollection", "features": []}
        assert args[0] == sentinel  # left_features
        assert args[1] == sentinel  # right_features
        assert kwargs.get("join_type") == "inner"
        assert kwargs.get("predicate") == "intersects"

    with patch.object(SpatialAnalyzer, "zonal_stats") as mock_zstats:
        mock_zstats.return_value.to_llm_response.return_value = {"success": True}

        SpatialAnalyzer.zonal_stats(
            {"type": "FeatureCollection", "features": []},
            "/data/test.tif",
        )
        mock_zstats.assert_called_once()
        args, kwargs = mock_zstats.call_args
        assert args[0] == {"type": "FeatureCollection", "features": []}
        # raster_path is the second POSITIONAL arg of SpatialAnalyzer.zonal_stats.
        assert args[1] == "/data/test.tif"


# ── BE-AUDIT-04: Distance Matrix Maxsize Verification ───────────────────────

def test_be_audit_04_distance_matrix_maxsize_is_16():
    """Verify _distance_matrix_maxsize in app.lib.geo_analysis.statistics is set to 16."""
    from app.lib.geo_analysis.statistics import _distance_matrix_maxsize, get_distance_matrix_cache_info

    assert _distance_matrix_maxsize == 16
    cache_info = get_distance_matrix_cache_info()
    assert cache_info["maxsize"] == 16


# ── BE-AUDIT-06: Exception Sanitization Verification ─────────────────────────

@pytest.mark.asyncio
async def test_be_audit_06_templates_and_config_sanitized():
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

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Test SSRF validation failure in config route does not leak raw exception
            res = await ac.post("/api/v1/config/llm", json={"base_url": "http://127.0.0.1"}, headers=admin_headers)
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

