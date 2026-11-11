"""G17 HTTP API 合同测试：真实路由 + sqlite 会话归属 + 统一错误信封。

镜像 tests/data/test_upload_identity_v4.py 的 app/db fixture 模式。
"""
from __future__ import annotations

import io
import json
from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings as app_settings
from app.models.db_model import Base, Conversation, User  # noqa: F401

SESSION = "sess-g17-api-0001"

_GEO = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"n": 1},
         "geometry": {"type": "Point", "coordinates": [116.3, 39.8]}},
        {"type": "Feature", "properties": {"n": 2},
         "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
    ],
}


@pytest_asyncio.fixture
async def app_and_db(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'g17_api.db'}"
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    test_session = async_sessionmaker(bind=engine, expire_on_commit=False)

    from app.core.auth import get_current_user_optional_with_version
    from app.api.routes import data_import as data_import_routes

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with test_session() as db:
        db.add(User(id="g17-user", username="g17-user", email="g17@example.com",
                    password_hash="x", is_active=True, role="viewer"))
        db.add(Conversation(id=SESSION, user_id="g17-user"))
        await db.commit()

    @asynccontextmanager
    async def override_async_db_session():
        async with test_session() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    from app.tools import _utils

    monkeypatch.setattr(_utils, "async_db_session", override_async_db_session)
    monkeypatch.setattr(data_import_routes, "async_db_session", override_async_db_session)
    monkeypatch.setattr(app_settings, "DATA_DIR", str(tmp_path))

    app = FastAPI()
    from app.core.exception import (
        unified_http_exception_handler,
        unified_validation_exception_handler,
    )

    app.add_exception_handler(StarletteHTTPException, unified_http_exception_handler)
    app.add_exception_handler(RequestValidationError, unified_validation_exception_handler)
    app.include_router(data_import_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user_optional_with_version] = lambda: {
        "user_id": "g17-user", "id": "g17-user"
    }
    try:
        yield app
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(app_and_db):
    transport = ASGITransport(app=app_and_db)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _multipart(files: list, session_id: str = SESSION):
    payload = []
    for name, content in files:
        payload.append(("files", (name, io.BytesIO(content), "application/octet-stream")))
    payload.append(("session_id", (None, session_id)))
    return payload


