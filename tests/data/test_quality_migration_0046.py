"""V9 P1 —— migration 0046（quality_reports / quality_rule_results）up/down 守卫。

对齐 test_uploads_index_migration.py 的守卫模式：
  1. ORM 模型声明了两张表（create_all 新库直接带上）；
  2. alembic upgrade head 后两表真实存在（存量库升级也带上）；
  3. downgrade -1 后两表消失、re-upgrade 幂等（往返）。
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # tests/data/ → tests/ → 仓库根
TABLES = ("quality_reports", "quality_rule_results")


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    # 继承当前进程环境（Windows 下整替 PATH 会丢系统目录 → python 起不来），
    # 只覆盖 DATABASE_URL 指向临时库。
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env.setdefault("JWT_SECRET_KEY", "test-secret-migration-32-chars-okay")
    env.setdefault("USE_REDIS", "false")
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def test_models_declare_quality_tables():
    import app.models.data_quality  # noqa: F401 — 显式注册进 Base.metadata
    from app.core.database import Base

    assert {"quality_reports", "quality_rule_results"} <= set(
        Base.metadata.tables.keys()
    )


def test_migration_up_down_roundtrip(tmp_path):
    db_path = tmp_path / "roundtrip.db"
    up = _alembic(db_path, "upgrade", "head")
    assert up.returncode == 0, (
        f"upgrade failed rc={up.returncode}\nstdout={up.stdout[-2000:]}\n"
        f"stderr={up.stderr[-2000:] if up.stderr else ''}"
    )
    present = _tables(db_path)
    for t in TABLES:
        assert t in present, f"upgrade head 后缺少 {t}"

    # 显式回退到 0046 的父版本（不耦合后续新增的 head 数量）
    down = _alembic(db_path, "downgrade", "c1e2f3a4b5c6")
    assert down.returncode == 0, (
        f"downgrade failed rc={down.returncode}\nstdout={down.stdout[-2000:]}\n"
        f"stderr={down.stderr[-2000:] if down.stderr else ''}"
    )
    after_down = _tables(db_path)
    for t in TABLES:
        assert t not in after_down, f"downgrade 至 0046 之前后 {t} 应消失"

    again = _alembic(db_path, "upgrade", "head")
    assert again.returncode == 0, (
        f"re-upgrade failed rc={again.returncode}\nstdout={again.stdout[-2000:]}\n"
        f"stderr={again.stderr[-2000:] if again.stderr else ''}"
    )
    for t in TABLES:
        assert t in _tables(db_path), f"re-upgrade 后缺少 {t}（幂等往返）"
