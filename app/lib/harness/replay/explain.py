"""Explainability bundle（B8，ADR-0183 决策六）。

给开发者的因果链产物（JSON + Markdown）：

    task → plan/tool 决策 → 收据 → 变异/观测 → gate → goal → 输出

并显式披露：rejected options（候选工作流）、fallback/repair、user overrides、
invalidation/recompute（superseded/stale）、missing evidence（not_evaluated）。
数据源二选一：生产录制 ReplayTrace（explain_trace）或离线重放结果
（explain_replay）。
"""
from __future__ import annotations

from typing import Any, Dict, List

_NOT_EVALUATED = "not_evaluated"


def _chain_stages(trace: Dict[str, Any], *names: str) -> List[Dict[str, Any]]:
    return [
        rec for rec in ((trace.get("chain") or {}).get("stages") or [])
        if isinstance(rec, dict) and rec.get("stage") in names
    ]


def explain_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    """生产录制轨迹 → 因果链 bundle（只读投影，不新增事实）。"""
    candidates = _chain_stages(trace, "CANDIDATE_WORKFLOWS")
    verdict = trace.get("verdict") or {}
    selected_records = _chain_stages(trace, "SELECTED_WORKFLOW")
    gate_checks = {}
    outcome = (trace.get("outcome") or {}).get("outcome")
    missing = [
        name for name, check in gate_checks.items()
        if isinstance(check, dict) and check.get("evaluated") is False
    ]
    return {
        "kind": "recorded_trace",
        "task": {
            "user_input": trace.get("user_input") or "",
            "normalized_goal": trace.get("normalized_goal") or "",
            "outcome": outcome,
        },
        "decisions": {
            "selected_workflow": trace.get("selected_workflow") or "",
            "selected_records": selected_records,
            "rejected_options": [
                rec.get("candidates") or rec for rec in candidates
            ],
        },
        "tool_calls": trace.get("tool_calls") or [],
        "mutations": trace.get("mutations") or {},
        "artifacts": trace.get("artifacts") or {},
        "verdict": {
            "map_product": verdict.get("map_product"),
            "final_verdict": verdict.get("final_verdict"),
            "goal_satisfaction": None,
        },
        "cost": {"timing_ms": trace.get("timing_ms") or {},
                 "llm_usage": trace.get("llm_usage") or {}},
        "disclosures": _disclosures(
            candidates, (verdict.get("map_product") or {}),
            [(n, "") for n in missing], trace.get("warnings") or [],
        ),
        "behavior_digest": trace.get("behavior_digest") or "",
    }


def explain_replay(scenario, result) -> Dict[str, Any]:
    """离线重放结果 → 因果链 bundle。"""
    turns = []
    for turn_spec, turn_result in zip(scenario.turns, result.turns):
        gate = turn_result.gate_result or {}
        checks = gate.get("checks") or {}
        missing = [
            name for name, check in checks.items()
            if isinstance(check, dict) and check.get("evaluated") is False
        ]
        failed = [
            name for name, check in checks.items()
            if isinstance(check, dict) and check.get("passed") is False
        ]
        turns.append({
            "task": {"user_input": turn_spec.user_input},
            "tool_calls": [
                {"call_id": op.call_id, "tool": op.tool,
                 "is_error": op.is_error,
                 "error_msg": op.error_msg or None}
                for op in turn_spec.ops
            ],
            "mutations": turn_result.mutation_outcomes,
            "verdict": {
                "overall_passed": gate.get("overall_passed"),
                "goal": turn_result.goal_satisfaction,
                "failed_checks": failed,
            },
            "evidence_count": turn_result.evidence_count,
            "exact_diffs": turn_result.exact_diffs,
            "disclosures": _disclosures(
                [], {}, [(name, "") for name in missing],
                [d for d in turn_result.exact_diffs],
            ),
        })
    return {
        "kind": "offline_replay",
        "scenario_id": scenario.scenario_id,
        "category": scenario.category,
        "replay_digest": result.replay_digest,
        "levels_run": result.levels_run,
        "not_run": result.not_run,
        "faults": scenario.faults,
        "turns": turns,
    }


def _disclosures(
    candidates: List[Any],
    map_product: Dict[str, Any],
    not_evaluated: List[Any],
    warnings: List[Any],
) -> Dict[str, Any]:
    """rejected/fallback/override/invalidation/missing-evidence 的统一披露面。"""
    repairs = map_product.get("repairs") or []
    issues = map_product.get("issues") or []
    return {
        "rejected_options": candidates,
        "fallback_or_repair": repairs,
        "user_overrides": [
            issue for issue in issues
            if isinstance(issue, dict)
            and str(issue.get("code", "")).startswith("user_")
        ],
        "invalidation_or_recompute": [
            issue for issue in issues
            if isinstance(issue, dict)
            and str(issue.get("code", "")).startswith(
                ("stale_", "superseded"))
        ],
        "missing_evidence": not_evaluated,
        "warnings": warnings[:16],
    }


def render_markdown(explanation: Dict[str, Any]) -> str:
    """bundle → Markdown（静态渲染，无 JS / 无外部资源）。"""
    lines: List[str] = []
    kind = explanation.get("kind")
    if kind == "offline_replay":
        lines.append(f"# Replay Explainability — {explanation['scenario_id']}")
        lines.append("")
        lines.append(f"- category: `{explanation['category']}`")
        lines.append(f"- replay_digest: `{explanation['replay_digest'][:16]}…`")
        lines.append(f"- levels: {explanation['levels_run']} "
                     f"(not run: {explanation['not_run']})")
        if explanation.get("faults"):
            lines.append(f"- faults: {explanation['faults']}")
        for turn in explanation.get("turns") or []:
            lines.append("")
            lines.append(f"## Turn {turn['verdict'] and ''}"
                         f"{turn['task']['user_input'][:60]}")
            for call in turn.get("tool_calls") or []:
                status = "error" if call.get("is_error") else "ok"
                lines.append(f"- `{call['tool']}` [{status}]"
                             f"{(' — ' + call['error_msg']) if call.get('error_msg') else ''}")
            verdict = turn["verdict"]
            lines.append(f"- gate: overall_passed={verdict['overall_passed']}"
                         f" failed={verdict['failed_checks']}")
            lines.append(f"- goal: {verdict['goal'].get('status')}"
                         f" ({verdict['goal'].get('reason')})")
            disc = turn.get("disclosures") or {}
            if disc.get("missing_evidence"):
                lines.append(f"- missing evidence: {disc['missing_evidence']}")
    else:
        task = explanation.get("task") or {}
        lines.append("# Trace Explainability")
        lines.append("")
        lines.append(f"- outcome: `{task.get('outcome')}`")
        lines.append(f"- goal: {task.get('normalized_goal')}")
        lines.append(f"- digest: `{str(explanation.get('behavior_digest'))[:16]}…`")
        for call in explanation.get("tool_calls") or []:
            lines.append(f"- `{call.get('tool_name')}` [{call.get('status')}]")
        disc = explanation.get("disclosures") or {}
        if disc.get("fallback_or_repair"):
            lines.append(f"- repairs: {disc['fallback_or_repair']}")
    lines.append("")
    return "\n".join(lines)
