"""Test: Alembic env.py must have target_metadata set (not None)."""
import ast


def test_target_metadata_not_none():
    """target_metadata must be assigned to Base.metadata, not None."""
    with open("migrations/env.py") as f:
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
