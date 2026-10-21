"""#563 regression: manage.py's module-level ``rich`` imports must resolve.

manage.py (the ops CLI entrypoint: create-admin / check / dev) imports
``rich.console/table/panel`` at module scope. `rich` was missing from every
requirements manifest, so a clean ``pip install -r requirements-dev.txt``
crashed with ModuleNotFoundError on EVERY manage.py command. The dependency
is now declared in requirements.txt (pulled in by requirements-dev.txt
via ``-r``); these tests pin the contract so the drift cannot silently
return (CI never executed manage.py before, so nothing caught it).
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_manage_py_help_runs_with_declared_deps():
    """python manage.py --help must exit 0 on a fresh install: the module-level
    rich imports resolve because rich is declared in requirements.txt."""
    result = subprocess.run(
        [sys.executable, "manage.py", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"manage.py --help failed (rc={result.returncode}): "
        f"stdout={result.stdout[-800:]!r} stderr={result.stderr[-800:]!r}"
    )
    assert "usage" in result.stdout.lower()


def test_rich_declared_in_requirements():
    """The dependency manifest must declare rich so a clean install resolves
    manage.py's top-level imports."""
    reqs = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^rich\s*[>=<]", reqs, re.M), (
        "requirements.txt does not declare the rich dependency that "
        "manage.py imports at module level"
    )


# ── #1750: create-admin 在未初始化 schema 上的可行动失败 ──────────────────
# init_db() 对非 sqlite 故意跳过 create_all（schema 归 Alembic，audit #839）。
# 此前 cmd_create_admin 无表存在预检：全新 Postgres 上以裸
# `relation "users" does not exist` traceback 失败，运维得不到「先跑
# alembic upgrade head」的指引。以下测试钉住预检 + 指引契约。


def _run_manage(args, env_overrides=None, timeout=120):
    """子进程跑 manage.py；env 继承 pytest 进程（conftest 基线已就位），
    按需覆盖 DATABASE_URL 等键（None = 删除该键）。"""
    env = dict(os.environ)
    env.setdefault("JWT_SECRET_KEY", "test-secret")
    env.setdefault("LLM_API_KEY", "sk-test")
    for k, v in (env_overrides or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        timeout=timeout,
    )


def test_create_admin_happy_path_on_fresh_sqlite(tmp_path):
    """正常路径不被预检破坏：全新 SQLite 上 create-admin 直接成功。"""
    proc = _run_manage(
        ["create-admin", "admin", "admin@example.com", "password123"],
        {"DATABASE_URL": f"sqlite:///{tmp_path / 'webgis.db'}"},
    )
    assert proc.returncode == 0, (
        f"rc={proc.returncode} stdout={proc.stdout[-600:]!r} "
        f"stderr={proc.stderr[-600:]!r}"
    )
    assert "Admin 创建成功" in proc.stdout


def test_create_admin_unreachable_postgres_fails_without_traceback(tmp_path):
    """非 sqlite 库连不上：可读错误 + exit 2，而不是裸 traceback。"""
    proc = _run_manage(
        ["create-admin", "admin", "admin@example.com", "password123"],
        {
            # 裸 postgresql:// —— _to_sync_url/_to_async_url 显式支持的形态
            # （#1592；带 +psycopg2 后缀会让 async engine 构造在 import 期炸）。
            "DATABASE_URL": "postgresql://nobody:nobody@127.0.0.1:1/nodb",
            "ALLOW_CREATE_ALL_ON_POSTGRES": None,
        },
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2
    assert "无法连接数据库" in combined
    assert "Traceback" not in combined


def test_create_admin_unmigrated_schema_guides_alembic_upgrade(tmp_path):
    """#1750 主场景：库「连上」但 users 表不存在（schema 未迁移）——
    必须输出 `alembic upgrade head` 指引并 exit 2，而非 traceback。

    用 stub inspector 模拟「可连接、无表」的非 sqlite 库（无需真实
    Postgres）；经 manage.main() 全路径（init_db → 预检）验证。
    """
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import os\n"
        "import sys\n"
        "\n"
        "sys.path.insert(0, os.getcwd())\n"
        "import sqlalchemy\n"
        "\n"
        "class _StubInspector:\n"
        "    def get_table_names(self):\n"
        "        return []\n"
        "\n"
        "sqlalchemy.inspect = lambda engine: _StubInspector()\n"
        "import manage\n"
        "manage.main()\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.setdefault("JWT_SECRET_KEY", "test-secret")
    env.setdefault("LLM_API_KEY", "sk-test")
    env["DATABASE_URL"] = "postgresql://nobody@127.0.0.1:1/nodb"
    env.pop("ALLOW_CREATE_ALL_ON_POSTGRES", None)
    proc = subprocess.run(
        [sys.executable, str(driver), "create-admin", "admin",
         "admin@example.com", "password123"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        timeout=120,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2, combined[-800:]
    assert "users 表不存在" in combined
    assert "alembic upgrade head" in combined
    assert "Traceback" not in combined