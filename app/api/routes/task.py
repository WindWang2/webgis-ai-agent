"""Task API Route - 任务状态查询与取消"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.api.routes.chat import engine

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


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: str) -> TaskStatusResponse:
    """查询任务状态和步骤详情"""
    task_info = engine.tracker.get(task_id)
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
async def list_tasks(session_id: Optional[str] = None) -> TaskListResponse:
    """列出任务，可按 session 过滤"""
    if session_id:
        task_infos = engine.tracker.list_by_session(session_id)
    else:
        # 返回所有任务
        task_infos = list(engine.tracker._tasks.values())

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
async def cancel_task(task_id: str) -> TaskCancelResponse:
    """取消正在执行的任务"""
    cancelled = engine.tracker.cancel(task_id)
    return TaskCancelResponse(cancelled=cancelled)