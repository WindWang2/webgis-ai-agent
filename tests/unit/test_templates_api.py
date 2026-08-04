"""Template CRUD API tests — async-client pattern to stay asyncpg-safe in CI.

Uses httpx.AsyncClient + an isolated sqlite async engine (overriding
get_async_db), mirroring the proven test_critical_auth_hardening pattern.
The prior TestClient + real-Postgres-asyncpg combination raced asyncpg's
loop-bound connections under TestClient's threadpool.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.main import app
from app.models.db_model import Base, CartographyTemplate
from app.core.database import get_async_db
from app.core.auth import create_access_token

user_token = create_access_token({"sub": "user_123", "role": "viewer"})
user_headers = {"Authorization": f"Bearer {user_token}"}

other_user_token = create_access_token({"sub": "user_456", "role": "viewer"})
other_user_headers = {"Authorization": f"Bearer {other_user_token}"}

# Engine/session created per-test in setup_db (file-based sqlite so all sessions
# in one test see the same data; :memory: is per-connection and would hide the
# POST from the GET).
_test_engine = None
_TestSession = None


async def _override_get_async_db():
    async with _TestSession() as s:
        yield s


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    """Create a file-based sqlite engine + tables, clear overrides/templates."""
    global _test_engine, _TestSession
    _test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'templates.db'}",
        connect_args={"check_same_thread": False},
    )
    _TestSession = async_sessionmaker(bind=_test_engine, expire_on_commit=False)
    app.dependency_overrides.clear()
    app.dependency_overrides[get_async_db] = _override_get_async_db
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _TestSession() as s:
        from sqlalchemy import delete
        await s.execute(delete(CartographyTemplate).where(not CartographyTemplate.is_builtin))
        await s.commit()
    yield
    app.dependency_overrides.clear()
    await _test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_user_template_unauthenticated_fails(client):
    """Test POST /templates without auth returns 401 Unauthorized."""
    req_data = {
        "name": "未认证模板",
        "kind": "symbology",
        "payload": {
            "mode": "single",
            "geometry": "Polygon",
            "style": {"fill_color": "#1d4ed8", "opacity": 0.85, "stroke_color": "#1e3a8a", "stroke_width": 2.0},
        },
    }
    response = await client.post("/api/v1/templates", json=req_data)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_user_template_success(client):
    """Test saving a new user symbology template via POST /api/v1/templates."""
    req_data = {
        "name": "自定义蓝色行政区",
        "kind": "symbology",
        "description": "用户自定义的单值蓝调填充",
        "keywords": ["用户", "自定义", "蓝色"],
        "payload": {
            "mode": "single",
            "geometry": "Polygon",
            "style": {
                "fill_color": "#1d4ed8",
                "opacity": 0.85,
                "stroke_color": "#1e3a8a",
                "stroke_width": 2.0,
            },
        },
    }

    response = await client.post("/api/v1/templates", json=req_data, headers=user_headers)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["name"] == "自定义蓝色行政区"
    assert data["kind"] == "symbology"
    assert data["is_builtin"] is False
    assert data["creator_id"] == "user_123"
    assert data["id"].startswith("tmpl_user_")


@pytest.mark.asyncio
async def test_create_user_template_invalid_payload(client):
    """Test creating template with invalid payload for kind fails with 422."""
    req_data = {
        "name": "无效图层",
        "kind": "symbology",
        "payload": {
            "mode": "single",
            # missing required fill_color
            "opacity": 0.5,
        },
    }

    response = await client.post("/api/v1/templates", json=req_data, headers=user_headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_user_template_appears_in_list_templates(client):
    """Test that created user template immediately appears in the GET /templates endpoint.

    NOTE: the AI-tool list_templates path queries the DB directly (not via the
    get_async_db dependency), so it can't see the sqlite-overridden test data.
    That cross-path coverage lives in test_tools_templates.py against the real DB.
    """
    req_data = {
        "name": "我的夜间大屏底图",
        "kind": "basemap",
        "keywords": ["大屏", "夜间"],
        "payload": {
            "providerId": "carto-dark-vec",
            "vectorStyleUrl": "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        },
    }
    post_res = await client.post("/api/v1/templates", json=req_data, headers=user_headers)
    assert post_res.status_code == 201

    # Check GET endpoint
    get_res = await client.get("/api/v1/templates?kind=basemap")
    assert get_res.status_code == 200
    templates_list = get_res.json()
    assert any(t["name"] == "我的夜间大屏底图" for t in templates_list)


@pytest.mark.asyncio
async def test_delete_built_in_template_forbidden(client):
    """Test deleting built-in template returns 403 Forbidden."""
    response = await client.delete("/api/v1/templates/tmpl_bm_positron", headers=user_headers)
    assert response.status_code == 403
    assert "built-in" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_user_template_forbidden_for_other_user(client):
    """Test that user B cannot delete template created by user A."""
    req_data = {
        "name": "用户A的模板",
        "kind": "layout",
        "payload": {
            "paperSize": "A4",
            "orientation": "landscape",
            "showLegend": True,
            "showNorthArrow": True,
            "showScaleBar": True,
            "showGrid": False,
        },
    }
    post_res = await client.post("/api/v1/templates", json=req_data, headers=user_headers)
    tmpl_id = post_res.json()["id"]

    del_res = await client.delete(f"/api/v1/templates/{tmpl_id}", headers=other_user_headers)
    assert del_res.status_code == 403
    assert "authorized" in del_res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_user_template_success(client):
    """Test deleting user-created template succeeds with 200 for the creator."""
    req_data = {
        "name": "临时模板",
        "kind": "layout",
        "payload": {
            "paperSize": "A4",
            "orientation": "landscape",
            "showLegend": True,
            "showNorthArrow": True,
            "showScaleBar": True,
            "showGrid": False,
        },
    }
    post_res = await client.post("/api/v1/templates", json=req_data, headers=user_headers)
    tmpl_id = post_res.json()["id"]

    del_res = await client.delete(f"/api/v1/templates/{tmpl_id}", headers=user_headers)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"
