"""Lakehouse V7 — migration 0034 守卫（单 head / additive up-down-up）。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PY = sys.executable


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = {
        **__import__("os").environ,
        "DATABASE_URL": db_url,
        "WEBGIS_MIGRATION_DB": db_url,
    }
    return subprocess.run(
        [VENV_PY, "-m", "alembic", *args],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )


def test_single_head():
    """0034 不产生双 head（并发 Epic 竞争面 —— R0-26 的持续守卫）。"""
    result = _alembic("", "heads")
    assert result.returncode == 0, result.stderr
    heads = [
        line.strip().split(" ")[0]
        for line in result.stdout.splitlines() if "(head)" in line
    ]
    assert len(heads) == 1, f"multiple heads: {heads}"
    assert heads[0].startswith("0034_lakehouse_catalog")


def test_migration_up_down_up_sqlite(tmp_path):
    db = tmp_path / "mig.db"
    url = f"sqlite:///{db}"
    up1 = _alembic(url, "upgrade", "head")
    assert up1.returncode == 0, up1.stderr[-2000:]
    import sqlite3

    conn = sqlite3.connect(str(db))
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()
    assert "lakehouse_catalog_items" in tables
    down = _alembic(url, "downgrade", "0033_geocompute_v6_cluster")
    assert down.returncode == 0, down.stderr[-2000:]
    conn = sqlite3.connect(str(db))
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()
    assert "lakehouse_catalog_items" not in tables
    up2 = _alembic(url, "upgrade", "head")
    assert up2.returncode == 0, up2.stderr[-2000:]
