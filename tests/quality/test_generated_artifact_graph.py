"""生成物依赖图红线（Quality V2 W12）。

三条红线：
1. 账本当前：generated-artifacts.json 的输入指纹与当前源码一致
   （注册表/源文件变了但产物没再生成 = stale，合并前即红）；
2. 覆盖完整：每个声明的生成物必须存在且登记在图里；关键字节闸产物
   （manifest/drift/security/chaos/cancellation/realtime 快照）不得缺席；
3. 确定性：同提交两次 build_graph_state() 指纹一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from app.lib.quality.artifact_graph import (  # noqa: E402
    DECLARED,
    REPO_ROOT,
    build_graph_state,
    find_stale,
    load_recorded,
)


@pytest.fixture(scope="module")
def recorded():
    data = load_recorded()
    assert data, "生成物账本缺失：跑 scripts/check_generated_staleness.py --update"
    return data


def test_ledger_is_current(recorded):
    stale = find_stale(recorded)
    assert not stale, (
        f"生成物过期（输入指纹不符）: {stale} —— 先跑对应 gen_* 再生成，"
        "再 python scripts/check_generated_staleness.py --update 刷新账本")


def test_declared_artifacts_exist_and_covered():
    must_exist = [
        "docs/quality/QUALITY_MANIFEST.md",
        "docs/quality/quality-manifest.json",
        "docs/quality/QUALITY_REPORT.md",
        "docs/quality/CONTRACT_DRIFT_REPORT.md",
        "docs/quality/certifications/SECURITY_CONTROLS.md",
        "docs/quality/certifications/CANCELLATION_COVERAGE.md",
        "docs/quality/certifications/CHAOS_FAULT_REGISTRY.md",
        "tests/quality/snapshots/realtime-contract.json",
    ]
    declared = {e.artifact for e in DECLARED}
    for rel in must_exist:
        assert rel in declared, f"关键字生成物未登记依赖图: {rel}"
        assert (REPO_ROOT / rel).exists(), f"声明的生成物不存在: {rel}"


def test_graph_state_deterministic():
    assert build_graph_state() == build_graph_state()


def test_every_generator_script_exists():
    for entry in DECLARED:
        assert (REPO_ROOT / entry.generator).exists(), (
            f"{entry.artifact} 的生成脚本缺失: {entry.generator}")
        assert entry.inputs, f"{entry.artifact} 必须声明至少一个语义输入"


def test_generator_scripts_not_gitignored():
    """回归闸（quality-v1 教训：check_tool_descriptor_coverage.py 漏入库
    曾使 master 全量收集红）：依赖图里的生成脚本与 runner 不得被
    .gitignore 吞掉——文件存在但永远进不了版本库 = 本地绿 CI 红的
    最强制造机。"""
    import subprocess

    candidates = [e.generator for e in DECLARED] + [
        "scripts/quality_runner.py",
        "scripts/check_generated_staleness.py",
    ]
    for rel in candidates:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", rel], cwd=REPO_ROOT,
            capture_output=True)
        assert proc.returncode != 0, (
            f"{rel} 被 .gitignore 忽略 —— 本地存在但永远不会入库"
            "（/scripts/* 白名单必须显式登记）")
