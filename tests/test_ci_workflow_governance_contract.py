"""G15（2026-09-27）：CI workflow 治理防回归断言。

对 .github/workflows/ 下全部 workflow（当前 contract.yml / production.yml /
quality-e2e.yml）断言三条治理不变量：

  1. 顶层必有 concurrency，且 cancel-in-progress 按 pr/push 区分（仅
     pull_request 取消）—— master push（含在途 deploy）与 nightly 调度
     绝不能被并发组杀掉（production.yml 已固化的语义，G15 补齐 contract.yml）；
  2. 每个 job 必有正的 timeout-minutes —— 无超时 job 挂死会烧穿 runner
     预算且永不红；
  3. needs 引用的 job 必须存在（无悬空引用），依赖图无环。

纯结构断言（yaml.safe_load），pattern 同 tests/test_ci_release_gate_contract.py。
校验逻辑集中在 collect_violations()，可对任意 workflows 目录运行，便于
演练（对删掉任一 timeout-minutes 的 fixture 目录运行必须报红）：

    python tests/test_ci_workflow_governance_contract.py
    python tests/test_ci_workflow_governance_contract.py --workflows-dir <fixture>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# 三个治理对象必须存在（防静默删除/改名后断言空转）；目录下新增的
# workflow 文件自动纳入同一套不变量。
EXPECTED_WORKFLOWS = ("contract.yml", "production.yml", "quality-e2e.yml")

# pr/push 区分的既定写法：只有 pull_request 才取消在途 run。
PR_CANCEL_MARK = "github.event_name == 'pull_request'"


def _workflow_files(workflows_dir: Path) -> list[Path]:
    return sorted(set(workflows_dir.glob("*.yml")) | set(workflows_dir.glob("*.yaml")))


def _needs_list(job: dict) -> list[str]:
    needs = job.get("needs", [])
    if isinstance(needs, str):
        return [needs]
    return [entry for entry in needs if isinstance(entry, str)]


def collect_violations(workflows_dir: Path = WORKFLOWS_DIR) -> list[str]:
    """返回全部治理违规（空列表 = 通过）；对任意 fixture 目录亦可运行。"""
    if not workflows_dir.is_dir():
        return [f"workflows 目录不存在: {workflows_dir}"]

    violations: list[str] = []
    files = _workflow_files(workflows_dir)
    found = {path.name for path in files}
    for expected in EXPECTED_WORKFLOWS:
        if expected not in found:
            violations.append(f"[{expected}] 预期的 workflow 文件缺失")

    for path in files:
        tag = path.name
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            violations.append(
                f"[{tag}] concurrency/timeout-minutes/needs 断言无法执行: YAML 解析失败 {exc}"
            )
            continue
        if not isinstance(doc, dict):
            violations.append(
                f"[{tag}] concurrency/timeout-minutes/needs 断言无法执行: 顶层不是映射"
            )
            continue

        violations.extend(_concurrency_violations(tag, doc))

        jobs = doc.get("jobs")
        if not isinstance(jobs, dict) or not jobs:
            violations.append(
                f"[{tag}] concurrency/timeout-minutes/needs 断言无法执行: 缺少 jobs 或为空"
            )
            continue

        violations.extend(_timeout_violations(tag, jobs))
        violations.extend(_needs_violations(tag, jobs))

    return violations


def _concurrency_violations(tag: str, doc: dict) -> list[str]:
    concurrency = doc.get("concurrency")
    if not isinstance(concurrency, dict):
        return [f"[{tag}] 顶层缺少 concurrency 块"]
    violations: list[str] = []
    group = concurrency.get("group")
    if not (isinstance(group, str) and group.strip()):
        violations.append(f"[{tag}] concurrency.group 缺失或为空")
    cancel = concurrency.get("cancel-in-progress")
    if not (isinstance(cancel, str) and PR_CANCEL_MARK in cancel):
        violations.append(
            f"[{tag}] concurrency.cancel-in-progress 未区分 pr/push"
            f"（须含 `{PR_CANCEL_MARK}`：仅取消 PR，master push / nightly 不取消）"
        )
    return violations


def _timeout_violations(tag: str, jobs: dict) -> list[str]:
    violations: list[str] = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            violations.append(f"[{tag}] timeout 断言无法执行: job {job_id} 不是映射")
            continue
        timeout = job.get("timeout-minutes")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            violations.append(
                f"[{tag}] job {job_id} 缺少正的 timeout-minutes（现值: {timeout!r}）"
            )
    return violations


def _needs_violations(tag: str, jobs: dict) -> list[str]:
    violations: list[str] = []
    edges: dict[str, list[str]] = {}
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            edges[job_id] = []
            continue
        needs_raw = job.get("needs", [])
        # 畸形 needs（非字符串、或列表含非字符串条目）显式报违规，
        # 不静默过滤 —— 否则悬空检查对这类输入空转。
        if not (
            isinstance(needs_raw, str)
            or (isinstance(needs_raw, list) and all(isinstance(e, str) for e in needs_raw))
        ):
            violations.append(
                f"[{tag}] job {job_id} needs 含非法条目（须为字符串或字符串列表）: {needs_raw!r}"
            )
        edges[job_id] = _needs_list(job)

    for job_id, needs in edges.items():
        for dep in needs:
            if dep not in jobs:
                violations.append(f"[{tag}] job {job_id} needs 悬空引用: {dep}")

    # Kahn 拓扑排序：未消化的 job 即处于环上。
    indegree = {job_id: 0 for job_id in jobs}
    dependents: dict[str, list[str]] = {job_id: [] for job_id in jobs}
    for job_id, needs in edges.items():
        for dep in needs:
            if dep in indegree:
                indegree[job_id] += 1
                dependents[dep].append(job_id)
    queue = [job_id for job_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        for nxt in dependents[queue.pop()]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
        visited += 1
    if visited != len(jobs):
        cyclic = sorted(job_id for job_id, degree in indegree.items() if degree > 0)
        violations.append(f"[{tag}] needs 依赖图存在环: {', '.join(cyclic)}")
    return violations


def test_expected_workflows_present():
    found = {path.name for path in _workflow_files(WORKFLOWS_DIR)}
    for expected in EXPECTED_WORKFLOWS:
        assert expected in found, f"workflow 文件缺失: {expected}"


def test_all_governance_invariants_hold():
    """主断言：全部 workflow 满足 timeout/concurrency/needs 三条不变量。"""
    violations = collect_violations()
    assert violations == [], "CI workflow 治理断言失败:\n" + "\n".join(violations)


def test_concurrency_invariant():
    assert not [v for v in collect_violations() if "concurrency" in v]


def test_timeout_invariant():
    assert not [v for v in collect_violations() if "timeout-minutes" in v]


def test_needs_invariant():
    assert not [v for v in collect_violations() if "needs" in v]


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI workflow 治理校验（G15）")
    parser.add_argument(
        "--workflows-dir",
        type=Path,
        default=WORKFLOWS_DIR,
        help="待校验的 .github/workflows 目录（默认当前仓库）",
    )
    args = parser.parse_args(argv)
    violations = collect_violations(args.workflows_dir)
    if violations:
        print("CI workflow 治理校验失败:")
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print(f"OK: {args.workflows_dir} 全部 workflow 满足治理不变量")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
