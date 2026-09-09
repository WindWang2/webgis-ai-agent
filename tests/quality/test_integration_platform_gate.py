"""Quality V3 平台结构闸（Epic 10）。

把 Subagent-A 挑战收口的三条不变量变成结构锁：
1. 生产零 import：``app/`` 下（integration 包自身除外）禁止 import
   ``app.lib.integration``（协调面是开发者侧工具，不是运行时依赖）；
2. 协调面脚本必须入库（history: check_tool_descriptor_coverage.py 漏入库
   曾使 master 全量收集红 —— .gitignore 白名单 + 本闸双保险）；
3. preflight 挂进 quick lane（CI release DAG 覆盖面内的强制点，C-1）。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

INTEGRATION_PACKAGE = "app/lib/integration"

#: Quality V3 全部协调面脚本（.gitignore 白名单必须覆盖，且必须 git tracked）
V3_SCRIPTS = (
    "scripts/check_integration_preflight.py",
    "scripts/allocate_migration.py",
    "scripts/allocate_adr.py",
    "scripts/gen_integration_manifest.py",
    "scripts/simulate_merge.py",
    "scripts/gen_release_readiness.py",
    "scripts/integration_harness.py",
)

_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+app\.lib\.integration", re.MULTILINE)


def test_no_production_import_of_integration_package():
    """不变量 1：app/ 下零运行时 import（integration 包自身除外）。"""
    violations = []
    pkg_root = REPO / INTEGRATION_PACKAGE
    for py in sorted((REPO / "app").rglob("*.py")):
        if py.resolve().is_relative_to(pkg_root.resolve()):
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        if _IMPORT_RE.search(text):
            violations.append(
                str(py.relative_to(REPO)))
    assert violations == [], (
        f"生产代码 import 协调面包（架构不变量 1 破坏）: {violations}")


def test_v3_scripts_are_git_tracked_and_whitelisted():
    """不变量 2：脚本被 .gitignore 白名单放行且实际 tracked。"""
    tracked = set(subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True,
        timeout=30).stdout.splitlines())
    missing = [s for s in V3_SCRIPTS if s not in tracked]
    assert missing == [], (
        f"协调面脚本未入库（.gitignore 白名单缺漏？）: {missing}")


def test_preflight_wired_into_quick_lane():
    """不变量 3：quick lane 是 preflight 的 CI 强制点。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "quality_runner_module", REPO / "scripts/quality_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    quick_cmds = mod.LANES["quick"]["commands"]
    assert any(
        "check_integration_preflight.py" in " ".join(str(c) for c in cmd)
        for cmd in quick_cmds), "preflight 未挂进 quick lane"


def test_v3_scripts_exist_on_disk():
    for s in V3_SCRIPTS:
        # gen_integration_manifest 等 W6+ 交付；允许尚未实现，
        # 但已存在的必须与白名单一致 —— 用存在性探测的宽松版本：
        # W16 完成时收紧为全存在（在 test_quality_v3_completion_proof 中锁）。
        if (REPO / s).exists():
            assert (REPO / s).is_file()
