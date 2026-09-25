"""录制轨迹 → 可重放场景（方向 8 / ADR-0212 决策三；ADR-0214 D3 expect 回填）。

recorder 产出的 ReplayTrace 不再是 write-only：真实流量沉淀为回归
场景（``Scenario``），经 ``OfflineReplayer`` 离线确定性重放 —— 「录制
→ 重放」闭环的最后一块。

**expect 回填（ADR-0214 D3，去 green-by-construction）**：recorded 场景的
expect 从录制事实派生（`goal` ← map_product.task_complete、
`decision_rederive` ← 决策索引、`dispatch` ← dispatch_evidence），再经
:func:`calibrate_recorded_scenario` 对当前重放环境做一次校准 —— 与当前
推导不符的期望叶被裁掉并记录收据（诚实披露），保留下来的期望在后续
重放中可失败。纪律：只期望录制时成立、且当前环境可复现的事实；
证据缺席的维度不进 expect（不伪造）。

诚实降级纪律（B1/B3）：

- args 为 digest-only（bound_meta 折叠 / 超预算降级）→ 场景打
  ``degraded`` tag：证据级重放仍可跑（receipt 形状驱动 gate），但
  ``arguments`` 不可还原是显式事实；
- ``result_ref`` 缺席（录制面没拿到 receipt 形状）→ op result 为空
  dict，gate 评测按无收据的诚实语义走 —— 绝不伪造收据；
- MAP_MUTATIONS 链记录只有 command 名/action_id（无参数），无法重建
  T2 变异 specs → recorded 场景恒为 evidence-level（T1）；变异级回归
  仍是语料场景（corpus）的职责。

多轮链接：同 session 多条 trace 按 ``created_at_epoch`` 排序合并为一个
multi-turn Scenario —— 重放器共享 harness 的会话语义承载跨轮情境
持续性验证。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.lib.harness.replay.replayer import (
    OfflineReplayer,
    Scenario,
    ScenarioOp,
    TurnSpec,
    compare_exact,
)


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


# ── 候选 expect 派生（录制事实 → 期望树；ADR-0214 D3）────────────────────────


def _dispatch_pins(
    trace: Dict[str, Any], calls: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """dispatch_evidence × tool_calls（同工具按出现序 join）→ dispatch
    expect pins + T3 fixture（tool → capability 声明）。"""
    evidence = [
        dict(e) for e in (trace.get("dispatch_evidence") or [])
        if isinstance(e, dict) and (e.get("tool") or e.get("action"))
    ]
    if not evidence:
        return {}, {}
    pins: Dict[str, Any] = {}
    registry: Dict[str, List[str]] = {}
    used: set = set()
    for call in calls:
        tool = str(call.get("tool_name") or "")
        if not tool:
            continue
        match = next(
            ((i, e) for i, e in enumerate(evidence)
             if i not in used and str(e.get("tool") or "") == tool),
            None,
        )
        if match is None:
            continue
        i, entry = match
        used.add(i)
        call_id = str(call.get("tool_call_id") or "")
        if not call_id:
            continue
        refused = str(entry.get("action") or "") == "refused"
        cap = str(entry.get("capability")
                  or entry.get("rank_capability") or "")[:96]
        pin: Dict[str, Any] = {"allowed": not refused}
        if refused and cap:
            pin["capability"] = cap
        pins[call_id] = pin
        if cap:
            caps = registry.setdefault(tool[:96], [])
            if cap not in caps and len(caps) < 8:
                caps.append(cap)
    return pins, registry


def _candidate_expect(
    trace: Dict[str, Any], calls: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """trace 录制事实 → 候选 expect 树 + tool_registry fixture。

    只派生录制时成立、且重放器可离线重推导的维度（goal / dispatch /
    decision_rederive / receipt）；gate per-check 是重放器自评面、录制链
    不带（FINAL_VERDICT 只带 verdict/final_map_status）→ 不伪造 gate 期望。
    """
    expect: Dict[str, Any] = {}
    verdict = trace.get("verdict") if isinstance(trace.get("verdict"), dict) else {}
    map_product = verdict.get("map_product") \
        if isinstance(verdict.get("map_product"), dict) else {}
    # goal：task_complete=True 是录制时的事实；重放端 L5 推导不成立即红。
    if map_product.get("task_complete") is True:
        expect["goal"] = {"status": "pass"}
    # 决策重推导 pins（capability_resolution 面）。
    for d in trace.get("decisions") or []:
        if not isinstance(d, dict) \
                or d.get("kind") != "capability_resolution" \
                or not d.get("decision_id"):
            continue
        selected = str(d.get("selected") or "")
        if not selected:
            continue
        expect.setdefault("decision_rederive", {})[
            str(d["decision_id"])[:24]
        ] = {"selected": selected}
    # dispatch pins + T3 fixture。
    dispatch_pins, registry = _dispatch_pins(trace, calls)
    if dispatch_pins:
        expect["dispatch"] = dispatch_pins
    # receipt pins（T4）：录制的调用终态 + ref 铸造面 —— 真实 dispatch
    # 合同重放必须复现（ok→ok / error→error / 有 ref → ref_minted）。
    for call in calls:
        call_id = str(call.get("tool_call_id") or "")
        status = str(call.get("status") or "")
        if not call_id or status not in ("ok", "error"):
            continue
        ref = call.get("result_ref") if isinstance(call.get("result_ref"), dict) else {}
        pin: Dict[str, Any] = {"status": status}
        if status == "ok" and ref.get("geojson_ref"):
            pin["ref_minted"] = True
        if status == "error" and call.get("error_msg"):
            pin["error_code"] = str(call["error_msg"])[:48]
        expect.setdefault("receipt", {})[call_id] = pin
    return expect, registry


def _trace_turn(trace: Dict[str, Any]) -> Tuple[TurnSpec, Dict[str, List[str]]]:
    calls = [
        call for call in (trace.get("tool_calls") or [])
        if isinstance(call, dict) and (call.get("tool_name") or call.get("tool_call_id"))
    ]
    expect, registry = _candidate_expect(trace, calls)
    return TurnSpec(
        user_input=str(trace.get("user_input") or ""),
        ops=[_op_from_tool_call(call, i) for i, call in enumerate(calls)],
        expect=expect,
    ), registry


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
    expect 为**候选**形态（录制事实派生）—— 提交回归前应经
    :func:`calibrate_recorded_scenario` 校准（裁掉当前环境不可复现的叶）。
    """
    normalized = [t for t in (_normalize(x) for x in traces) if t]
    if not normalized:
        return None
    normalized.sort(key=lambda t: (
        t.get("created_at_epoch") if isinstance(t.get("created_at_epoch"), (int, float))
        else float("inf"),
        str(t.get("turn_id") or ""),
    ))
    turns: List[TurnSpec] = []
    registry: Dict[str, List[str]] = {}
    for t in normalized:
        turn, turn_registry = _trace_turn(t)
        turns.append(turn)
        for tool, caps in turn_registry.items():
            merged = registry.setdefault(tool, [])
            for cap in caps:
                if cap not in merged and len(merged) < 8:
                    merged.append(cap)

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

    has_expect = any(turn.expect for turn in turns)
    dispatch_backed = any("dispatch" in turn.expect for turn in turns)
    receipt_backed = any("receipt" in turn.expect for turn in turns)
    if has_expect:
        tags.append("expect_recorded")

    return Scenario(
        scenario_id=f"rec-{session_id[:24]}-{first_turn[:24]}",
        category="recorded",
        description=str(normalized[0].get("user_input") or "")[:160],
        turns=turns,
        tags=tags,
        decisions=decisions,
        registry_digest=registry_digest,
        tool_registry=registry,
        dispatch_backed=dispatch_backed,
        receipt_backed=receipt_backed,
        expect_source="recorded" if has_expect else "",
    )


