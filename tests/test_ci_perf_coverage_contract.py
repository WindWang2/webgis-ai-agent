"""Issue #564：perf 套件进 PR 门、perf_harness_v2 不再被 --cov lane 误收集、
覆盖率闸为真（后端 --cov-fail-under 非装饰值 + 前端 vitest thresholds）。

结构断言，pattern 同 tests/test_real_services_ci_wiring.py。
"""
import ast
import re
import shlex
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production.yml"
PERF_DIR = REPO_ROOT / "tests" / "benchmarks"

# test-perf PR lane 的显式文件清单（与 workflow 的 run 命令保持一致）
PR_LANE_PERF_FILES = (
    "test_perf_harness.py",
    "test_transport_perf.py",
    "test_job_runtime_perf.py",
    "test_provenance_perf.py",
    "test_bench_runner_478.py",
)

_STDLIB = set(sys.stdlib_module_names)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job_run_text(job: str) -> str:
    return "\n".join(
        s.get("run", "") for s in _workflow()["jobs"][job]["steps"] if s.get("run")
    )


def _perf_marked_files() -> set:
    marked = set()
    for f in PERF_DIR.glob("test_*.py"):
        src = f.read_text(encoding="utf-8")
        if "pytestmark = pytest.mark.perf" in src or "@pytest.mark.perf" in src:
            marked.add(f.name)
    return marked


def _top_level_third_party_imports() -> set:
    """PR-lane perf 文件在模块层的第三方 import（ast 解析，避开 docstring/注释）。

    例：test_job_runtime_perf.py 的 `import pytest_asyncio` —— 若 lane 的
    pip install 不装 pytest-asyncio，收集期即 ImportError，lane 红。
    """
    third = set()
    first_party = {"app", "tests"}
    for name in PR_LANE_PERF_FILES:
        tree = ast.parse((PERF_DIR / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    mod = a.name.split(".")[0]
                    if mod not in _STDLIB and mod not in first_party:
                        third.add(mod)
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # 相对导入
                    continue
                mod = (node.module or "").split(".")[0]
                if mod and mod not in _STDLIB and mod not in first_party:
                    third.add(mod)
    return third


def _module_of_pip_package(pkg: str) -> str:
    """pip 包名 -> import 模块名的归一化：pytest-asyncio -> pytest_asyncio、
    psycopg2-binary -> psycopg2（`-`→`_`，剥离 -binary/-stubs 后缀）。"""
    name = pkg.split("==")[0].split(">=")[0].split("<")[0].strip().replace("-", "_")
    for suffix in ("_binary", "_stubs"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def test_pr_perf_lane_installs_all_module_level_imports():
    """test-perf PR lane 的 pip install 必须覆盖 perf 文件模块层的全部第三方
    import —— 缺任何一个（如 pytest-asyncio）都会在收集期 ImportError，
    lane 红 → release-gate 红。覆盖集 = lane install 行参数 ∪ requirements.txt。"""
    run = _job_run_text("test-perf")
    install_lines = [line for line in run.splitlines() if "pip install" in line]
    assert install_lines, "test-perf lane 必须有 pip install 步骤"

    covered: set = set()
    for line in install_lines:
        for arg in shlex.split(line):
            if arg in ("pip", "install") or arg.startswith("-"):
                continue
            if arg.endswith(".txt"):  # -r requirements.txt，单独解析
                continue
            covered.add(_module_of_pip_package(arg))
    req = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    for raw in req.splitlines():
        raw = raw.split("#", 1)[0].strip()
        if not raw or raw.startswith("-"):
            continue
        covered.add(_module_of_pip_package(raw))

    missing = sorted(_top_level_third_party_imports() - covered)
    assert not missing, (
        "test-perf PR lane 的 pip install 未覆盖 perf 文件模块层 import: "
        f"{missing} —— 收集期 ImportError → lane 红 → release-gate 红"
    )


def test_every_perf_marked_file_is_wired_into_a_lane():
    """每个 perf-marked 文件都必须有归属 lane：PR test-perf 显式清单，或
    nightly 的 marker 选择器（-m "cartography or perf"）。"""
    pr_run = _job_run_text("test-perf")
    nightly_run = _job_run_text("nightly-matrix")
    for f in sorted(_perf_marked_files()):
        assert f in pr_run or '-m "cartography or perf"' in nightly_run, (
            f"{f} 未进入任何 lane 的 perf 选择器"
        )


def test_test_perf_pr_lane_runs_fast_subset_no_cov():
    run = _job_run_text("test-perf")
    assert "--no-cov" in run
    for f in (
        "test_perf_harness.py",
        "test_transport_perf.py",
        "test_job_runtime_perf.py",
        "test_provenance_perf.py",
        "test_bench_runner_478.py",
    ):
        assert f in run, f"test-perf PR lane 缺少 {f}"
    # v2 的 event-loop lag 是墙钟断言，保持 nightly 专属，不进 PR
    assert "test_perf_harness_v2.py" not in run


def test_backend_lane_excludes_perf_marker():
    run = _job_run_text("test-backend")
    assert "not perf" in run


def test_perf_harness_v2_is_perf_marked_and_nightly_runs_it_no_cov():
    src = (PERF_DIR / "test_perf_harness_v2.py").read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.perf" in src, (
        "v2 必须带 perf marker（否则被 test-backend 在 --cov 下收集，墙钟断言 flaky）"
    )
    nightly_run = _job_run_text("nightly-matrix")
    assert '-m "cartography or perf"' in nightly_run
    assert "--no-cov" in nightly_run


def test_backend_coverage_gate_is_non_decorative():
    run = _job_run_text("test-backend")
    m = re.search(r"--cov-fail-under=(\d+)", run)
    assert m, "test-backend 必须带 --cov-fail-under"
    assert int(m.group(1)) >= 10, "覆盖闸不能是装饰性的低值"


def test_frontend_vitest_has_real_coverage_thresholds():
    cfg = (REPO_ROOT / "frontend" / "vitest.config.ts").read_text(encoding="utf-8")
    m = re.search(r"thresholds:\s*\{(.*?)\}", cfg, re.S)
    assert m, "vitest.config.ts 必须声明 coverage.thresholds"
    block = m.group(1)
    for key in ("lines", "functions", "statements", "branches"):
        km = re.search(rf"{key}:\s*(\d+)", block)
        assert km and int(km.group(1)) > 0, f"thresholds.{key} 必须为正数"
