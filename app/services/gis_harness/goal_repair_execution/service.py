"""G42 服务编排 —— 预览 / 启动 / 状态 / 取消 / 惰性对账。

生命周期（与 G30 skill job 同族，不另建执行权威）：

- **预览零副作用**：从存储块重建绑定矩阵；交付义务经 G18 只读预览披露；
- **start 零等待**：幂等建 attempt 行后立即返回；执行体是独立 asyncio
  任务（不嵌套持 wave 槽 —— R37/R38）；UI 入口在会话有活跃 Pi turn 时
  返回 busy（不自等待同一 writer/gate）；
- **重终验**：动作收口后经 ``maybe_finalize_map_product(final_gate=True)``
  走原 evaluator（单一真相源；attempt 只记录前后 verdict）；
- **惰性对账**：waiting_delivery / runner 丢失（进程重启）在状态读路径
  上对账 —— 可确认幂等证据则收口，否则 outcome_unknown（不盲重放）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from . import bindings as bindings_mod
from . import store
from .actions import (
    RepairOwnerContext,
    preview_delivery_for_binding,
)
from .bindings import ENTRY_DATA_DELIVERY
from .contracts import (
    ACTION_FAILED,
    ACTION_SUCCEEDED,
    ACTION_WAITING_DELIVERY,
    DISPOSITION_BLOCKED,
    ENTRY_UI,
    ERROR_BUDGET_DENIED,
    ERROR_GOAL_SATISFIED,
    ERROR_NO_PROGRESS,
    ERROR_PLAN_NOT_FOUND,
    ERROR_SESSION_BUSY,
    GoalRepairErrorCode,
    MAX_ACK_CODES,
    OUTCOME_OUTCOME_UNKNOWN,
    PREVIEW_SCHEMA,
)
from .runner import run_attempt_job

logger = logging.getLogger(__name__)

#: runner 丢失判定的心跳宽限（状态读路径对账用）。必须覆盖单 attempt 的
#: 最坏合法时长（6 动作 × 120s 交付等待 + reobserve 45s + 终验）—— 心跳
#: （AsyncJobHandle 看门狗）持续刷新时永不应触发（review P2）。
RUNNER_LOST_GRACE_S = 900.0


# ── 状态快照（预览/启动共用的权威读取）──────────────────────────────────

async def _load_state(
    session_id: str,
) -> Tuple[Any, Dict[str, Any], Dict[str, Any], int, int]:
    """(plan, chapter, stored_block, mapspec_revision, observation_seq)。"""
    from app.services.gis_harness.render_observation import (
        load_render_observation,
        observation_sequence,
    )
    from app.services.session_data import session_data_manager
    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(session_id)
    chapter = plan.gis_chapter if plan and isinstance(
        plan.gis_chapter, dict) else {}
    stored = chapter.get("map_product") if isinstance(
        chapter.get("map_product"), dict) else {}
    try:
        map_state = await session_data_manager.get_map_state(session_id)
    except Exception:  # noqa: BLE001 — 读失败按 0 处理（前置条件将拒绝）
        map_state = None
    revision = 0
    if isinstance(map_state, dict):
        try:
            revision = int(
                map_state.get("_cartographic_mutation_revision") or 0)
        except (TypeError, ValueError):
            revision = 0
    obs = await load_render_observation(session_id, map_state)
    return plan, chapter, stored, revision, observation_sequence(obs)


def _goal_fingerprint(plan: Any, chapter: Dict[str, Any]) -> str:
    from app.services.session_plan import goal_key

    raw = goal_key(chapter, getattr(plan, "user_goal", "") or "")
    return hashlib.sha1(str(raw).encode("utf-8")).hexdigest()[:16]


def _evidence_signature(
    chapter: Dict[str, Any], stored: Dict[str, Any], revision: int,
    observation_seq: int,
) -> str:
    """领域证据签名（A22：同证据重复执行 = 无进展）。"""
    receipts = chapter.get("export_receipts") \
        if isinstance(chapter.get("export_receipts"), list) else []
    receipt_ids = [
        f"{str(r.get('format') or '')[:16]}:{str(r.get('revision') or '')[:12]}"
        for r in receipts[:8] if isinstance(r, dict)
    ]
    blob = json.dumps({
        "revision": revision,
        "observation_seq": observation_seq,
        "export_receipts": receipt_ids,
        "rows_fingerprint": str(stored.get("rows_fingerprint") or "")[:64],
        "product_fp": str(stored.get("product_state_fingerprint")
                          or "")[:64],
    }, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


# ── 预览（零副作用）──────────────────────────────────────────────────────

async def build_repair_preview(
    session_id: str, *, ctx: Optional[RepairOwnerContext] = None,
) -> Dict[str, Any]:
    """存储块 → 版本化补全预览（不执行任何动作；R06/R07 的服务端事实源）。"""
    if not session_id:
        raise GoalRepairErrorCode(ERROR_PLAN_NOT_FOUND, "缺少 session_id")
    plan, chapter, stored, revision, obs_seq = await _load_state(session_id)
    goal_block = stored.get("goal_satisfaction") \
        if isinstance(stored.get("goal_satisfaction"), dict) else {}
    plan_block = stored.get("goal_repair_plan") \
        if isinstance(stored.get("goal_repair_plan"), dict) else None
    verdict = str(goal_block.get("verdict") or "")[:24]
    if not plan and not stored:
        raise GoalRepairErrorCode(
            ERROR_PLAN_NOT_FOUND, "会话无可终验的目标（无 map_product 块）")
    if verdict == "satisfied":
        raise GoalRepairErrorCode(
            ERROR_GOAL_SATISFIED, "目标已满足 —— 无待补全项")
    if plan_block is None:
        raise GoalRepairErrorCode(
            ERROR_PLAN_NOT_FOUND,
            "无可执行的修复计划（无注册动作或硬 blocker —— 见完成卡披露）")

    rows = bindings_mod.adjudicate_actions(
        plan_block, stored_goal=goal_block, chapter=chapter,
        mapspec_revision=revision)
    # 交付义务披露（G18 只读预览；失败 = 诚实披露，动作时仍复检）。
    for row in rows:
        if row.get("entry") != ENTRY_DATA_DELIVERY:
            continue
        params = row.get("params") or {}
        preview, err = await preview_delivery_for_binding(
            ctx or RepairOwnerContext(session_id=session_id),
            str(params.get("format") or ""),
            str(params.get("source_ref") or ""),
            filename_hint=str(goal_block.get("summary")
                              or plan_block.get("goal_id") or "delivery"),
        )
        if preview is None:
            row["ack_note"] = f"预览暂不可用：{err}"[:120]
            continue
        blocking = bindings_mod.delivery_blocking_issues(preview)
        if blocking:
            row.update(disposition=DISPOSITION_BLOCKED,
                       reason="; ".join(blocking)[:160],
                       ack_codes=[])
            continue
        row["ack_codes"] = bindings_mod.delivery_ack_codes(preview)

    active = store.find_active_attempt(
        session_id, str(plan_block.get("goal_id") or ""))
    attempts = store.find_attempts(
        session_id, str(plan_block.get("goal_id") or ""), limit=3)
    return {
        "schema_version": PREVIEW_SCHEMA,
        "session_id": session_id[:128],
        "goal_id": str(plan_block.get("goal_id") or "")[:128],
        "verdict": verdict,
        "signal": str(plan_block.get("signal") or "")[:32],
        "plan_version": bindings_mod.plan_version_for(plan_block),
        "goal_fingerprint": _goal_fingerprint(plan, chapter),
        "mapspec_revision": revision,
        "observation_seq": obs_seq,
        "evidence_signature": _evidence_signature(
            chapter, stored, revision, obs_seq),
        "actions": rows,
        "blockers": [str(b)[:64]
                     for b in (plan_block.get("blockers") or [])[:6]],
        "progress_rule": str(plan_block.get("progress_rule") or "")[:48],
        "active_attempt": active,
        "attempts": attempts,
    }


# ── 启动（幂等单 claim；零等待返回）────────────────────────────────────

async def _resolve_owner_identity(
    session_id: str, ctx: RepairOwnerContext,
) -> RepairOwnerContext:
    """会话归属身份的服务端权威派生（并入执行 ctx；客户端不可自报）。

    Pi 直调路径的 JobOrigin 不携带 owner 身份（桥接层只绑 session/turn
    关联）—— attempt 执行体（后台任务）也不继承调用方 contextvar。交付
    等需要 actor 的既有服务按 Conversation 行取会话真实属主：认证会话的
    user_id 权威可回读；匿名会话无 user_id 时保持 ctx 原值（owner_token
    由入口层路由/工具上下文带入，存储侧仅存摘要不可逆）。
    """
    try:
        from app.core.database import async_db_session
        from app.models.db_model import Conversation

        async with async_db_session() as db:
            conv = await db.get(Conversation, session_id)
        conv_user = str(getattr(conv, "user_id", "") or "") or None
    except Exception:  # noqa: BLE001 — 身份派生失败保持入口 ctx（服务复检兜底）
        logger.debug("[goal-repair] owner identity resolve failed",
                     exc_info=True)
        return ctx
    if conv_user and conv_user != ctx.user_id:
        return RepairOwnerContext(
            session_id=ctx.session_id, user_id=conv_user,
            org_id=ctx.org_id, project_id=ctx.project_id,
            owner_token=ctx.owner_token, turn_id=ctx.turn_id,
            tool_call_id=ctx.tool_call_id)
    return ctx


async def start_repair_attempt(
    session_id: str,
    *,
    selected_action_ids: Optional[List[str]] = None,
    acknowledged_losses: Optional[List[str]] = None,
    ctx: RepairOwnerContext,
    entry: str = ENTRY_UI,
    session_busy_check: bool = True,
) -> Dict[str, Any]:
    """授权一次有界补全执行；返回 attempt 视图（不等待执行完成）。"""
    ctx = await _resolve_owner_identity(session_id, ctx)
    preview = await build_repair_preview(session_id, ctx=ctx)

    # 活跃 attempt 优先（R11/R16）：双击/重发 = 同一 attempt。
    if preview.get("active_attempt"):
        view = dict(preview["active_attempt"])
        view["already_running"] = True
        return view

    # UI 入口的 busy 语义（Pi 回调入口 = 当前 turn 即合法上下文）。
    if session_busy_check:
        try:
            from app.agent_pi_bridge import get_active_turn_entry

            if get_active_turn_entry(session_id) is not None:
                raise GoalRepairErrorCode(
                    ERROR_SESSION_BUSY,
                    "会话已有活跃 agent turn —— 补全启动须等待其完成（busy）",
                    detail={"session_id": session_id})
        except GoalRepairErrorCode:
            raise
        except Exception:  # noqa: BLE001 — 活跃判定失败不阻断
            logger.debug("[goal-repair] active-turn check unavailable",
                         exc_info=True)

    # 选择校验（R07/R29）：客户端只能提交预览内 action id；参数一律服务端派生。
    rows = preview["actions"]
    by_id = {str(r.get("action_id")): r for r in rows}
    selected = [str(a) for a in (selected_action_ids or [])
                if str(a) in by_id][:8]
    if selected_action_ids and len(selected) != len(
            set(str(a) for a in selected_action_ids)):
        raise GoalRepairErrorCode(
            "INVALID_ACTION_IDS", "提交的动作选择不在当前计划内（重新预览）")
    if not selected:
        selected = bindings_mod.executable_selection(rows)
    if not selected:
        raise GoalRepairErrorCode(
            "INVALID_ACTION_IDS",
            "当前计划无可执行动作（需用户资料或不支持 —— 见预览披露）")

    # no_progress（R14/A22）：同计划 + 同证据签名 + 已有终态 attempt → 拒绝。
    sig = preview["evidence_signature"]
    for prev in preview.get("attempts") or []:
        prev_sig = ""
        summary = store.read_summary(int(prev["attempt_id"]))
        prev_sig = str(summary.get("evidence_signature") or "")
        if prev_sig and prev_sig == sig:
            raise GoalRepairErrorCode(
                ERROR_NO_PROGRESS,
                "同一修复计划在相同证据下已执行过 —— 无新证据不重复执行",
                detail={"previous_attempt": prev["attempt_id"],
                        "evidence_signature": sig})

    # 预算 admission（副作用前；repair_cartography 内部自理不预扣）。
    denied: List[str] = []
    for aid in selected:
        row = by_id[aid]
        if row.get("action") == "repair_cartography":
            continue
        budget_class = str(row.get("budget_class") or "repair")
        try:
            from app.services.gis_harness.loop_budget import (
                loop_retry_admissible,
            )

            admissible, why = loop_retry_admissible(
                session_id, budget_class)
        except Exception:  # noqa: BLE001 — 预算面失败按允许（执行时兜底）
            admissible, why = True, ""
        if not admissible:
            denied.append(f"{aid}:{budget_class}:{str(why)[:48]}")
    writable = [aid for aid in selected
                if by_id[aid].get("side_effect") == "write"]
    if writable and len(denied) >= len(writable):
        raise GoalRepairErrorCode(
            ERROR_BUDGET_DENIED,
            f"全部写入动作被预算拒绝（{'; '.join(denied[:3])}）",
            detail={"denied": denied[:6]})

    acked = [str(a)[:48] for a in (acknowledged_losses or
                                   [])[:MAX_ACK_CODES]]
    owner = {
        "session_id": session_id,
        "user_id": ctx.user_id,
        "org_id": ctx.org_id,
        "project_id": ctx.project_id,
        "owner_token": ctx.owner_token,
        "turn_id": ctx.turn_id,
        "tool_call_id": ctx.tool_call_id,
        "entry": entry,
    }
    from app.services.session_plan import load_session_plan
    plan = await load_session_plan(session_id)
    chapter = plan.gis_chapter if plan and isinstance(
        plan.gis_chapter, dict) else {}
    plan_block = chapter.get("map_product", {}).get("goal_repair_plan", {})
    prerequisites = {
        "goal_fingerprint": preview["goal_fingerprint"],
        "mapspec_revision": preview["mapspec_revision"],
        "observation_seq": preview["observation_seq"],
        "evidence_signature": sig,
        "verdict_before": preview["verdict"],
    }
    created = store.submit_attempt_row(
        session_id=session_id,
        goal_id=preview["goal_id"],
        plan_version=preview["plan_version"],
        plan=plan_block if isinstance(plan_block, dict) else {},
        bindings=rows,
        prerequisites=prerequisites,
        selected_action_ids=selected,
        acknowledged_losses=acked,
        owner=owner,
        parent_attempt_id=str(
            (preview.get("attempts") or [{}])[0].get("attempt_id") or ""),
    )
    if not created["reused"]:
        task = asyncio.create_task(
            run_attempt_job(int(created["job_id"])),
            name=f"goal-repair:{created['job_id']}",
        )
        from .runner import register_runner_task
        register_runner_task(int(created["job_id"]), task)
    view = dict(created["view"])
    view["selected_action_ids"] = selected
    view["budget_denied"] = denied[:6]
    return view


# ── 状态 / 取消 / 惰性对账 ───────────────────────────────────────────────

async def get_attempt_status(attempt_id: Any) -> Dict[str, Any]:
    """attempt 视图 + 惰性对账（waiting_delivery 收口 / runner 丢失披露）。"""
    view = store.ensure_attempt(attempt_id)
    await reconcile_attempt(view)
    return store.ensure_attempt(attempt_id)


async def reconcile_attempt(view: Dict[str, Any]) -> None:
    """读路径对账：迟到交付收口 / 崩溃 runner 的 outcome_unknown 披露。"""
    if not view.get("active"):
        await _reconcile_waiting_delivery(view)
        return
    from .runner import runner_alive
    if runner_alive(int(view["attempt_id"])):
        return
    # 活跃但本进程无 runner：宽限内可能仍在原进程；超宽限按丢失对账。
    # 心跳优先（执行体活跃期间持续刷新；跨进程判断的唯一可靠信号），
    # 缺省回退 created_at（review P2：宽限须长于最坏合法时长）。
    last_alive = view.get("heartbeat_at") or view.get("created_at")
    age_s = 0.0
    if last_alive:
        try:
            from datetime import datetime, timezone

            dt = datetime.fromisoformat(str(last_alive))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_s = (datetime.now(timezone.utc) - dt).total_seconds()
        except ValueError:
            age_s = 0.0
    if age_s < RUNNER_LOST_GRACE_S:
        return
    _mark_runner_lost(int(view["attempt_id"]))


def _mark_runner_lost(job_id: int) -> None:
    """runner 丢失（进程重启）：对账可确认幂等证据则收口，否则 unknown。"""
    import contextlib

    from sqlalchemy import select as _select

    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from app.services.jobs.lifecycle import JobStatus
    from app.services.jobs.store import DurableJobStore

    with contextlib.closing(SessionLocal()) as db:
        row = db.execute(
            _select(AnalysisTask).where(AnalysisTask.id == job_id)
        ).scalar_one_or_none()
        if row is None or row.status not in (
                JobStatus.pending.value, JobStatus.queued.value,
                JobStatus.running.value):
            return
        # 未启动（pending，无 dispatch 消费痕迹）→ 可安全终态 failed（零副作用）。
        started = bool(row.result_summary) or \
            row.status == JobStatus.running.value
        if not started:
            DurableJobStore.mark_failed_sync(
                db, job_id, error=GoalRepairErrorCode(
                    "RUNNER_LOST", "执行进程丢失（未启动，零副作用）"),
                worker_id="goal-repair-reconcile")
            db.commit()
            return
        summary = dict(row.result_summary) if isinstance(
            row.result_summary, dict) else {}
        summary.setdefault("outcome", OUTCOME_OUTCOME_UNKNOWN)
        summary.setdefault(
            "outcome_note",
            "runner 进程在副作用后丢失 —— 幂等证据按既有回执对账，不盲重放")
        # 新 dict 赋值（同对象回赋不触发 JSON 列变更检测）。
        row.result_summary = summary
        DurableJobStore.mark_failed_sync(
            db, job_id, error=GoalRepairErrorCode(
                "RUNNER_LOST", "执行进程丢失（outcome_unknown；已提交副作用"
                               "按幂等键对账）"),
            worker_id="goal-repair-reconcile")
        db.commit()


async def _conversation_user_id(session_id: str) -> Optional[str]:
    """Conversation 行的权威 user_id（匿名/读取失败 → None）。"""
    try:
        from app.core.database import async_db_session
        from app.models.db_model import Conversation

        async with async_db_session() as db:
            conv = await db.get(Conversation, session_id)
        return str(getattr(conv, "user_id", "") or "") or None
    except Exception:  # noqa: BLE001
        return None


async def _reconcile_waiting_delivery(view: Dict[str, Any]) -> None:
    """已终态 attempt 的 waiting_delivery 动作 → 交付完成时收口 + 重终验。

    只对幂等可确认的事实收口（delivery job 终态）；对账后触发一次合法
    重终验（finalizer 幂等门去重）。
    """
    summary = store.read_summary(int(view["attempt_id"]))
    actions = list(summary.get("actions") or [])
    changed = False
    for act in actions:
        if not isinstance(act, dict):
            continue
        if act.get("status") != ACTION_WAITING_DELIVERY:
            continue
        job_id = str(act.get("job_id") or "")
        if not job_id:
            continue
        try:
            from app.services.data_delivery import get_delivery_job
            from app.services.data_delivery.source_resolver import (
                DeliveryActor,
            )

            # actor 身份按 Conversation 行权威派生（review P1：dispatch_spec
            # 不落原始 owner_token；与 _resolve_owner_identity 同源）。匿名
            # 会话无 user_id → 授权不放宽，维持等待披露。
            spec_owner = store.read_dispatch_spec(
                int(view["attempt_id"])) or {}
            owner = spec_owner.get("owner") or {}
            user_id = owner.get("user_id") or None
            if not user_id:
                conv_user = await _conversation_user_id(
                    str(view.get("session_id") or ""))
                user_id = conv_user
            if not user_id:
                continue  # 匿名交付回执按既有授权面不可读 —— 诚实维持等待
            actor = DeliveryActor(user_id=user_id)
            job_view = await get_delivery_job(job_id, actor)
        except Exception:  # noqa: BLE001 — 无权/不存在 → 维持等待披露
            continue
        status = str(getattr(job_view, "status", "") or "")[:16]
        if status == "completed":
            act["status"] = ACTION_SUCCEEDED
            act["detail"] = f"delivery completed (reconciled): {act.get('detail', '')}"[:200]
            changed = True
            # review P2：迟到成功照常计费（runner 只对等待内终态计费）。
            try:
                from app.services.gis_harness.loop_budget import loop_charge

                loop_charge(str(view.get("session_id") or ""), "repair")
            except Exception:  # noqa: BLE001 — 计费失败不阻断收口
                logger.debug("[goal-repair] reconcile charge failed",
                             exc_info=True)
        elif status in ("failed", "cancelled"):
            act["status"] = ACTION_FAILED
            act["error_code"] = f"DELIVERY_{status.upper()}"
            act["detail"] = f"delivery {status} (reconciled)"
            changed = True
    if not changed:
        return
    summary["actions"] = actions
    store.write_summary_patch(int(view["attempt_id"]), summary)
    # 迟到回执进入下一次合法终验（A20；finalizer 幂等门去重）。
    try:
        from app.services.gis_harness.map_completion import (
            maybe_finalize_map_product,
        )

        await maybe_finalize_map_product(
            str(view.get("session_id") or ""),
            reason="goal_repair_reconcile", final_gate=True)
    except Exception:  # noqa: BLE001 — 终验失败留给下一触发点
        logger.debug("[goal-repair] reconcile finalize failed", exc_info=True)


async def cancel_repair_attempt(
    attempt_id: Any, *, ctx: RepairOwnerContext,
) -> Dict[str, Any]:
    """两阶段取消（DB cancel_requested_at + 进程 token；幂等）。"""
    view = store.ensure_attempt(attempt_id)
    if not view.get("active"):
        return {**view, "cancel_requested": False,
                "note": "attempt 已终态 —— 取消是 no-op"}
    from app.core.database import SessionLocal
    from app.services.jobs import registry as cancellation_registry
    from app.services.jobs.store import DurableJobStore
    import contextlib

    job_id = int(view["attempt_id"])
    with contextlib.closing(SessionLocal()) as db:
        changed, current = DurableJobStore.request_cancel_sync(db, job_id)
        db.commit()
    cancellation_registry.cancel(str(job_id), "cancelled by user")
    from .runner import runner_alive
    if not runner_alive(job_id):
        # 进程内无执行体（跨进程/已丢失）→ 直接确认终态（DB 探针语义）。
        with contextlib.closing(SessionLocal()) as db:
            DurableJobStore.confirm_cancelled_sync(
                db, job_id, message="cancelled by user (no live runner)",
                worker_id="goal-repair-cancel")
            db.commit()
    refreshed = store.ensure_attempt(attempt_id)
    return {**refreshed, "cancel_requested": True}


__all__ = [
    "build_repair_preview", "start_repair_attempt", "get_attempt_status",
    "cancel_repair_attempt", "reconcile_attempt",
]
