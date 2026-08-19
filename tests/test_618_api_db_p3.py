"""#618 P3 remaining: items 4, 5, 9, 13.

4. Drop leftover data-fabric single-column indexes that are left-prefixes of
   composites (write amplification).
5. Add knowledge_documents.creator_id index (list_documents filter).
9. Templates list + session-detail messages must paginate in SQL, not hydrate
   the whole table / relationship.
13. Report routes must forward owner_token into verify_session_owner.
"""
from __future__ import annotations

import inspect
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-618-p3-32-chars-okxxxx")
os.environ.setdefault("ENV", "development")

from app.core.database import Base  # noqa: E402
from app.models.db_model import Conversation, Message, User  # noqa: E402
from app.models.report import Report  # noqa: E402

import app.models.data_fabric  # noqa: F401, E402
import app.models.knowledge_base  # noqa: F401, E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REV_0019 = "0019_drop_leftover_duplicate_indexes"

LEFTOVER_SINGLE_INDEXES = (
    ("ix_spatial_catalog_items_source_id", "spatial_catalog_items", "source_id"),
    ("ix_spatial_catalog_items_geometry_type", "spatial_catalog_items", "geometry_type"),
    ("ix_materializations_dataset_id", "materializations", "dataset_id"),
)
CREATOR_INDEX = "ix_knowledge_documents_creator_id"


def _index_col_tuples(table) -> set[tuple[str, ...]]:
    return {tuple(c.name for c in ix.columns) for ix in table.indexes}


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "DATABASE_URL": f"sqlite:///{db_path}",
            "JWT_SECRET_KEY": "test-secret-migration-32-chars-okay",
            "USE_REDIS": "false",
            "HOME": str(Path.home()),
        },
        capture_output=True,
        text=True,
        timeout=600,
    )


def _table_index_names(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (table,),
            )
        }


def _0020_path() -> Path:
    matches = sorted((REPO_ROOT / "migrations" / "versions").glob("0020*.py"))
    assert matches, "missing migrations/versions/0020_*.py"
    return matches[0]


# ── Item 4 / 5: model contracts ──────────────────────────────────────────


def test_item4_leftover_columns_are_left_prefixes_of_composites():
    """Do not drop a singleton unless a composite starts with the same column."""
    cat = Base.metadata.tables["spatial_catalog_items"]
    mat = Base.metadata.tables["materializations"]
    cat_idx = _index_col_tuples(cat)
    mat_idx = _index_col_tuples(mat)
    assert ("source_id", "name") in cat_idx
    assert ("geometry_type", "feature_type") in cat_idx
    assert ("dataset_id", "ref_id") in mat_idx


def test_item4_model_does_not_declare_leftover_singletons():
    """index=True on those columns would recreate the write-amp indexes."""
    cat = Base.metadata.tables["spatial_catalog_items"]
    mat = Base.metadata.tables["materializations"]
    assert ("source_id",) not in _index_col_tuples(cat)
    assert ("geometry_type",) not in _index_col_tuples(cat)
    assert ("dataset_id",) not in _index_col_tuples(mat)
    leftover_names = {name for name, _t, _c in LEFTOVER_SINGLE_INDEXES}
    declared = {ix.name for ix in cat.indexes} | {ix.name for ix in mat.indexes}
    assert leftover_names.isdisjoint(declared)


def test_item5_model_declares_creator_id_index():
    kd = Base.metadata.tables["knowledge_documents"]
    names = {ix.name for ix in kd.indexes}
    assert CREATOR_INDEX in names
    assert ("creator_id",) in _index_col_tuples(kd)


# ── Item 4 / 5: migration 0020 ───────────────────────────────────────────


def test_item4_0020_chains_from_0019_and_is_reversible_on_disk():
    src = _0020_path().read_text(encoding="utf-8")
    assert "down_revision" in src and REV_0019 in src
    assert "def downgrade" in src
    for name, _table, _col in LEFTOVER_SINGLE_INDEXES:
        assert name in src
    assert CREATOR_INDEX in src
    # Must not drop trailing-column singletons that are not left-prefixes.
    assert "ix_spatial_catalog_items_name" not in src
    assert "ix_spatial_catalog_items_feature_type" not in src
    assert "ix_materializations_ref_id" not in src


def test_item4_0020_refuses_to_drop_without_covering_composite():
    """The upgrade path inspects indexes and skips a drop if no left-prefix composite."""
    src = _0020_path().read_text(encoding="utf-8")
    assert "column_names" in src or "get_indexes" in src
    assert "len(" in src


