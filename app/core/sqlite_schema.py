"""Versioned local schema bootstrap using the same revisions as deployment."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine


def upgrade_sqlite_schema(engine: Engine) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    # Reserve SQLite's writer before inspecting the version. Other bootstraps
    # wait, then inspect the committed schema, rather than racing create/ALTER.
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            tables = set(inspect(connection).get_table_names())
            current = MigrationContext.configure(connection).get_current_revision()
            if tables - {"alembic_version"} and current is None:
                raise RuntimeError(
                    "Unversioned SQLite database: startup refused. Back up the database, "
                    "compare its schema with a known Alembic revision, stamp only that "
                    "verified revision, then run alembic upgrade head with all writers stopped. "
                    "Do not stamp head to bypass migrations. See docs/DEPLOYMENT.md."
                )
            if current != head:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
