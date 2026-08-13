"""Agent 工具 → durable job 提交桥（规范 §21 / §22）。

同步工具（跑在 registry 的线程池里）用这里的 ``submit_durable_job`` 把重计算交给
Celery，并同时得到一个 durable job 行。它做四件事：

  1. 从 contextvar 里的 JobOrigin 取出 session/owner/agent step 关联，写进 job 行 ——
     前端因此能把后台 GIS job 挂到对应 tool step 下，而不是看到两条无关条目。
  2. 幂等：同一逻辑提交（用户双击 / SSE 重连 / API retry）复用同一行、不重复入队。
  3. 把 job_id 作为参数传给 Celery 任务，worker 据此写进度、读取消、落终态。
  4. 把 celery_task_id 回填到 job 行，让旧的 `/tasks/status/{celery_task_id}` 端点
     和新任务中心指向同一个逻辑执行。

「什么算重操作」由调用方决定 —— 规范 §22 明确不要求所有 GIS 工具都 Celery 化。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from app.services.jobs.cancellation import registry as cancellation_registry
from app.services.jobs.context import current_origin
from app.services.jobs.lifecycle import JobKind, JobStatus
from app.services.jobs.store import DurableJobStore
from app.tools._utils import db_session

logger = logging.getLogger(__name__)


def build_execution_key(task_type: str, session_id: Optional[str], params: dict[str, Any]) -> str:
    """稳定的幂等键：(task_type, session, 规范化参数) 的摘要。

    只用于 durable/background 提交 —— 不给所有工具硬编码 hash（规范 §16）。
    参数用 sort_keys 序列化，保证 dict 顺序不影响键。
    """
    payload = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"{task_type}:{session_id or '-'}:{digest}"


def submit_durable_job(
    *,
    celery_task,
    task_type: str,
    display_name: str,
    params: dict[str, Any],
    task_args: tuple = (),
    task_kwargs: Optional[dict[str, Any]] = None,
    kind: JobKind = JobKind.analysis,
    session_id: Optional[str] = None,
    idempotent: bool = True,
    max_retries: int = 3,
) -> dict[str, Any]:
    """创建 durable job 并入队 Celery 任务。

    Args:
        celery_task: Celery task 对象（会以 ``job_id=`` 关键字额外接收 job id）。
        task_type: 落库的 task_type（≤50 字符）。
        display_name: 任务中心展示名。
        params: 用于幂等键与参数摘要的业务参数（会脱敏后落库）。
        task_args / task_kwargs: 传给 Celery 任务的参数。
        idempotent: 是否启用幂等键。不可逆且可能被重复触发的操作应保持 True。

    Returns:
        工具可直接返回给 LLM 的 dict：``{status, job_id, task_id, message, idempotent_reuse}``。
    """
    origin = current_origin()
    effective_session = session_id or (origin.session_id if origin else None)
    idem_key = (
        build_execution_key(task_type, effective_session, params) if idempotent else None
    )

    dispatch_spec = {
        "task": celery_task.name,
        "args": list(task_args),
        "kwargs": dict(task_kwargs or {}),
    }

    with db_session() as db:
        job = DurableJobStore.create_sync(
            db,
            dispatch_spec=dispatch_spec,
            task_type=task_type,
            kind=kind,
            display_name=display_name,
            parameters=params,
            session_id=effective_session,
            owner_id=(origin.owner_id if origin else None),
            owner_token=(origin.owner_token if origin else None),
            org_id=(origin.org_id if origin else None),
            project_id=(origin.project_id if origin else None),
            run_id=(origin.run_id if origin else None),
            turn_id=(origin.turn_id if origin else None),
            tool_call_id=(origin.tool_call_id if origin else None),
            agent_task_id=(origin.agent_task_id if origin else None),
            agent_step_id=(origin.agent_step_id if origin else None),
            idempotency_key=idem_key,
            max_retries=max_retries,
        )
        job_id = int(job.id)
        existing_celery_id = job.celery_task_id
        already_terminal = DurableJobStore.is_terminal_job(job)
        db.commit()

    # F17: 提交时注册进程内取消 token —— 否则 TaskTracker.cancel() 的级联
    # cancellation_registry.cancel(job_id) 找不到 token，变成空操作。
    cancellation_registry.register(job_id)

    if origin is not None:
        origin.record_job(job_id)

    # 幂等复用：已入队或已完成的 job 不再重复提交，避免重复写分析资产
    if existing_celery_id or already_terminal:
        logger.info(
            "[jobs] reusing durable job_id=%s (celery=%s terminal=%s)",
            job_id, existing_celery_id, already_terminal,
        )
        return {
            "status": "analysis_task_reused",
            "job_id": str(job_id),
            "task_id": existing_celery_id,
            "idempotent_reuse": True,
            "message": f"{display_name} 已在执行或已完成，复用同一后台任务（job {job_id}）。",
        }

    kwargs = dict(task_kwargs or {})
    kwargs["job_id"] = job_id

    # 先**原子认领**（pending → queued），只有认领成功的调用方才真正入队。
    # 顺序颠倒会留一个 TOCTOU 窗口：两个并发的相同提交都看到 celery_task_id 为空、
    # 都 apply_async，于是同一个 job 收到两条消息 → 重复执行不可逆的 GIS 操作。
    with db_session() as db:
        claimed = DurableJobStore.transition_sync(
            db, job_id, JobStatus.queued, expected=[JobStatus.pending]
        )
        db.commit()

    if not claimed:
        with db_session() as db:
            current = DurableJobStore.get_sync(db, job_id)
            existing_celery_id = current.celery_task_id if current else None
        logger.info("[jobs] job_id=%s already claimed by a concurrent submit", job_id)
        return {
            "status": "analysis_task_reused",
            "job_id": str(job_id),
            "task_id": existing_celery_id,
            "idempotent_reuse": True,
            "message": f"{display_name} 已在执行中，复用同一后台任务（job {job_id}）。",
        }

    try:
        async_result = celery_task.apply_async(args=list(task_args), kwargs=kwargs)
    except Exception as exc:
        # 入队失败：把 job 落成 failed，否则它会永远停在 queued 且没有执行体
        # （stale 清扫只覆盖 running/cancelling）。
        logger.error("[jobs] enqueue failed job_id=%s: %s", job_id, exc)
        with db_session() as db:
            DurableJobStore.mark_failed_sync(db, job_id, error=exc, message="enqueue failed")
            db.commit()
        raise

    # 回填 celery_task_id。worker 可能已经把状态推到 running/completed，所以这里
    # 不做状态迁移，只更新标识列 —— 否则旧的 /tasks/status/{celery_task_id}
    # 端点会永远查不到这个 job。
    with db_session() as db:
        DurableJobStore.set_celery_task_id_sync(db, job_id, async_result.id)
        db.commit()

    logger.info(
        "[jobs] queued job_id=%s celery=%s type=%s agent_task=%s",
        job_id, async_result.id, task_type,
        (origin.agent_task_id if origin else None),
    )
    return {
        "status": "analysis_task_started",
        "job_id": str(job_id),
        "task_id": async_result.id,
        "idempotent_reuse": False,
        "message": (
            f"{display_name} 已作为后台任务启动（job {job_id}）。"
            "可在任务中心查看进度，也可随时取消。"
        ),
    }
