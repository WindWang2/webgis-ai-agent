"""Issue #671：本地门禁与 CI PR lane 对齐的契约断言。

scripts/ci-local.sh 必须逐条包含 production.yml PR 阻塞 lane 的 gate 命令，
使得 workflow 改动而脚本不跟（gate drift）在这里红，而不是在推送后的 CI 红。

结构断言，pattern 同 tests/test_ci_perf_coverage_contract.py。
"""
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production.yml"
SCRIPT = REPO_ROOT / "scripts" / "ci-local.sh"

# 与 tests/test_ci_perf_coverage_contract.py 的 PR_LANE_PERF_FILES 同源：
# perf lane 的文件清单必须一字不差地出现在本地脚本里。
from tests.test_ci_perf_coverage_contract import PR_LANE_PERF_FILES


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job_run_text(job: str) -> str:
    return "\n".join(
        s.get("run", "") for s in _workflow()["jobs"][job]["steps"] if s.get("run")
    )


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows 检出无 POSIX exec 位（git index 100755 → 工作树 0o100666，"
    "core.fileMode=false）；可执行位由"
    "test_ci_local_script_is_committed_executable 跨平台守护",
)
def test_ci_local_script_exists_and_is_executable():
    """#1692：工作树 exec 位只在 POSIX 文件系统上有意义。

    Windows 上该断言恒红（本机实证 1 failed），而 CI（ubuntu）恒绿 ——
    契约文件自己成了开发者 --deselect 绕过契约层的理由。平台相关部分
    在此 skip，平台不变式由下面的 git index mode 用例守护。
    """
    assert SCRIPT.is_file(), "scripts/ci-local.sh 不存在"
    assert os.stat(SCRIPT).st_mode & stat.S_IXUSR, "scripts/ci-local.sh 不可执行"


