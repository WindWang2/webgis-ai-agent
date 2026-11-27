from collections import OrderedDict
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import chat_session_views, data_delivery, map as map_routes, report as report_routes
from app.core import database
from app.core.auth import get_current_user, get_current_user_optional, get_current_user_optional_with_version
from app.core.config import settings
from app.core.database import Base, get_async_db
from app.models.db_model import AnalysisTask, Conversation
from app.models.project import Project
from app.models.report import Report
from app.services import artifact_registry, export_storage, report_service
from app.services.data_delivery import store as delivery_store

CATALOG = "/api/v1/chat/sessions/{}/generated-files"
OWNER = {"X-Test-User": "alice"}
TOKEN = {"X-Session-Token": "private-token"}


@pytest.fixture
async def catalog(tmp_path, monkeypatch):
    database_path = tmp_path / "catalog.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    sync_engine = create_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(sync_engine))
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[Conversation.__table__, Report.__table__, AnalysisTask.__table__, Project.__table__],
        ))
    sessions = async_sessionmaker(engine)

    async def get_db():
        async with sessions() as db:
            yield db

    def identity(request: Request):
        return {"user_id": request.headers.get("X-Test-User", "anonymous")}

    def required_identity(request: Request):
        if not request.headers.get("X-Test-User"):
            raise HTTPException(401)
        return identity(request)

    app = FastAPI()
    app.include_router(chat_session_views.router, prefix="/api/v1/chat")
    app.include_router(map_routes.router, prefix="/api/v1")
    app.include_router(report_routes.router, prefix="/api/v1")
    app.include_router(data_delivery.router, prefix="/api/v1")
    app.dependency_overrides.update({
        get_async_db: get_db, get_current_user_optional: identity,
        get_current_user_optional_with_version: identity, get_current_user: required_identity,
    })

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    receipts = OrderedDict()
    monkeypatch.setattr(export_storage, "EXPORT_OWNERS", receipts)
    monkeypatch.setattr(map_routes, "_EXPORT_OWNERS", receipts)
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    for module in (report_service, report_routes):
        monkeypatch.setattr(module, "REPORT_DIR", str(report_dir))
    report_file = report_dir / "result.md"
    report_file.write_text("report bytes", encoding="utf-8")
    stamp = datetime(2025, 1, 2, tzinfo=timezone.utc)
    async with sessions.begin() as db:
        db.add_all([
            Conversation(id="owned", user_id="alice"),
            Conversation(id="guest", owner_token=TOKEN["X-Session-Token"]),
            Conversation(id="empty", user_id="alice"),
            Conversation(id="data-only", user_id="alice"),
            Conversation(id="guest-data", owner_token=TOKEN["X-Session-Token"]),
        ])
        for rid, sid, status, path in [
            ("ready", "owned", "completed", report_file),
            ("processing", "owned", "processing", None),
            ("missing", "owned", "completed", report_dir / "absent.md"),
            ("guest-report", "guest", "completed", report_file),
        ]:
            db.add(Report(id=rid, session_id=sid, title="Map summary", format="markdown",
                          status=status, file_path=str(path) if path else None, created_at=stamp))
        for job_id, sid, status in [(21, "data-only", "completed"), (22, "guest-data", "completed"),
                                    (23, "data-only", "running")]:
            delivery_id = f"{job_id:032x}"
            db.add(AnalysisTask(id=job_id, task_type="data_delivery", session_id=sid, status=status,
                                parameters={"delivery_id": delivery_id, "format": "csv"}, created_at=stamp))
            directory = delivery_store.final_dir(delivery_id)
            directory.mkdir()
            names = [(f"city-{job_id}.csv", "data"), (f"city-{job_id}.schema.json", "schema")]
            for name, _ in names:
                (directory / name).write_text(name, encoding="utf-8")
            delivery_store.write_receipt(directory, {
                "delivery_id": delivery_id, "job_id": str(job_id), "status": status, "format": "csv",
                "filename": f"city-{job_id}", "members": [{"path": n, "role": r} for n, r in names],
            })
    records = {"owned": [], "guest": [], "empty": [], "data-only": [], "guest-data": []}
    for sid, name, owner, present in [
        ("owned", "result.pdf", "alice", True),
        ("owned", "old.pdf", "alice", False),
        ("owned", "foreign.pdf", "bob", True),
        ("owned", "unknown.pdf", None, True),
        ("owned", "elsewhere.pdf", "session:guest", True),
        ("guest", "guest.png", "session:guest", True),
    ]:
        records[sid].append(artifact_registry.ArtifactRecord(
            artifact_id=f"ref:export/{name}", session_id=sid, created_at=10,
        ))
        if owner:
            export_storage.set_export_owner(name, owner)
        if present:
            (tmp_path / "exports" / name).write_bytes(name.encode())
    records["owned"].append(artifact_registry.ArtifactRecord(artifact_id="ref:chart", session_id="owned"))
    ledger = AsyncMock(side_effect=records.__getitem__)
    monkeypatch.setattr(artifact_registry, "list_artifacts", ledger)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, ledger
    finally:
        await engine.dispose()
        sync_engine.dispose()


