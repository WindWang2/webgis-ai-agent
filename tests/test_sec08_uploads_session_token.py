"""#1109 / SEC-08: uploads single-item endpoints must forward X-Session-Token.

Mirrors tests/test_sec08_session_owner_token.py matrix for:
  GET    /api/v1/uploads/{id}
  GET    /api/v1/uploads/{id}/geojson
  DELETE /api/v1/uploads/{id}

Coverage:
  - SEC-08 anon session (owner_token set): no/wrong token → 404; correct → 200
  - Legacy NULL/NULL: #1109 fail-closed (enumerable IDOR closed)
  - Authenticated user-bound session: owner OK, other user 404 (token irrelevant)
"""
import os
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-sec08-uploads-32charsxx")
os.environ.setdefault("ENV", "development")

from app.models.db_model import Base, Conversation, User  # noqa: E402
from app.models.upload import UploadRecord  # noqa: E402
from app.core.database import get_async_db  # noqa: E402
from app.core.auth import get_current_user, hash_password  # noqa: E402
from app.tools import _utils  # noqa: E402
from app.api.routes import upload as upload_routes  # noqa: E402
from app.services.history_service_async import AsyncHistoryService  # noqa: E402


_MOCK_USER = {"user_id": "sec08-uploads-user", "role": "viewer"}


