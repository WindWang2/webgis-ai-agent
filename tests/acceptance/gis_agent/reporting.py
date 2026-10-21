"""G05 期望评估与报告 —— 三态判定（pass / fail / not_evaluated）。

判定规则：
- 期望的 path 缺席（投影面无数据）→ 该检查判 ``not_evaluated``
  （生产接线缺失或该场景链路未到达该面），**不是** pass；
- 负例场景用 ``expected_verdict="fail"`` 声明（判错面必须翻红才通过）；
- 场景通过 = 每条检查 判定 == 期望（防恒绿：负例若无注入效果会因
  实际 pass ≠ 期望 fail 而失败）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .contracts import (
    CheckResult,
    CheckVerdict,
    Expectation,
    ScenarioContract,
    ScenarioRunResult,
)


class _Missing:
    """path 缺席哨兵（区别于值本身是 None）。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — 调试面
        return "<missing>"


MISSING = _Missing()


def lookup(evidence: Dict[str, Any], path: str) -> Any:
    """dotted path 取值（支持 [i] 索引段）；缺席返回 MISSING。"""
    cur: Any = evidence
    for seg in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if seg.startswith("[") and seg.endswith("]"):
            if not isinstance(cur, (list, tuple)):
                return MISSING
            idx = int(seg[1:-1])
            if idx >= len(cur):
                return MISSING
            cur = cur[idx]
        elif isinstance(cur, dict):
            if seg not in cur:
                return MISSING
            cur = cur[seg]
        else:
            return MISSING
    return cur


def _verdict_of(actual: Any, exp: Expectation) -> tuple[CheckVerdict, str]:
    """单检查判定。返回 (verdict, detail)。"""
    if actual is MISSING:
        return CheckVerdict.NOT_EVALUATED, (
            f"path '{exp.path}' absent in evidence (face not reached / "
            f"production wiring missing)")
    op, want = exp.op, exp.value
    ok = False
    if op == "eq":
        ok = actual == want
    elif op == "ne":
        ok = actual != want
    elif op == "in":
        ok = actual in (want or [])
    elif op == "not_in":
        if isinstance(want, (list, tuple, set)):
            # 列表语义：期望词表中任何元素都不得出现在 actual（集合式互斥）
            ok = not any(w in (actual or []) for w in want) \
                if isinstance(actual, (list, tuple, set)) \
                else actual not in want
        else:
            ok = actual != want
    elif op == "contains":
        ok = want in (actual or [])
    elif op == "ge":
        ok = _num(actual) is not None and _num(actual) >= float(want)
    elif op == "le":
        ok = _num(actual) is not None and _num(actual) <= float(want)
    elif op == "exists":
        ok = actual is not None and actual != "" and actual != []
    elif op == "absent":
        ok = actual is None or actual == "" or actual == []
    elif op == "regex":
        ok = bool(re.search(str(want), str(actual)))
    else:
        return CheckVerdict.NOT_EVALUATED, f"unknown op '{op}'"
    detail = f"actual={_short(actual)} op={op} want={_short(want)}"
    return (CheckVerdict.PASS if ok else CheckVerdict.FAIL), detail


def _short(v: Any, limit: int = 90) -> str:
    s = repr(v)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def evaluate_expectations(
    scenario: ScenarioContract, evidence: Dict[str, Any],
) -> List[CheckResult]:
    results: List[CheckResult] = []
    for exp in scenario.expectations:
        verdict, detail = _verdict_of(lookup(evidence, exp.path), exp)
        results.append(CheckResult(
            expectation_id=exp.id, face=exp.face.value, kind=exp.kind,
            verdict=verdict, expected_verdict=exp.expected_verdict,
            detail=detail, actual=None if verdict is CheckVerdict.NOT_EVALUATED
            else _short(lookup(evidence, exp.path)),
        ))
    return results


# ── 汇总报告（CLI / 覆盖热区图消费）──────────────────────────────────────

def summarize_runs(runs: List[ScenarioRunResult]) -> Dict[str, Any]:
    total = len(runs)
    ok = sum(1 for r in runs if r.ok)
    check_counts: Dict[str, int] = {"pass": 0, "fail": 0, "not_evaluated": 0}
    expectation_mismatches = 0
    face_failures: Dict[str, int] = {}
    for r in runs:
        for c in r.checks:
            check_counts[c.verdict.value] = check_counts.get(
                c.verdict.value, 0) + 1
            if c.verdict.value != c.expected_verdict:
                expectation_mismatches += 1
                face_failures[c.face] = face_failures.get(c.face, 0) + 1
    return {
        "scenarios_total": total,
        "scenarios_ok": ok,
        "scenarios_failed": total - ok,
        "runner_errors": sum(1 for r in runs if r.error),
        "checks": check_counts,
        "expectation_mismatches": expectation_mismatches,
        "mismatch_by_face": dict(sorted(face_failures.items())),
    }


def coverage_matrix(scenarios: List[ScenarioContract],
                    runs: Optional[List[ScenarioRunResult]] = None) -> List[Dict[str, Any]]:
    """场景 × 断言面覆盖矩阵（热区图数据源）。"""
    results_by_id = {r.scenario_id: r for r in (runs or [])}
    rows: List[Dict[str, Any]] = []
    for s in scenarios:
        faces = sorted({e.face.value for e in s.expectations})
        run = results_by_id.get(s.id)
        row: Dict[str, Any] = {
            "scenario": s.id,
            "family": s.family,
            "negative": s.negative,
            "faces": faces,
            "turns": len(s.turns),
            "checks": len(s.expectations),
        }
        if run is not None:
            row["ok"] = run.ok
            row["first_failing_stage"] = run.first_failing_stage
            row["duration_ms"] = run.duration_ms
            if run.error:
                row["runner_error"] = run.error
        rows.append(row)
    return rows


def first_failing_stage(run: ScenarioRunResult) -> str:
    return run.first_failing_stage


def render_text_report(runs: List[ScenarioRunResult],
                       scenarios: Optional[List[ScenarioContract]] = None) -> str:
    """人读汇总（CLI stdout；机器可读结构走 summarize/coverage）。"""
    lines: List[str] = ["G05 task-acceptance report", "=" * 60]
    for r in runs:
        lines.append(r.summary_line())
        for c in r.checks:
            if c.verdict.value != c.expected_verdict:
                lines.append(
                    f"  !! {c.expectation_id} [{c.face}/{c.kind}] "
                    f"got={c.verdict.value} want={c.expected_verdict} "
                    f"— {c.detail}")
        if r.error:
            lines.append(f"  !! runner error: {r.error}")
    summary = summarize_runs(runs)
    lines.append("-" * 60)
    lines.append(
        f"scenarios: {summary['scenarios_ok']}/{summary['scenarios_total']} ok; "
        f"checks: pass={summary['checks']['pass']} "
        f"fail={summary['checks']['fail']} "
        f"not_evaluated={summary['checks']['not_evaluated']}; "
        f"mismatches={summary['expectation_mismatches']}")
    return "\n".join(lines)
