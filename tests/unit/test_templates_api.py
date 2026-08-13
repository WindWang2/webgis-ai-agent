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


async def _purge_non_builtin_templates(session):
    """Delete leftover non-builtin templates so each test starts from a clean table.

    Uses is_(False) rather than `not`: Python's `not` on a SQLAlchemy Column
    object evaluates to the plain bool False, so `where(not col)` compiles to
    WHERE false and silently deletes nothing (E712 regression from PR #303).
    """
    from sqlalchemy import delete
    await session.execute(
        delete(CartographyTemplate).where(CartographyTemplate.is_builtin.is_(False))
    )
    await session.commit()


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
        await _purge_non_builtin_templates(s)
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

    # Check GET endpoint —— 分页形状 {items, total, ...}
    # List is scoped to the caller: unauthenticated gallery is builtins only.
    get_res = await client.get("/api/v1/templates?kind=basemap", headers=user_headers)
    assert get_res.status_code == 200
    templates_list = get_res.json()["items"]
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


@pytest.mark.asyncio
async def test_setup_purge_deletes_non_builtin_templates():
    """Regression: the setup purge deletes non-builtin rows.

    PR #303's lint fix rewrote `is_builtin == False` as `not is_builtin`;
    Python `not` on a Column object is the plain bool False, so the delete
    compiled to WHERE false and cleaned nothing.
    """
    from sqlalchemy import select

    # Insert a non-builtin template through the same DB session path the
    # fixture uses to set up test state.
    async with _TestSession() as s:
        s.add(
            CartographyTemplate(
                id="tmpl_user_cleanup_probe",
                name="清理探针模板",
                kind="symbology",
                payload={"mode": "single"},
                is_builtin=False,
            )
        )
        await s.commit()

    # Sanity: the probe row exists before cleanup runs.
    async with _TestSession() as s:
        probe = (
            await s.execute(
                select(CartographyTemplate).where(
                    CartographyTemplate.id == "tmpl_user_cleanup_probe"
                )
            )
        ).scalar_one_or_none()
        assert probe is not None

    # Run the exact cleanup the setup_db fixture runs.
    async with _TestSession() as s:
        await _purge_non_builtin_templates(s)

    # The non-builtin row must be gone from the DB.
    async with _TestSession() as s:
        probe = (
            await s.execute(
                select(CartographyTemplate).where(
                    CartographyTemplate.id == "tmpl_user_cleanup_probe"
                )
            )
        ).scalar_one_or_none()
        assert probe is None


_LAYOUT_PAYLOAD = {
    "paperSize": "A4",
    "orientation": "landscape",
    "showLegend": True,
    "showNorthArrow": True,
    "showScaleBar": True,
    "showGrid": False,
}


@pytest.mark.asyncio
async def test_unauthenticated_list_hides_user_templates(client):
    """Anonymous gallery must not enumerate another tenant's saved templates."""
    post_res = await client.post(
        "/api/v1/templates",
        json={"name": "他人模板", "kind": "layout", "payload": _LAYOUT_PAYLOAD},
        headers=user_headers,
    )
    assert post_res.status_code == 201
    tmpl_id = post_res.json()["id"]

    anon = await client.get("/api/v1/templates?source=user&summary=false")
    assert anon.status_code == 200
    names = {t["name"] for t in anon.json()["items"]}
    assert "他人模板" not in names

    other = await client.get("/api/v1/templates?source=user&summary=false", headers=other_user_headers)
    assert other.status_code == 200
    assert "他人模板" not in {t["name"] for t in other.json()["items"]}

    owner = await client.get("/api/v1/templates?source=user&summary=false", headers=user_headers)
    assert any(t["name"] == "他人模板" for t in owner.json()["items"])

    forbidden = await client.get(f"/api/v1/templates/{tmpl_id}", headers=other_user_headers)
    assert forbidden.status_code == 404
    allowed = await client.get(f"/api/v1/templates/{tmpl_id}", headers=user_headers)
    assert allowed.status_code == 200
    assert allowed.json()["name"] == "他人模板"
