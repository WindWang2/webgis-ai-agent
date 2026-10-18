"""security F-04：share-link 过期判定在真实 DB（naive DateTime 列）上不得 500。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def env(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.routes import report as report_mod
    from app.core.database import Base, get_async_db
    from app.models.report import Report

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _db():
        async with fac() as s:
            yield s

    app = FastAPI()
    app.include_router(report_mod.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = _db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, fac, Report
    await engine.dispose()


async def _add(fac, Report, code, expires):
    async with fac() as s:
        s.add(Report(id=code.ljust(36, "0"), session_id="s", title="t",
                     format="html", status="completed",
                     share_code=code, share_expires_at=expires))
        await s.commit()


@pytest.mark.asyncio
async def test_shared_info_future_expiry_ok(env):
    client, fac, Report = env
    await _add(fac, Report, "future",
               (datetime.now(timezone.utc) + timedelta(days=1)).replace(tzinfo=None))
    resp = await client.get("/api/v1/reports/shared/future")
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_shared_info_past_expiry_reports_expired(env):
    client, fac, Report = env
    await _add(fac, Report, "past",
               (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None))
    resp = await client.get("/api/v1/reports/shared/past")
    assert resp.status_code == 200
    assert resp.json()["success"] is False
    view = await client.get("/api/v1/reports/shared/past/view")
    assert view.status_code == 404


def test_share_expired_helper_handles_naive_and_aware():
    from app.api.routes.report import _share_expired

    now = datetime.now(timezone.utc)
    assert _share_expired(None) is False
    assert _share_expired((now - timedelta(minutes=1)).replace(tzinfo=None))
    assert not _share_expired((now + timedelta(minutes=1)).replace(tzinfo=None))
    assert _share_expired(now - timedelta(minutes=1))
