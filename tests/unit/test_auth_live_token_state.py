"""security F-06 / F-07：decode-only 依赖复核 DB 实时 ver/is_active/role。"""
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


@pytest.fixture
async def live_db(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import auth as auth_mod
    from app.core.config import settings
    from app.models.db_model import Base, User

    monkeypatch.setattr(settings, "AUTH_DISABLED", False, raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def _session():
        async with fac() as s:
            yield s
            await s.commit()

    monkeypatch.setattr("app.core.database.async_db_session", _session)
    getattr(auth_mod, "invalidate_live_user_state", lambda *a: None)()

    async def add_user(uid, role="viewer", ver=0, active=True):
        async with fac() as s:
            s.add(User(id=uid, username=uid, email=f"{uid}@x.io", role=role,
                       token_version=ver, is_active=active))
            await s.commit()

    yield add_user, fac
    getattr(auth_mod, "invalidate_live_user_state", lambda *a: None)()
    await engine.dispose()


def _creds(sub, role="admin", ver=0):
    from app.core.auth import create_access_token

    tok = create_access_token({"sub": sub, "role": role}, token_version=ver)
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok)


@pytest.mark.asyncio
async def test_get_current_user_rejects_revoked_version(live_db):
    from app.core.auth import get_current_user

    add_user, _ = live_db
    await add_user("u-rev", ver=1)
    with pytest.raises(HTTPException) as ei:
        await get_current_user(_creds("u-rev", ver=0))
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_rejects_disabled(live_db):
    from app.core.auth import get_current_user

    add_user, _ = live_db
    await add_user("u-off", active=False)
    with pytest.raises(HTTPException) as ei:
        await get_current_user(_creds("u-off"))
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_get_current_user_uses_db_role(live_db):
    from app.core.auth import get_current_user
    from app.core.scopes import scopes_for_role

    add_user, _ = live_db
    await add_user("u-demoted", role="viewer")
    user = await get_current_user(_creds("u-demoted", role="admin"))
    assert user["role"] == "viewer"
    assert user["scopes"] <= scopes_for_role("viewer")


@pytest.mark.asyncio
async def test_optional_revoked_becomes_anonymous(live_db):
    from app.core.auth import get_current_user_optional

    add_user, _ = live_db
    await add_user("u-rev2", ver=3)
    user = await get_current_user_optional(_creds("u-rev2", ver=0))
    assert user["user_id"] == "anonymous"


@pytest.mark.asyncio
async def test_missing_row_keeps_claims(live_db):
    from app.core.auth import get_current_user

    user = await get_current_user(_creds("u-nodb", role="editor"))
    assert user["user_id"] == "u-nodb"


@pytest.mark.asyncio
async def test_with_version_prefers_db_role(live_db):
    """F-06：get_current_user_with_version 不再让 JWT role 覆盖 DB role。"""
    from app.core.auth import get_current_user_with_version
    from app.core.scopes import scopes_for_role

    add_user, fac = live_db
    await add_user("u-dem2", role="viewer")
    async with fac() as db:
        user = await get_current_user_with_version(
            credentials=_creds("u-dem2", role="admin"), db=db)
    assert user["role"] == "viewer"
    assert user["scopes"] <= scopes_for_role("viewer")
