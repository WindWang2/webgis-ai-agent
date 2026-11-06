"""Exercise real versioned upgrades and refuse destructive/ambiguous cutovers."""
import hashlib
import importlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.core.sqlite_schema import upgrade_sqlite_schema


def _upgrade(connection, revision):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


def test_local_startup_upgrades_managed_0099_data_to_head(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'managed.db'}")
    try:
        with engine.begin() as conn:
            _upgrade(conn, "0099_story_map_assets")
            conn.execute(text("INSERT INTO conversations(id, owner_token) VALUES (:id,:token)"),
                         [{"id": str(i), "token": f"raw-{i}"} for i in range(502)])
        # Credential conversion cannot race old writers during a normal start.
        with pytest.raises(RuntimeError, match="maintenance cutover"):
            upgrade_sqlite_schema(engine)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT owner_token FROM conversations WHERE id='501'")).scalar_one() == "raw-501"
        monkeypatch.setenv("OWNER_TOKEN_MIGRATION_QUIESCED", "1")
        upgrade_sqlite_schema(engine)
        upgrade_sqlite_schema(engine)
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() == "0103_workflow_run_session"
            assert "session_id" in {c["name"] for c in inspect(conn).get_columns("workflow_runs")}
            assert conn.execute(text("SELECT count(*) FROM conversations")).scalar_one() == 502
            assert conn.execute(text("SELECT owner_token FROM conversations WHERE id='501'")).scalar_one() == hashlib.sha256(b"raw-501").hexdigest()
    finally:
        engine.dispose()


def test_fresh_sqlite_bootstraps_are_serialized(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'new.db'}", connect_args={"timeout": 30})
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: upgrade_sqlite_schema(engine), range(2)))
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() == "0103_workflow_run_session"
            assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 1
    finally:
        engine.dispose()


def test_unversioned_local_database_is_preserved_and_refused(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE conversations(id TEXT PRIMARY KEY, owner_token TEXT)")
            conn.exec_driver_sql("INSERT INTO conversations VALUES ('old','secret')")
        with pytest.raises(RuntimeError, match="Do not stamp head"):
            upgrade_sqlite_schema(engine)
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT * FROM conversations").all() == [("old", "secret")]
            assert inspect(conn).get_table_names() == ["conversations"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("incompatible", ["event", "action", "long_session"])
def test_spatial_downgrade_refuses_before_any_ddl(incompatible, monkeypatch):
    engine = create_engine("sqlite://")
    module = importlib.import_module("migrations.versions.0100_spatial_watch_action_revision")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE spatial_events(event_id TEXT, org_id TEXT, session_id VARCHAR(128))")
            conn.exec_driver_sql("CREATE TABLE spatial_watches(session_id VARCHAR(128), revision INTEGER)")
            conn.exec_driver_sql("CREATE TABLE spatial_watch_fires(watch_id TEXT, event_id TEXT, action TEXT)")
            if incompatible == "event":
                conn.exec_driver_sql("INSERT INTO spatial_events VALUES ('same','a',NULL), ('same','b',NULL)")
            elif incompatible == "action":
                conn.exec_driver_sql("INSERT INTO spatial_watch_fires VALUES ('w','e','a'),('w','e','b')")
            else:
                conn.execute(text("INSERT INTO spatial_watches VALUES (:sid,1)"), {"sid": "s" * 65})
            before = conn.exec_driver_sql("SELECT name,sql FROM sqlite_master ORDER BY name").all()
            monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(conn)))
            with pytest.raises(RuntimeError, match="before schema changes"):
                module.downgrade()
            assert conn.exec_driver_sql("SELECT name,sql FROM sqlite_master ORDER BY name").all() == before
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_schema_failure_stops_application_startup(monkeypatch):
    from app import main
    from app.core import database

    def broken():
        raise RuntimeError("migration refused")

    monkeypatch.setattr(database, "init_db", broken)
    with pytest.raises(RuntimeError, match="migration refused"):
        async with main.lifespan(main.app):
            pytest.fail("startup must not yield")


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [None, {"user_id": "owner"}])
async def test_expired_terrain_capability_rechecks_live_owner(user, monkeypatch):
    from app.api.routes import layer
    from app.core import signing

    expected = object()
    verify = AsyncMock(return_value=expected)
    monkeypatch.setattr(signing, "verify_signature", lambda *args: False)
    monkeypatch.setattr(layer, "verify_session_owner", verify)
    request = SimpleNamespace(path_params={"ref_id": "ref:dem"})
    db = SimpleNamespace(execute=AsyncMock())
    result = await layer._require_owned_session_qtoken(
        request, session_id="session-live", token=None, owner_token="live-secret",
        exp=1, sig="expired", db=db, _user=user,
    )
    assert result is expected
    verify.assert_awaited_once_with(db, session_id="session-live", user_id="owner" if user else None, owner_token="live-secret")
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_terrain_capability_alone_grants_nothing(monkeypatch):
    from fastapi import HTTPException
    from app.api.routes import layer
    from app.core import signing

    monkeypatch.setattr(signing, "verify_signature", lambda *args: False)
    verify = AsyncMock(side_effect=HTTPException(404, "Session not found"))
    monkeypatch.setattr(layer, "verify_session_owner", verify)
    with pytest.raises(HTTPException) as error:
        await layer._require_owned_session_qtoken(
            SimpleNamespace(path_params={"ref_id": "ref:dem"}),
            session_id="session-live", token=None, owner_token=None,
            exp=1, sig="expired", db=SimpleNamespace(), _user=None,
        )
    assert error.value.status_code == 404
    verify.assert_awaited_once()