def trace_to_scenario(trace: Any) -> Optional[Scenario]:
    """单条录制轨迹 → Scenario（:func:`traces_to_scenario` 的单元素便捷形态）。"""
    return traces_to_scenario([trace])


# ── expect 校准（ADR-0214 D3：录制事实 × 当前重放环境）────────────────────────

_DROP_SENTINEL = object()


def _strip_empty(value: Any) -> Any:
    """递归剥离空 dict 节点（裁剪后空子树不残留，underfilled 判定才诚实）。"""
    if isinstance(value, dict):
        out = {}
        for key, sub in value.items():
            stripped = _strip_empty(sub)
            if isinstance(stripped, dict) and not stripped:
                continue
            out[key] = stripped
        return out
    if isinstance(value, list):
        return [_strip_empty(item) for item in value]
    return value


def _prune_expect(
    expected: Any, actual: Any, path: str,
    dropped: List[Dict[str, Any]],
) -> Any:
    """校准：与当前推导不符的期望叶裁掉（收据留痕）；符合的保留。"""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            dropped.append({"path": path or "$", "reason": "actual_not_dict"})
            return {}
        kept: Dict[str, Any] = {}
        for key, sub in expected.items():
            pruned = _prune_expect(
                sub, actual.get(key),
                f"{path}.{key}" if path else key, dropped)
            if pruned is not _DROP_SENTINEL:
                kept[key] = pruned
        return kept
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            dropped.append({"path": path or "$", "reason": "actual_list_short"})
            return _DROP_SENTINEL
        kept_items = []
        for i, sub in enumerate(expected):
            pruned = _prune_expect(sub, actual[i], f"{path}[{i}]", dropped)
            if pruned is not _DROP_SENTINEL:
                kept_items.append(pruned)
        return kept_items
    if expected != actual:
        dropped.append({
            "path": path or "$",
            "reason": "mismatch",
            "recorded": expected,
            "current": actual,
        })
        return _DROP_SENTINEL
    return expected


