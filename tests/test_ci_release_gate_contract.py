"""TEST-01（deep-review 2026-09-19）：contract / perf-budget 必须进入发布 DAG。

审计结论：contract.yml 与 quality-e2e.yml 的 perf-budgets lane 都只在各自
workflow 里跑，不在 production.yml 的 release-gate.needs 上 —— master push
可以在契约红 / 预算红的情况下直达 build→deploy-prod。修复是在
production.yml 增加 contract-gate 与 perf-budget-gate 两个聚合 job 并纳入
release-gate。本文件守住该接线不被拆掉（结构断言，pattern 同
tests/test_real_services_ci_wiring.py）。

分支保护（required checks）是仓库外部设置，需另行同步（见 PR 描述）。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production.yml"
CONTRACT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "contract.yml"
QUALITY_E2E_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality-e2e.yml"


def _workflow(path: Path = WORKFLOW) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _job_run_text(doc: dict, job: str) -> str:
    return "\n".join(
        s.get("run", "") for s in doc["jobs"][job]["steps"] if s.get("run")
    )


def test_release_gate_requires_contract_and_perf_budget_lanes():
    needs = _workflow()["jobs"]["release-gate"]["needs"]
    assert "contract-gate" in needs, (
        "contract-gate 必须进入 release DAG，否则 API 契约红可直达 deploy"
    )
    assert "perf-budget-gate" in needs, (
        "perf-budget-gate 必须进入 release DAG，否则预算红可直达 deploy"
    )


def test_contract_gate_runs_contract_commands():
    """contract-gate 的命令面必须覆盖 contract.yml 的确定性检查。"""
    run = _job_run_text(_workflow(), "contract-gate")
    assert "tests/quality/test_api_compatibility.py" in run, (
        "contract-gate 必须跑 OpenAPI 快照（tests/quality/test_api_compatibility.py）"
    )
    assert "tests/unit/api_contract" in run, (
        "contract-gate 必须跑 response_model / field contract 门（tests/unit/api_contract）"
    )
    assert "tests/test_api_docs_drift.py" in run, (
        "contract-gate 必须跑 api-docs drift 门"
    )
    assert "test_schemathesis.py" in run, (
        "contract-gate 必须跑 schemathesis fuzz shard"
    )


def test_contract_gate_covers_the_source_workflow_commands():
    """与 contract.yml 的 run 行对齐：源 lane 的关键命令不得在 gate 里缺失。"""
    source = _workflow(CONTRACT_WORKFLOW)
    source_run = _job_run_text(source, "contract")
    gate_run = _job_run_text(_workflow(), "contract-gate")
    for marker in (
        "tests/quality/test_api_compatibility.py",
        "tests/unit/api_contract",
        "tests/test_api_docs_drift.py",
        "test_schemathesis.py",
    ):
        assert marker in source_run, f"contract.yml 自身不再包含 {marker}"
        assert marker in gate_run, f"contract-gate 缺少 contract.yml 的命令 {marker}"


def test_perf_budget_gate_runs_budget_script_with_self_test():
    run = _job_run_text(_workflow(), "perf-budget-gate")
    assert "scripts/perf/run_budget.py" in run, (
        "perf-budget-gate 必须跑 scripts/perf/run_budget.py"
    )
    assert "--self-test" in run, (
        "perf-budget-gate 必须跑 --self-test（合成超线必须红，防 gate 空转）"
    )
    assert "--iterations 20" in run, (
        "perf-budget-gate 必须与 quality-e2e.yml 的 CI 迭代数一致（20）"
    )


def test_perf_budget_gate_covers_the_source_workflow_command():
    source = _workflow(QUALITY_E2E_WORKFLOW)
    source_run = _job_run_text(source, "perf-budgets")
    gate_run = _job_run_text(_workflow(), "perf-budget-gate")
    assert "scripts/perf/run_budget.py" in source_run, (
        "quality-e2e.yml 的 perf-budgets lane 变了；gate 需同步"
    )
    assert "scripts/perf/run_budget.py" in gate_run
