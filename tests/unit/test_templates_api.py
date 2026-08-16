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


# ============================================================================
# Issue #428: GET /templates double-applied the pagination offset (DB-level
# .offset + a second Python slice of the merged seed+DB list), had no ORDER BY,
# and counted only DB rows in `total`. These tests pin the corrected contract:
# sequential pages form a contiguous, gap-free, duplicate-free union of the
# merged catalog and has_more flips exactly at the end.
# ============================================================================

from datetime import datetime, timedelta, timezone

from app.schemas.template_schema import SEED_TEMPLATES

_SYM_PAYLOAD = {
    "mode": "single",
    "geometry": "Polygon",
    "style": {"fill_color": "#1d4ed8", "opacity": 0.85},
}


async def _insert_user_templates(
    count: int, kind: str = "symbology", creator: str = "user_123", prefix: str = "tmpl_user_pg"
):
    """Insert `count` user templates with strictly staggered created_at values.

    ids are {prefix}_000..N-1 with created_at ascending in i, so the
    deterministic (created_at DESC, id) order is i = N-1 .. 0.
    """
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with _TestSession() as s:
        for i in range(count):
            s.add(
                CartographyTemplate(
                    id=f"{prefix}_{i:03d}",
                    creator_id=creator,
                    org_id=None,
                    kind=kind,
                    name=f"分页模板 {i:03d}",
                    category=kind,
                    keywords=[],
                    description=f"pagination probe {i:03d}",
                    payload=dict(_SYM_PAYLOAD),
                    is_builtin=False,
                    version=1,
                    created_at=base + timedelta(minutes=i),
                    updated_at=base + timedelta(minutes=i),
                )
            )
        await s.commit()


