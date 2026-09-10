"""合并模拟测试（Quality V3 W8）—— Epic §15 完成证明的单元层。

真实 git 合成仓库 + 两个模拟分支，覆盖五类冲突全部检出 + 兼容重叠
正例 + unknown/not_checked 诚实契约。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.lib.integration import merge_sim
from app.lib.integration.manifest import build_manifest
from app.lib.integration.merge_sim import compare_pair
from app.lib.integration.ownership import OwnershipDocument, OwnershipRule


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


DOC = OwnershipDocument(
    version=1, adr_watermark=118, migration_watermark=33,
    rules=(
        OwnershipRule(pattern="migrations/versions/*", owner="core",
                      policy="allocator", suites=("integration",)),
        OwnershipRule(pattern="CHANGELOG.md", owner="docs",
                      policy="append-only"),
        OwnershipRule(pattern="app/tools/*", owner="tools",
                      policy="additive-only"),
        OwnershipRule(pattern="app/lib/gis/algorithms/*", owner="algorithms",
                      policy="additive-only"),
        OwnershipRule(pattern="app/api/routes/*", owner="api",
                      policy="coordinated"),
        OwnershipRule(pattern="deploy/alerts-rules.json", owner="observability",
                      policy="single-writer"),
        OwnershipRule(pattern="docs/quality/*", owner="quality",
                      policy="regenerate-dont-edit"),
        OwnershipRule(pattern="app/lib/observability/events.py",
                      owner="observability", policy="coordinated"),
    ))


@pytest.fixture()
def pair_repo(tmp_path: Path) -> Path:
    """master + 两个同源分支（各自可再定制）。"""
    repo = tmp_path / "repo"
    for d in ("app/tools", "app/lib/gis/algorithms",
              "app/lib/observability", "app/api/routes",
              "migrations/versions", "docs/quality", "deploy"):
        (repo / d).mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    (repo / "app/lib/observability/events.py").write_text(
        "EVENT_CATALOG = {}\n", encoding="utf-8")
    for d in ("app/tools", "app/lib/gis/algorithms", "app/api/routes",
              "migrations/versions", "docs/quality", "deploy",
              "docs/integration"):
        (repo / d).mkdir(parents=True, exist_ok=True)
        (repo / d / ".gitkeep").write_text("", encoding="utf-8")
    # 供 CLI/compare_pair 从 repo 内加载 doc（与 DOC 等价的子集）
    import json as _json
    (repo / "docs/integration/ownership.json").write_text(_json.dumps({
        "version": 1, "adr_watermark": 118, "migration_watermark": 33,
        "rules": [r.__dict__ | {"suites": list(r.suites)} for r in DOC.rules],
    }, ensure_ascii=False), encoding="utf-8")
    # 既有 migration（供 same_revision 场景双方共改）
    (repo / "migrations/versions/0033_base.py").write_text(
        'revision = "0033_base"\ndown_revision = "0032_prev"\n',
        encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    for name in ("feat/a", "feat/b"):
        _git(repo, "checkout", "-q", "-b", name)
        _git(repo, "checkout", "-q", "master")
    _git(repo, "checkout", "-q", "feat/a")
    return repo


def _commit_migration(repo: Path, seq: str, down: str) -> str:
    path = repo / f"migrations/versions/{seq}_probe.py"
    path.parent.mkdir(parents=True, exist_ok=True)  # git 不跟踪空目录
    path.write_text(f'revision = "{seq}_probe"\ndown_revision = "{down}"\n',
                    encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"migration {seq}")
    return path.relative_to(repo).as_posix()


def test_all_conflict_axes_detected(pair_repo: Path):
    repo = pair_repo
    # 分支 A：migration 0034_a 挂 0033、改 0033_base、新 tool ID、alerts、events
    _git(repo, "checkout", "-q", "feat/a")
    _commit_migration(repo, "0034_a", "0033_base")
    (repo / "migrations/versions/0033_base.py").write_text(
        'revision = "0033_base"\ndown_revision = "0032_prev"\n'
        '# a: add index\n', encoding="utf-8")
    (repo / "app/tools/dup_tool.py").write_text(
        'name = "same_tool"\n', encoding="utf-8")
    (repo / "deploy/alerts-rules.json").write_text("{}\n", encoding="utf-8")
    (repo / "app/lib/observability/events.py").write_text(
        'EVENT_CATALOG = {"harness": {}}\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a changes")

    # 分支 B：migration 0034_b 也挂 0033（同序号 + fork）、同改 0033_base、
    # 同 tool ID、同 alerts
    _git(repo, "checkout", "-q", "feat/b")
    _commit_migration(repo, "0034_b", "0033_base")
    (repo / "migrations/versions/0033_base.py").write_text(
        'revision = "0033_base"\ndown_revision = "0032_prev"\n'
        '# b: drop column\n', encoding="utf-8")
    (repo / "app/tools/dup_tool.py").write_text(
        'name = "same_tool"\n', encoding="utf-8")
    (repo / "deploy/alerts-rules.json").write_text('[{"a": 1}]\n',
                                                   encoding="utf-8")
    (repo / "app/lib/observability/events.py").write_text(
        'EVENT_CATALOG = {"data": {}}\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "b changes")

    ma = build_manifest("feat/a", "master", repo, doc=DOC)
    mb = build_manifest("feat/b", "master", repo, doc=DOC)
    report = compare_pair(ma, mb, repo_root=repo, doc=DOC)

    severity = {a.axis: a.severity for a in report.axes}
    assert severity["migration_same_file"] == "blocking", "共改 0033_base 必须检出"
    assert severity["migration_sequence_collision"] == "blocking", "同 0034 序号"
    assert severity["migration_forked_down"] == "blocking", "同挂 0033 双新增"
    assert severity["registry_id_collision"] == "blocking"
    assert severity["shared_single_writer"] == "blocking"
    assert severity["events_vocabulary"] == "blocking"
    assert not report.ok
    # 诚实契约：checked/unknown/not_checked 三段齐备
    d = report.as_dict()
    assert "checked" in d and "unknown" in d and "not_checked" in d
    assert len(d["not_checked"]) >= 2


def test_compatible_overlap_is_clean(pair_repo: Path):
    """Epic §15 正例：双方各加不冲突的 tool/migration 序号 → OK。"""
    repo = pair_repo
    _git(repo, "checkout", "-q", "feat/a")
    _commit_migration(repo, "0034", "0033_base")
    (repo / "app/tools/tool_a.py").write_text('name = "tool_a"\n',
                                              encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a")

    _git(repo, "checkout", "-q", "feat/b")
    _commit_migration(repo, "0035_b", "0034_a")  # 线性链（rebase 后形态）
    (repo / "app/tools/tool_b.py").write_text('name = "tool_b"\n',
                                              encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "b")

    report = compare_pair(build_manifest("feat/a", "master", repo, doc=DOC),
                          build_manifest("feat/b", "master", repo, doc=DOC),
                          repo_root=repo, doc=DOC)
    # 线性链（B 挂在 A 之后）= 合并后单头 → fork 必须是 clear
    severity = {a.axis: a.severity for a in report.axes}
    assert severity["migration_forked_down"] == "clear", (
        "线性链不该报 fork")
    assert severity["registry_id_collision"] == "clear"
    assert severity["migration_sequence_collision"] == "clear"
    assert report.ok, "兼容重叠不得阻塞"


def test_regen_dual_input_is_advisory(pair_repo: Path):
    """regenerate-dont-edit 双输入变更 → advisory（合并后统一再生成）。"""
    repo = pair_repo
    for name in ("feat/a", "feat/b"):
        _git(repo, "checkout", "-q", name)
        (repo / "docs/quality/QUALITY_MANIFEST.md").write_text(
            f"x {name}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", name)
    report = compare_pair(build_manifest("feat/a", "master", repo, doc=DOC),
                          build_manifest("feat/b", "master", repo, doc=DOC),
                          repo_root=repo, doc=DOC)
    severity = {a.axis: a.severity for a in report.axes}
    assert severity["shared_regenerate"] == "advisory"
    assert report.ok, "advisory 不阻塞"


def test_id_extraction_degrade_goes_unknown(pair_repo: Path):
    repo = pair_repo
    for name in ("feat/a", "feat/b"):
        _git(repo, "checkout", "-q", name)
        (repo / "app/tools/no_ids.py").write_text("# 无 name= 的维护\n",
                                                  encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", name)
    ma = build_manifest("feat/a", "master", repo, doc=DOC)
    assert ma.ids_extracted is False
    report = compare_pair(ma, build_manifest("feat/b", "master", repo, doc=DOC),
                          repo_root=repo, doc=DOC)
    axis = next(a for a in report.axes if a.axis == "registry_id_collision")
    assert axis.severity == "unknown", "提取降级不得谎报为已比对"


def test_report_serialization_roundtrip(pair_repo: Path):
    repo = pair_repo
    report = compare_pair(build_manifest("feat/a", "master", repo, doc=DOC),
                          build_manifest("feat/b", "master", repo, doc=DOC),
                          repo_root=repo, doc=DOC)
    d = report.as_dict()
    assert json.loads(json.dumps(d)) == d
    assert d["branches"] == ["feat/a", "feat/b"]


def test_cli_blocked_and_clean(pair_repo: Path, monkeypatch, capsys):
    """CLI 端到端：阻塞 → exit 1；兼容 → exit 0。"""
    repo = pair_repo
    _git(repo, "checkout", "-q", "feat/a")
    _commit_migration(repo, "0034", "0033_base")
    _git(repo, "checkout", "-q", "feat/b")
    _commit_migration(repo, "0034", "0033_base")

    import importlib.util
    import sys

    repo_root = Path(merge_sim.__file__).parents[3]
    spec = importlib.util.spec_from_file_location(
        "simulate_merge", repo_root / "scripts/simulate_merge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(mod, "REPO", repo)
    monkeypatch.setattr(mod, "OUT_DIR", repo / ".agent-work/merge-sim")
    monkeypatch.setattr(sys, "argv",
                        ["simulate_merge.py", "--branches", "feat/a,feat/b",
                         "--base", "master"])
    assert mod.main() == 1, "同序号 fork 必须阻塞"
    out = capsys.readouterr().out
    assert "BLOCKED" in out
