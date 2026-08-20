"""#664：perf 基准隔离运行契约的 wiring 守卫。

perf 基线在隔离进程下记录（CI 专属 test-perf lane、文件 docstring 的
`-m perf` 用法）。无 marker 过滤的本地全量跑把 perf 项混在 ~4500 个测试
中段执行，堆积累 + 负载波动使 median 超基线（同机三次实测 0/4/7 failed，
干净 master 最差）。契约：未以 `-m` 选择 perf 时 perf 项**可见 skip** 并
教学正确命令；`-m perf` 选择时行为不变。

以子进程验证两态（真实走一遍收集 + guard），不 mock pytest 自身。
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 探针目标选 transport perf：5 个 workload、全部带 perf marker、独立跑最快
# （~4s；skip 态更短）—— 足以证明 guard 的两态，无需扫全部 perf 文件。
_PROBE_TARGET = "tests/benchmarks/test_transport_perf.py"


def _run_pytest(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", _PROBE_TARGET,
            "-q", "--no-cov", "-p", "no:cacheprovider",
            *args,
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_unfiltered_run_skips_perf_baselines():
    """无 marker 过滤 → perf 项全部可见 skip（确定性），不中段执行。"""
    proc = _run_pytest()
    assert proc.returncode == 0, f"pytest 失败:\n{proc.stdout[-2000:]}\n{proc.stderr[-1000:]}"
    assert "5 skipped" in proc.stdout, (
        f"无过滤运行应 5 skipped（隔离契约，#664），实际:\n{proc.stdout[-500:]}"
    )
    assert "passed" not in proc.stdout.splitlines()[-1], (
        "perf 项不应在无过滤运行中执行"
    )


def test_explicit_perf_marker_still_runs():
    """`-m perf` 显式选择 → 照常执行，guard 不介入。

    只锁"执行了而非 skip"—— 时序结果（passed/failed）不设断言：机器负载
    相位会让基准超线（#664 记录的现象），那是 perf lane 基线门禁自己的
    事，wiring 守卫不重复裁决。
    """
    import re

    proc = _run_pytest("-m", "perf")
    assert proc.returncode in (0, 1), (
        f"pytest 异常退出:\n{proc.stdout[-2000:]}\n{proc.stderr[-1000:]}"
    )
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    assert "skipped" not in summary, (
        f"-m perf 下 perf 项不应被 guard skip，实际 summary: {summary!r}"
    )
    counts = [
        int(n)
        for n, _word in re.findall(r"(\d+) (passed|failed|error)", summary)
    ]
    assert sum(counts) == 5, (
        f"-m perf 应执行全部 5 个 workload（计数和=5），实际 summary: {summary!r}"
    )
