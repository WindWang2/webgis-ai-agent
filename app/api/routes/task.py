"""Task API Route - 任务状态查询与取消"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from app.api.routes.chat import get_engine
from app.core.auth import (
    get_current_user,
    get_owner_token,
    require_owned_session,
    verify_session_owner,
)
from app.core.database import get_async_db
from app.models.db_model import Conversation
from app.services.jobs import DurableJobStore
from app.services.jobs import registry as cancellation_registry
from app.services.task_queue import TaskQueueService
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/tasks", tags=["任务管理"])


class TaskStepResponse(BaseModel):
    """任务步骤响应"""
    id: str
    tool: str
    status: str
    error: Optional[str] = None


class TaskStatusResponse(BaseModel):
    """任务状态响应"""
    task_id: str
    session_id: str
    original_request: str
    status: str
    steps: list[TaskStepResponse]


class TaskListResponse(BaseModel):
    """任务列表响应"""
    tasks: list[TaskStatusResponse]


class TaskCancelResponse(BaseModel):
    """任务取消响应"""
    cancelled: bool


async def _verify_task_owner(db: AsyncSession, task_id: str, user_id) -> None:
    """跨租户守卫：任务必须属于调用方（经 session 所有权解析）。

    审计 S34：task_id 之前不验主，且仅 8 hex（32 位熵）可被暴力枚举。
    这里通过 task.session_id → Conversation.user_id 链路验证。
    """
    task_info = get_engine().tracker.get(task_id)
    if not task_info:
        # 不存在也返回 404，避免存在性泄露
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    # 旧任务可能 session_id 为空字符串 —— 此时无法做所有权证明，统一拒绝
    if not task_info.session_id:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    await verify_session_owner(db, task_info.session_id, user_id=user_id)


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user),
) -> TaskStatusResponse:
    """查询任务状态和步骤详情"""
    await _verify_task_owner(db, task_id, _user.get("user_id"))
    task_info = get_engine().tracker.get(task_id)
    # _verify_task_owner 已确认存在；防御性再读一次
    if not task_info:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    steps = [
        TaskStepResponse(
            id=step.id,
            tool=step.tool,
            status=step.status.value,
            error=step.error,
        )
        for step in task_info.steps
    ]

    return TaskStatusResponse(
        task_id=task_info.id,
        session_id=task_info.session_id,
        original_request=task_info.original_request,
        status=task_info.status.value,
        steps=steps,
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    session_id: str = Query(..., description="按会话 ID 过滤（必填，否则跨租户泄漏）"),
    _conv: Conversation = Depends(require_owned_session),
) -> TaskListResponse:
    """列出任务，必须按 session_id 过滤。

    审计 S33：之前 session_id 缺省时返回 tracker.list_all() —— 即所有用户
    所有任务的 original_request 原文，构成大面积信息泄漏。session_id 现在
    强制必填，且校验归属。
    """
    task_infos = get_engine().tracker.list_by_session(session_id)

    tasks = []
    for task_info in task_infos:
        steps = [
            TaskStepResponse(
                id=step.id,
                tool=step.tool,
                status=step.status.value,
                error=step.error,
            )
            for step in task_info.steps
        ]
        tasks.append(
            TaskStatusResponse(
                task_id=task_info.id,
                session_id=task_info.session_id,
                original_request=task_info.original_request,
                status=task_info.status.value,
                steps=steps,
            )
        )

    return TaskListResponse(tasks=tasks)


@router.delete("/{task_id}", response_model=TaskCancelResponse)
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user),
) -> TaskCancelResponse:
    """取消正在执行的任务。

    ADR-0052：除了点燃 agent task 的 token，这里也把该 turn 派生的 durable job 的
    取消请求**落库** —— 否则跨进程 worker 观察不到取消，后台 GIS 计算会一路跑完。
    行为与新的 ``DELETE /tasks/jobs/{job_id}`` 端点一致。
    """
    await _verify_task_owner(db, task_id, _user.get("user_id"))
    tracker = get_engine().tracker
    task_info = tracker.get(task_id)
    background_job_ids = list(task_info.background_job_ids) if task_info else []
    cancelled = tracker.cancel(task_id)
    for job_id in background_job_ids:
        await DurableJobStore.request_cancel(db, job_id)
    if background_job_ids:
        await db.commit()
    return TaskCancelResponse(cancelled=cancelled)


# ── Celery Task Status API ──────────────────────────────────────────


async def _verify_celery_owner(
    db: AsyncSession, celery_task_id: str, user_id: str, owner_token: Optional[str]
) -> None:
    """校验 celery task 归属。

    ADR-0052：以前唯一事实源是 ``TaskQueueService._task_owners`` —— 一个进程内
    class dict。后果是 API 重启后所有 Celery 任务突然 404（合法用户也查不到、
    取消不了），多副本部署下更是「注册在哪个 pod 就只有那个 pod 认」。

    现在内存 map 降级为快路径缓存，durable 行（analysis_tasks.celery_task_id +
    归属列）才是事实源。任一命中即通过；两者都不认才 404。
    """
    if TaskQueueService.verify_owner(celery_task_id, user_id):
        return
    if await DurableJobStore.verify_celery_owner(
        db, celery_task_id, owner_id=(user_id or None), owner_token=owner_token
    ):
        return
    raise HTTPException(status_code=404, detail="Task not found")


@router.get("/status/{task_id}")
async def get_celery_task_status(
    task_id: str,
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """查询 Celery 异步任务状态（审计 S34：校验任务所有权）

    ADR-0052：保持原响应键不变（Celery 语义的 status/result/progress），但在存在
    durable job 行时用它补充 job_id / durable_status / progress —— Celery result
    backend 不可用（无 Redis）时进度仍然可读，因为事实源已经在数据库里。
    """
    await _verify_celery_owner(db, task_id, _user.get("user_id", ""), owner_token)
    # 计算隔离不变式 1：AsyncResult.info/.ready() 是 Celery result backend 的
    # socket I/O，前端每 3s 轮询 —— 直接在循环上执行会卡住所有 SSE 流（#386）。
    payload = await asyncio.to_thread(TaskQueueService.get_task_status, task_id)

    job = await DurableJobStore.get_by_celery_id(db, task_id)
    if job is not None:
        payload["job_id"] = str(job.id)
        payload["durable_status"] = str(job.status or "")
        if job.progress is not None:
            payload["progress"] = int(job.progress)
        if job.progress_message:
            payload["message"] = job.progress_message
    return payload


@router.delete("/status/{task_id}")
async def revoke_celery_task(
    task_id: str,
    db: AsyncSession = Depends(get_async_db),
    _user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """撤销 Celery 异步任务（审计 S34：校验任务所有权）

    ADR-0052：撤销改为「先协作、后 revoke」—— 先把 durable job 的
    ``cancel_requested_at`` 落库并点燃本进程 token（worker 在 checkpoint 处优雅
    退出，顺带清理临时产物），再调 Celery revoke 兜底。terminate 不再是所有任务
    的第一取消手段（规范 §12）。
    """
    await _verify_celery_owner(db, task_id, _user.get("user_id", ""), owner_token)

    job = await DurableJobStore.get_by_celery_id(db, task_id)
    if job is not None:
        await DurableJobStore.request_cancel(db, job.id)
        await db.commit()
        cancellation_registry.cancel(str(job.id), "cancelled by user")

    # 计算隔离不变式 1：control.revoke 是 broker socket I/O，offload 到线程（#386）。
    revoked = await asyncio.to_thread(TaskQueueService.revoke_task, task_id)
    return {"revoked": revoked, "task_id": task_id}
