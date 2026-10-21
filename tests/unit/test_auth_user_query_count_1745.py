"""#1745（4）：认证底座依赖不得每请求多发一条 SELECT organizations。

``User.organization`` 曾是 ``lazy="selectin"`` —— 加载期策略，每次
``select(User)`` 都附带一条 organizations IN 查询。而
``auth.get_current_user_with_version``（``require_admin`` / ``require_scope``
及大量依赖的底座）只消费 token_version/is_active/role/org_id 四个标量列。

修复：关系改惰性加载（``lazy="select"``）。本测试锁住「一次 User 查询 =
一条 SQL」，并确认四个标量列与 require_admin 的消费面不受影响。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import event


@pytest.fixture()
async def live_db(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import auth as auth_mod
    from app.core.config import settings
    from app.models.db_model import Base, Organization, User

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

    async def add_user(uid, role="viewer", ver=0, active=True, org_id=1):
        async with fac() as s:
            if org_id is not None:
                existing = await s.get(Organization, org_id)
                if existing is None:
                    s.add(Organization(id=org_id, name="org", slug=f"org-{org_id}"))
                    await s.commit()
            s.add(User(id=uid, username=uid, email=f"{uid}@x.io", role=role,
                       token_version=ver, is_active=active, org_id=org_id))
            await s.commit()

    yield add_user, fac, engine
    getattr(auth_mod, "invalidate_live_user_state", lambda *a: None)()
    await engine.dispose()


def _creds(sub, role="admin", ver=0):
    from app.core.auth import create_access_token

    tok = create_access_token({"sub": sub, "role": role}, token_version=ver)
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok)


async def _count_queries(engine, coro_factory):
    """在同步引擎上数 SQL 语句（async 引擎的事件挂在 sync_engine 上）。"""
    counter = {"n": 0}

    def before_execute(conn, clauseelement, multiparams, params, execution_options):
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_execute", before_execute)
    try:
        result = await coro_factory()
    finally:
        event.remove(engine.sync_engine, "before_execute", before_execute)
    return result, counter["n"]


@pytest.mark.asyncio
async def test_user_lookup_issues_single_select(live_db):
    """一次 indexed PK lookup = 一条 SQL（selectin 会多打一条 organizations）。"""
    from sqlalchemy import select

    from app.core.database import async_db_session
    from app.models.db_model import User

    add_user, _fac, engine = live_db
    await add_user("u-1745", role="admin", ver=2)
    async with async_db_session() as db:
        result, n = await _count_queries(
            engine,
            lambda: db.execute(select(User).where(User.id == "u-1745")),
        )
    assert n == 1, f"User lookup must be a single SELECT, got {n}"
    loaded = result.scalar_one()
    # 认证面消费的四个标量列仍在（组织关系不再被附带加载）。
    assert loaded.token_version == 2
    assert loaded.is_active is True
    assert loaded.role == "admin"
    assert loaded.org_id == 1


@pytest.mark.asyncio
async def test_with_version_dependency_single_db_roundtrip(live_db):
    """底座依赖本身：一次 execute、无附带组织查询，四个标量列可消费。"""
    from app.core.auth import get_current_user_with_version
    from app.core.database import async_db_session

    add_user, _fac, engine = live_db
    await add_user("u-1745b", role="admin", ver=0)

    async with async_db_session() as db:
        profile, n = await _count_queries(
            engine,
            lambda: get_current_user_with_version(
                credentials=_creds("u-1745b", role="admin"), db=db,
            ),
        )
    assert n == 1, f"auth base dependency must issue one SELECT, got {n}"
    assert profile["user_id"] == "u-1745b"
    assert profile["role"] == "admin"
    assert profile["org_id"] == 1
    assert profile["user"].token_version == 0


@pytest.mark.asyncio
async def test_require_admin_still_reads_live_role(live_db):
    """消费面不变：require_admin 仍以 DB 实时 role 为准（降级即时生效）。"""
    from app.core.auth import get_current_user_with_version, require_admin
    from app.core.database import async_db_session

    add_user, _fac, _engine = live_db
    await add_user("u-1745c", role="viewer", ver=0)
    async with async_db_session() as db:
        profile = await get_current_user_with_version(
            credentials=_creds("u-1745c", role="admin"), db=db)
        # token claim 是 admin、DB 是 viewer → 依赖以 DB 为准。
        assert profile["role"] == "viewer"
        with pytest.raises(HTTPException) as ei:
            await require_admin(profile)
        assert ei.value.status_code == 403