@pytest.mark.asyncio
async def test_templates_list_pagination_contiguous_user_source(client):
    """Sequential offsets must yield every user template exactly once (#428).

    150 user rows, source=user (seeds dropped), limit=100: page 1 → 100 rows,
    page 2 → the remaining 50, page 3 → empty. Old code applied the offset at
    both the SQL layer and the merged-list re-slice, so offset=100 returned an
    empty page while 50 rows existed.
    """
    await _insert_user_templates(150)

    seen: list[str] = []
    page_specs = [(0, 100, True), (100, 50, False), (200, 0, False)]
    for offset, expect_len, expect_more in page_specs:
        res = await client.get(
            f"/api/v1/templates?source=user&summary=false&limit=100&offset={offset}",
            headers=user_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 150, f"total at offset={offset}"
        assert body["limit"] == 100
        assert body["offset"] == offset
        assert body["has_more"] is expect_more, f"has_more at offset={offset}"
        items = body["items"]
        assert len(items) == expect_len, f"page size at offset={offset}"
        seen.extend(t["id"] for t in items)

    expected = [f"tmpl_user_pg_{i:03d}" for i in range(149, -1, -1)]
    assert seen == expected


@pytest.mark.asyncio
async def test_templates_list_pagination_merged_catalog_seeds_first(client):
    """Default source: seeds lead the merged catalog, DB rows follow (#428).

    The test DB starts empty, so the merged catalog = all SEED_TEMPLATES plus
    the 10 inserted user rows (newest first). Paging at an awkward page size
    (13) must cover the union exactly once, contiguously across page bounds.
    """
    await _insert_user_templates(10)

    seed_count = len(SEED_TEMPLATES)
    total = seed_count + 10
    limit = 13

    seen: list[str] = []
    offset = 0
    pages = 0
    while True:
        res = await client.get(
            f"/api/v1/templates?summary=false&limit={limit}&offset={offset}",
            headers=user_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == total
        items = body["items"]
        seen.extend(t["id"] for t in items)
        pages += 1
        if not body["has_more"]:
            break
        offset += limit
        assert pages < 20, "pagination did not terminate"

    assert pages == 6  # 72 items / 13 per page
    expected = [s["id"] for s in SEED_TEMPLATES] + [
        f"tmpl_user_pg_{i:03d}" for i in range(9, -1, -1)
    ]
    assert seen == expected
    # Duplicate-free: a DB row carrying a seed id must not appear twice.
    assert len(seen) == len(set(seen)) == total


@pytest.mark.asyncio
async def test_templates_list_pagination_beyond_range(client):
    """An offset past the end returns an empty page with has_more=False."""
    await _insert_user_templates(3)
    res = await client.get(
        "/api/v1/templates?summary=false&limit=50&offset=10000",
        headers=user_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["items"] == []
    assert body["has_more"] is False
    assert body["total"] == len(SEED_TEMPLATES) + 3


@pytest.mark.asyncio
async def test_templates_list_pagination_single_page(client):
    """One page spanning the whole catalog: has_more flips off exactly."""
    await _insert_user_templates(5)
    res = await client.get(
        "/api/v1/templates?summary=false&limit=200&offset=0",
        headers=user_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == len(SEED_TEMPLATES) + 5
    assert len(body["items"]) == body["total"]
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_templates_list_pagination_seed_db_dedupe(client):
    """A DB row shadowing a seed id appears exactly once (seed version wins)."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with _TestSession() as s:
        s.add(
            CartographyTemplate(
                id=SEED_TEMPLATES[0]["id"],
                creator_id="user_123",
                org_id=None,
                kind=SEED_TEMPLATES[0]["kind"],
                name="DB copy of a seed",
                category=SEED_TEMPLATES[0]["kind"],
                keywords=[],
                description=None,
                payload=dict(_SYM_PAYLOAD),
                is_builtin=True,
                version=1,
                created_at=base,
                updated_at=base,
            )
        )
        await s.commit()

    res = await client.get(
        "/api/v1/templates?summary=false&limit=200", headers=user_headers
    )
    assert res.status_code == 200
    body = res.json()
    ids = [t["id"] for t in body["items"]]
    assert ids.count(SEED_TEMPLATES[0]["id"]) == 1
    # The seed representation is canonical (not the stale DB copy).
    first = next(t for t in body["items"] if t["id"] == SEED_TEMPLATES[0]["id"])
    assert first["name"] == SEED_TEMPLATES[0]["name"]
    assert body["total"] == len(SEED_TEMPLATES)


@pytest.mark.asyncio
async def test_templates_list_pagination_deterministic_order(client):
    """No ORDER BY made paging nondeterministic across requests (#428)."""
    await _insert_user_templates(40)
    orders = []
    for _ in range(2):
        res = await client.get(
            "/api/v1/templates?source=user&summary=false&limit=20&offset=0",
            headers=user_headers,
        )
        assert res.status_code == 200
        orders.append([t["id"] for t in res.json()["items"]])
    assert orders[0] == orders[1]
    # Newest-first ordering among DB rows.
    assert orders[0][0] == "tmpl_user_pg_039"


@pytest.mark.asyncio
async def test_templates_list_pagination_with_search(client):
    """q-filtered pages stay contiguous; total reflects the filtered catalog."""
    await _insert_user_templates(150)
    res = await client.get(
        "/api/v1/templates?source=user&summary=false&limit=100&offset=0&q=probe 14",
        headers=user_headers,
    )
    assert res.status_code == 200
    body = res.json()
    # ids 140-149 match "probe 14"; keyword search also matches names
    # ("分页模板 14x") via the shared description/name filter.
    assert body["total"] == 10
    assert len(body["items"]) == 10
    assert body["has_more"] is False
    ids = {t["id"] for t in body["items"]}
    assert ids == {f"tmpl_user_pg_{i:03d}" for i in range(140, 150)}


@pytest.mark.asyncio
async def test_templates_list_pagination_anonymous(client):
    """Anonymous listing is seeds+builtin DB rows only, still contiguous."""
    await _insert_user_templates(5, creator="user_123")
    res = await client.get("/api/v1/templates?summary=false&limit=200")
    assert res.status_code == 200
    body = res.json()
    ids = [t["id"] for t in body["items"]]
    # Anonymous scope: builtins only — user rows invisible, seeds present.
    assert body["total"] == len(SEED_TEMPLATES)
    assert not any(i.startswith("tmpl_user_pg_") for i in ids)
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_templates_list_pagination_kind_filter(client):
    """kind-scoped paging: total matches the filtered catalog, pages contiguous."""
    await _insert_user_templates(30, kind="symbology", prefix="tmpl_user_sym")
    await _insert_user_templates(20, kind="basemap", prefix="tmpl_user_bm")
    sym_seeds = [s for s in SEED_TEMPLATES if s.get("kind") == "symbology"]
    res = await client.get(
        "/api/v1/templates?kind=symbology&summary=false&limit=40",
        headers=user_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == len(sym_seeds) + 30
    assert len(body["items"]) == 40
    assert body["has_more"] is True
    # All items are symbology (seeds first, then user rows newest-first).
    assert all(t["kind"] == "symbology" for t in body["items"])


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
