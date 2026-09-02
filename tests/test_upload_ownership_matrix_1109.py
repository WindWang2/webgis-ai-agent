"""#1109 closure: upload ownership matrix + legacy NULL/NULL migration.

Matrix (per issue acceptance) across GET /uploads/{id}, GET .../geojson,
DELETE /uploads/{id}, GET /uploads?session_id=...:
  1. authenticated owner                → 200
  2. authenticated other user           → 404
  3. anonymous correct token            → 200
  4. anonymous wrong token              → 404
  5. anonymous no token                 → 404
  6. legacy NULL/NULL conversation      → 404 (fail-closed; migration mints
                                          random tokens nobody can present)
  7. session missing                    → 404
Plus: migration g1109 mints non-null tokens for every NULL/NULL row.
"""
import os
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-sec08-matrix-32charsxxx")
os.environ.setdefault("ENV", "development")

from app.models.db_model import Base, Conversation, User  # noqa: E402
from app.models.upload import UploadRecord  # noqa: E402
from app.core.database import get_async_db  # noqa: E402
from app.core.auth import get_current_user, hash_password  # noqa: E402
from app.tools import _utils  # noqa: E402
from app.api.routes import upload as upload_routes  # noqa: E402
from app.services.history_service_async import AsyncHistoryService  # noqa: E402


_OWNER = {"user_id": "matrix-owner", "role": "viewer"}


@pytest_asyncio.fixture
async def app_and_db(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'matrix.db'}"
    test_engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    test_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
    app.dependency_overrides[get_async_db] = override_async_db_session
    app.dependency_overrides[get_current_user] = lambda: _OWNER
    try:
        yield app, test_session, tmp_path
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def client(app_and_db):
    app, _, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest_asyncio.fixture
async def db(app_and_db):
    _, session, _ = app_and_db
    async with session() as s:
        yield s


def _seed_upload(session_id: str, tmp_path: Path) -> UploadRecord:
    d = tmp_path / "uploads" / f"u-{session_id[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "data.geojson"
    p.write_text('{"type":"FeatureCollection","features":[]}')
    return UploadRecord(
        filename=str(p), original_name="data.geojson", file_type="vector",
        format="geojson", crs="EPSG:4326", geometry_type="Point",
        feature_count=0, bbox=[0, 0, 0, 0], file_size=p.stat().st_size,
        session_id=session_id,
    )


async def _mk_anon_upload(db, tmp_path, sid="matrix-anon"):
    conv = await AsyncHistoryService(db).get_or_create_conversation(sid, user_id=None)
    rec = _seed_upload(conv.id, tmp_path)
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return conv, rec


# ─── 3/4/5: anonymous session token states ─────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_anon_correct_token_200(client, db, app_and_db):
    _, _, tmp_path = app_and_db
    conv, rec = await _mk_anon_upload(db, tmp_path)
    h = {"X-Session-Token": conv.owner_token}
    assert (await client.get(f"/api/v1/uploads/{rec.id}", headers=h)).status_code == 200
    assert (await client.get(f"/api/v1/uploads/{rec.id}/geojson", headers=h)).status_code == 200
    assert (await client.get(
        "/api/v1/uploads", params={"session_id": conv.id}, headers=h
    )).status_code == 200


@pytest.mark.asyncio
async def test_matrix_anon_wrong_token_404(client, db, app_and_db):
    _, _, tmp_path = app_and_db
    conv, rec = await _mk_anon_upload(db, tmp_path)
    h = {"X-Session-Token": "wrong"}
    assert (await client.get(f"/api/v1/uploads/{rec.id}", headers=h)).status_code == 404
    assert (await client.get(f"/api/v1/uploads/{rec.id}/geojson", headers=h)).status_code == 404
    assert (await client.delete(f"/api/v1/uploads/{rec.id}", headers=h)).status_code == 404
    assert (await client.get(
        "/api/v1/uploads", params={"session_id": conv.id}, headers=h
    )).status_code == 404


@pytest.mark.asyncio
async def test_matrix_anon_no_token_404(client, db, app_and_db):
    _, _, tmp_path = app_and_db
    conv, rec = await _mk_anon_upload(db, tmp_path)
    assert (await client.get(f"/api/v1/uploads/{rec.id}")).status_code == 404
    assert (await client.get(f"/api/v1/uploads/{rec.id}/geojson")).status_code == 404
    assert (await client.delete(f"/api/v1/uploads/{rec.id}")).status_code == 404
    assert (await client.get(
        "/api/v1/uploads", params={"session_id": conv.id}
    )).status_code == 404


