"""G23 — 字段计算执行 → durable job 接线（field_compute 域绑定）。

G19 的执行语义保持不变（同解释器同快照、收据幂等、发布闸门、回滚）；
本模块只把**执行活性/取消/进度**搬进 analysis_tasks 事实源：

  * 幂等键 = (session, plan_id, compute_fingerprint) —— fingerprint 已覆盖
    列/表达式/范围/单位/IR 版本；输入内容版本由执行期 anchor 复核保证
    （排队中源变化 → typed 失败，不静默算新数据，A10/A14）。
  * 收据短路优先于 job 化：既有 completed 收据 → 直接返回（BASE 行为，
    无副作用、不建 job 行）。
  * 并发执行收敛：同键活跃 job 存在（另一副本在跑同一计划）→ 复用该
    job 并等待其终态后取收据 —— 与 BASE 的进程内 fingerprint 锁同语义，
    但跨副本成立（认领即互斥）。
  * 取消：进程内登记照旧；新增 durable 桥 —— 任意副本取消经 DB 事实 +
    看门狗点燃本进程 token（`_check_cancel` 的 current_token 分支）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from app.services.data_operations.runner import (
    AlreadyFinished,
    find_active_jobs,
    run_async_job,
    submit_async_job,
)

logger = logging.getLogger(__name__)

TASK_TYPE = "field_compute_apply"

#: 等待「另一副本正在执行的同一计划」的收敛上限（与 pytest timeout 量级对齐）
JOIN_ACTIVE_JOB_TIMEOUT_S = 600.0


@dataclass
class ComputeJobActor:
    """提交者身份（job 归属；不参与数据隔离 —— 隔离由 session 归属保证）。"""

    user_id: Optional[str] = None
    owner_token: Optional[str] = None
    org_id: Optional[int] = None


def build_idempotency_key(
    session_id: str, plan_id: str, compute_fingerprint: str
) -> str:
    return f"{TASK_TYPE}:{session_id}:{plan_id}:{compute_fingerprint}"


def find_active_compute_job(
    session_id: str, plan_id: str
) -> Optional[Dict[str, Any]]:
    """该计划当前是否有活跃 durable job。"""
    hits = find_active_jobs(
        task_type=TASK_TYPE,
        session_id=session_id,
        parameter_match={"plan_id": plan_id},
    )
    if not hits:
        return None
    job_id, status = hits[0]
    return {"job_id": job_id, "status": status}


async def submit_compute_job(
    *,
    plan: Any,
    session_id: str,
    project_id: str,
    owner: str,
    actor: Optional[ComputeJobActor] = None,
    background: bool = False,
    progress_hook: Optional[Callable] = None,
    execute_locked: Callable,
    payload_loader: Callable,
    receipt_finder: Callable,
) -> Dict[str, Any]:
    """durable 版 execute：收据短路 → 幂等建 job →（内联或后台）执行。

    Returns:
        ``{"receipt": ComputeReceipt}``（短路/内联完成）或
        ``{"job_id": ..., "status": ...}``（background=True 的新提交）。
    """
    actor = actor or ComputeJobActor()
    # 1) 收据短路（域级幂等，不产生 job 行 —— 与 BASE 完全一致）
    current = await payload_loader(session_id, plan.input_ref)
    from app.services.data_preparation.field_compute.snapshot import canonical_hash

    content_hash = canonical_hash(current)
    existing = await receipt_finder(
        session_id, plan.compute_fingerprint, plan.input_ref, content_hash
    )
    if existing is not None:
        return {"receipt": existing, "idempotent_reuse": True}

    # 2) 幂等建 job
    key = build_idempotency_key(session_id, plan.plan_id, plan.compute_fingerprint)
    params = {
        "session_id": session_id,
        "plan_id": plan.plan_id,
        "compute_fingerprint": plan.compute_fingerprint,
        "input_ref": plan.input_ref,
        "row_budget": getattr(plan, "row_budget", None),
    }
    job_id, job_status, reused = submit_async_job(
        task_type=TASK_TYPE,
        display_name=f"字段计算 · {plan.plan_id[:24]}",
        params=params,
        idempotency_key=key,
        owner_id=actor.user_id,
        owner_token=actor.owner_token,
        org_id=actor.org_id,
        session_id=session_id,
        project_id=project_id or None,
    )

    if reused and job_status in ("pending", "queued", "running", "cancelling"):
        # 另一副本（或先前提交）正在执行同一计划：等待其终态后收敛 ——
        # 与 BASE 的进程内 fingerprint 锁排队语义一致，但跨副本成立。
        join_status, receipt = await _join_active_job(
            job_id, session_id, plan, receipt_finder
        )
        if join_status == "cancelled":
            # 排队期间用户取消的是「这个计划的执行」：等待方继承取消，
            # 不因先到的终态吞掉取消意图（BASE：codes == [CANCELLED, CANCELLED]）。
            return {
                "job_id": str(job_id), "status": "cancelled",
                "plan_id": plan.plan_id, "idempotent_reuse": True,
            }
        if receipt is not None:
            return {"receipt": receipt, "idempotent_reuse": True}
        # failed/stale：旧 job 终态释放幂等键，重提一次新执行（同一键，
        # store.create_sync 会因旧行不可复用而建新行）
        job_id, job_status, reused = submit_async_job(
            task_type=TASK_TYPE,
            display_name=f"字段计算 · {plan.plan_id[:24]}",
            params=params,
            idempotency_key=key,
            owner_id=actor.user_id,
            owner_token=actor.owner_token,
            org_id=actor.org_id,
            session_id=session_id,
            project_id=project_id or None,
        )
        if reused:
            # 极端竞态（重提瞬间又被并发占位）：如实报告进行中，交由调用方轮询
            return {
                "job_id": str(job_id), "status": job_status,
                "plan_id": plan.plan_id, "idempotent_reuse": True,
            }

    if background:
        runner_task = asyncio.get_running_loop().create_task(
            _run_compute_job(
                job_id, plan, session_id, project_id, owner,
                execute_locked, progress_hook,
            ),
            name=f"field-compute:{job_id}",
        )
        _RUNNERS[job_id] = runner_task
        runner_task.add_done_callback(lambda t: _RUNNERS.pop(job_id, None))
        return {
            "job_id": str(job_id),
            "status": "running",
            "plan_id": plan.plan_id,
            "idempotent_reuse": reused,
            "poll": f"/api/v1/tasks/jobs/{job_id}",
        }

    # 内联执行（BASE 兼容：execute() 同步返回收据）
    status = await _run_compute_job(
        job_id, plan, session_id, project_id, owner,
        execute_locked, progress_hook,
    )
    if status.value == "completed":
        receipt = await receipt_finder(
            session_id, plan.compute_fingerprint, plan.input_ref, content_hash
        )
        # 收据仍在（内容未变）：返回；内容变了（罕见竞态）→ 如实重查失败
        if receipt is not None:
            return {"receipt": receipt, "idempotent_reuse": reused}
    # 未成功（cancelled/failed）：终态事实由 job 行承载；调用方按 job 状态
    # 转译 typed 错误
    return {
        "job_id": str(job_id),
        "status": status.value,
        "plan_id": plan.plan_id,
        "idempotent_reuse": reused,
    }


#: 本进程在飞的 compute runner（后台模式）：取消桥与关停用
_RUNNERS: Dict[int, asyncio.Task] = {}


async def _join_active_job(
    job_id: int,
    session_id: str,
    plan: Any,
    receipt_finder: Callable,
) -> tuple[str, Optional[Any]]:
    """等待活跃 job 终态。返回 (终态, 收据或 None)。

    * completed → 尝试取收据（内容一致才有）；取不到 → (completed, None)
      由调用方按未完成处理
    * cancelled → ("cancelled", None) —— 等待方继承取消
    * failed/stale → (status, None) —— 调用方可重提
    * 超时 → ("timeout", None)
    """
    from app.services.data_operations.runner import _sync_session

    from app.models.db_model import AnalysisTask

    loop = asyncio.get_event_loop()
    deadline = loop.time() + JOIN_ACTIVE_JOB_TIMEOUT_S
    while loop.time() < deadline:
        await asyncio.sleep(0.5)
        with _sync_session() as db:
            row = db.get(AnalysisTask, job_id)
            status = str(row.status) if row is not None else "unknown"
            db.commit()
        if status == "completed":
            current = await _safe_payload(session_id, plan.input_ref)
            if current is None:
                return "completed", None
            from app.services.data_preparation.field_compute.snapshot import canonical_hash

            receipt = await receipt_finder(
                session_id, plan.compute_fingerprint, plan.input_ref,
                canonical_hash(current),
            )
            return "completed", receipt
        if status in ("failed", "cancelled", "stale"):
            return status, None
    return "timeout", None


async def _safe_payload(session_id: str, ref: str) -> Any:
    try:
        from app.services.session_data import session_data_manager

        return await session_data_manager.get(session_id, ref)
    except Exception:  # noqa: BLE001
        return None


async def _run_compute_job(
    job_id: int,
    plan: Any,
    session_id: str,
    project_id: str,
    owner: str,
    execute_locked: Callable,
    progress_hook: Optional[Callable],
) -> Any:
    """执行体：_execute_locked（G19 原语义）+ 进度 + fenced 终态。"""

    async def _body(handle):
        def _progress(done_rows: int, total_rows: Optional[int]) -> None:
            # 进度由 handle 的节流器约束；同步回调里不能 await ——
            # 用 create_task 触发一次节流写（丢失不致命）
            try:
                asyncio.get_running_loop().create_task(
                    handle.progress(
                        phase="compute",
                        message=f"已计算 {done_rows} 行",
                        current_step=done_rows,
                        total_steps=total_rows,
                    )
                )
            except Exception:  # noqa: BLE001
                pass

        hook = progress_hook or _progress
        try:
            receipt = await execute_locked(
                plan, session_id, project_id=project_id, owner=owner,
                progress=hook,
            )
        except Exception as exc:  # noqa: BLE001
            # 域层把协作取消翻译成 ExecError("CANCELLED")：job 侧必须收敛为
            # cancelled 终态（迟到完成不改写、失败与取消不混淆）。
            if getattr(exc, "code", "") == "CANCELLED":
                from app.lib.cancellation import OperationCancelled

                raise OperationCancelled(str(exc)[:200], job_id=job_id) from exc
            raise
        return receipt.to_bounded_dict()

    def _ref_of(result: Any) -> str:
        if isinstance(result, dict):
            return str(result.get("output_ref") or "")
        return str(getattr(result, "output_ref", "") or "")

    try:
        status = await run_async_job(
            job_id, _body,
            result_ref_builder=_ref_of,
        )
        return status
    except AlreadyFinished:
        from app.services.jobs.lifecycle import JobStatus

        return JobStatus.running  # 并发副本先认领：无本进程终态
