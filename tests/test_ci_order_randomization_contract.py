"""TEST-11（deep-review 2026-09-19）：QUALITY_ORDER_SEED 顺序轮换必须有 CI lane。

tests/conftest.py 支持确定性 shuffle（非零 seed 打乱收集顺序，暴露全局/
registry 泄漏的顺序污染），但此前只有 scripts/quality_runner.py 的本地
profile 设置它 —— CI 从未启用，顺序污染在门禁里不可见。

契约（nightly 档，成本选择见 workflow 注释）：
  * production.yml 有 order-randomization job；
  * 仅 nightly / 手动触发（不进 PR 阻塞路径）；
  * 至少 2 个固定 seed（失败可用同 seed 精确重放）；
  * 显式导出 QUALITY_ORDER_SEED=<matrix seed>；
  * 跑 tests/unit（无 coverage）并与主 lane 同款 marker 排除。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_order_randomization_job_exists_nightly_only():
    job = _workflow()["jobs"].get("order-randomization")
    assert job is not None, "缺少 order-randomization lane（QUALITY_ORDER_SEED 从未在 CI 启用）"
    condition = str(job.get("if", ""))
    assert "schedule" in condition, "顺序轮换应是 nightly / 手动档，不进 PR 路径"


def test_order_randomization_uses_fixed_seed_matrix():
    job = _workflow()["jobs"]["order-randomization"]
    seeds = (job.get("strategy") or {}).get("matrix", {}).get("seed")
    assert seeds and len(seeds) >= 2, (
        f"至少 2 个固定 seed（失败可精确重放），实际: {seeds!r}"
    )
    env_text = "\n".join(
        "\n".join(f"{k}={v}" for k, v in (s.get("env") or {}).items())
        for s in job["steps"]
    )
    assert "QUALITY_ORDER_SEED" in env_text, "lane 必须导出 QUALITY_ORDER_SEED"
    assert "matrix.seed" in env_text, "QUALITY_ORDER_SEED 必须来自 matrix seed"


def test_order_randomization_runs_unit_suite_without_coverage():
    job = _workflow()["jobs"]["order-randomization"]
    run_text = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "tests/unit" in run_text, "seeded lane 跑单元套件（成本选择，见 job 注释）"
    assert "--no-cov" in run_text, "shuffle lane 不需要 coverage（与 perf/cartography 隔离哲学一致）"
    assert "not perf" in run_text and "not cartography" in run_text and "not real_services" in run_text, (
        "seeded lane 必须与 test-backend 同款 marker 排除（perf/cartography/real_services 各有专属 lane）"
    )


def test_order_randomization_is_not_release_blocking():
    needs = _workflow()["jobs"]["release-gate"]["needs"]
    assert "order-randomization" not in needs, (
        "顺序轮换是 nightly 发现性 lane，不阻塞发布 DAG（PR 成本纪律）"
    )
