"""Semantic Integration Manifest 测试（Quality V3 W6）。

用真实 git 合成仓库（tmp_path 内 init + commits + branches）做正/负例：
分支间真实 diff、真实 merge-base 三点语义，不 mock git。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.lib.integration.manifest import build_manifest, changed_files
from app.lib.integration.ownership import OwnershipDocument, OwnershipRule


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture()
def synthetic_repo(tmp_path: Path) -> Path:
    """master + feature 分支：tool/算法/migration/CHANGELOG/前端契约混改。"""
    repo = tmp_path / "repo"
    (repo / "app/tools").mkdir(parents=True)
    (repo / "app/lib/gis/algorithms").mkdir(parents=True)
    (repo / "migrations/versions").mkdir(parents=True)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "docs/integration").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    (repo / "app/tools/existing.py").write_text(
        'name = "existing_tool"\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    _git(repo, "checkout", "-q", "-b", "feat/probe")
    (repo / "app/tools/new_tool.py").write_text(
        'register(ToolDescriptor(\n    name="new_cool_tool",\n))\n',
        encoding="utf-8")
    (repo / "app/lib/gis/algorithms/new_alg.py").write_text(
        'name = "kriging_new"\n', encoding="utf-8")
    (repo / "migrations/versions/0034_probe.py").write_text(
        'revision = "0034_probe"\ndown_revision = "0033_base"\n',
        encoding="utf-8")
    with open(repo / "CHANGELOG.md", "a", encoding="utf-8") as fh:
        fh.write("- probe\n")
    (repo / "frontend/src/api/mapTypes.ts").write_text(
        "export interface MapSpec { v: 1 }\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feature changes")
    return repo


def _doc_for(tmp_path: Path) -> OwnershipDocument:
    return OwnershipDocument(
        version=1, adr_watermark=118, migration_watermark=33,
        rules=(
            OwnershipRule(pattern="docs/adr/*", owner="docs",
                          policy="allocator"),
            OwnershipRule(pattern="migrations/versions/*", owner="core",
                          policy="allocator", suites=("integration",)),
            OwnershipRule(pattern="CHANGELOG.md", owner="docs",
                          policy="append-only"),
            OwnershipRule(pattern="app/tools/*", owner="tools",
                          policy="additive-only", suites=("backend",)),
            OwnershipRule(pattern="app/lib/gis/algorithms/*", owner="algorithms",
                          policy="additive-only", suites=("science",)),
            OwnershipRule(pattern="frontend/src/api/*", owner="frontend",
                          policy="coordinated", suites=("frontend",)),
        ))


def test_manifest_surfaces_and_risk(synthetic_repo: Path):
    m = build_manifest("feat/probe", "master", synthetic_repo,
                       doc=_doc_for(synthetic_repo))
    assert m.files_changed == [
        "CHANGELOG.md",
        "app/lib/gis/algorithms/new_alg.py",
        "app/tools/new_tool.py",
        "frontend/src/api/mapTypes.ts",
        "migrations/versions/0034_probe.py",
    ]
    assert m.tools == ["new_cool_tool"], "语义 ID 应从描述符提取"
    assert m.algorithms == ["kriging_new"]
    assert m.migrations == ["migrations/versions/0034_probe.py"]
    assert m.frontend_contracts == ["frontend/src/api/mapTypes.ts"]
    assert set(m.shared_files) == {"CHANGELOG.md",
                                   "migrations/versions/0034_probe.py"}
    assert m.risk_level == "high", "migration + append-only 必须高风险"
    # suites = 规则自带（backend/science）∪ 风险兜底（high → quick+integration）
    assert set(m.required_suites) == {"quick", "integration", "backend",
                                      "science", "frontend"}
    assert m.git_commit and m.branch == "feat/probe" and m.base == "master"


def test_manifest_deterministic_and_serializable(synthetic_repo: Path):
    doc = _doc_for(synthetic_repo)
    a = build_manifest("feat/probe", "master", synthetic_repo, doc=doc)
    b = build_manifest("feat/probe", "master", synthetic_repo, doc=doc)
    assert a.to_json() == b.to_json(), "同输入必须字节相同（无时间戳）"
    json.loads(a.to_json())  # 合法 JSON


def test_manifest_low_risk_branch(synthetic_repo: Path):
    _git(synthetic_repo, "checkout", "-q", "master")
    _git(synthetic_repo, "checkout", "-q", "-b", "feat/plain")
    (synthetic_repo / "app/tools/plain.py").write_text(
        "# 普通工具维护，无 ID、无共享面\n", encoding="utf-8")
    _git(synthetic_repo, "add", "-A")
    _git(synthetic_repo, "commit", "-q", "-m", "plain")
    m = build_manifest("feat/plain", "master", synthetic_repo,
                       doc=_doc_for(synthetic_repo))
    # additive-only 文件命中 → medium（无共享面/无 migration）
    assert m.risk_level == "medium"
    assert set(m.required_suites) == {"quick", "backend"}
    assert m.shared_files == []
    assert m.ids_extracted is False, "无 name= → 提取降级必须显式披露"
    assert "app/tools/plain.py" in m.domains["tools"]


def test_changed_files_missing_branch_raises(synthetic_repo: Path):
    from contextlib import contextmanager

    @contextmanager
    def _no_raise():
        yield

    with pytest.raises(RuntimeError):
        changed_files("feat/does-not-exist", "master", synthetic_repo)


def test_manifest_real_repo_self_check():
    """真实仓库本分支 manifest 可生成（base=origin/master）。"""
    m = build_manifest("HEAD", "origin/master")
    assert m.files_changed, "本分支应有改动"
    assert "quick" in m.required_suites
    d = m.as_dict()
    assert d["risk"]["level"] in ("none", "medium", "high")
