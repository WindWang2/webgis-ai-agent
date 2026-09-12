"""任务管理子系统契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/task.py` 内联迁出；路由文件只 import。
``/tasks/status/{task_id}`` 两个端点镜像 Celery 语义
（ADR-0052：status/result/progress 键保持不变），用 extra="allow"
承载 durable job 的补充键。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class TaskStepResponse(BaseModel):
    """任务步骤响应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"id": "step-1", "tool": "buffer_analysis", "status": "done"}]
        }
    )

    id: str
    tool: str
    status: str
    error: Optional[str] = None


class TaskStatusResponse(BaseModel):
    """任务状态响应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "task_id": "task_ab12cd34",
                    "session_id": "sess-123",
                    "original_request": "做一份缓冲区分析",
                    "status": "running",
                    "steps": [],
                }
            ]
        }
    )

    task_id: str
    session_id: str
    original_request: str
    status: str
    steps: list[TaskStepResponse]


class TaskListResponse(BaseModel):
    """任务列表响应。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"tasks": []}]}
    )

    tasks: list[TaskStatusResponse]


class TaskCancelResponse(BaseModel):
    """DELETE /tasks/{task_id} 响应。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"cancelled": True}]}
    )

    cancelled: bool


class CeleryTaskStatusResponse(BaseModel):
    """GET /tasks/status/{task_id} 响应（Celery 语义 + durable 补充键）。

    ADR-0052：status/result/progress 键保持不变；存在 durable job 行时
    补充 job_id / durable_status / progress / message。
    """

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "status": "SUCCESS",
                    "result": None,
                    "progress": 100,
                    "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "durable_status": "succeeded",
                }
            ]
        },
    )

    status: Optional[str] = None
    result: Optional[Any] = None
    progress: Optional[float] = None
    message: Optional[str] = None
    job_id: Optional[str] = None
    durable_status: Optional[str] = None


class CeleryTaskRevokeResponse(BaseModel):
    """DELETE /tasks/status/{task_id} 响应。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"revoked": True, "task_id": "celery-abc"}]}
    )

    revoked: bool
    task_id: str
