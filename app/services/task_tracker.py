"""TaskTracker - 任务状态跟踪器

根据 docs/superpower/specs/2026-04-08-task-planning-react-design.md 设计实现
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


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


class TaskTracker:
    """内存任务跟踪器"""

    MAX_TASKS_PER_SESSION = 20
    MAX_TOTAL_TASKS = 500

    def __init__(self) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        self._session_tasks: dict[str, list[str]] = {}
        self._step_counters: dict[str, int] = {}  # task_id -> step counter

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

    def _evict_if_needed(self):
        """Evict oldest finished tasks if total exceeds limit."""
        if len(self._tasks) <= self.MAX_TOTAL_TASKS:
            return
        finished_ids = [
            tid for tid, t in self._tasks.items()
            if t.status in (TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled)
        ]
        for tid in finished_ids[:len(self._tasks) - self.MAX_TOTAL_TASKS + 50]:
            self._tasks.pop(tid, None)
            self._step_counters.pop(tid, None)
            for sids in self._session_tasks.values():
                if tid in sids:
                    sids.remove(tid)

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
        """取消任务"""
        task = self._tasks.get(task_id)
        if not task:
            return False

        task.status = TaskStatus.cancelled
        task._cancelled = True
        task.finished_at = datetime.now(timezone.utc)
        return True

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