async def test_owned_files_and_authorized_downloads(catalog):
    client, _ = catalog
    response = await client.get(CATALOG.format("owned"), headers=OWNER)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    items = response.json()["items"]
    assert [i["id"] for i in items] == [
        "report:missing", "report:processing", "report:ready", "ref:export/old.pdf", "ref:export/result.pdf",
    ]
    assert items[2] == {
        "id": "report:ready", "name": "Map summary.md", "format": "md", "source": "report",
        "created_at": datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp(),
        "download_url": "/api/v1/reports/ready/download",
    }
    assert items[4] == {
        "id": "ref:export/result.pdf", "name": "result.pdf", "format": "pdf", "source": "export",
        "created_at": 10, "download_url": "/api/v1/export/download/result.pdf",
    }
    assert all(items[i]["download_url"] is None for i in (0, 1, 3))
    assert (await client.get(items[2]["download_url"], headers=OWNER)).content == b"report bytes"
    assert (await client.get(items[4]["download_url"], headers=OWNER)).content == b"result.pdf"
    assert (await client.get(items[4]["download_url"])).status_code == 401
    assert (await client.get(CATALOG.format("empty"), headers=OWNER)).json() == {"items": []}


async def test_session_token_files_and_report_auth(catalog):
    client, _ = catalog
    response = await client.get(CATALOG.format("guest"), headers=TOKEN)
    assert response.status_code == 200
    [export] = response.json()["items"]
    assert export["name"] == "guest.png"
    assert (await client.get(export["download_url"])).content == b"guest.png"
    assert (await client.get(export["download_url"].split("?")[0])).status_code == 401
    signed_in = {**OWNER, **TOKEN}
    response = await client.get(CATALOG.format("guest"), headers=signed_in)
    report, _ = response.json()["items"]
    assert report["id"] == "report:guest-report"
    assert (await client.get(report["download_url"], headers=signed_in)).content == b"report bytes"
    assert (await client.get(report["download_url"], headers=TOKEN)).status_code == 401
    assert (await client.get(report["download_url"], headers=OWNER)).status_code == 404


async def test_other_sessions_fail_closed_before_lookup(catalog):
    client, ledger = catalog
    for session_id, headers in [
        ("owned", {}), ("owned", {"X-Test-User": "bob"}), ("unknown", OWNER),
        ("guest", OWNER), ("guest", {"X-Session-Token": "unrelated-token"}),
    ]:
        response = await client.get(CATALOG.format(session_id), headers=headers)
        assert response.status_code == 404
    ledger.assert_not_awaited()


async def test_delivery_members_are_downloadable_only_for_their_session(catalog):
    client, _ = catalog
    for job_id, session_id, headers in [(21, "data-only", OWNER), (22, "guest-data", TOKEN)]:
        response = await client.get(CATALOG.format(session_id), headers=headers)
        assert response.status_code == 200
        data, schema = response.json()["items"]
        url = f"/api/v1/data-delivery/jobs/{job_id}/download"
        assert data["source"] == schema["source"] == "delivery"
        assert data["format"] == "csv" and schema["format"] == "json"
        assert data["download_url"] == url
        assert schema["download_url"] == f"{url}?member=city-{job_id}.schema.json"
        for item in (data, schema):
            assert (await client.get(item["download_url"], headers=headers)).text == item["name"]
            assert (await client.get(item["download_url"], headers={"X-Test-User": "bob"})).status_code == 404
