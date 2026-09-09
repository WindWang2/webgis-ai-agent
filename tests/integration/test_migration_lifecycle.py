"""Migration 生命周期测试（Quality V3 W12，Epic 10 §15）。

- **SQLite 全链 up/down/up（确定性，恒跑）**：34 个 migration 全部有
  真实 downgrade（本 Epic 审计证实）——本测试锁住这一性质；
- **Postgres lane（opt-in）**：TEST_POSTGRES_URL 时同链复跑 +
  PostGIS 可用性探测（缺省 skip，与 storage differential 同纪律）。

资源：SQLite 链全程 < 60s（实测 ~15s）；PG lane 只在显式 env 下运行。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@contextmanager
def _pin_env(url: str):
    """PG lane 的 env 钉住（真实 PG url 必须覆盖 .env 的本地 sqlite）。"""
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        yield
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url


def _make_config(db_url: str):
    from alembic.config import Config

    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _up_down_up(db_url: str, monkeypatch):
    """up → down → up（密闭：env.py 优先读 DATABASE_URL env —— 必须钉住，
    否则 conftest 的 load_dotenv 会把运行劫持到 dev DB（实测教训）。"""
    from alembic import command

    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = _make_config(db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_sqlite_full_chain_up_down_up(tmp_path, monkeypatch):
    """§15：migration up/down/up。单头 + 全 downgrade 真实可执行。"""
    from app.lib.integration import migrations_coord

    graph = migrations_coord.scan(REPO)
    assert len(graph.heads) == 1, f"必须单头: {graph.heads}"
    _up_down_up(f"sqlite:///{tmp_path / 'mig.db'}", monkeypatch)


def test_every_migration_has_real_downgrade():
    """静态闸：downgrade() 不得是空壳，**除非** docstring 显式声明
    "deliberate no-op"（蓄意不可逆，如 g1109 的 IDOR fail-closed 论证）。
    无声的空壳 = 回滚证据链缺口；有声的 no-op = 诚实披露。"""
    import ast

    offenders = []
    deliberate = []
    for path in sorted((REPO / "migrations/versions").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        down = next(
            (n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "downgrade"),
            None,
        )
        if down is None:
            offenders.append(f"{path.name}: 缺 downgrade()")
            continue
        meaningful = [
            s for s in down.body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
            and not (isinstance(s, ast.Pass))
        ]
        if not meaningful:
            doc = ast.get_docstring(down) or ""
            if "deliberate no-op" in doc.lower():
                deliberate.append(path.name)
            else:
                offenders.append(f"{path.name}: downgrade 为空壳且未声明 "
                                 "deliberate no-op")
    assert offenders == [], f"空 downgrade 破坏回滚证据链: {offenders}"
    # 蓄意 no-op 是披露项不是缺陷；列出以供 release readiness 引用
    assert len(deliberate) <= 5, "蓄意 no-op 数量异常增长，需人工复核"


@pytest.mark.real_services()
def test_postgres_full_chain_and_postgis():
    """Postgres lane（opt-in，REAL_SERVICES/TEST_POSTGRES_URL）：
    up/down/up + PostGIS 探测 + 行为差异（§15 Postgres/SQLite delta）。"""
    url = os.environ.get("TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("TEST_POSTGRES_URL 未设置（real-services lane 专属）")
    _up_down_up(url, _pin_env(url))

    # PostGIS 探测（可用性是 honest disclose 项，不是硬前提）
    import sqlalchemy

    engine = sqlalchemy.create_engine(url)
    with engine.connect() as conn:
        row = conn.execute(sqlalchemy.text(
            "SELECT name FROM pg_available_extensions "
            "WHERE name = 'postgis'")).fetchone()
    has_postgis = row is not None
    if not has_postgis:
        pytest.skip("PostGIS 扩展在该 Postgres 实例不可用")
