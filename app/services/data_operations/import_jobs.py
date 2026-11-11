"""G23 — 导入提交 → durable job 接线（data_import 域绑定）。

把 ``DataImportService.start_commit`` 的进程内执行改为以 ``analysis_tasks``
行（ADR-0052）为在飞证据的持久任务，语义对齐 G18 数据交付：

  * 幂等键 = (owner 指纹, batch, plan_version, 选中成员 plan_hashes 指纹) ——
    覆盖 owner scope / operation / plan version / source revision（源 sha256
    已铸入 plan_hash）/ 参数指纹；重复提交返回同一 job（A01），计划变更
    得到新键不复用旧 job（A02）。
  * 执行体 = 本进程 asyncio runner（认领 pending|queued→running 原子迁移；
    看门狗轮询 DB 取消 + 心跳；终态带 worker_id+attempt fencing）。
  * 批次状态文件（batch.json）仍是**成员收据**事实源；job 行承载批次级
    状态/进度/成员清单投影。commit 后 receipt 前崩溃 → 幂等键查询既有
    job + 成员收据对账补齐，不盲目重做（A11）。
  * 恢复判据：``_load_owned`` 的「复位残留 COMMITTING」只在**没有活跃
    durable job** 时发生（A03：另一副本的活任务不再被误判）。

不在本模块（保持 domain 分层）：staging/解释/摄入本体 —— 全部复用
data_import 既有实现，预览与提交同解释器同快照（A14 由既有结构保持）。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.data_import import staging
from app.services.data_import.models import (
    BatchStatus,
    ImportBatchState,
    MemberStatus,
    utc_now_iso,
)
from app.services.data_operations.runner import (
    AlreadyFinished,
    find_active_jobs,
    run_async_job,
    submit_async_job,
)

logger = logging.getLogger(__name__)

TASK_TYPE = "data_import_commit"

#: job result_summary 里成员清单的硬上限（有界投影，防 JSON 撑爆）
MAX_JOB_MEMBER_PROJECTION = 50


@dataclass
class JobActor:
    """提交者身份（路由/Pi 上下文构造；job 归属与幂等域的 owner scope）。"""

    user_id: Optional[str] = None
    owner_token: Optional[str] = None
    org_id: Optional[int] = None

    @property
    def scope(self) -> str:
        return self.user_id or self.owner_token or "anon"


def build_idempotency_key(
    actor: JobActor,
    batch_id: str,
    plan_version: int,
    plan_hashes: Dict[str, str],
    receipt_state: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """稳定幂等键：owner scope + 批次 + 计划版本 + 目标成员（计划指纹+收据态）。

    带密钥的签名 URL / 临时上传凭证不进键（不是稳定 identity）。

    收据态进键的原因：job 幂等复用只对「同一逻辑提交」合法；而域事实源
    是收据 —— 收据态变化（PARTIAL 失败成员重提、产出 ref 失效/被篡改后的
    重跑）意味着**新的执行意图**，必须能开新 job，而不是被上一个
    completed job 的 TTL 复用窗口挡住。并发重复提交时收据态相同 → 同键
    → 单 job（A01）；计划版本/成员计划变化 → 键变化（A02）。
    """
    receipt_state = receipt_state or {}
    parts = []
    for k in sorted(plan_hashes):
        r = receipt_state.get(k) or {}
        parts.append(
            f"{k}:{plan_hashes[k]}:{r.get('status') or '-'}:{r.get('output_ref') or '-'}"
        )
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return f"{TASK_TYPE}:{actor.scope}:{batch_id}:v{plan_version}:{digest}"


async def pending_members(state, targets) -> List[str]:
    """按「当前计划 hash 无 completed 收据**且产出 ref 可读**」筛出要执行的成员。

    收据事实源在 batch.json；job 化不改变这一归属。可读性复核与
    commit_member 的幂等复用同一判据（ref 字符串不算就绪 —— 伪造/失效
    ref 视为待重跑，故障注入反验 3 保持）。
    """
    from app.services.data_import.commit import _verify_output_readable

    out = []
    for m in targets:
        receipt = state.receipts.get(m.member_key)
        current_hash = state.plan_hashes.get(m.member_key, "")
        if (
            receipt is not None
            and receipt.status == "completed"
            and receipt.plan_hash == current_hash
            and receipt.output_ref
        ):
            try:
                if await _verify_output_readable(receipt.output_ref, state.session_id):
                    continue
            except Exception:  # noqa: BLE001 — 不可读 → 待重跑
                pass
        out.append(m.member_key)
    return out


def find_active_import_job(
    session_id: str, batch_id: str
) -> Optional[Dict[str, Any]]:
    """该批次当前是否有活跃（非终态）durable job。恢复判据的核心查询。"""
    hits = find_active_jobs(
        task_type=TASK_TYPE,
        session_id=session_id,
        parameter_match={"batch_id": batch_id},
    )
    if not hits:
        return None
    job_id, status = hits[0]
    return {"job_id": job_id, "status": status}


async def submit_import_commit(
    service: Any,
    batch_id: str,
    session_id: Optional[str],
    member_keys: Optional[List[str]],
    expected_plan_version: Optional[int],
    *,
    actor: Optional[JobActor] = None,
    background: bool = True,
) -> Dict[str, Any]:
    """durable 版 start_commit：幂等建 job → 置 COMMITTING → 启动 runner。

    与旧 ``start_commit`` 保持同一校验顺序（在 service 锁内），返回值增加
    ``job_id``；``background=False`` 时内联等待完成（小 fixture 同步收据，
    内部同样走 durable job）。
    """
    actor = actor or JobActor()
    async with service._lock(batch_id):
        state = await service._load_owned(batch_id, session_id)
        if state.status == BatchStatus.COMMITTING:
            existing = service._commit_tasks.get(batch_id)
            if existing is not None and not existing.done():
                raise service._conflict(batch_id)
            # 进程内无在飞任务但状态仍是 COMMITTING：要么是另一副本的活跃
            # job（_load_owned 已据 durable 事实保留状态），要么是崩溃残留
            # （_load_owned 已归一化）。归一化后状态不会是 COMMITTING，
            # 所以到这里仍 COMMITTING ⇒ 必有活跃 job ⇒ 拒绝重复提交。
            raise service._conflict(batch_id)
        if expected_plan_version is not None and state.plan_version != expected_plan_version:
            from app.services.data_import.service import DataImportError

            raise DataImportError(
                "PLAN_VERSION_CONFLICT",
                f"期望 {expected_plan_version} 实际 {state.plan_version}",
                status=409,
            )
        targets = [
            m for m in state.members
            if m.selected or (member_keys and m.member_key in set(member_keys))
        ]
        if member_keys is not None:
            known = {m.member_key for m in state.members}
            missing = [k for k in member_keys if k not in known]
            if missing:
                from app.services.data_import.service import DataImportError

                raise DataImportError(
                    "UNKNOWN_MEMBER", ",".join(missing[:5]), status=404
                )
        targets = [m for m in targets if m.unsupported_reason is None]
        if not targets:
            from app.services.data_import.service import DataImportError

            raise DataImportError("NO_SELECTED_MEMBERS", "未选择任何可导入成员")
        unplanned = [m.member_key for m in targets if m.member_key not in state.plan_hashes]
        if unplanned:
            from app.services.data_import.service import DataImportError

            raise DataImportError(
                "PLAN_REQUIRED", f"成员缺少解释计划: {', '.join(unplanned[:5])}"
            )

        target_keys = [m.member_key for m in targets]
        pending = await pending_members(state, targets)
        # 完成短路（域级幂等，收据是事实源）：全部目标已有可读的同计划
        # completed 收据 → 不建 job、不重跑，返回既有收据摘要并按 BASE
        # 语义标注 duplicate（重复提交不产生新副作用）。
        if not pending:
            from app.services.data_import.staging import batch_summary

            for k in target_keys:
                receipt = state.receipts.get(k)
                if receipt is not None and receipt.status == "completed":
                    receipt.duplicate = True
            await asyncio.to_thread(staging.save_state, state)
            summary = batch_summary(state)
            summary["receipts"] = {
                k: state.receipts[k].model_dump()
                for k in target_keys if k in state.receipts
            }
            summary["idempotent_reuse"] = True
            summary["message"] = "全部目标成员已有同计划完成收据（幂等短路）"
            return summary

        key = build_idempotency_key(
            actor, batch_id, state.plan_version,
            {k: state.plan_hashes.get(k, "") for k in target_keys},
            receipt_state={
                k: {
                    "status": state.receipts[k].status,
                    "output_ref": state.receipts[k].output_ref,
                }
                for k in target_keys if k in state.receipts
            },
        )
        params = {
            "batch_id": batch_id,
            "plan_version": state.plan_version,
            "member_keys": pending[:MAX_JOB_MEMBER_PROJECTION],
            "member_count": len(pending),
            "total_selected": len(target_keys),
        }
        job_id, job_status, reused = submit_async_job(
            task_type=TASK_TYPE,
            kind="data_import",
            display_name=f"数据导入 · {batch_id[:12]} · {len(pending)}/{len(target_keys)} 成员",
            params=params,
            idempotency_key=key,
            owner_id=actor.user_id,
            owner_token=actor.owner_token,
            org_id=actor.org_id,
            session_id=state.session_id,
        )
        # 落批内状态：COMMITTING + active_job_id（跨进程在飞证据）。
        # 执行目标仍是全量 targets；BASE 语义 = 全部 targets 置 COMMITTING，
        # 已完成成员在循环里经 commit_member 幂等复核（可读性 + duplicate
        # 标注），而不是在提交层跳过 —— 伪造/失效 ref 因此被诚实重跑。
        state.status = BatchStatus.COMMITTING
        state.cancel_requested = False
        state.committing_since = utc_now_iso()
        state.last_commit_started_at = state.committing_since
        state.active_job_id = job_id
        for m in targets:
            m.status = MemberStatus.COMMITTING
        service._committing_ids.add(batch_id)
        try:
            await asyncio.to_thread(staging.save_state, state)
        except OSError as exc:
            # A19：staging 写失败（如磁盘满）→ typed 拒绝 + job 落 failed，
            # 不留半启动状态、不裸抛 OSError
            service._committing_ids.discard(batch_id)
            from app.services.data_operations.runner import _sync_session
            from app.services.jobs.store import DurableJobStore

            with _sync_session() as db:
                DurableJobStore.mark_failed_sync(
                    db, job_id, error=exc, message="STAGING_WRITE_FAILED")
                db.commit()
            from app.services.data_import.service import DataImportError

            raise DataImportError(
                "STAGING_WRITE_FAILED",
                f"staging 状态写入失败（{exc}）；提交未启动", status=507,
            ) from exc

    cancel_event = asyncio.Event()
    service._cancel_events[batch_id] = cancel_event

    if reused:
        # 幂等命中活跃/近期 job：执行体在别处（或已完成），不再启动 runner。
        service._committing_ids.discard(batch_id)
        service._cancel_events.pop(batch_id, None)
        return {
            "batch_id": batch_id,
            "job_id": str(job_id),
            "status": "committing" if job_status in ("pending", "queued", "running") else job_status,
            "members": target_keys,
            "idempotent_reuse": True,
            "poll": f"/api/v1/data-import/batches/{batch_id}",
        }

    runner = _make_runner(service, batch_id, session_id, target_keys, cancel_event, job_id)
    if not background:
        try:
            return await runner()
        finally:
            service._committing_ids.discard(batch_id)
            service._cancel_events.pop(batch_id, None)

    existing_task = service._commit_tasks.get(batch_id)
    if existing_task is not None and not existing_task.done():
        service._committing_ids.discard(batch_id)
        service._cancel_events.pop(batch_id, None)
        raise service._conflict(batch_id)
    service._commit_tasks[batch_id] = asyncio.create_task(
        runner(), name=f"data-import-commit:{job_id}"
    )
    return {
        "batch_id": batch_id,
        "job_id": str(job_id),
        "status": "committing",
        "members": target_keys,
        "idempotent_reuse": False,
        "poll": f"/api/v1/data-import/batches/{batch_id}",
    }


def _make_runner(
    service: Any,
    batch_id: str,
    session_id: Optional[str],
    member_keys: List[str],
    cancel_event: asyncio.Event,
    job_id: int,
):
    """构造执行体闭包：逐成员提交（复用 service 既有循环语义）+ job 进度。"""

    async def _body(handle) -> Dict[str, Any]:
        return await service._run_commit_members(
            batch_id, session_id, member_keys, cancel_event, handle
        )

    async def _runner() -> Dict[str, Any]:
        cancelled = False
        try:
            await run_async_job(
                job_id,
                _body,
                session_cancel_event=cancel_event,
                result_ref_builder=lambda r: (
                    f"/api/v1/data-import/batches/{batch_id}/receipts"
                ),
            )
        except AlreadyFinished:
            # 认领失败（并发副本先认领）：本进程不留任何在飞标记
            pass
        except Exception:  # noqa: BLE001 — run_async_job 已落终态；runner 外层不重复报错
            cancelled = True
        finally:
            service._commit_tasks.pop(batch_id, None)
            service._cancel_events.pop(batch_id, None)
            service._committing_ids.discard(batch_id)
        # 同步路径需要摘要；后台路径的摘要以 job result_summary 为准
        try:
            fresh = await service._load_owned(batch_id, session_id)
            from app.services.data_import.staging import batch_summary

            summary = batch_summary(fresh)
            summary["job_id"] = str(job_id)
            summary["receipts"] = {
                k: fresh.receipts[k].model_dump()
                for k in member_keys if k in fresh.receipts
            }
            return summary
        except Exception:  # noqa: BLE001 — 摘要失败不影响已落终态
            return {"batch_id": batch_id, "job_id": str(job_id)}

    return _runner


def member_status_projection(state: ImportBatchState) -> List[Dict[str, Any]]:
    """批次成员 → job result_summary 的有界投影（≤50，单行 error）。

    与 atlas_export 的 pages 投影同一安全约定：只保留展示字段，绝不带
    拒绝行原文/坐标/载荷。
    """
    out: List[Dict[str, Any]] = []
    for m in state.members[:MAX_JOB_MEMBER_PROJECTION]:
        receipt = state.receipts.get(m.member_key)
        err = None
        if receipt is not None and receipt.error_message:
            err = str(receipt.error_message).splitlines()[0][:160]
        out.append({
            "member_key": m.member_key[:64],
            "display_name": (m.display_name or "")[:96],
            "status": (receipt.status if receipt is not None else m.status.value)[:24],
            "output_ref": (receipt.output_ref if receipt is not None else None),
            "input_rows": (receipt.input_rows if receipt is not None else None),
            "rejected_rows": (receipt.rejected_rows if receipt is not None else None),
            "error": err,
            "plan_hash": (receipt.plan_hash if receipt is not None else state.plan_hashes.get(m.member_key, "")) or "",
        })
    return out


def batch_outcome_summary(state: ImportBatchState, member_keys: List[str]) -> Dict[str, Any]:
    """job 终态 result_summary（有界：批次状态 + 计数 + 成员投影 + retained 披露）。"""
    receipts = {k: state.receipts[k] for k in member_keys if k in state.receipts}
    completed = [k for k, r in receipts.items() if r.status == "completed"]
    failed = [k for k, r in receipts.items() if r.status == "failed"]
    cancelled = [k for k, r in receipts.items() if r.status == "cancelled"]
    # 已发布产物在取消/失败后不可回滚 —— 明示 retained/residual（任务书 P4）
    retained_refs = [
        r.output_ref for r in receipts.values()
        if r.status == "completed" and r.output_ref
    ]
    return {
        "batch_id": state.batch_id,
        "batch_status": state.status.value,
        "plan_version": state.plan_version,
        "member_count": len(member_keys),
        "completed": len(completed),
        "failed": len(failed),
        "cancelled": len(cancelled),
        "members": member_status_projection(state),
        "retained_refs": retained_refs[:MAX_JOB_MEMBER_PROJECTION],
        "retained_note": (
            "取消/部分失败前已发布的成员产物不可自动回滚，已保留并列于 "
            "retained_refs；源文件未被修改。"
            if state.status in (BatchStatus.CANCELLED, BatchStatus.PARTIAL, BatchStatus.FAILED)
            and retained_refs
            else ""
        ),
    }