@pytest_asyncio.fixture
async def app_and_db(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'sec08_uploads.db'}"
    test_engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    test_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_async_db():
        async with test_session() as s:
            yield s

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def override_async_db_session():
        async with test_session() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(_utils, "async_db_session", override_async_db_session)
    monkeypatch.setattr(
        "app.api.routes.upload.async_db_session", override_async_db_session
    )
    monkeypatch.setattr(upload_routes.settings, "DATA_DIR", str(tmp_path))

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(upload_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
    app.dependency_overrides[get_current_user] = lambda: _MOCK_USER
    try:
        yield app, test_session, tmp_path
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def client(app_and_db):
    app, _, _ = app_and_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db(app_and_db):
    _, session, _ = app_and_db
    async with session() as s:
        yield s


def _seed_upload(session_id: str, tmp_path: Path, name: str = "data.geojson") -> UploadRecord:
    upload_dir = tmp_path / "uploads" / f"u-{session_id[:8]}"
    upload_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = upload_dir / name
    geojson_path.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature",'
        '"geometry":{"type":"Point","coordinates":[0,0]},"properties":{}}]}'
    )
    return UploadRecord(
        filename=str(geojson_path),
        original_name=name,
        file_type="vector",
        format="geojson",
        crs="EPSG:4326",
        geometry_type="Point",
        feature_count=1,
        bbox=[0.0, 0.0, 0.0, 0.0],
        file_size=geojson_path.stat().st_size,
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_upload_routes_forward_owner_token():
    """Static contract: single-item endpoints declare + forward X-Session-Token."""
    import inspect

    for fn in (
        upload_routes.get_upload,
        upload_routes.get_upload_geojson,
        upload_routes.delete_upload,
        upload_routes.list_uploads,
    ):
        src = inspect.getsource(fn)
        assert 'alias="X-Session-Token"' in src
        assert "owner_token=owner_token" in src
    helper = inspect.getsource(upload_routes._verify_session_owner)
    assert "owner_token=owner_token" in helper


@pytest.mark.asyncio
async def test_sec08_anon_upload_404_without_token(client, db, app_and_db):
    """SEC-08 anon upload: no X-Session-Token → 404 on GET/DELETE/geojson."""
    _, _, tmp_path = app_and_db
    async with db as session:
        conv = await AsyncHistoryService(session).get_or_create_conversation(
            "sec08-up-anon-1", user_id=None
        )
        assert conv.owner_token
        rec = _seed_upload(conv.id, tmp_path)
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        uid = rec.id

    for method, path in (
        ("get", f"/api/v1/uploads/{uid}"),
        ("get", f"/api/v1/uploads/{uid}/geojson"),
        ("delete", f"/api/v1/uploads/{uid}"),
    ):
        resp = await getattr(client, method)(path)
        assert resp.status_code == 404, (method, path, resp.status_code, resp.text)


@pytest.mark.asyncio
async def test_sec08_anon_upload_404_with_wrong_token(client, db, app_and_db):
    """SEC-08 anon upload: wrong X-Session-Token → 404."""
    _, _, tmp_path = app_and_db
    async with db as session:
        conv = await AsyncHistoryService(session).get_or_create_conversation(
            "sec08-up-anon-2", user_id=None
        )
        rec = _seed_upload(conv.id, tmp_path)
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        uid = rec.id

    headers = {"X-Session-Token": "totally-wrong"}
    for method, path in (
        ("get", f"/api/v1/uploads/{uid}"),
        ("get", f"/api/v1/uploads/{uid}/geojson"),
        ("delete", f"/api/v1/uploads/{uid}"),
    ):
        resp = await getattr(client, method)(path, headers=headers)
        assert resp.status_code == 404, (method, path, resp.status_code, resp.text)


@pytest.mark.asyncio
async def test_sec08_anon_upload_200_with_correct_token(client, db, app_and_db):
    """SEC-08 anon owner with correct token can GET detail + geojson."""
    _, _, tmp_path = app_and_db
    async with db as session:
        conv = await AsyncHistoryService(session).get_or_create_conversation(
            "sec08-up-anon-3", user_id=None
        )
        token = conv.owner_token
        rec = _seed_upload(conv.id, tmp_path)
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        uid = rec.id

    headers = {"X-Session-Token": token}
    resp = await client.get(f"/api/v1/uploads/{uid}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == uid

    resp = await client.get(f"/api/v1/uploads/{uid}/geojson", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "FeatureCollection"


@pytest.mark.asyncio
async def test_sec08_anon_upload_delete_with_correct_token(client, db, app_and_db):
    """SEC-08 anon owner with correct token can DELETE own upload."""
    _, _, tmp_path = app_and_db
    async with db as session:
        conv = await AsyncHistoryService(session).get_or_create_conversation(
            "sec08-up-anon-del", user_id=None
        )
        token = conv.owner_token
        rec = _seed_upload(conv.id, tmp_path)
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        uid = rec.id

    resp = await client.delete(
        f"/api/v1/uploads/{uid}",
        headers={"X-Session-Token": token},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # Already deleted → 404
    resp = await client.get(
        f"/api/v1/uploads/{uid}",
        headers={"X-Session-Token": token},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_legacy_null_null_upload_fail_closed(client, db, app_and_db):
    """#1109: legacy NULL/NULL conversation uploads are DENIED (no token).

    The grandfather made every legacy anonymous upload readable/deletable by
    any authenticated caller who enumerated the sequential upload id. After
    the closure (predicate fail-closed + migration g1109 minting random
    tokens), access requires the minted token nobody holds.
    """
    _, _, tmp_path = app_and_db
    async with db as session:
        session.add(
            Conversation(
                id="sec08-up-legacy",
                user_id=None,
                owner_token=None,
                title="legacy",
            )
        )
        await session.flush()
        rec = _seed_upload("sec08-up-legacy", tmp_path)
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        uid = rec.id

    for method, path in (
        ("get", f"/api/v1/uploads/{uid}"),
        ("get", f"/api/v1/uploads/{uid}/geojson"),
        ("delete", f"/api/v1/uploads/{uid}"),
    ):
        resp = await getattr(client, method)(path)
        assert resp.status_code == 404, (method, path, resp.status_code)

    # A wrong token is equally denied.
    resp = await client.get(
        f"/api/v1/uploads/{uid}", headers={"X-Session-Token": "guessed"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_user_bound_upload_owner_ok_other_404(client, db, app_and_db):
    """Authenticated user-bound session: owner can read; other user 404."""
    _, _, tmp_path = app_and_db
    owner_id = _MOCK_USER["user_id"]
    other_id = "other-user-sec08"

    async with db as session:
        session.add(
            User(
                id=owner_id,
                username="sec08-uploads-user",
                email="sec08-uploads@example.com",
                password_hash=hash_password("unused-password-xx"),
                role="viewer",
                is_active=True,
            )
        )
        await session.flush()
        session.add(
            Conversation(id="sec08-up-bound", user_id=owner_id, title="bound")
        )
        await session.flush()
        rec = _seed_upload("sec08-up-bound", tmp_path)
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        uid = rec.id

    # Owner (dependency override) → 200
    resp = await client.get(f"/api/v1/uploads/{uid}")
    assert resp.status_code == 200

    # Swap caller to other user via override
    app, _, _ = app_and_db
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": other_id,
        "role": "viewer",
    }
    try:
        resp = await client.get(f"/api/v1/uploads/{uid}")
        assert resp.status_code == 404
        resp = await client.delete(f"/api/v1/uploads/{uid}")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides[get_current_user] = lambda: _MOCK_USER


@pytest.mark.asyncio
async def test_list_uploads_requires_token_for_anon_session(client, db, app_and_db):
    """list_uploads also forwards X-Session-Token for SEC-08 anon sessions."""
    _, _, tmp_path = app_and_db
    async with db as session:
        conv = await AsyncHistoryService(session).get_or_create_conversation(
            "sec08-up-list", user_id=None
        )
        token = conv.owner_token
        rec = _seed_upload(conv.id, tmp_path)
        session.add(rec)
        await session.commit()

    sid = "sec08-up-list"
    resp = await client.get("/api/v1/uploads", params={"session_id": sid})
    assert resp.status_code == 404

    resp = await client.get(
        "/api/v1/uploads",
        params={"session_id": sid},
        headers={"X-Session-Token": "wrong"},
    )
    assert resp.status_code == 404

    resp = await client.get(
        "/api/v1/uploads",
        params={"session_id": sid},
        headers={"X-Session-Token": token},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
