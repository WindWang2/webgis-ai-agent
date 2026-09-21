"""录制轨迹 → 可重放场景（方向 8 / ADR-0212 决策三）。

recorder 产出的 ReplayTrace 不再是 write-only：真实流量沉淀为回归
场景（``Scenario``），经 ``OfflineReplayer`` 离线确定性重放 —— 「录制
→ 重放」闭环的最后一块。

诚实降级纪律（B1/B3）：

- args 为 digest-only（bound_meta 折叠 / 超预算降级）→ 场景打
  ``degraded`` tag：证据级重放仍可跑（receipt 形状驱动 gate），但
  ``arguments`` 不可还原是显式事实；
- ``result_ref`` 缺席（录制面没拿到 receipt 形状）→ op result 为空
  dict，gate 评测按无收据的诚实语义走（通常 red）—— 绝不伪造收据；
- MAP_MUTATIONS 链记录只有 command 名/action_id（无参数），无法重建
  T2 变异 specs → recorded 场景恒为 evidence-level（T1）；变异级回归
  仍是语料场景（corpus）的职责。

多轮链接：同 session 多条 trace 按 ``created_at_epoch`` 排序合并为一个
multi-turn Scenario —— 重放器共享 harness 的会话语义承载跨轮情境
持续性验证。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.lib.harness.replay.replayer import Scenario, ScenarioOp, TurnSpec


def _receipt_from_call(call: Dict[str, Any]) -> Dict[str, Any]:
    """tool_call 块 → canned receipt（``result_ref`` 即消毒后的收据形状）。"""
    ref = call.get("result_ref")
    if isinstance(ref, dict):
        return dict(ref)
    return {}


def _op_from_tool_call(call: Dict[str, Any], index: int) -> ScenarioOp:
    arguments = call.get("arguments")
    return ScenarioOp(
        call_id=str(call.get("tool_call_id") or f"call-{index + 1}"),
        tool=str(call.get("tool_name") or ""),
        arguments=dict(arguments) if isinstance(arguments, dict) else {},
        result=_receipt_from_call(call),
        is_error=bool(call.get("is_error")),
        error_msg=str(call.get("error_msg") or ""),
        duration_ms=float(call.get("duration_ms") or 0.0),
    )


def _is_degraded(trace: Dict[str, Any]) -> bool:
    for call in trace.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        if call.get("args_truncated"):
            return True
        args = call.get("arguments")
        if isinstance(args, dict) and "_digest_only" in args:
            return True
    return False


def _trace_turn(trace: Dict[str, Any]) -> TurnSpec:
    calls = [
        call for call in (trace.get("tool_calls") or [])
        if isinstance(call, dict) and (call.get("tool_name") or call.get("tool_call_id"))
    ]
    return TurnSpec(
        user_input=str(trace.get("user_input") or ""),
        ops=[_op_from_tool_call(call, i) for i, call in enumerate(calls)],
        expect={},
    )


def _normalize(trace: Any) -> Dict[str, Any]:
    if isinstance(trace, dict):
        return trace
    to_dict = getattr(trace, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        return converted if isinstance(converted, dict) else {}
    return {}


def traces_to_scenario(traces: Iterable[Any]) -> Optional[Scenario]:
    """同 session 的录制轨迹（≥1 条）→ 一个可重放 Scenario。

    多条 = multi-turn（按 ``created_at_epoch`` 排序）；0 条 = None。
    """
    normalized = [t for t in (_normalize(x) for x in traces) if t]
    if not normalized:
        return None
    normalized.sort(key=lambda t: (
        t.get("created_at_epoch") if isinstance(t.get("created_at_epoch"), (int, float))
        else float("inf"),
        str(t.get("turn_id") or ""),
    ))
    turns = [_trace_turn(t) for t in normalized]
    session_id = str(normalized[0].get("session_id") or "session")
    first_turn = str(normalized[0].get("turn_id") or "turn")
    tags = ["recorded"]
    if len(turns) > 1:
        tags.append("multi_turn")
    if any(_is_degraded(t) for t in normalized):
        tags.append("degraded")

    decisions: List[Dict[str, Any]] = []
    registry_digest = ""
    for t in normalized:
        for d in t.get("decisions") or []:
            if isinstance(d, dict) and d.get("decision_id") \
                    and len(decisions) < 16:
                decisions.append(dict(d))
        env = t.get("env")
        if not registry_digest and isinstance(env, dict):
            registry_digest = str(env.get("registry_digest") or "")

    return Scenario(
        scenario_id=f"rec-{session_id[:24]}-{first_turn[:24]}",
        category="recorded",
        description=str(normalized[0].get("user_input") or "")[:160],
        turns=turns,
        tags=tags,
        decisions=decisions,
        registry_digest=registry_digest,
    )


def trace_to_scenario(trace: Any) -> Optional[Scenario]:
    """单条录制轨迹 → Scenario（:func:`traces_to_scenario` 的单元素便捷形态）。"""
    return traces_to_scenario([trace])


__all__ = ["traces_to_scenario", "trace_to_scenario"]