def test_ci_local_script_is_committed_executable():
    """#1692：exec 位的跨平台等价物 —— 断言 git index mode 100755。

    git index 记录提交时的 exec 位，与工作树/文件系统无关：Windows 检出
    （core.fileMode=false）把 100755 物化成 0o100666，但 `git ls-files -s`
    仍报 100755 —— 这正是「与平台无关的不变式」，本地与 CI 同值。

    encoding="utf-8" 显式钉死（#1651 同款坑）：git 输出恒为 UTF-8，而
    text=True 默认按区域编码解码，中文 Windows（GBK 区域）会抛
    UnicodeDecodeError。仓内先例：app/lib/quality/discovery.py。
    """
    out = subprocess.run(
        ["git", "ls-files", "-s", "scripts/ci-local.sh"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
        check=True,
    )
    assert "100755" in out.stdout, "scripts/ci-local.sh 未以可执行位（100755）提交"


def test_ruff_gate_matches_workflow():
    """lint job 的 ruff 命令（仓级，非仅改动文件）必须在脚本中逐字出现。"""
    run = _job_run_text("lint")
    ruff_lines = [
        line.strip() for line in run.splitlines() if line.strip().startswith("ruff check")
    ]
    assert ruff_lines, "workflow lint job 缺少 ruff check 命令"
    for line in ruff_lines:
        assert line in _script(), f"ci-local.sh 未包含 workflow 的 ruff 命令: {line}"


def test_eslint_gate_matches_workflow():
    run = _job_run_text("lint")
    assert "pnpm exec eslint . --max-warnings 0" in run, "workflow lint job 的 eslint 命令变了"
    assert "pnpm exec eslint . --max-warnings 0" in _script(), (
        "ci-local.sh 必须跑仓级 eslint（--max-warnings 0），而非仅改动文件"
    )


def test_frontend_test_and_typecheck_gates_match_workflow():
    run = _job_run_text("test-frontend")
    # pnpm 是唯一包管理器（audit5 #1083）；断言带词边界，防 npm/pnpm 子串互混
    for cmd in ("pnpm run test:ci", "pnpm run typecheck", "pnpm run build"):
        assert cmd in run, f"workflow test-frontend job 不再使用 {cmd}"
        assert cmd in _script(), f"ci-local.sh 未包含 {cmd}"


def test_backend_pytest_selection_matches_workflow():
    run = _job_run_text("test-backend")
    marker = '-m "not perf and not cartography and not real_services"'
    assert marker in run, "workflow test-backend 的 marker 选择变了"
    assert marker in _script(), "ci-local.sh 的后端 pytest 选择必须与 test-backend lane 一致"
    assert "--cov-fail-under=75" in _script(), "ci-local.sh 缺少覆盖率闸（与 test-backend lane 对齐）"


def test_backend_pytest_timeout_matches_workflow():
    """#1573 放宽 CI 后端 lane 到 --timeout=180 后脚本未跟 —— 本地慢用例
    假红。后端 pytest 的超时必须与 test-backend lane 逐字对齐（锚定 marker
    行，避免与 perf/cartography lane 的 --timeout 互混）。"""
    run = _job_run_text("test-backend")
    wf = re.search(r"--timeout=(\d+)", run)
    assert wf, "workflow test-backend lane 缺少 --timeout"
    marker = '-m "not perf and not cartography and not real_services"'
    m = re.search(
        rf"--timeout=(\d+) --timeout-method=thread \\\n\s*{re.escape(marker)}",
        _script(),
    )
    assert m, "ci-local.sh 后端 pytest 缺少 --timeout=... --timeout-method=thread（marker 行前）"
    assert m.group(1) == wf.group(1), (
        f"ci-local.sh 后端 pytest 超时与 test-backend lane 漂移："
        f"本地 {m.group(1)}s vs CI {wf.group(1)}s"
    )


def test_perf_lane_file_list_matches_workflow():
    run = _job_run_text("test-perf")
    script = _script()
    for f in PR_LANE_PERF_FILES:
        assert f in run, f"test-perf lane 缺少 {f}（上游契约已变）"
        assert f in script, f"ci-local.sh 的 perf 文件清单缺少 {f}"
    assert "-m perf" in script and "--no-cov" in script
    assert "--timeout=180 --timeout-method=thread" in script, (
        "ci-local.sh 的 perf lane 缺少 --timeout=180 --timeout-method=thread（与 test-perf lane 对齐）"
    )


def test_cartography_gate_matches_workflow():
    run = _job_run_text("cartography-smoke")
    assert "-m cartography" in run
    assert "-m cartography" in _script(), "ci-local.sh 缺少 cartography smoke 选择"


# ── #700 流程硬化：--fast 的契约层清单锁定 ─────────────────────────────────
# 契约层是精选的根目录跨模块契约测试（两次事故同根：--fast 不碰后端
# pytest）。此断言防止清单被静默删薄；新增契约文件时应显式加入。

CONTRACT_TIER_FILES = [
    "tests/test_tool_meta_contract.py",
    "tests/test_subagent_context_isolation_436.py",
    "tests/test_ci_local_gate_contract.py",
    "tests/test_ci_perf_coverage_contract.py",
    "tests/test_ci_release_gate_contract.py",
    "tests/test_ci_order_randomization_contract.py",
    "tests/test_metrics_token_wiring.py",
    "tests/test_import_boundaries.py",
]


def test_fast_gate_contract_tier_files_present():
    script = _script()
    for f in CONTRACT_TIER_FILES:
        assert f in script, (
            f"ci-local.sh --fast 的契约层缺少 {f} —— 该文件钉着跨模块契约，"
            "被移除会让 #678/#694 型事故复发"
        )


def test_import_boundary_gate_matches_workflow():
    """ADR-0216：import 边界 gate 命令必须与 workflow lint job 逐字对齐。"""
    run = _job_run_text("lint")
    assert "python scripts/check_import_boundaries.py" in run, (
        "workflow lint job 缺少 import 边界门禁"
    )
    assert "python scripts/check_import_boundaries.py" in _script(), (
        "ci-local.sh 缺少 import 边界门禁"
    )


def test_dependency_bounds_gate_matches_workflow():
    """#1512 / #1734：依赖上界 gate（pandas<3.0 / sqlalchemy<2.1 / greenlet /
    lock 漂移）在 workflow lint job 里，却不在 ci-local.sh —— 本地漏跑即
    「回归无人察觉」（#1512 立项目的反面）。命令必须两边逐字对齐。"""
    run = _job_run_text("lint")
    assert "python scripts/check_dependency_bounds.py" in run, (
        "workflow lint job 缺少 Dependency Bounds Gate"
    )
    assert "python scripts/check_dependency_bounds.py" in _script(), (
        "ci-local.sh 缺少 Dependency Bounds Gate（与 lint job 对齐）"
    )


def test_skills_lock_and_source_registry_gates_match_workflow():
    """#1734：skills-lock 完整性（#1349）与 ADS source-registry lint
    （DS1 / ADR-0171）接入 workflow lint job 后，ci-local.sh 必须同跑 ——
    否则本地与 CI 再次漂移（这两个门禁此前零接线，从未生效）。"""
    run = _job_run_text("lint")
    assert "python scripts/verify_skills_lock.py" in run, (
        "workflow lint job 缺少 skills-lock 完整性门禁"
    )
    assert "python scripts/check_source_registry.py" in run, (
        "workflow lint job 缺少 ADS source-registry lint"
    )
    for cmd in (
        "python scripts/verify_skills_lock.py",
        "python scripts/check_source_registry.py",
    ):
        assert cmd in _script(), f"ci-local.sh 未包含 {cmd}"


def test_contract_tier_step_exists():
    assert 'step "contract tier (root-level cross-module contracts)"' in _script(), (
        "ci-local.sh 丢失了契约层步骤（#700）"
    )
