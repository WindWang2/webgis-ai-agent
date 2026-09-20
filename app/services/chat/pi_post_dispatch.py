"""Pi post-dispatch disclosure pipeline（方向 09：Pi↔Harness 边界收敛）。

把 ``agent_pi_bridge._dispatch_tool_bound`` 中散落的 GIS 后置披露段
（SessionPlan 证据 → 运行态投影 → 地图完成度终验 → cartography harness
证据 → 证据链阶段）收敛为一个 typed、顺序固定、逐段 never-raise 的管线，
并为 stream（``agent_settled``）与 non-stream（``prompt`` 清洁收口）提供
**同一个** turn 结算管线，消除双路径漂移（此前非流式缺 turn-settle 投影 /
链持久化 / checkpoint，process_died 时 tracker 甚至结算成 completed）。

边界裁决（ADR-0204）：

- 本模块不拥有调度正确性（ToolDispatchService）与 turn 生命周期（PiBridge）；
  只拥有「dispatch 之后如何向 Harness/计划/前端披露」。
- 不在模块顶层 import ``agent_pi_bridge``（防环）。cartography harness 访问器
  与事件类型（``_get_session_harness`` / ``ToolCallEvent`` / 锁异常）在
  **调用时**经 bridge 命名空间惰性解析 —— 这是既有测试的 patch 面，也是
  ADR-0022 式的刻意 rendezvous。
- 「增值披露绝不阻断工具返回」纪律在此统一实现：每个披露段独立 never-raise
  （stale MapSpec generation 的诚实短路除外 —— 它返回标记，由 bridge 决定
  诚实响应）。

ok / error 分支的既有不对称被**显式保留**并记录（error 不推进
runtime_state_machine、不做完成度终验），不再靠复制粘贴隐藏；见 ADR-0204
follow-up。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# SessionPlan/评估会话锁竞争语义（py3.11+ 二者同型；显式元组保留旧路径兼容）。
_LOCK_CONTENTION_EXCS = (TimeoutError, asyncio.TimeoutError)


# ── Contracts ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DispatchDisclosure:
    """一次工具 dispatch 的披露输入（bridge 组装；pipeline 只读）。

    ``turn_id`` 是回调自带的 verifiedTurnId（未验证直调为空）；
    ``active_turn_id`` 是 bridge 按 session 解析的在飞 turn（迟到回调守卫与
    证据链都以此为准，#1407 语义：优先 verifiedTurnId 归因）。
    """

    session_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict
    status: str                    # "ok" | "error" | "repeated"（repeated 不进计划/投影）
    raw_result: dict = field(default_factory=dict)
    llm_payload: str = ""
    geojson_ref: str = ""
    map_actions: tuple = ()
    turn_id: str = ""
    active_turn_id: str = ""
    late_for_plan: bool = False    # #1407：迟到回调不写计划证据、不触发终验/投影
    duration_ms: int = 0           # dispatch 起点→结果落定的真实耗时（证据/链共用）


@dataclass
class DisclosureOutcome:
    """pipeline 回答「披露后发生了什么」；bridge 负责转成 PiToolResponse/SSE 缓存。"""

    plan_sse: str = ""
    # ok 分支无条件缓存（含空事件串，保持既有行为）；error 分支仅非空才缓存。
    cache_plan_sse_unconditionally: bool = False
    finalization_payload: Optional[dict] = None   # 非 pending 的 map_finalization 负载
    stale_generation: bool = False                # 诚实短路：旧 MapSpec 世代结果


# ── Dispatch post-disclosure（_dispatch_tool_bound 后半段）────────────────


async def apply_post_dispatch_disclosure(d: DispatchDisclosure) -> DisclosureOutcome:
    """dispatch 之后的全部 GIS 披露，顺序即权威序：

    1. SessionPlan 证据（ok/error，迟到回调跳过；锁竞争重试一次）
    2. 运行态投影（workflow_instance 双分支；runtime_state_machine 仅 ok；
       runtime_bridge 投影双分支 —— 仅非迟到）
    3. 完成度终验 + map_finalization 负载（仅 ok、非迟到）
    4. cartography harness 证据（任意 status；stale 世代 → 诚实短路）
    5. 证据链阶段（有活跃 turn 才记；stale 短路时跳过 —— 既有行为）
    """
    out = DisclosureOutcome()
    if not d.late_for_plan:
        if d.status == "ok":
            out.plan_sse = await _apply_tool_evidence(d, success=True)
            out.cache_plan_sse_unconditionally = True
            await _advance_runtime_projections(d, ok=True)
            out.finalization_payload = await _finalize_after_tool(d)
        elif d.status == "error":
            out.plan_sse = await _apply_tool_evidence(d, success=False)
            await _advance_runtime_projections(d, ok=False)

    out.stale_generation = await _record_cartographic_evidence(d)
    if out.stale_generation:
        return out

    _record_dispatch_trace_stages(d)
    return out


async def _apply_tool_evidence(d: DispatchDisclosure, *, success: bool) -> str:
    """kernel.apply_tool_evidence + 锁竞争重试一次；返回待缓存的 plan SSE 文本。

    两分支此前是逐行复制的 ~55 行块（含 TimeoutError 重试、turn_id 二次解析）；
    收敛为单实现。重试沿用同一 resolved turn id（替代旧代码重试路径重新解析
    active turn 的微小漂移，归因更确定）。
    """
    try:
        from app.services.harness_kernel import get_runtime
        from app.services.session_plan import events_to_sse

        plan_events = await get_runtime(d.session_id).apply_tool_evidence(
            d.tool_name,
            d.raw_result,
            success=success,
            geojson_ref=(d.geojson_ref or None),
            tool_call_id=d.tool_call_id,
            turn_id=(d.turn_id or d.active_turn_id or ""),
            host="pi",
        )
        return events_to_sse(plan_events, d.session_id) if plan_events else ""
    except _LOCK_CONTENTION_EXCS:
        # Session-lock contention（如长制图评估持锁）：重试一次，避免信封
        # 更新（可能是 supersede）静默丢失。
        logger.warning(
            "[PiBridge][post-dispatch] SessionPlan apply lock contention "
            "session=%s tool=%s success=%s — retrying once",
            d.session_id, d.tool_name, success,
        )
        try:
            from app.services.harness_kernel import get_runtime
            from app.services.session_plan import events_to_sse

            plan_events = await get_runtime(d.session_id).apply_tool_evidence(
                d.tool_name,
                d.raw_result,
                success=success,
                geojson_ref=(d.geojson_ref or None),
                tool_call_id=d.tool_call_id,
                turn_id=(d.turn_id or d.active_turn_id or ""),
                host="pi",
            )
            return events_to_sse(plan_events, d.session_id) if plan_events else ""
        except Exception:
            logger.exception(
                "[PiBridge][post-dispatch] SessionPlan apply retry failed "
                "session=%s tool=%s",
                d.session_id, d.tool_name,
            )
    except Exception:
        logger.exception(
            "[PiBridge][post-dispatch] SessionPlan apply failed session=%s tool=%s",
            d.session_id, d.tool_name,
        )
    return ""


async def _advance_runtime_projections(d: DispatchDisclosure, *, ok: bool) -> None:
    """推进三套运行态投影（增值披露，逐段 never-raise）。

    既有不对称显式化：workflow_instance 与 runtime_bridge 投影双分支推进；
    runtime_state_machine 仅 ok 分支（error 不推进 —— 保持现状，见 ADR-0204
    follow-up）。
    """
    reason = f"tool_result:{d.tool_name}" if ok else f"tool_error:{d.tool_name}"
    try:
        from app.services.gis_harness.workflow_instance import (
            maybe_update_workflow_instance,
        )
        await maybe_update_workflow_instance(
            d.session_id,
            reason=reason,
            event="auto" if ok else "tool_failure",
        )
    except Exception:  # noqa: BLE001 — 实例态是增值披露
        logger.debug(
            "[PiBridge][post-dispatch] workflow instance update failed "
            "session=%s tool=%s", d.session_id, d.tool_name, exc_info=True,
        )
    if ok:
        try:
            from app.services.gis_harness.runtime_state_machine import (
                maybe_update_runtime_state,
            )
            await maybe_update_runtime_state(
                d.session_id,
                reason=reason,
                trigger="execution_progressed",
            )
        except Exception:  # noqa: BLE001 — 阶段投影是增值披露
            logger.debug(
                "[PiBridge][post-dispatch] runtime state update failed "
                "session=%s tool=%s", d.session_id, d.tool_name, exc_info=True,
            )
    try:
        from app.services.gis_harness.runtime_bridge import (
            maybe_update_runtime_projection,
        )
        await maybe_update_runtime_projection(d.session_id, reason=reason)
    except Exception:  # noqa: BLE001 — 运行态投影是增值披露
        logger.debug(
            "[PiBridge][post-dispatch] runtime projection update failed "
            "session=%s tool=%s", d.session_id, d.tool_name, exc_info=True,
        )


async def _finalize_after_tool(d: DispatchDisclosure) -> Optional[dict]:
    """ADR-0081 完成度终验（幂等、有界）；返回非 pending 的披露负载或 None。"""
    try:
        from app.services.gis_harness.map_completion import (
            current_mapspec_for_disclosure,
            finalization_sse_payload,
            maybe_finalize_map_product,
        )
        completion = await maybe_finalize_map_product(
            d.session_id, reason=f"tool_result:{d.tool_name}"
        )
        if completion is not None and completion.status != "pending":
            spec_snapshot = (None, None)
            if completion.repairs_applied:
                spec_snapshot = await current_mapspec_for_disclosure(d.session_id)
            return finalization_sse_payload(
                completion,
                d.session_id,
                mapspec=spec_snapshot[0],
                mutation_revision=spec_snapshot[1],
            )
    except Exception:  # noqa: BLE001 — 终验是增值信号，绝不阻断工具返回
        logger.exception(
            "[PiBridge][post-dispatch] map finalization failed session=%s tool=%s",
            d.session_id, d.tool_name,
        )
    return None


async def _record_cartographic_evidence(d: DispatchDisclosure) -> bool:
    """记录 cartography harness 证据并触发共享评估；返回是否 stale 世代短路。

    调用时经 bridge 命名空间解析 harness 访问器/事件类型/锁异常 —— 既有测试
    在该命名空间打 patch（rendezvous，保持）。锁降级/丢失按既有语义折算为
    stale（跳过本帧 harness 上下文，不把成功调用标 500）。
    """
    from app import agent_pi_bridge as bridge

    raw = d.raw_result if isinstance(d.raw_result, dict) else {}
    has_cartographic_generation = bool(raw.get("mapspec_fingerprint"))
    from app.services.cartography_runtime import result_indicates_map_change

    indicates_map_change = result_indicates_map_change(raw)
    harness = bridge._get_session_harness(
        d.session_id,
        create=has_cartographic_generation or indicates_map_change,
    )
    if harness is None:
        return False

    is_error = d.status == "error"
    ev = {"status": d.status, "llm_payload_len": len(d.llm_payload)}
    from app.lib.harness.evidence import CARTOGRAPHIC_RESULT_EVIDENCE_KEYS
    for k in CARTOGRAPHIC_RESULT_EVIDENCE_KEYS:
        if k in raw:
            ev[k] = raw[k]
    event = bridge.ToolCallEvent(
        tool_call_id=d.tool_call_id,
        # #789：保留真实工具名（mutation 台账按结构分类，见 PiAgentHarness）。
        tool_name=d.tool_name,
        arguments=d.arguments,
        duration_ms=d.duration_ms,
        is_error=is_error,
        error_msg=(d.llm_payload[:200] if is_error else ""),
        result=ev,
        session_id=d.session_id,
    )
    if has_cartographic_generation and d.status == "ok":
        # v2(review R1-P2-6)：工具已成功提交，降级锁不得把成功调用标成 500
        # —— 兜底为跳过本帧 harness 上下文（下一事件源会重建）。
        try:
            generation_current = await bridge._persist_cartographic_harness_context(
                d.session_id, event, list(d.map_actions)
            )
        except (bridge.LockDegradedError, bridge.LockLostError) as lock_err:
            logger.warning(
                "[PiBridge][post-dispatch] harness context skipped (lock "
                "unavailable) session=%s tool=%s: %s",
                d.session_id, d.tool_name, lock_err,
            )
            generation_current = False
        if not generation_current:
            return True
    if d.status == "ok":
        for ma in d.map_actions:
            harness.record_map_action_issued(
                session_id=d.session_id,
                tool_call_id=d.tool_call_id,
                turn_id="",
                action_id=ma["action_id"],
                command=ma["command"],
                requested=ma["requested"],
                mapspec_fingerprint=ma.get("mapspec_fingerprint"),
            )
    harness.record_event(event)

    if has_cartographic_generation or indicates_map_change:
        try:
            await bridge.evaluate_cartographic_session(d.session_id)
        except Exception as review_error:  # noqa: BLE001 — GIS success is immutable
            logger.warning(
                "[PiBridge][post-dispatch] cartographic evaluation unavailable "
                "for %s: %s", d.session_id, review_error,
            )
    return False


def _record_dispatch_trace_stages(d: DispatchDisclosure) -> None:
    """ADR-0103（§十）：TOOL_CALLS/ARGUMENTS/TOOL_RESULTS/MAP_MUTATIONS 阶段。"""
    try:
        from app.lib.runtime.gis_trace import Stage, record_stage

        if d.active_turn_id:
            record_stage(d.active_turn_id, Stage.TOOL_CALLS, tool=d.tool_name,
                         call_id=d.tool_call_id or "")
            record_stage(d.active_turn_id, Stage.ARGUMENTS, tool=d.tool_name,
                         args=str(d.arguments)[:_RECORD_ARGS_BOUND])
            record_stage(d.active_turn_id, Stage.TOOL_RESULTS, tool=d.tool_name,
                         status=d.status,
                         latency_ms=d.duration_ms)
            if d.map_actions:
                record_stage(d.active_turn_id, Stage.MAP_MUTATIONS, tool=d.tool_name,
                             actions=[ma.get("action_id", "") for ma in d.map_actions[:8]],
                             commands=[ma.get("command", "") for ma in d.map_actions[:8]])
    except Exception:  # noqa: BLE001
        logger.debug("[PiBridge][post-dispatch] gis trace record failed", exc_info=True)


_RECORD_ARGS_BOUND = 512


# ── Turn settle（stream agent_settled 与 non-stream 清洁收口共用）──────────


async def settle_turn_projections(
    session_id: str,
    turn_id: str,
    *,
    reason: str = "turn_settled",
    state_trigger: str = "execution_settled",
) -> Optional[dict]:
    """turn 收口披露管线（双路径共用，幂等门兜底）。

    顺序：完成度终验（final gate）→ WorkflowInstance → RuntimeState
    （turn_settled 旗标）→ 九域上下文 checkpoint（受状态机 kill switch）→
    证据链 USER_OUTPUT + 持久化。返回 map_finalization 负载（含幂等门跳过时
    已存储的完成态），供 task_complete / 录制 / SSE 披露。
    """
    map_product: Optional[dict] = None
    try:
        from app.services.gis_harness.map_completion import (
            current_mapspec_for_disclosure,
            finalization_sse_payload,
            maybe_finalize_map_product,
            read_stored_map_product,
        )
        completion = await maybe_finalize_map_product(
            session_id, reason=reason, final_gate=True,
        )
        try:
            from app.services.gis_harness.workflow_instance import (
                maybe_update_workflow_instance,
            )
            await maybe_update_workflow_instance(session_id, reason=reason, event="auto")
        except Exception:  # noqa: BLE001 — 增值披露
            logger.debug(
                "[PiBridge][settle] workflow instance update failed session=%s",
                session_id, exc_info=True,
            )
        try:
            from app.services.gis_harness.runtime_state_machine import (
                maybe_update_runtime_state,
            )
            await maybe_update_runtime_state(
                session_id, reason=reason, trigger=state_trigger, turn_settled=True,
            )
        except Exception:  # noqa: BLE001 — 增值披露
            logger.debug(
                "[PiBridge][settle] runtime state update failed session=%s",
                session_id, exc_info=True,
            )
        try:
            from app.services.gis_harness.runtime_state_machine import (
                runtime_state_enabled,
            )
            if runtime_state_enabled():
                from app.services.gis_harness.context_layers import (
                    checkpoint_context_layers,
                )
                await checkpoint_context_layers(session_id)
        except Exception:  # noqa: BLE001 — 增值披露
            logger.debug(
                "[PiBridge][settle] context checkpoint failed session=%s",
                session_id, exc_info=True,
            )
        if completion is not None and completion.status != "pending":
            spec_snapshot = (None, None)
            if completion.repairs_applied:
                spec_snapshot = await current_mapspec_for_disclosure(session_id)
            map_product = finalization_sse_payload(
                completion,
                session_id,
                mapspec=spec_snapshot[0],
                mutation_revision=spec_snapshot[1],
            )
        elif completion is None:
            # 幂等门跳过（complete+revision 一致）→ 仍披露已存储的完成态。
            map_product = await read_stored_map_product(session_id)
    except Exception:  # noqa: BLE001 — 增值信号
        logger.exception(
            "[PiBridge][settle] turn-settle finalization failed session=%s",
            session_id,
        )
    # 证据链与 finalization 分离 try：finalization 失败不阻断链持久化。
    try:
        from app.lib.runtime.chain_emitters import emit_chain_for
        from app.lib.runtime.gis_trace import Stage
        from app.services.gis_harness.trace_store import persist_turn_chain

        emit_chain_for(
            turn_id,
            Stage.USER_OUTPUT,
            final_gate=True,
            task_complete=(
                map_product.get("task_complete")
                if isinstance(map_product, dict)
                else False
            ) is True,
        )
        persist_turn_chain(turn_id, session_id=session_id)
    except Exception:  # noqa: BLE001 — 记录面绝不阻断 settle
        pass
    return map_product


__all__ = [
    "DispatchDisclosure",
    "DisclosureOutcome",
    "apply_post_dispatch_disclosure",
    "settle_turn_projections",
]
