"""TaskTracker - 任务状态跟踪器

根据 docs/superpower/specs/2026-04-08-task-planning-react-design.md 设计实现

ADR-0052 之后 TaskTracker 不再是任务的**持久事实源** —— 它是「本进程内正在跑的
agent turn」的热态视图，durable 事实在 analysis_tasks 表（见 app/services/jobs）。
同时每个 task 现在持有一个 CancellationToken：``cancel()`` 会点燃它，执行引擎
``await token.wait()`` 从而**抢占式**打断在飞的工具，而不是等当前工具自然跑完；
token 还通过 contextvar 传进同步 GIS 代码，让长循环能在 checkpoint 处协作退出。
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.services.jobs.cancellation import CancellationToken
from app.services.jobs.cancellation import registry as cancellation_registry


class StepStatus(str, Enum):
    """步骤执行状态"""
    running = "running"
    completed = "completed"
    failed = "failed"


class TaskStatus(str, Enum):
    """任务执行状态"""
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class TaskStep:
    """任务步骤"""
    id: str                          # 自增如 "step-1"
    tool: str                        # 工具名称
    params: dict                     # 工具参数
    status: StepStatus = StepStatus.running
    result: Any = None
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    #: ADR-0052：本步骤派生出的 durable job id。前端据此把后台 GIS job 挂到
    #: 对应 tool step 下面，形成 Agent Turn → Tool Step → Durable Job 链。
    background_job_ids: list[str] = field(default_factory=list)

    @property
    def is_error(self) -> bool:
        return self.status == StepStatus.failed or self.error is not None


@dataclass
class TaskInfo:
    """任务信息"""
    id: str                          # UUID (格式: task-{8位hex})
    session_id: str                # 所属会话
    original_request: str          # 用户原始消息
    steps: list[TaskStep] = field(default_factory=list)
    status: TaskStatus = TaskStatus.running
    plan: list[dict] = field(default_factory=list)  # JSON 格式的任务树/规划
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    _cancelled: bool = field(default=False, repr=False)  # 内部取消标志
    #: ADR-0052：贯穿式取消信号。cancel() 点燃它 → 执行引擎抢占在飞工具，
    #: 同步 GIS 循环在 checkpoint 处退出。
    cancel_token: CancellationToken | None = field(default=None, repr=False)

    @property
    def background_job_ids(self) -> list[str]:
        """本 task 全部步骤派生的 durable job id（按出现顺序去重）。"""
        seen: list[str] = []
        for step in self.steps:
            for job_id in step.background_job_ids:
                if job_id not in seen:
                    seen.append(job_id)
        return seen


class _TrackStepContext:
    """TaskTracker step lifecycle context manager."""

    def __init__(
        self,
        tracker: "TaskTracker",
        task_id: str | None,
        tool: str,
        params: dict,
    ):
        self.tracker = tracker
        self.task_id = task_id
        self.tool = tool
        self.params = params
        self.step: TaskStep | None = None

    async def __aenter__(self) -> TaskStep | None:
        if not self.task_id or self.task_id not in self.tracker._tasks:
            return None
        try:
            self.step = self.tracker.start_step(self.task_id, self.tool, self.params)
            return self.step
        except Exception:
            return None

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not self.task_id or not self.step:
            return False

        if exc_type is not None:
            err_msg = str(exc_val) if exc_val else f"Exception: {exc_type.__name__}"
            try:
                self.tracker.fail_step(self.task_id, self.step.id, err_msg)
            except Exception:
                pass
            return False  # Re-raise exception

        if getattr(self.step, "error", None) or getattr(self.step, "is_error", False):
            err_msg = getattr(self.step, "error", None) or "Step failed with error"
            try:
                self.tracker.fail_step(self.task_id, self.step.id, str(err_msg))
            except Exception:
                pass
        else:
            try:
                self.tracker.complete_step(self.task_id, self.step.id, self.step.result)
            except Exception:
                pass
        return False


class TaskTracker:
    """内存任务跟踪器"""

    MAX_TASKS_PER_SESSION = 20
    MAX_TOTAL_TASKS = 500

    def __init__(self) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        self._session_tasks: dict[str, list[str]] = {}
        self._step_counters: dict[str, int] = {}  # task_id -> step counter

    def track_step(self, task_id: str | None, tool: str, params: dict) -> _TrackStepContext:
        """返回 TaskTracker 步骤生命周期 Context Manager"""
        return _TrackStepContext(self, task_id, tool, params)

    def _generate_task_id(self) -> str:
        """生成 8 位 hex 的 task_id"""
        return f"task-{uuid.uuid4().hex[:8]}"

    def create(self, session_id: str, request: str) -> TaskInfo:
        """创建新任务"""
        # Evict old tasks if limits exceeded
        self._evict_if_needed()

        task_id = self._generate_task_id()
        task = TaskInfo(
            id=task_id,
            session_id=session_id,
            original_request=request,
            cancel_token=cancellation_registry.register(task_id),
        )
        self._tasks[task_id] = task

        # 记录 session 与 task 的关联
        if session_id not in self._session_tasks:
            self._session_tasks[session_id] = []
        self._session_tasks[session_id].append(task_id)

        # Per-session task limit
        session_task_ids = self._session_tasks[session_id]
        while len(session_task_ids) > self.MAX_TASKS_PER_SESSION:
            old_id = session_task_ids.pop(0)
            self._tasks.pop(old_id, None)
            self._step_counters.pop(old_id, None)

        # 初始化步骤计数器
        self._step_counters[task_id] = 0

        return task

    def list_all(self) -> list[TaskInfo]:
        """Return all tracked tasks."""
        return list(self._tasks.values())

    def _evict_if_needed(self):
        """Evict oldest tasks if total exceeds limit.

        Finished tasks are evicted first (existing behavior); if the cap is
        still exceeded — e.g. many running tasks abandoned by streams whose
        client disconnected mid-turn — the oldest remaining tasks are evicted
        regardless of status, so the tracker stays bounded. Evicting a running
        task is safe: _TrackStepContext and start_step/complete_step guard on
        task existence and no-op for unknown ids.
        """
        if len(self._tasks) <= self.MAX_TOTAL_TASKS:
            return
        finished_ids = [
            tid for tid, t in self._tasks.items()
            if t.status in (TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled)
        ]
        for tid in finished_ids[:len(self._tasks) - self.MAX_TOTAL_TASKS + 50]:
            self._drop_task(tid)

        # Still over the cap: evict the oldest tasks (dict insertion order)
        # regardless of status.
        for tid in list(self._tasks)[:max(0, len(self._tasks) - self.MAX_TOTAL_TASKS)]:
            self._drop_task(tid)

    def _drop_task(self, tid: str) -> None:
        """Remove a task and its bookkeeping; drop now-empty session keys."""
        self._tasks.pop(tid, None)
        self._step_counters.pop(tid, None)
        # ADR-0052: token 随 task 一起回收，避免 registry 随进程寿命单调增长
        cancellation_registry.discard(tid)
        for sid, ids in list(self._session_tasks.items()):
            if tid in ids:
                ids.remove(tid)
                if not ids:
                    self._session_tasks.pop(sid, None)

    def start_step(self, task_id: str, tool: str, params: dict) -> TaskStep:
        """启动步骤"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        # 自增步骤编号
        self._step_counters[task_id] += 1
        step_num = self._step_counters[task_id]
        step_id = f"step-{step_num}"

        step = TaskStep(
            id=step_id,
            tool=tool,
            params=params,
        )
        task.steps.append(step)

        return step

    def complete_step(self, task_id: str, step_id: str, result: Any) -> None:
        """完成步骤"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        step = next((s for s in task.steps if s.id == step_id), None)
        if not step:
            raise ValueError(f"Step {step_id} not found in task {task_id}")

        step.status = StepStatus.completed
        step.result = result
        step.finished_at = datetime.now(timezone.utc)

    def fail_step(self, task_id: str, step_id: str, error: str) -> None:
        """标记步骤失败"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        step = next((s for s in task.steps if s.id == step_id), None)
        if not step:
            raise ValueError(f"Step {step_id} not found in task {task_id}")

        step.status = StepStatus.failed
        step.error = error
        step.finished_at = datetime.now(timezone.utc)

    def complete_task(self, task_id: str) -> None:
        """完成任务"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        task.status = TaskStatus.completed
        task.finished_at = datetime.now(timezone.utc)

    def fail_task(self, task_id: str, error: str) -> None:
        """标记任务失败"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        task.status = TaskStatus.failed
        task.finished_at = datetime.now(timezone.utc)
        # 可选：记录失败原因到任务中

    def cancel(self, task_id: str) -> bool:
        """取消任务。点燃 CancellationToken，取消随即贯穿整条执行路径。

        ADR-0052 之前这里只翻一个 bool，chat 引擎在「每轮之间 / 每个工具完成之后」
        轮询它 —— 正在跑的 NDVI 或缓冲区分析会一路算完（30–60s）才停，CPU 并没有
        被释放。现在：

          * 执行引擎 ``await token.wait()``，取消到达即 **抢占式** cancel 在飞的
            asyncio 工具任务（见 execution_engine 的并行 wave 循环）；
          * token 经 contextvar 传入同步 GIS 代码（asyncio.to_thread 会复制
            context），长循环在 ``jobs.checkpoint()`` 处协作退出；
          * token 级联到本 turn 派生的 durable job，worker 侧也会观察到取消。

        幂等：重复取消返回 True 但不重复点燃 token（规范 §5）。
        """
        task = self._tasks.get(task_id)
        if not task:
            return False

        task.status = TaskStatus.cancelled
        task._cancelled = True
        task.finished_at = datetime.now(timezone.utc)
        token = task.cancel_token or cancellation_registry.register(task_id)
        task.cancel_token = token
        token.cancel("cancelled by user")
        return True

    def cancel_token_for(self, task_id: str) -> CancellationToken | None:
        """取回 task 的取消 token（工具管道用它绑定执行上下文）。"""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if task.cancel_token is None:
            task.cancel_token = cancellation_registry.register(task_id)
        return task.cancel_token

    def is_cancelled(self, task_id: str) -> bool:
        """检查任务是否已取消"""
        task = self._tasks.get(task_id)
        if not task:
            return False
        return task._cancelled

    def get(self, task_id: str) -> TaskInfo | None:
        """获取任务"""
        return self._tasks.get(task_id)

    def list_by_session(self, session_id: str) -> list[TaskInfo]:
        """按会话列出任务"""
        task_ids = self._session_tasks.get(session_id, [])
        return [self._tasks[tid] for tid in task_ids if tid in self._tasks]


def detect_geojson(result: Any) -> bool:
    """检测工具返回结果是否包含可渲染的地图数据（GeoJSON 或栅格热力图）"""
    if not isinstance(result, dict):
        return False
    if result.get("type") == "FeatureCollection":
        return True
    if result.get("type") == "heatmap_raster" and result.get("image"):
        return True
    for v in result.values():
        if isinstance(v, dict) and v.get("type") == "FeatureCollection":
            return True
    return False