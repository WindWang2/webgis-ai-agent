"""Versioned local schema bootstrap using the same revisions as deployment."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

#: 本地启动托管水位（#2123 迁移 watermark 残余）：启动路径只把 schema 推进到
#: 该 revision（含），**不追 alembic head**。水位之上的修订（0104+）由显式
#: 维护面掌管（deploy/maintenance-upgrade.sh / `alembic upgrade head` 一次性
#: 容器，见 docs/DEPLOYMENT.md）——其中 0102 凭据切换等修订要求全部写入者
#: 静默，普通启动绝不能在 head 漂移时隐式扩大启动面。新修订毕业后上抬本
#: 常量，并同步 tests/unit/test_review_migration_cutover.py 的水位断言。
_MANAGED_WATERMARK = "0103_workflow_run_session"


def upgrade_sqlite_schema(engine: Engine) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    target = _MANAGED_WATERMARK
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
            if current != target:
                # upgrade 到 target 在当前版本已是 target 的后代（维护面越过
                # 水位）时是 no-op —— 启动路径绝不回退维护面推进过的 schema。
                config.attributes["connection"] = connection
                command.upgrade(config, target)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
