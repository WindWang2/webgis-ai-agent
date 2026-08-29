"""Issue #671：本地门禁与 CI PR lane 对齐的契约断言。

scripts/ci-local.sh 必须逐条包含 production.yml PR 阻塞 lane 的 gate 命令，
使得 workflow 改动而脚本不跟（gate drift）在这里红，而不是在推送后的 CI 红。

结构断言，pattern 同 tests/test_ci_perf_coverage_contract.py。
"""
import os
import stat
from pathlib import Path

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


def test_ci_local_script_exists_and_is_executable():
    assert SCRIPT.is_file(), "scripts/ci-local.sh 不存在"
    assert os.stat(SCRIPT).st_mode & stat.S_IXUSR, "scripts/ci-local.sh 不可执行"


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
]


def test_fast_gate_contract_tier_files_present():
    script = _script()
    for f in CONTRACT_TIER_FILES:
        assert f in script, (
            f"ci-local.sh --fast 的契约层缺少 {f} —— 该文件钉着跨模块契约，"
            "被移除会让 #678/#694 型事故复发"
        )


def test_contract_tier_step_exists():
    assert 'step "contract tier (root-level cross-module contracts)"' in _script(), (
        "ci-local.sh 丢失了契约层步骤（#700）"
    )
