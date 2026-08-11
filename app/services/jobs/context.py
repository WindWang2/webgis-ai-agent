"""Agent ↔ durable job 关联上下文（规范 §5 / §21）。

目标形态：

    Agent Turn
     └─ Tool Step
         └─ Durable Job

原状态下 TaskTracker 的 ``task-xxxxxxxx`` 与 Celery 的 ``celery_task_id`` 是两个
完全无关的命名空间 —— 前端会看到两条互不相干的条目，用户不知道那个「后台任务」属于
刚才哪一步。

这里用一个 contextvar 承载「我是谁派生出来的」：工具在执行期间创建 durable job 时，
``DurableJobStore.create`` 自动把 session/owner/run/turn/tool_call/agent step 填进
job 行，并把新 job id 追加到 ``created_job_ids``。工具管道在 dispatch 结束后读取该
列表，把 ``background_job_ids`` 放进 ``step_result`` SSE，前端由此把后台 job 挂到
对应的 tool step 下面。

工具本身不需要改签名 —— 与 cancellation 一样走 contextvar，``asyncio.to_thread``
会复制 context。
"""
from __future__ import annotations

import contextlib
import contextvars
import uuid
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class JobOrigin:
    """一次工具执行的来源标识 + 该执行派生出的 durable job 收集器。"""

    session_id: Optional[str] = None
    owner_id: Optional[str] = None
    owner_token: Optional[str] = None
    org_id: Optional[int] = None
    project_id: Optional[str] = None
    run_id: Optional[str] = None
    turn_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    agent_task_id: Optional[str] = None
    agent_step_id: Optional[str] = None
    tool_name: Optional[str] = None
    #: 本次执行期间创建的 durable job id（按创建顺序）
    created_job_ids: list[str] = field(default_factory=list)

    def record_job(self, job_id: str | int) -> None:
        sid = str(job_id)
        if sid not in self.created_job_ids:
            self.created_job_ids.append(sid)

    def child(self, **overrides) -> "JobOrigin":
        """派生一个继承 owner/session 的子上下文（收集器不共享）。"""
        data = {
            "session_id": self.session_id,
            "owner_id": self.owner_id,
            "owner_token": self.owner_token,
            "org_id": self.org_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "tool_call_id": self.tool_call_id,
            "agent_task_id": self.agent_task_id,
            "agent_step_id": self.agent_step_id,
            "tool_name": self.tool_name,
        }
        data.update(overrides)
        return JobOrigin(**data)


CURRENT_ORIGIN: contextvars.ContextVar[Optional[JobOrigin]] = contextvars.ContextVar(
    "webgis_current_job_origin", default=None
)


def current_origin() -> Optional[JobOrigin]:
    return CURRENT_ORIGIN.get()


@contextlib.contextmanager
def use_origin(origin: Optional[JobOrigin]) -> Iterator[Optional[JobOrigin]]:
    reset = CURRENT_ORIGIN.set(origin)
    try:
        yield origin
    finally:
        CURRENT_ORIGIN.reset(reset)


def new_run_id() -> str:
    """生成 run_id。与既有 ``wfrun_`` 风格保持一致的短前缀 id。"""
    return f"run-{uuid.uuid4().hex[:12]}"


def new_turn_id() -> str:
    return f"turn-{uuid.uuid4().hex[:12]}"