@pytest.fixture(scope="module")
def migrated_sqlite(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("mig618") / "mig.db"
    result = _alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, f"upgrade head 失败:\n{result.stdout}\n{result.stderr}"
    return db_path


def test_item4_5_migrated_schema_drops_leftovers_and_adds_creator(migrated_sqlite):
    engine = __import__("sqlalchemy").create_engine(f"sqlite:///{migrated_sqlite}")
    insp = sa_inspect(engine)
    try:
        catalog_names = {i["name"] for i in insp.get_indexes("spatial_catalog_items")}
        mat_names = {i["name"] for i in insp.get_indexes("materializations")}
        kd_names = {i["name"] for i in insp.get_indexes("knowledge_documents")}
        catalog_cols = {tuple(i["column_names"]) for i in insp.get_indexes("spatial_catalog_items")}
        mat_cols = {tuple(i["column_names"]) for i in insp.get_indexes("materializations")}
    finally:
        engine.dispose()

    assert "ix_spatial_catalog_items_source_id" not in catalog_names
    assert "ix_spatial_catalog_items_geometry_type" not in catalog_names
    assert "ix_materializations_dataset_id" not in mat_names
    assert ("source_id", "name") in catalog_cols
    assert ("geometry_type", "feature_type") in catalog_cols
    assert ("dataset_id", "ref_id") in mat_cols
    assert CREATOR_INDEX in kd_names


def test_item4_5_downgrade_to_0019_restores_leftovers_and_drops_creator(tmp_path):
    db_path = tmp_path / "roundtrip.db"
    up = _alembic(db_path, "upgrade", "head")
    assert up.returncode == 0, f"upgrade head 失败:\n{up.stdout}\n{up.stderr}"

    down = _alembic(db_path, "downgrade", REV_0019)
    assert down.returncode == 0, f"downgrade {REV_0019} 失败:\n{down.stdout}\n{down.stderr}"

    catalog = _table_index_names(db_path, "spatial_catalog_items")
    mats = _table_index_names(db_path, "materializations")
    kd = _table_index_names(db_path, "knowledge_documents")
    assert "ix_spatial_catalog_items_source_id" in catalog
    assert "ix_spatial_catalog_items_geometry_type" in catalog
    assert "ix_materializations_dataset_id" in mats
    assert CREATOR_INDEX not in kd

    again = _alembic(db_path, "upgrade", "head")
    assert again.returncode == 0, f"re-upgrade 失败:\n{again.stdout}\n{again.stderr}"
    catalog = _table_index_names(db_path, "spatial_catalog_items")
    mats = _table_index_names(db_path, "materializations")
    kd = _table_index_names(db_path, "knowledge_documents")
    assert "ix_spatial_catalog_items_source_id" not in catalog
    assert "ix_spatial_catalog_items_geometry_type" not in catalog
    assert "ix_materializations_dataset_id" not in mats
    assert CREATOR_INDEX in kd


# ── Item 9: templates remainder pagination ───────────────────────────────


def test_item9_templates_seed_db_page_window():
    from app.api.routes.templates import _seed_db_page_window

    # seeds first, then DB remainder
    assert _seed_db_page_window(62, offset=0, limit=100) == (0, 62, 0, 38)
    assert _seed_db_page_window(62, offset=50, limit=13) == (50, 62, 0, 1)
    assert _seed_db_page_window(62, offset=62, limit=13) == (62, 62, 0, 13)
    assert _seed_db_page_window(62, offset=70, limit=13) == (62, 62, 8, 13)
    assert _seed_db_page_window(0, offset=10, limit=20) == (0, 0, 10, 20)
    # whole page is seeds: do not touch DB rows
    assert _seed_db_page_window(80, offset=0, limit=20) == (0, 20, 0, 0)


def test_item9_templates_list_does_not_python_slice_full_table():
    from app.api.routes import templates as tmpl_mod

    src = inspect.getsource(tmpl_mod.list_templates)
    assert "merged[offset:offset + limit]" not in src
    assert "_seed_db_page_window" in src
    # empty-q path must count + LIMIT the remainder, not execute(stmt) all rows
    assert "func.count" in src or "count()" in src


# ── Item 9: session detail message pagination ────────────────────────────


@pytest_asyncio.fixture
async def chat_app_and_db(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'chat618.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.core.database import get_async_db
    from app.api.routes import chat as chat_routes
    from app.tools import _utils
    from contextlib import asynccontextmanager

    async def override_get_async_db():
        async with session_factory() as s:
            yield s

    @asynccontextmanager
    async def override_async_db_session():
        async with session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(_utils, "async_db_session", override_async_db_session)
    monkeypatch.setattr("app.api.routes.chat.async_db_session", override_async_db_session)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
    try:
        yield app, session_factory, engine
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _seed_conversation(db, *, n_messages: int) -> None:
    db.add(User(id="user-1", username="u1", email="u1@example.com"))
    await db.flush()
    db.add(Conversation(id="c1", user_id="user-1", title="会话"))
    await db.flush()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n_messages):
        db.add(
            Message(
                conversation_id="c1",
                role="user" if i % 2 == 0 else "assistant",
                content=f"m{i}",
                created_at=base + timedelta(seconds=i),
            )
        )
    await db.commit()


def test_item9_session_detail_declares_limit_offset():
    from app.api.routes.chat import get_session_detail

    src = inspect.getsource(get_session_detail)
    sig = inspect.signature(get_session_detail)
    assert "limit" in sig.parameters
    assert "offset" in sig.parameters
    assert "db.refresh" not in src
    assert "Message" in src


@pytest.mark.asyncio
async def test_item9_session_detail_pages_newest_then_chronological(chat_app_and_db):
    from app.core.auth import create_access_token

    app, session_factory, _engine = chat_app_and_db
    async with session_factory() as db:
        await _seed_conversation(db, n_messages=250)

    token = create_access_token({"sub": "user-1", "username": "u1", "role": "viewer"})
    headers = {"Authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        default = await client.get("/api/v1/chat/sessions/c1", headers=headers)
        page0 = await client.get(
            "/api/v1/chat/sessions/c1?limit=50&offset=0", headers=headers
        )
        page1 = await client.get(
            "/api/v1/chat/sessions/c1?limit=50&offset=50", headers=headers
        )
        too_big = await client.get(
            "/api/v1/chat/sessions/c1?limit=201", headers=headers
        )

    assert default.status_code == 200, default.text
    body = default.json()
    # Default 200: newest 200, rendered oldest→newest for frontend restore.
    assert len(body["messages"]) == 200
    assert [m["content"] for m in body["messages"]] == [f"m{i}" for i in range(50, 250)]
    assert body["total"] == 250
    assert body["limit"] == 200
    assert body["has_more"] is True

    assert page0.status_code == 200
    p0 = page0.json()
    assert [m["content"] for m in p0["messages"]] == [f"m{i}" for i in range(200, 250)]
    assert p0["has_more"] is True

    assert page1.status_code == 200
    p1 = page1.json()
    assert [m["content"] for m in p1["messages"]] == [f"m{i}" for i in range(150, 200)]

    assert too_big.status_code == 422


@pytest.mark.asyncio
async def test_item9_session_detail_sql_uses_limit_not_refresh(chat_app_and_db):
    from app.core.auth import create_access_token

    app, session_factory, engine = chat_app_and_db
    async with session_factory() as db:
        await _seed_conversation(db, n_messages=80)

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        token = create_access_token({"sub": "user-1", "username": "u1", "role": "viewer"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/chat/sessions/c1?limit=20&offset=0",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)

    assert resp.status_code == 200, resp.text
    msg_sql = [s for s in statements if "from messages" in s.lower()]
    assert msg_sql, f"expected a messages SELECT, got {statements}"
    assert any("limit" in s.lower() for s in msg_sql), msg_sql
    assert len(resp.json()["messages"]) == 20


# ── Item 13: report routes forward owner_token ───────────────────────────


def test_item13_report_routes_pass_owner_token():
    from app.api.routes import report as report_mod

    for fn in (
        report_mod.create_report,
        report_mod.list_reports,
        report_mod.get_report,
        report_mod.download_report,
        report_mod.create_share_link,
    ):
        src = inspect.getsource(fn)
        assert "get_owner_token" in src or "owner_token" in inspect.signature(fn).parameters
        assert "owner_token=owner_token" in src or "owner_token=owner_token" in inspect.getsource(
            report_mod._check_report_owner
        )

    helper = inspect.getsource(report_mod._check_report_owner)
    assert "owner_token=owner_token" in helper
    create_src = inspect.getsource(report_mod.create_report)
    list_src = inspect.getsource(report_mod.list_reports)
    assert "owner_token=owner_token" in create_src
    assert "owner_token=owner_token" in list_src


@pytest_asyncio.fixture
async def report_app_and_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'report618.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from fastapi import FastAPI
    from app.core.database import get_async_db
    from app.core.auth import get_current_user
    from app.api.routes import report as report_mod

    async def override_get_async_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    app.include_router(report_mod.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "logged-in"}
    try:
        yield app, session_factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_item13_anon_token_session_list_reports(report_app_and_db):
    """Logged-in caller + X-Session-Token can list an anonymous token session.

    Pre-fix: verify_session_owner was called without owner_token → 404 even
    with a valid header. Legacy NULL-token sessions stay grandfathered.
    """
    app, session_factory = report_app_and_db
    async with session_factory() as db:
        db.add(Conversation(id="anon-tok", user_id=None, owner_token="secret-tok", title="anon"))
        db.add(Conversation(id="anon-legacy", user_id=None, owner_token=None, title="legacy"))
        db.add(
            Report(
                id="r-tok",
                session_id="anon-tok",
                title="t",
                format="markdown",
                status="completed",
            )
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/api/v1/reports", params={"session_id": "anon-tok"})
        wrong = await client.get(
            "/api/v1/reports",
            params={"session_id": "anon-tok"},
            headers={"X-Session-Token": "nope"},
        )
        ok = await client.get(
            "/api/v1/reports",
            params={"session_id": "anon-tok"},
            headers={"X-Session-Token": "secret-tok"},
        )
        legacy = await client.get(
            "/api/v1/reports", params={"session_id": "anon-legacy"}
        )
        detail_ok = await client.get(
            "/api/v1/reports/r-tok", headers={"X-Session-Token": "secret-tok"}
        )
        detail_missing = await client.get("/api/v1/reports/r-tok")

    assert missing.status_code == 404
    assert wrong.status_code == 404
    assert ok.status_code == 200, ok.text
    assert ok.json()["success"] is True
    assert ok.json()["data"]["total"] == 1
    assert legacy.status_code == 200
    assert detail_ok.status_code == 200
    assert detail_missing.status_code == 404
