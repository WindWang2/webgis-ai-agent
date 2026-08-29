"""Issue #532：Playwright runtime validator 必须在某个 lane 真正运行。

修复前 test_runtime_validator_full_flow 在所有 lane 都 self-skip：没有任何 lane
安装 frontend/node_modules（playwright）+ chromium，真实 MapLibre 渲染门零 CI
覆盖。修复：新增 runtime-validator lane（nightly + 手动）安装两者并以
REQUIRE_BROWSER=1 硬失败模式运行；测试内的 skip 守卫在 REQUIRE_BROWSER=1 时
转为失败。本文件守住该 lane 不被拆掉（结构断言，pattern 同
tests/test_real_services_ci_wiring.py）。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_runtime_validator_lane_exists_and_is_nightly_only():
    jobs = _workflow()["jobs"]
    assert "runtime-validator" in jobs, "缺少 runtime-validator lane"
    cond = jobs["runtime-validator"].get("if", "")
    assert "schedule" in cond and "workflow_dispatch" in cond, (
        "runtime-validator 应只在 nightly/手动触发（Chromium 下载 + CDN 依赖外网）"
    )
    # 不进 PR release DAG（避免 CDN 抖动阻塞 PR）
    needs = _workflow()["jobs"]["release-gate"]["needs"]
    assert "runtime-validator" not in needs


def test_runtime_validator_lane_installs_playwright():
    steps = _workflow()["jobs"]["runtime-validator"]["steps"]
    run_text = "\n".join(str(s.get("run", "")) for s in steps if s.get("run"))
    assert "pnpm install --frozen-lockfile" in run_text, "lane 必须 pnpm install --frozen-lockfile（playwright 包依赖；audit5 #1083 单一 lockfile）"
    assert "playwright install" in run_text, "lane 必须安装 playwright chromium"


def test_runtime_validator_lane_runs_with_require_browser():
    steps = _workflow()["jobs"]["runtime-validator"]["steps"]
    run_step = next(
        s for s in steps if "test_runtime_validator.py" in str(s.get("run", ""))
    )
    assert run_step.get("env", {}).get("REQUIRE_BROWSER") == "1", (
        "lane 必须设置 REQUIRE_BROWSER=1（缺浏览器 = 硬失败，而非绿色 SKIPPED）"
    )
    assert "--no-cov" in run_step["run"]


def test_runtime_validator_guard_fails_hard_under_require_browser():
    test_src = (
        REPO_ROOT / "tests" / "unit" / "test_runtime_validator.py"
    ).read_text(encoding="utf-8")
    assert "REQUIRE_BROWSER" in test_src, "测试必须实现 REQUIRE_BROWSER 守卫"
    assert "pytest.fail" in test_src, "REQUIRE_BROWSER=1 时缺浏览器必须 pytest.fail"