async def test_api_full_journey(client):
    # 1. 创建批次（多文件 multipart）
    r = await client.post(
        "/api/v1/data-import/batches",
        files=_multipart([("a.geojson", json.dumps(_GEO).encode()),
                          ("b.geojson", json.dumps(_GEO).encode())]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "inspected"
    assert len(body["sources"]) == 2 and len(body["members"]) == 2
    batch_id = body["batch_id"]
    keys = [m["member_key"] for m in body["members"]]

    # 2. 读取详情 / 列表
    r2 = await client.get(f"/api/v1/data-import/batches/{batch_id}", params={"session_id": SESSION})
    assert r2.status_code == 200 and r2.json()["batch_id"] == batch_id
    r3 = await client.get("/api/v1/data-import/batches", params={"session_id": SESSION})
    assert any(b["batch_id"] == batch_id for b in r3.json()["batches"])

    # 3. 计划 + 预览
    r4 = await client.post(
        f"/api/v1/data-import/batches/{batch_id}/plan",
        params={"session_id": SESSION},
        json={"member_configs": {k: {} for k in keys}, "selected": keys},
    )
    assert r4.status_code == 200 and r4.json()["plan_version"] == 1
    r5 = await client.post(
        f"/api/v1/data-import/batches/{batch_id}/preview",
        params={"session_id": SESSION},
        json={"member_keys": keys, "row_limit": 10},
    )
    assert r5.status_code == 200 and len(r5.json()["previews"]) == 2

    # 4. 提交（202 语义）+ 轮询收据
    r6 = await client.post(
        f"/api/v1/data-import/batches/{batch_id}/commit",
        params={"session_id": SESSION},
        json={},
    )
    assert r6.status_code == 200 and r6.json()["status"] == "committing"
    import asyncio

    for _ in range(60):
        r7 = await client.get(
            f"/api/v1/data-import/batches/{batch_id}/receipts", params={"session_id": SESSION}
        )
        if r7.json()["status"] not in ("committing",):
            break
        await asyncio.sleep(0.5)
    assert r7.json()["status"] == "completed", r7.text
    assert all(x["status"] == "completed" for x in r7.json()["receipts"].values())

    # 5. 清理
    r8 = await client.delete(f"/api/v1/data-import/batches/{batch_id}", params={"session_id": SESSION})
    assert r8.status_code == 200 and r8.json()["deleted"] is True


async def test_api_ownership_404(client):
    files = _multipart([("a.geojson", json.dumps(_GEO).encode())])
    r = await client.post("/api/v1/data-import/batches", files=files)
    assert r.status_code == 200
    bid = r.json()["batch_id"]

    # 未验证会话
    r2 = await client.get(
        f"/api/v1/data-import/batches/{bid}", params={"session_id": "sess-not-mine"}
    )
    assert r2.status_code == 404
    # 缺 session_id
    r3 = await client.get(f"/api/v1/data-import/batches/{bid}")
    assert r3.status_code in (404, 422)


async def test_api_bad_batch_shape(client):
    r = await client.post(
        "/api/v1/data-import/batches",
        files=[("files", ("x.txt", io.BytesIO(b"nope"), "text/plain"))],
        data={"session_id": SESSION},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["members"][0]["unsupported_reason"] == "unsupported_format"


async def test_api_plan_conflict_409(client):
    r = await client.post(
        "/api/v1/data-import/batches",
        files=_multipart([("a.geojson", json.dumps(_GEO).encode())]),
    )
    bid = r.json()["batch_id"]
    key = r.json()["members"][0]["member_key"]
    r2 = await client.post(
        f"/api/v1/data-import/batches/{bid}/plan",
        params={"session_id": SESSION},
        json={"member_configs": {key: {}}, "selected": [key]},
    )
    assert r2.status_code == 200
    r3 = await client.post(
        f"/api/v1/data-import/batches/{bid}/plan",
        params={"session_id": SESSION},
        json={"member_configs": {key: {}}, "expected_plan_version": 99},
    )
    assert r3.status_code == 409
    assert "PLAN_VERSION_CONFLICT" in r3.text


async def test_api_diagnostics_endpoint(client):
    """P2-4：拒绝行明细可经 API 读取；含 "/" 的 member_key 不被路由拦截。"""
    # 150 行全部非法坐标 → 超内联上限 → 落 diag 产物
    rows = [["abc", "39.8"] for _ in range(150)]
    csv_text = "lng,lat\n" + "".join(f"{a},{b}\n" for a, b in rows)
    r = await client.post(
        "/api/v1/data-import/batches",
        files=[("files", ("bad.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv"))],
        data={"session_id": SESSION},
    )
    assert r.status_code == 200
    batch_id = r.json()["batch_id"]
    member_key = r.json()["members"][0]["member_key"]
    rp = await client.post(
        f"/api/v1/data-import/batches/{batch_id}/plan?session_id={SESSION}",
        json={"member_configs": {member_key: {"mode": "points", "x_column": "lng",
                                              "y_column": "lat", "source_crs_declared": "EPSG:4326"}},
              "selected": [member_key]},
    )
    assert rp.status_code == 200
    rc = await client.post(
        f"/api/v1/data-import/batches/{batch_id}/commit?session_id={SESSION}", json={}
    )
    assert rc.status_code == 200
    import asyncio

    for _ in range(60):
        rr = await client.get(
            f"/api/v1/data-import/batches/{batch_id}/receipts?session_id={SESSION}"
        )
        if rr.json()["status"] != "committing":
            break
        await asyncio.sleep(0.5)
    receipt = rr.json()["receipts"][member_key]
    assert receipt["status"] == "completed"
    assert receipt["rejected_rows"] == 150

    # 明细端点可读
    rd = await client.get(
        f"/api/v1/data-import/batches/{batch_id}/diagnostics",
        params={"session_id": SESSION, "member_key": member_key},
    )
    assert rd.status_code == 200, rd.text
    body = rd.json()
    assert body["lines"] == 150 and len(body["rows"]) == 150

    # 含 "/" 的 member_key：路由可达成（返回业务 404，而非 FastAPI 路由 404）
    rd2 = await client.get(
        f"/api/v1/data-import/batches/{batch_id}/diagnostics",
        params={"session_id": SESSION, "member_key": "c:src-1:zipset:sub/dir:setA"},
    )
    assert rd2.status_code == 404
    assert "明细" in rd2.text or "diagnostics" in rd2.text.lower()
