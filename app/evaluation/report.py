"""Benchmark report rendering (ADR-0092 B3/B5)."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.evaluation.runner import CaseResult

#: B3 metric set, in report order. ``None`` = not measured (rendered n/a).
#: V3（Goal §十二）追加语义规划指标（None = 该案例未声明对应契约）。
_METRIC_ORDER = [
    "task_correct",
    "capability_precision",
    "capability_recall",
    "algorithm_correct",
    "methodology_honesty_ok",
    "ontology_top1_correct",
    "recipe_selection_correct",
    "no_false_professional_analysis",
    "qualification_states_correct",
    "fallback_tier_correct",
    "planning_deterministic",
    "unnecessary_tool_count",
    "numerical_correct",
    "artifact_contract_valid",
    "map_product_complete",
    "render_verified",
    "tool_call_count",
    "retry_count",
    "reused_artifact_count",
    "elapsed_ms",
    # ── Benchmark Factory V2（None = 未声明对应契约，渲染 n/a）──────────
    "scope_binding_correct",
    "allowed_tools_ok",
    "turns_correct",
    "coreference_binding_ok",
    "policy_mode_correct",
    "policy_deterministic",
    "injection_contained",
    "evidence_grounding_correct",
    "evidence_positive_proof_ok",
]


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_markdown(results: Iterable[CaseResult]) -> str:
    results = list(results)
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if r.status == "fail")
    skipped = sum(1 for r in results if r.status == "skipped")
    lines: List[str] = [
        "# GIS Agent Benchmark Report",
        "",
        f"- Cases: {len(results)}  pass: {passed}  fail: {failed}  skipped: {skipped}",
        "- Deterministic-first: schema/planner/trace/numeric assertions only; no LLM judge.",
        "",
        "## Summary",
        "",
        "| case | group | status | " + " | ".join(_METRIC_ORDER) + " |",
        "|---|---|---|" + "---|" * len(_METRIC_ORDER),
    ]
    for r in results:
        cells = " | ".join(_fmt(r.metrics.get(k)) for k in _METRIC_ORDER)
        lines.append(
            f"| {r.case_id} | {r.group} | {r.status} | {cells} |"
        )
    lines.append("")
    failed_results = [r for r in results if r.failures]
    if failed_results:
        lines.append("## Failures")
        lines.append("")
        for r in failed_results:
            lines.append(f"### {r.case_id} — {r.name}")
            lines.append("")
            for f in r.failures[:12]:
                lines.append(f"- {f}")
            if len(r.failures) > 12:
                lines.append(f"- … {len(r.failures) - 12} more")
            lines.append("")
    if skipped:
        lines.append("## Skipped")
        lines.append("")
        for r in results:
            if r.status == "skipped":
                lines.append(f"- {r.case_id}: {r.skipped_reason}")
        lines.append("")
    return "\n".join(lines)


# ── Benchmark Factory V2（additive：JSON / 分组聚合 / 基线 diff / 归因）──
# 既有 render_markdown 保持字节兼容；以下函数只增不改。


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def result_to_entry(result: CaseResult) -> Dict[str, Any]:
    """CaseResult → 稳定 JSON 条目（case 级定位：id/group/metrics/failures）。

    elapsed_ms 不入 entry —— 时间面不是回归语义（两遍运行的 diff 锚）。
    """
    return {
        "case_id": result.case_id,
        "group": result.group,
        "name": result.name,
        "status": result.status,
        "passed": result.passed,
        "metrics": {k: _jsonable(v) for k, v in sorted(result.metrics.items())},
        "failures": list(result.failures),
        "skipped_reason": result.skipped_reason,
    }


def aggregate_by_group(results: Iterable[CaseResult]) -> Dict[str, Dict[str, Any]]:
    """按组聚合：通过率 + 数值指标均值 + bool 指标通过率（确定性序）。

    每组给出 ``cases/passed/failed/skipped/pass_rate`` 与逐指标聚合：
    bool → 通过率（None 缺席不计入分母），数值 → 均值，其余跳过。
    """
    groups: Dict[str, List[CaseResult]] = {}
    for r in results:
        groups.setdefault(r.group, []).append(r)
    out: Dict[str, Dict[str, Any]] = {}
    for group in sorted(groups):
        rows = groups[group]
        passed = sum(1 for r in rows if r.passed)
        failed = sum(1 for r in rows if r.status == "fail")
        skipped = sum(1 for r in rows if r.status == "skipped")
        metric_keys: set = set()
        for r in rows:
            metric_keys.update(r.metrics.keys())
        metric_stats: Dict[str, Any] = {}
        for key in sorted(metric_keys):
            values = [r.metrics.get(key) for r in rows if key in r.metrics]
            declared = [v for v in values if v is not None]
            if not declared:
                metric_stats[key] = {"declared": 0, "aggregate": None}
                continue
            if all(isinstance(v, bool) for v in declared):
                metric_stats[key] = {
                    "declared": len(declared),
                    "aggregate": round(
                        sum(1.0 for v in declared if v) / len(declared), 4),
                }
            elif all(isinstance(v, (int, float)) and not isinstance(v, bool)
                     for v in declared):
                metric_stats[key] = {
                    "declared": len(declared),
                    "aggregate": round(sum(float(v) for v in declared)
                                       / len(declared), 4),
                }
            else:
                metric_stats[key] = {"declared": len(declared),
                                     "aggregate": None}
        out[group] = {
            "cases": len(rows),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pass_rate": round(passed / len(rows), 4) if rows else 0.0,
            "metrics": metric_stats,
        }
    return out


def render_json(
    results: Iterable[CaseResult],
    *,
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """机器报告（确定性 JSON）：逐 case 条目 + 分组聚合 + 语料清单。"""
    rows = list(results)
    report = {
        "schema": "gis-benchmark-report.v2",
        "cases": len(rows),
        "passed": sum(1 for r in rows if r.passed),
        "failed": sum(1 for r in rows if r.status == "fail"),
        "skipped": sum(1 for r in rows if r.status == "skipped"),
        "entries": [result_to_entry(r) for r in rows],
        "groups": aggregate_by_group(rows),
    }
    if manifest is not None:
        report["manifest"] = manifest
    return report


def diff_against_baseline(
    current: Dict[str, Any], baseline: Dict[str, Any],
) -> Dict[str, Any]:
    """基线 diff（回归归因）：new_failures / fixed / metric_moved / 案例增删。

    语义对齐 ``app/lib/harness/replay/bench.py::compare_results``（按 id
    对照 + 漂移分类），但对照面是 case 级断言结果与声明指标：
    - ``new_failures``：基线 pass → 当前 fail（回归，逐 case 定位）；
    - ``fixed``：基线 fail → 当前 pass；
    - ``metric_moved``：两侧同 status 但声明指标值变化（语义漂移预警）；
    - ``new_cases`` / ``missing_cases``：语料增删（漂移信号）。
    elapsed 类非断言字段不参与（见 result_to_entry）。
    """
    base_entries = {
        e["case_id"]: e for e in (baseline.get("entries") or [])
    }
    cur_entries = {
        e["case_id"]: e for e in (current.get("entries") or [])
    }
    new_failures, fixed, metric_moved = [], [], []
    for cid, cur in cur_entries.items():
        base = base_entries.get(cid)
        if base is None:
            continue
        if base.get("passed") and not cur.get("passed"):
            new_failures.append({
                "case_id": cid, "group": cur.get("group"),
                "failures": (cur.get("failures") or [])[:6],
            })
        elif (not base.get("passed")) and cur.get("passed"):
            fixed.append({"case_id": cid, "group": cur.get("group")})
        elif base.get("passed") and cur.get("passed"):
            bm = base.get("metrics") or {}
            cm = cur.get("metrics") or {}
            moved = {
                k: {"baseline": bm.get(k), "current": cm.get(k)}
                for k in sorted(set(bm) | set(cm))
                if bm.get(k) != cm.get(k) and k != "elapsed_ms"
            }
            if moved:
                metric_moved.append({"case_id": cid, "metrics": moved})
    new_cases = sorted(set(cur_entries) - set(base_entries))
    missing_cases = sorted(set(base_entries) - set(cur_entries))
    return {
        "schema": "gis-benchmark-diff.v2",
        "baseline_cases": len(base_entries),
        "current_cases": len(cur_entries),
        "new_failures": new_failures,
        "fixed": fixed,
        "metric_moved": metric_moved,
        "new_cases": new_cases,
        "missing_cases": missing_cases,
    }


def write_baseline(results: Iterable[CaseResult], path: Any) -> None:
    """当前结果落盘为基线（JSON；同 commit 复跑 diff 应为空归因）。"""
    import json

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(render_json(list(results)), fh,
                  ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def load_baseline(path: Any) -> Dict[str, Any]:
    import json

    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
