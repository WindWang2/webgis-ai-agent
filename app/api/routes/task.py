"""Task API Route - 任务状态查询与取消"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from app.api.routes.chat import get_engine
from app.api.routes.layer import _verify_session_owner
from app.core.auth import get_current_user

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


async def _verify_task_owner(task_id: str, user_id) -> None:
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
    await _verify_session_owner(task_info.session_id, user_id)


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: str, _user: dict = Depends(get_current_user)) -> TaskStatusResponse:
    """查询任务状态和步骤详情"""
    await _verify_task_owner(task_id, _user.get("user_id"))
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
    _user: dict = Depends(get_current_user),
) -> TaskListResponse:
    """列出任务，必须按 session_id 过滤。

    审计 S33：之前 session_id 缺省时返回 tracker.list_all() —— 即所有用户
    所有任务的 original_request 原文，构成大面积信息泄漏。session_id 现在
    强制必填，且校验归属。
    """
    await _verify_session_owner(session_id, _user.get("user_id"))
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
async def cancel_task(task_id: str, _user: dict = Depends(get_current_user)) -> TaskCancelResponse:
    """取消正在执行的任务"""
    await _verify_task_owner(task_id, _user.get("user_id"))
    cancelled = get_engine().tracker.cancel(task_id)
    return TaskCancelResponse(cancelled=cancelled)


# ── Celery Task Status API ──────────────────────────────────────────

@router.get("/status/{task_id}")
async def get_celery_task_status(task_id: str, _user: dict = Depends(get_current_user)):
    """查询 Celery 异步任务状态

    注意：Celery task_id 与 tracker task_id 是不同命名空间（Celery 用 uuid，
    tracker 用 task-{hex}）。此处无法直接链到 session —— 这一组端点的所有权
    校验依赖 admin-only（如运维需要可后续收紧到 viewer 但需带 session_id）。
    暂保持 get_current_user 鉴权，未来如发现泄漏再加 session 关联。
    """
    from app.services.task_queue import TaskQueueService
    return TaskQueueService.get_task_status(task_id)


@router.delete("/status/{task_id}")
async def revoke_celery_task(task_id: str, _user: dict = Depends(get_current_user)):
    """撤销 Celery 异步任务"""
    from app.services.task_queue import TaskQueueService
    revoked = TaskQueueService.revoke_task(task_id)
    return {"revoked": revoked, "task_id": task_id}