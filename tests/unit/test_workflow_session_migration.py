"""Existing workflow runs retain truthful NULL scope across the additive migration."""
import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_workflow_session_migration_preserves_legacy_rows(monkeypatch):
    module = importlib.import_module("migrations.versions.0103_workflow_run_session")
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE workflow_runs(id TEXT PRIMARY KEY, status TEXT)"))
            connection.execute(text("INSERT INTO workflow_runs VALUES ('legacy', 'failed')"))
            monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
            module.upgrade()
            assert connection.execute(text("SELECT session_id FROM workflow_runs WHERE id='legacy'")).scalar_one() is None
            connection.execute(text("UPDATE workflow_runs SET session_id='owned-evidence' WHERE id='legacy'"))
            assert connection.execute(text("SELECT session_id FROM workflow_runs")).scalar_one() == "owned-evidence"
            module.downgrade()
            assert "session_id" not in {column["name"] for column in inspect(connection).get_columns("workflow_runs")}
            assert connection.execute(text("SELECT status FROM workflow_runs WHERE id='legacy'")).scalar_one() == "failed"
    finally:
        engine.dispose()
