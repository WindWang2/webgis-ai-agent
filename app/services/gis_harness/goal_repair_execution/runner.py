"""G42 执行体 —— 顺序动作 + 前置复核 + 预算 + 取消 + 原终验对账。

与 G30 runner 同族纪律：

- ``run_async_job`` 驱动（fenced 终态 / 心跳 / 两阶段取消）；
- 步间 ``handle.checkpoint()`` 协作取消 —— 取消时已积累回执经
  ``write_summary_patch`` 保留（刷新/重连可回看，R24/R28）；
- 每动作前置复核（goal 指纹 / 预算 admission）在副作用**之前**；
- 动作全部收口后经 ``maybe_finalize_map_product(final_gate=True)`` 走原
  evaluator —— 本模块不写第二份 goal report（WP11）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from . import store
from .actions import (
    RepairOwnerContext,
    execute_delivery,
    execute_map_export,
    execute_requalify,
    execute_runtime_repair,
    execute_state_reread,
    wait_fresh_observation,
)
from .bindings import (
    ENTRY_DATA_DELIVERY,
    ENTRY_MAP_EXPORT,
    ENTRY_REQUALIFY,
    ENTRY_REOBSERVE,
    ENTRY_REREAD,
    ENTRY_RUNTIME_REPAIR,
)
from .contracts import (
    ACTION_BLOCKED,
    ACTION_FAILED,
    ACTION_NEEDS_USER,
    ACTION_SKIPPED_CANCELLED,
    ACTION_STALE,
    ACTION_SUCCEEDED,
    ACTION_UNSUPPORTED,
    DISPOSITION_BLOCKED,
    DISPOSITION_EXECUTABLE,
    DISPOSITION_NEEDS_ACK,
    DISPOSITION_NEEDS_USER,
    DISPOSITION_UNSUPPORTED,
    OUTCOME_CANCELLED,
    OUTCOME_COMPLETED,
    OUTCOME_NO_PROGRESS,
    OUTCOME_SUPERSEDED,
    bounded_action_receipt,
)

from app.lib.cancellation import OperationCancelled as _OperationCancelled

logger = logging.getLogger(__name__)

#: Pi 回调启动时等待原 turn settle 的上限（settle 链正常 ≤10s；宽限覆盖
#: 重终验/检查点链的最坏情形，超时按可继续处理 —— finalizer 幂等门兜底）。
TURN_SETTLE_WAIT_S = 120.0

#: 进程内执行体登记（读路径对账 / 取消直达；跨进程靠 DB 探针）。
_RUNNERS: Dict[int, "asyncio.Task[None]"] = {}


def register_runner_task(job_id: int, task: "asyncio.Task[None]") -> None:
    _RUNNERS[job_id] = task

    def _done(_t: "asyncio.Task[None]") -> None:
        _RUNNERS.pop(job_id, None)

    task.add_done_callback(_done)


def runner_alive(job_id: int) -> bool:
    task = _RUNNERS.get(int(job_id))
    return task is not None and not task.done()


async def _goal_fingerprint_now(session_id: str) -> str:
    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(session_id)
    chapter = plan.gis_chapter if plan and isinstance(
        plan.gis_chapter, dict) else {}
    from .service import _goal_fingerprint

    return _goal_fingerprint(plan, chapter)


async def run_attempt_job(job_id: int) -> None:
    """attempt 执行体入口（独立 asyncio 任务；不持 wave 槽）。"""
    from app.services.data_operations.runner import run_async_job

    try:
        await run_async_job(job_id, _execute_attempt)
    except Exception:  # noqa: BLE001 — run_async_job 已落 failed 终态
        logger.warning("[goal-repair] attempt %s failed", job_id, exc_info=True)


async def _execute_attempt(handle: Any) -> Dict[str, Any]:
    spec = store.read_dispatch_spec(int(handle.job_id)) or {}
    owner_raw = spec.get("owner") or {}
    ctx = RepairOwnerContext(
        session_id=str(owner_raw.get("session_id") or ""),
        user_id=owner_raw.get("user_id") or None,
        org_id=owner_raw.get("org_id") or None,
        project_id=owner_raw.get("project_id") or None,
        owner_token=owner_raw.get("owner_token") or None,
        turn_id=str(owner_raw.get("turn_id") or ""),
        tool_call_id=str(owner_raw.get("tool_call_id") or ""),
    )
    session_id = ctx.session_id
    prerequisites = spec.get("prerequisites") or {}
    # Pi 回调启动的 attempt：先等当前 turn settle（有界）—— ① 语义上
    # attempt 的重终验不得与原 turn 的 settle 终验交错（同一 finalizer 的
    # 去重门能挡，但读写并发窗口真实存在）；② 交付 runner / attempt 心跳
    # 与 settle 效果链的并发写在 sqlite 上退化为多写者锁排队（生产为
    # Postgres，此处串行化是正确的时序而非掩盖）。
    if ctx.turn_id:
        deadline = asyncio.get_event_loop().time() + TURN_SETTLE_WAIT_S
        while asyncio.get_event_loop().time() < deadline:
            handle.checkpoint()  # 复审 nit：等待期取消即时生效（≤间隔）
            try:
                from app.agent_pi_bridge import get_active_turn_entry

                if get_active_turn_entry(session_id) is None:
                    break
            except Exception:  # noqa: BLE001 — 判定失败立即继续（不阻塞）
                break
            await asyncio.sleep(0.5)
    bindings = {str(b.get("action_id")): b for b in (spec.get("bindings")
                                                     or [])
                if isinstance(b, dict)}
    selected = [str(a) for a in (spec.get("selected_action_ids") or [])[:8]]
    acked_losses = [str(a)[:48]
                    for a in (spec.get("acknowledged_losses") or [])[:8]]
    verdict_before = str(prerequisites.get("verdict_before") or "")[:24]

    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(session_id)
    chapter = plan.gis_chapter if plan and isinstance(
        plan.gis_chapter, dict) else {}

    receipts: List[Dict[str, Any]] = []
    outcome = OUTCOME_COMPLETED

    def _skip_rest(status: str, detail: str) -> None:
        for aid in selected:
            if any(r.get("action_id") == aid for r in receipts):
                continue
            binding = bindings.get(aid) or {}
            receipts.append(bounded_action_receipt(
                action_id=aid,
                action=str(binding.get("action") or ""),
                requirement_id=str(binding.get("requirement_id") or ""),
                status=status, detail=detail[:200]))

    try:
        for idx, aid in enumerate(selected):
            handle.checkpoint()
            binding = bindings.get(aid)
            if binding is None:
                continue
            await handle.progress(
                int(idx * 100 / max(len(selected), 1)),
                f"action {binding.get('action')}")

            # WP07 前置复核：goal 被替换 → 后续动作全部 stale（R12/R27）。
            try:
                fingerprint_now = await _goal_fingerprint_now(session_id)
            except Exception:  # noqa: BLE001 — 读失败按未漂移处理（CAS 兜底）
                fingerprint_now = str(
                    prerequisites.get("goal_fingerprint") or "")
            if fingerprint_now != str(
                    prerequisites.get("goal_fingerprint") or ""):
                outcome = OUTCOME_SUPERSEDED
                _skip_rest(
                    ACTION_STALE,
                    "goal superseded by a newer task — attempt closed "
                    "without executing remaining actions")
                break

            disposition = str(binding.get("disposition") or "")
            if disposition == DISPOSITION_NEEDS_ACK:
                # 损失确认义务（动作时复检词表；缺确认 = 零提交，R09）。
                receipt = await execute_delivery(
                    ctx, binding,
                    attempt_id=str(handle.job_id),
                    acked_losses=acked_losses,
                    filename_hint=str(
                        (chapter.get("map_product") or {}).get(
                            "goal_satisfaction", {}).get("summary")
                        or "delivery")[:60],
                    check_cancel=handle.checkpoint)
                receipts.append(receipt)
                await _charge_if_executed(ctx, binding, receipt)
                continue
            if disposition in (DISPOSITION_NEEDS_USER,
                               DISPOSITION_UNSUPPORTED,
                               DISPOSITION_BLOCKED):
                status = {
                    DISPOSITION_NEEDS_USER: ACTION_NEEDS_USER,
                    DISPOSITION_UNSUPPORTED: ACTION_UNSUPPORTED,
                    DISPOSITION_BLOCKED: ACTION_BLOCKED,
                }[disposition]
                receipts.append(bounded_action_receipt(
                    action_id=aid,
                    action=str(binding.get("action") or ""),
                    requirement_id=str(binding.get("requirement_id") or ""),
                    status=status,
                    detail=str(binding.get("reason") or "")[:200]))
                continue
            if disposition != DISPOSITION_EXECUTABLE:
                receipts.append(bounded_action_receipt(
                    action_id=aid,
                    action=str(binding.get("action") or ""),
                    requirement_id=str(binding.get("requirement_id") or ""),
                    status=ACTION_UNSUPPORTED,
                    detail=f"unknown disposition {disposition}"))
                continue

            # WP08 预算 admission（副作用前；repair_cartography 内部自理）。
            action_name = str(binding.get("action") or "")
            if action_name != "repair_cartography":
                budget_class = str(binding.get("budget_class") or "repair")
                try:
                    from app.services.gis_harness.loop_budget import (
                        loop_retry_admissible,
                    )

                    admissible, why = loop_retry_admissible(
                        session_id, budget_class)
                except Exception:  # noqa: BLE001 — 预算面失败不阻断执行
                    admissible, why = True, ""
                if not admissible:
                    receipts.append(bounded_action_receipt(
                        action_id=aid, action=action_name,
                        requirement_id=str(
                            binding.get("requirement_id") or ""),
                        status=ACTION_BLOCKED,
                        detail=f"budget denied ({budget_class}): "
                               f"{str(why)[:96]}",
                        error_code="BUDGET_DENIED"))
                    continue

            entry = str(binding.get("entry") or "")
            try:
                if entry == ENTRY_MAP_EXPORT:
                    receipt = await execute_map_export(
                        ctx, binding, attempt_id=str(handle.job_id))
                elif entry == ENTRY_DATA_DELIVERY:
                    receipt = await execute_delivery(
                        ctx, binding, attempt_id=str(handle.job_id),
                        acked_losses=acked_losses,
                        filename_hint=str(
                            (chapter.get("map_product") or {}).get(
                                "goal_satisfaction", {}).get("summary")
                            or "delivery")[:60],
                        check_cancel=handle.checkpoint)
                elif entry == ENTRY_RUNTIME_REPAIR:
                    receipt = await execute_runtime_repair(
                        ctx, binding, chapter=chapter)
                elif entry == ENTRY_REOBSERVE:
                    receipt = await wait_fresh_observation(
                        ctx, binding,
                        start_seq=prerequisites.get("observation_seq"),
                        revision=int(prerequisites.get("mapspec_revision")
                                     or 0),
                        check_cancel=handle.checkpoint)
                elif entry == ENTRY_REREAD:
                    receipt = await execute_state_reread(ctx, binding)
                elif entry == ENTRY_REQUALIFY:
                    receipt = await execute_requalify(
                        ctx, binding, chapter)
                else:
                    receipt = bounded_action_receipt(
                        action_id=aid, action=action_name,
                        requirement_id=str(
                            binding.get("requirement_id") or ""),
                        status=ACTION_UNSUPPORTED,
                        detail=f"no executor for entry {entry}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 单动作失败不级联
                logger.warning("[goal-repair] action %s failed", aid,
                               exc_info=True)
                receipt = bounded_action_receipt(
                    action_id=aid, action=action_name,
                    requirement_id=str(binding.get("requirement_id") or ""),
                    status=ACTION_FAILED,
                    detail=f"{type(exc).__name__}: {exc}"[:200],
                    error_code="ACTION_EXECUTION_ERROR")
            receipts.append(receipt)
            await _charge_if_executed(ctx, binding, receipt)
    except (asyncio.CancelledError, _OperationCancelled):
        outcome = OUTCOME_CANCELLED
        for aid in selected:
            if any(r.get("action_id") == aid for r in receipts):
                continue
            binding = bindings.get(aid) or {}
            receipts.append(bounded_action_receipt(
                action_id=aid,
                action=str(binding.get("action") or ""),
                requirement_id=str(binding.get("requirement_id") or ""),
                status=ACTION_SKIPPED_CANCELLED,
                detail="cancelled before dispatch (no side effect "
                       "submitted)"))
        # 取消时保留已积累回执（R24/R28：已产生结果如实可回看）。
        store.write_summary_patch(int(handle.job_id), {
            "actions": receipts, "outcome": outcome,
            "final_verdict_before": verdict_before,
            "blockers": [], "evidence_signature": "",
            "cancelled_partial": True,
        })
        raise

    # WP11 原终验对账：goal 未被替换时走既有 finalizer（单一真相源）。
    verdict_after = verdict_before
    goal_id_after = ""
    chapter_after: Dict[str, Any] = chapter
    stored_after: Dict[str, Any] = {}
    try:
        fingerprint_now = await _goal_fingerprint_now(session_id)
    except Exception:  # noqa: BLE001
        fingerprint_now = str(prerequisites.get("goal_fingerprint") or "")
    if fingerprint_now != str(prerequisites.get("goal_fingerprint") or ""):
        outcome = OUTCOME_SUPERSEDED
    else:
        try:
            from app.services.gis_harness.map_completion import (
                maybe_finalize_map_product,
            )

            await maybe_finalize_map_product(
                session_id, reason="goal_repair_attempt", final_gate=True)
        except Exception:  # noqa: BLE001 — 终验失败留给下一触发点
            logger.warning("[goal-repair] re-finalize failed job=%s",
                           handle.job_id, exc_info=True)
        try:
            plan_after = await load_session_plan(session_id)
            chapter_after = plan_after.gis_chapter if plan_after and \
                isinstance(plan_after.gis_chapter, dict) else {}
            stored_after = chapter_after.get("map_product") \
                if isinstance(chapter_after.get("map_product"), dict) else {}
            goal_after = stored_after.get("goal_satisfaction") \
                if isinstance(stored_after.get("goal_satisfaction"),
                              dict) else {}
            verdict_after = str(goal_after.get("verdict") or "")[:24]
            goal_id_after = str(
                (stored_after.get("goal_repair_plan") or {}).get("goal_id")
                or "")[:128]
        except Exception:  # noqa: BLE001
            pass

    # A22 no_progress：无任何动作成功 + 终验无变化 + 证据签名未变。
    any_success = any(r.get("status") == ACTION_SUCCEEDED
                      for r in receipts)
    from .service import _evidence_signature  # 局部导入避免环
    try:
        from app.services.gis_harness.render_observation import (
            load_render_observation,
            observation_sequence,
        )
        from app.services.session_data import session_data_manager

        map_state = await session_data_manager.get_map_state(session_id)
        revision_after = 0
        if isinstance(map_state, dict):
            try:
                revision_after = int(
                    map_state.get("_cartographic_mutation_revision") or 0)
            except (TypeError, ValueError):
                revision_after = 0
        obs_after = await load_render_observation(session_id, map_state)
        sig_after = _evidence_signature(
            chapter_after, stored_after, revision_after,
            observation_sequence(obs_after))
    except Exception:  # noqa: BLE001
        sig_after = str(prerequisites.get("evidence_signature") or "")
    if (outcome == OUTCOME_COMPLETED and not any_success
            and verdict_after == verdict_before
            and sig_after == str(prerequisites.get("evidence_signature")
                                 or "")):
        outcome = OUTCOME_NO_PROGRESS

    return {
        "actions": receipts[:8],
        "outcome": outcome,
        "final_verdict_before": verdict_before,
        "final_verdict_after": verdict_after or None,
        "final_changed": bool(verdict_after and verdict_after != verdict_before),
        "goal_id_after": goal_id_after,
        "evidence_signature": sig_after,
        "blockers": [],
    }


async def _charge_if_executed(
    ctx: RepairOwnerContext, binding: Dict[str, Any], receipt: Dict[str, Any],
) -> None:
    """执行后计费（被拒/未执行不计；repair_cartography 内部自理不双扣）。"""
    status = str(receipt.get("status") or "")
    if status not in (ACTION_SUCCEEDED, ACTION_FAILED):
        return
    if str(binding.get("action") or "") == "repair_cartography":
        return
    budget_class = str(binding.get("budget_class") or "repair")
    try:
        from app.services.gis_harness.loop_budget import loop_charge

        loop_charge(ctx.session_id, budget_class)
    except Exception:  # noqa: BLE001 — 计费失败不阻断（loop_budget 内部兜底）
        logger.debug("[goal-repair] budget charge failed", exc_info=True)


__all__ = ["run_attempt_job", "register_runner_task", "runner_alive"]