async def calibrate_recorded_scenario(
    scenario: Scenario,
    *,
    replayer: Optional[OfflineReplayer] = None,
    seed: int = 0,
) -> Tuple[Scenario, Dict[str, Any]]:
    """录制场景 expect 校准：跑一次当前环境重放，裁掉不可复现的期望叶。

    返回（校准后场景副本, 收据）。校准保证：提交日的期望在当前环境下
    全部成立（与 --write-baseline 同时的冻结语义），此后任何维度的漂移
    都会翻红 —— 期望是可失败的，被裁掉的叶带收据可审计。

    场景无期望叶 → 收据标 ``underfilled=True``（诚实披露：
    该录制件没有可回填的可用事实，而不是假绿）。
    """
    rp = replayer or OfflineReplayer(seed=seed)
    calibrated = copy.deepcopy(scenario)
    result = await rp.replay_scenario(calibrated)
    dropped: List[Dict[str, Any]] = []
    for turn_result, turn in zip(result.turns, calibrated.turns):
        if not turn.expect:
            continue
        actual = turn_result.actual_projection(
            dispatch_backed=calibrated.dispatch_backed,
            receipt_backed=calibrated.receipt_backed)
        turn_dropped: List[Dict[str, Any]] = []
        pruned = _prune_expect(turn.expect, actual, "", turn_dropped)
        if pruned is _DROP_SENTINEL:
            turn.expect = {}
        else:
            stripped = _strip_empty(pruned)
            turn.expect = stripped if isinstance(stripped, dict) else {}
        for entry in turn_dropped:
            dropped.append({"turn": turn_result.turn_index, **entry})
    # 期望校验口径与重放器一致：剩余期望在当前环境必须零 diff（防呆：
    # 嵌套结构裁剪不应产生不一致状态）。
    verify = await rp.replay_scenario(calibrated)
    residual: List[Dict[str, Any]] = []
    for turn_result, turn in zip(verify.turns, calibrated.turns):
        semantic_expect = {
            k: v for k, v in turn.expect.items() if k != "user_text"}
        for diff in compare_exact(
                semantic_expect,
                turn_result.actual_projection(
                    dispatch_backed=calibrated.dispatch_backed,
                    receipt_backed=calibrated.receipt_backed)):
            residual.append({"turn": turn_result.turn_index, **diff})
    calibration: Dict[str, Any] = {
        "calibrated": True,
        "dropped": dropped[:64],
        "dropped_count": len(dropped),
        "residual_diffs": residual[:32],
        "underfilled": not any(turn.expect for turn in calibrated.turns),
    }
    if calibration["underfilled"]:
        if "expect_recorded" in calibrated.tags:
            calibrated.tags.remove("expect_recorded")
        calibrated.tags.append("expect_underfilled")
        calibrated.expect_source = ""
    calibrated.expect_calibration = [
        {"dropped_count": calibration["dropped_count"],
         "underfilled": calibration["underfilled"],
         "dropped": calibration["dropped"]},
    ]
    return calibrated, calibration


__all__ = [
    "traces_to_scenario",
    "trace_to_scenario",
    "calibrate_recorded_scenario",
]
