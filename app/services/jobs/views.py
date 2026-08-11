"""任务中心对外视图（规范 §10 / §35）。

前端只拿到「安全摘要」：不含 Celery traceback、worker 内部信息、DB 实现细节、原始
用户 prompt、凭据或完整 GeoJSON。同一个 JobView 同时承载 durable job 与内存 agent
task，前端因此只需要处理一种形状。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.models.db_model import AnalysisTask
from app.services.jobs.lifecycle import (
    JobStatus,
    coerce_status,
    is_active,
    is_cancellable,
    is_retryable_status,
)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class JobView(BaseModel):
    """统一 job 视图。"""

    id: str = Field(description="durable job id，或 agent task id（task-xxxxxxxx）")
    kind: str = Field(description="agent | analysis | workflow | explorer")
    name: str = Field(description="展示名。不含用户原始请求原文")
    status: str = Field(description="pending|queued|running|cancelling|completed|failed|cancelled|stale")
    progress: Optional[int] = Field(default=None, description="0–100；null 表示不确定进度")
    message: Optional[str] = None
    cancellable: bool = False
    retryable: bool = False
    active: bool = False
    attempt: int = 1
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    #: 关联的 agent task（durable job 由哪个 agent 任务派生）
    agent_task_id: Optional[str] = None
    agent_step_id: Optional[str] = None
    #: agent task 派生出的后台 job（形成 Turn → Step → Job 链）
    background_job_ids: list[str] = Field(default_factory=list)
    error: Optional[str] = Field(default=None, description="单行错误摘要，绝不含 traceback")
    result_ref: Optional[str] = Field(default=None, description="结果指针（artifact id / 路径）")
    step_count: int = 0
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    cancel_requested_at: Optional[str] = None


class JobListResponse(BaseModel):
    jobs: list[JobView]
    #: 是否仍有活跃 job。前端据此决定要不要继续轮询（规范 §32：无活跃 job → 0 轮询）
    has_active: bool = False
    #: 建议的下次轮询间隔（毫秒）。null 表示不要再轮询
    poll_after_ms: Optional[int] = None


class JobCancelResponse(BaseModel):
    id: str
    status: str
    #: 本次调用是否真的改变了状态。重复取消返回 False 但仍是 200（幂等）
    cancel_requested: bool
    cancelling: bool = False


class JobRetryResponse(BaseModel):
    id: str
    status: str
    retried: bool
    reason: str
    attempt: int = 1


#: 建议轮询间隔。有活跃 job 时 3s —— 复用 SSE 之外的轻量兜底，不引入 websocket。
ACTIVE_POLL_INTERVAL_MS = 3000


def job_from_record(record: AnalysisTask) -> JobView:
    """durable 行 → JobView。只读安全字段。"""
    status = coerce_status(record.status)
    # error_trace 已在写入侧脱敏为单行；此处再兜一层，杜绝历史行的多行 traceback 泄漏
    error = None
    if record.error_trace:
        error = str(record.error_trace).splitlines()[0][:500]

    return JobView(
        id=str(record.id),
        kind=str(record.job_kind or "analysis"),
        name=str(record.display_name or record.task_type or "job"),
        status=status.value,
        progress=(None if record.progress is None else int(record.progress)),
        message=record.progress_message,
        cancellable=is_cancellable(status),
        retryable=(
            is_retryable_status(status) and (record.retry_count or 0) < (record.max_retries or 0)
        ),
        active=is_active(status),
        attempt=int(record.attempt or 1),
        session_id=record.session_id,
        project_id=record.project_id,
        agent_task_id=record.agent_task_id,
        agent_step_id=record.agent_step_id,
        error=error,
        result_ref=record.result_ref,
        created_at=_iso(record.created_at),
        started_at=_iso(record.started_at),
        finished_at=_iso(record.completed_at),
        cancel_requested_at=_iso(record.cancel_requested_at),
    )


#: 内存 agent task 状态 → 统一 JobStatus。
_AGENT_STATUS_MAP: dict[str, JobStatus] = {
    "running": JobStatus.running,
    "completed": JobStatus.completed,
    "failed": JobStatus.failed,
    "cancelled": JobStatus.cancelled,
}


def job_from_agent_task(task: Any, *, background_job_ids: list[str] | None = None) -> JobView:
    """内存 TaskInfo → JobView。

    ``name`` 用步骤数/工具名合成，**不**回传 ``original_request`` 原文 —— 旧
    ``/tasks/{id}`` 端点为兼容仍返回原文，但统一视图不扩大泄漏面（规范 §35）。
    """
    raw_status = getattr(getattr(task, "status", None), "value", None) or str(
        getattr(task, "status", "running")
    )
    status = _AGENT_STATUS_MAP.get(raw_status, JobStatus.running)
    steps = list(getattr(task, "steps", []) or [])
    cancelling = bool(getattr(task, "_cancelled", False)) and status == JobStatus.running
    if cancelling:
        status = JobStatus.cancelling

    last_tool = None
    for step in reversed(steps):
        tool = getattr(step, "tool", None)
        if tool:
            last_tool = tool
            break

    error = None
    for step in reversed(steps):
        if getattr(step, "error", None):
            error = str(step.error).splitlines()[0][:500]
            break

    return JobView(
        id=str(getattr(task, "id", "")),
        kind="agent",
        name=(f"AI 任务 · {last_tool}" if last_tool else "AI 任务"),
        status=status.value,
        # agent task 没有真实百分比 —— 明确 indeterminate，而不是编一个假的 99%
        progress=(100 if status == JobStatus.completed else None),
        message=(f"{len(steps)} 个步骤" if steps else None),
        cancellable=is_cancellable(status),
        retryable=False,
        active=is_active(status),
        session_id=getattr(task, "session_id", None) or None,
        background_job_ids=list(background_job_ids or []),
        error=error,
        step_count=len(steps),
        created_at=_iso(getattr(task, "created_at", None)),
        started_at=_iso(getattr(task, "created_at", None)),
        finished_at=_iso(getattr(task, "finished_at", None)),
    )


def build_list_response(jobs: list[JobView]) -> JobListResponse:
    has_active = any(job.active for job in jobs)
    return JobListResponse(
        jobs=jobs,
        has_active=has_active,
        poll_after_ms=(ACTIVE_POLL_INTERVAL_MS if has_active else None),
    )


AgentOrDurable = Literal["agent", "durable"]


def classify_job_id(job_id: str) -> AgentOrDurable:
    """区分 agent task id（``task-xxxxxxxx``）与 durable job id（数字）。"""
    return "agent" if str(job_id).startswith("task-") else "durable"
