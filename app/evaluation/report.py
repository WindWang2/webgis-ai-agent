"""Benchmark report rendering (ADR-0092 B3/B5)."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from app.evaluation.runner import CaseResult

#: B3 metric set, in report order. ``None`` = not measured (rendered n/a).
_METRIC_ORDER = [
    "task_correct",
    "capability_precision",
    "capability_recall",
    "algorithm_correct",
    "numerical_correct",
    "artifact_contract_valid",
    "map_product_complete",
    "render_verified",
    "tool_call_count",
    "retry_count",
    "reused_artifact_count",
    "elapsed_ms",
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
