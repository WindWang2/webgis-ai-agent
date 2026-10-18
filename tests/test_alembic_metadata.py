"""Test: Alembic env.py must have target_metadata set (not None)."""
import ast


def test_target_metadata_not_none():
    """target_metadata must be assigned to Base.metadata, not None."""
    with open("migrations/env.py", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "target_metadata":
                    # Must NOT be None constant
                    assert not (isinstance(node.value, ast.Constant) and node.value.value is None), (
                        "target_metadata = None — Alembic autogenerate will produce empty migrations. "
                        "Should be: from app.core.database import Base; target_metadata = Base.metadata"
                    )
                    return

    pytest.fail("target_metadata assignment not found")


import pytest


# ══ V9 P6（ADR-0140 前身段）：编号唯一性 + 单头强校验 + 领号自证 ═══════
# 历史：0034×4、0035×3 两轮撞号（#1229/#1224 现场），此前本文件只查
# target_metadata —— 撞号在 CI 不可见。现在：revision id 重复即 fail、
# 前缀跨段位即 fail、多头即 fail（merge revision 是唯一合法收敛）。

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSIONS = REPO_ROOT / "migrations" / "versions"


def _iter_migration_files():
    return [p for p in sorted(VERSIONS.glob("*.py")) if p.name != "__init__.py"]


def test_revision_ids_globally_unique():
    """编号重复即 fail（不再只查单头）—— 0034/0035 撞号的直接形态。"""
    seen = {}
    for path in _iter_migration_files():
        rev = _extract_revision(path)
        assert rev, f"{path.name}: 缺 revision 声明"
        seen.setdefault(rev, []).append(path.name)
    dupes = {rev: files for rev, files in seen.items() if len(files) > 1}
    assert not dupes, f"revision id 重复（撞号）: {dupes}"


def _extract_revision(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            targets = node.targets
        for t in targets:
            if t.id == "revision" and isinstance(node.value, ast.Constant):
                return node.value.value
    return None


def test_single_head_via_script_directory():
    """alembic heads 单头强校验（ScriptDirectory 静态计算，无需 DB）。
    merge revision（c0d8322aa2cb / c1e2f3a4b5c6 先例）是唯一合法收敛。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    heads = sorted(str(h) for h in script.get_heads())
    assert len(heads) == 1, f"多头现场: {heads}"


def test_alloc_check_fails_on_duplicate_number_copy(tmp_path):
    """自证有效：含重复编号的临时副本上 `alloc_migration.py --check` 必须
    fail（门禁不能是永真的）。"""
    script = REPO_ROOT / "scripts" / "alloc_migration.py"
    tmp_versions = tmp_path / "versions"
    shutil.copytree(VERSIONS, tmp_versions,
                    ignore=shutil.ignore_patterns("__pycache__"))
    dup_source = next(p for p in tmp_versions.glob("0046_*.py"))
    dup = dup_source.read_text(encoding="utf-8").replace(
        'revision: str = "0046_quality_reports"',
        'revision: str = "0046_dupe_selfproof"').replace(
        'down_revision: Union[str, Sequence[str], None] = "c1e2f3a4b5c6"',
        'down_revision: Union[str, Sequence[str], None] = None')
    (tmp_versions / "0046_dupe_selfproof.py").write_text(dup, encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(script), "--check", str(tmp_versions)],
        capture_output=True, text=True, timeout=300,
    )
    assert res.returncode != 0, "重复编号必须 fail（门禁自证）"
    assert "0046" in res.stdout


def test_alloc_check_passes_on_current_tree():
    """当前树必须通过领号/唯一性/单头自检。"""
    script = REPO_ROOT / "scripts" / "alloc_migration.py"
    res = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True, text=True, timeout=300, cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_env_registers_every_model_table():
    """security F-09：env.py 的 model 注册段执行后，Base.metadata 必须覆盖
    app/models 下全部 ``__tablename__``（漏登记 = autogenerate 生成 drop_table）。"""
    env_src = (REPO_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    start = env_src.index("from app.core.database import Base")
    end = env_src.index("target_metadata = Base.metadata")
    registration = env_src[start:end]
    tablenames = set()
    for path in sorted((REPO_ROOT / "app" / "models").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets
            ) and isinstance(node.value, ast.Constant):
                tablenames.add(node.value.value)
    # 探针前缀：切片起点（from app.core.database import Base）之外的依赖——
    # env.py 顶部 import os 与 config = context.config 都不在切片内，而
    # DATABASE_URL 覆盖段（#1592 挪到 Base import 之后）会用到它们。前缀桩掉
    # config（真实 context.config 只在 alembic 进程内存在），使切片内容可独立
    # 执行，对 env.py 段内语句顺序漂移免疫。
    probe = (
        "import os\n"
        "from types import SimpleNamespace as _NS\n"
        "config = _NS(set_main_option=lambda *a, **k: None)\n"
        + registration
        + "\nimport json\nprint('TABLES=' + json.dumps(sorted(Base.metadata.tables)))\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=120,
    )
    assert res.returncode == 0, res.stderr[-2000:]
    import json

    line = [ln for ln in res.stdout.splitlines() if ln.startswith("TABLES=")][-1]
    registered = set(json.loads(line[len("TABLES="):]))
    missing = tablenames - registered
    assert not missing, f"env.py 未注册的表（autogenerate 会 drop）: {sorted(missing)}"


# ══ #1638：DATABASE_URL 含 % （百分号编码密码）不得炸 alembic Config ══════
# alembic 的 Config 底层是 configparser（默认 BasicInterpolation），
# set_main_option 写入时即校验插值语法：裸 % 后不是 %(name)s / %% 就抛
# ValueError: invalid interpolation syntax。RDS/Azure 等托管库的强密码普遍含
# @/%/: 字符、按 URL 规则必须百分号编码（p%40ss 是常态）——不转义则
# `alembic upgrade head` 崩在 env.py 入口、docker-entrypoint/k8s initContainer
# 拒绝启动，发布窗口全阻塞（而应用本体 create_engine 原生接受 % URL，
# 故障形态是"应用能连库、迁移跑不了"的不对称）。修复：写入前 %% 转义，
# configparser 读回时自动还原为单 %，online/offline 两个 mode 消费原 URL。


def test_env_url_segment_escapes_percent_for_configparser(monkeypatch):
    """执行 env.py 的 URL 设置段：含 % 的 DATABASE_URL 不得抛 ValueError，
    且 config.get_main_option 读回未转义的原文（往返一致）。"""
    import os

    from alembic.config import Config

    from app.core.database import _to_sync_url

    env_src = (REPO_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    start = env_src.index('db_url = os.getenv("DATABASE_URL")')
    end = env_src.index("# 审计 I6：必须显式 import")
    segment = env_src[start:end]
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p%40ss@localhost/db")
    config = Config()
    ns: dict = {"config": config, "os": os, "_to_sync_url": _to_sync_url}
    exec(segment, ns)
    assert config.get_main_option("sqlalchemy.url") == (
        "postgresql+psycopg2://u:p%40ss@localhost/db"
    )
    # get_section（run_migrations_online 的 engine_from_config 消费路径）
    # 同样必须还原为单 % 原 URL。
    assert config.get_section("alembic", {}).get("sqlalchemy.url") == (
        "postgresql+psycopg2://u:p%40ss@localhost/db"
    )


def test_alembic_upgrade_head_survives_percent_encoded_url(tmp_path):
    """端到端：DATABASE_URL 带 %xx（路径级百分号）时 alembic upgrade head
    必须跑完——覆盖 set_main_option 转义 → configparser 读回还原 →
    engine_from_config 消费 section 的整条链路（生产崩溃形态即此）。"""
    import os
    import sqlite3

    db_dir = tmp_path / "mig%40dir"
    db_dir.mkdir()
    db_path = db_dir / "db%20file.db"
    url = "sqlite:///" + str(db_path).replace("\\", "/")
    res = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env={
            **os.environ,  # 继承 SYSTEMROOT 等平台变量（Windows 子进程 _overlapped 需要）
            "PATH": os.environ.get("PATH", ""),
            "DATABASE_URL": url,
            "JWT_SECRET_KEY": "test-secret-migration-32-chars-okay",
            "USE_REDIS": "false",
            "HOME": str(Path.home()),
        },
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert res.returncode == 0, f"upgrade head 失败:\n{res.stdout}\n{res.stderr}"
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert "users" in tables, "迁移链必须真正落地（转义不得改变目标库路径）"
    assert "alembic_version" in tables