# ─── 1/2: authenticated user-bound session ─────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_user_bound_owner_200_other_404(client, db, app_and_db):
    app, _, tmp_path = app_and_db
    async with db as s:
        u = User(
            id=_OWNER["user_id"], username="mowner", email="m@example.com",
            password_hash=hash_password("unused-password-xx"), role="viewer",
            is_active=True,
        )
        s.add(u)
        await s.flush()
        s.add(Conversation(id="matrix-bound", user_id=_OWNER["user_id"], title="b"))
        await s.flush()
        rec = _seed_upload("matrix-bound", tmp_path)
        s.add(rec)
        await s.commit()
        await s.refresh(rec)
        uid = rec.id

    # Owner (dependency override) → 200 even with no token.
    assert (await client.get(f"/api/v1/uploads/{uid}")).status_code == 200
    # Residual wrong token must NOT lock the owner out (token ignored for bound).
    assert (await client.get(
        f"/api/v1/uploads/{uid}", headers={"X-Session-Token": "junk"}
    )).status_code == 200

    # Another authenticated user → 404 on every endpoint.
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "matrix-other", "role": "viewer",
    }
    try:
        assert (await client.get(f"/api/v1/uploads/{uid}")).status_code == 404
        assert (await client.get(f"/api/v1/uploads/{uid}/geojson")).status_code == 404
        assert (await client.delete(f"/api/v1/uploads/{uid}")).status_code == 404
    finally:
        app.dependency_overrides[get_current_user] = lambda: _OWNER


# ─── 6: legacy NULL/NULL — every endpoint denies ───────────────────────────


@pytest.mark.asyncio
async def test_matrix_legacy_null_null_denied_everywhere(client, db, app_and_db):
    _, _, tmp_path = app_and_db
    async with db as s:
        s.add(Conversation(id="matrix-legacy", user_id=None, owner_token=None, title="l"))
        await s.flush()
        rec = _seed_upload("matrix-legacy", tmp_path)
        s.add(rec)
        await s.commit()
        await s.refresh(rec)
        uid = rec.id

    for headers in (None, {"X-Session-Token": "guessed"}):
        h = headers or {}
        assert (await client.get(f"/api/v1/uploads/{uid}", headers=h)).status_code == 404
        assert (await client.get(f"/api/v1/uploads/{uid}/geojson", headers=h)).status_code == 404
        assert (await client.delete(f"/api/v1/uploads/{uid}", headers=h)).status_code == 404
        assert (await client.get(
            "/api/v1/uploads", params={"session_id": "matrix-legacy"}, headers=h
        )).status_code == 404


# ─── 7: session missing / upload missing ───────────────────────────────────


@pytest.mark.asyncio
async def test_matrix_missing_upload_404(client):
    assert (await client.get("/api/v1/uploads/999999")).status_code == 404
    assert (await client.delete("/api/v1/uploads/999999")).status_code == 404


# ─── migration g1109 ────────────────────────────────────────────────────────


def test_migration_g1109_mints_tokens_for_legacy_rows(tmp_path):
    """The Alembic migration backfill mints owner_token for every NULL/NULL row
    (exercised through its extracted _mint_legacy_tokens(bind) helper)."""
    import sqlalchemy as sa
    from sqlalchemy import create_engine

    from app.models.db_model import Base, Conversation
    from migrations.versions.g1109_legacy_owner_tokens import _mint_legacy_tokens

    eng = create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(sa.insert(Conversation).values([
            {"id": "lg-1", "user_id": None, "owner_token": None, "title": "a"},
            {"id": "lg-2", "user_id": None, "owner_token": None, "title": "b"},
            {"id": "lg-3", "user_id": None, "owner_token": "already-set", "title": "c"},
            {"id": "lg-4", "user_id": "u1", "owner_token": None, "title": "d"},
        ]))
        _mint_legacy_tokens(conn)
    with eng.connect() as conn:
        rows = conn.execute(
            sa.select(Conversation.id, Conversation.owner_token)
        ).all()
    eng.dispose()
    tokens = {r[0]: r[1] for r in rows}
    assert tokens["lg-1"] and len(tokens["lg-1"]) >= 32, "NULL/NULL row not minted"
    assert tokens["lg-2"] and tokens["lg-2"] != tokens["lg-1"], "tokens must be unique"
    assert tokens["lg-3"] == "already-set", "existing token must not be overwritten"
    assert tokens["lg-4"] is None, "user-bound row must not be touched"


def test_migration_g1109_downgrade_is_safe_noop():
    """Downgrade must not reset minted tokens to NULL (that would re-open the
    IDOR); leaving them in place is fail-closed under both code versions and
    keeps chain downgrades functional."""
    from migrations.versions.g1109_legacy_owner_tokens import downgrade

    # A deliberate no-op: returns without raising and without touching data.
    assert downgrade() is None
