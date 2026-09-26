"""B0 评测维度投影：ReplayTrace → ratchet 观测行（复用 #1269 行契约）。

维度词表（任务书 B0 十五维 → check_id 命名约定，方向由
``cartography_ratchet.resolve_direction`` 的既有规则判定）：

- ``gate.replay.*``  — 越高越好（gate./eval. 前缀 ⇒ low_bad，得分语义）；
- ``replay.*``      — 越高越糟（兜底 high_bad：延迟/成本/错误/修复计数）。

本模块只做投影，**不做任何新指标判定**；落账与裁决在
``cartography_metrics_store.record_quality_run(lane="replay")`` 与
``cartography_ratchet.evaluate_ratchet``。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from app.lib.runtime.decision_record import (
    DECISION_KIND_CAPABILITY_DISPATCH_DENIAL,
)


def _bool01(value: Any) -> float:
    return 1.0 if bool(value) else 0.0


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def project_metrics(
    trace: Dict[str, Any],
    *,
    scene_id: str,
    gate_result: Optional[Dict[str, Any]] = None,
    goal_satisfaction: Optional[Dict[str, Any]] = None,
    plan_stable: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """轨迹 → ``[{scene_id, check_id, value}]`` 观测行（ratchet 直读形态）。

    gate_result 是 HarnessEvaluator.evaluate_evidence 的结果（重放 T1 产出）；
    goal_satisfaction 是 derive_goal_satisfaction 的结果。二者缺席时对应
    维度诚实缺席（不伪造 0/1），与 not_evaluated≠pass 纪律一致。
    """
    rows: List[Dict[str, Any]] = []

    def _add(check_id: str, value: Optional[float]) -> None:
        numeric = _finite(value)
        if numeric is None:
            return
        rows.append({"scene_id": scene_id, "check_id": check_id, "value": numeric})

    verdict = trace.get("verdict") if isinstance(trace.get("verdict"), dict) else {}
    map_product = verdict.get("map_product") if isinstance(verdict.get("map_product"), dict) else {}
    work = trace.get("work") if isinstance(trace.get("work"), dict) else {}
    timing = trace.get("timing_ms") if isinstance(trace.get("timing_ms"), dict) else {}
    usage = trace.get("llm_usage") if isinstance(trace.get("llm_usage"), dict) else {}
    tool_calls = trace.get("tool_calls") if isinstance(trace.get("tool_calls"), list) else []
    chain = trace.get("chain") if isinstance(trace.get("chain"), dict) else {}

    # intent/goal fidelity & goal satisfaction（证据缺席则维度缺席）。
    if map_product:
        _add("gate.replay.task_complete", _bool01(map_product.get("task_complete")))
    if goal_satisfaction is not None:
        _add("gate.replay.goal_pass",
             _bool01(goal_satisfaction.get("status") == "pass"))

    # plan stability（重放比对结论；未比对则缺席）。
    if plan_stable is not None:
        _add("gate.replay.plan_stability", _bool01(plan_stable))

    # chain 覆盖度（证据链完整性，越高越好）。
    completeness = _finite(chain.get("completeness"))
    if completeness is not None:
        _add("gate.replay.chain_completeness", completeness * 100.0)

    # gate 维度得分（HarnessEvaluator 形态原样透传为 gate.<name> 行）。
    checks = gate_result.get("checks") if isinstance(gate_result, dict) and isinstance(gate_result.get("checks"), dict) else {}
    for name, check in checks.items():
        if not isinstance(check, dict):
            continue
        if check.get("evaluated") is not True:
            continue  # not_evaluated / not_applicable 不产数值行（诚实缺席）
        _add(f"gate.{str(name)[:118]}", _finite(check.get("score")))

    # 成本/延迟/工作量（越高越糟）。
    _add("replay.tool_calls", _finite(work.get("tool_calls")))
    _add("replay.token_total", _finite(usage.get("total_tokens")))
    _add("replay.duration_ms", _finite(timing.get("total")))
    _add("replay.tool_ms", _finite(timing.get("tool")))
    _add("replay.map_actions_unacked", _finite(work.get("map_actions_unacked")))
    _add("replay.artifacts", _finite(work.get("artifacts")))

    # 工具错误率（data/analysis correctness 的收据面代理）。
    if tool_calls:
        errors = sum(1 for c in tool_calls if isinstance(c, dict) and c.get("is_error"))
        _add("replay.tool_error_rate", errors * 100.0 / max(1, len(tool_calls)))
        arg_bytes_total = sum(
            float(c.get("arg_bytes") or 0)
            for c in tool_calls if isinstance(c, dict)
        )
        _add("replay.arg_bytes_total", arg_bytes_total)

    # 修复/恢复压力。
    repairs = map_product.get("repairs") if isinstance(map_product.get("repairs"), list) else []
    _add("replay.repair_count", float(len(repairs)))
    issues = map_product.get("issues") if isinstance(map_product.get("issues"), list) else []
    _add("replay.finding_count", float(len(issues)))

    # artifact bytes（artifacts 块内的 digest 尺寸计数代理）。
    artifacts = trace.get("artifacts") if isinstance(trace.get("artifacts"), dict) else {}
    _add("replay.artifact_digest_count", _finite(artifacts.get("count")))

    # 决策溯源面（ADR-0212）：决策数量（成本代理）+ dispatch 拒绝计数。
    decisions = trace.get("decisions") if isinstance(trace.get("decisions"), list) else []
    if decisions:
        _add("replay.decision_count", float(len(decisions)))
        denials = sum(
            1 for d in decisions
            if isinstance(d, dict)
            and d.get("kind") == DECISION_KIND_CAPABILITY_DISPATCH_DENIAL
        )
        if denials:
            _add("replay.capability_denials", float(denials))

    # 资源面（ADR-0214 D4）：plan cost delta 的 tolerant 观测行（数值
    # 绝不进 digest/exact，走 ratchet 行回归）。
    governor = trace.get("governor") if isinstance(trace.get("governor"), dict) else {}
    entries = governor.get("entries") if isinstance(governor.get("entries"), list) else []
    if entries:
        _add("replay.resource_entries", float(len(entries)))
    delta = governor.get("plan_cost_delta") if isinstance(governor.get("plan_cost_delta"), dict) else {}
    ratio = delta.get("ratio")
    if isinstance(ratio, (int, float)):
        _add("replay.plan_cost_ratio", float(ratio))

    return rows
