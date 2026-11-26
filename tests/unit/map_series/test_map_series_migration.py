"""migration g51 upgrade/downgrade 往返（DATABASE_URL 指向 /tmp 临时 sqlite）。

模式同 tests/unit/test_workflow_migration.py（in-process alembic command）。
"""
from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "map_series_g51.db"
    url = f"sqlite:///{db_path}"
    # migrations/env.py 读 DATABASE_URL 并覆盖 config 的 sqlalchemy.url ——
    # 必须钉 env 变量，否则 CI 的 Postgres DATABASE_URL 会赢。
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg, str(db_path)


def _tables(db_path):
    import sqlite3

    con = sqlite3.connect(db_path)
    tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    return tabs


def _columns(db_path, table):
    import sqlite3

    con = sqlite3.connect(db_path)
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    con.close()
    return set(cols)


def test_g51_upgrade_downgrade_roundtrip(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")

    assert "cartography_map_series" in _tables(db_path)
    assert "cartography_map_series_versions" in _tables(db_path)
    for col in (
        "id", "org_id", "name", "description", "is_builtin", "creator_id",
        "status", "design_spec_id", "spec_version", "payload",
        "current_version", "structural_digest", "created_at", "updated_at",
        "retired_at", "retirement_note",
    ):
        assert col in _columns(db_path, "cartography_map_series"), col
    for col in (
        "id", "org_id", "series_id", "version", "payload", "parent_version_id",
        "structural_digest", "deprecated_at", "deprecation_note",
        "created_by", "created_at",
    ):
        assert col in _columns(db_path, "cartography_map_series_versions"), col

    # downgrade 到 g50：两张表消失
    command.downgrade(cfg, "g50_carto_design_series")
    assert "cartography_map_series" not in _tables(db_path)
    assert "cartography_map_series_versions" not in _tables(db_path)

    # 再升级（可重入）
    command.upgrade(cfg, "head")
    assert "cartography_map_series" in _tables(db_path)
