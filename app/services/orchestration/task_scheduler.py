"""
任务调度器 - 基于优先级的任务分发
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
import heapq
import threading
from app.services.orchestration.models import (
    OrchestrationTask,
    TaskType,
    TaskPriority,
    TaskStatus,
    TaskResult,
)


class TaskScheduler:
    """
    任务调度器，实现
    
    特性:
    - 基于优先级堆的排序队列
    - 线程安全的并发访问
    - 支持任务取消、暂停、重试、优先级调整
    """

    def __init__(self):
        self._tasks: Dict[str, OrchestrationTask] = {}
        self._heap: List[tuple] = []
        self._lock = threading.RLock()
        self._task_counter = 0
        # 待分配队列（已准备好等待分配的Task）
        self._assigned_queue: List[str] = []

    def enqueue(self, task: OrchestrationTask) -> int:
        """
        任务入队
        
        Args:
            task: 要入队的任务
            
        Returns:
            当前队列长度
        """
        with self._lock:
            if task.id in self._tasks:
                return len(self._tasks)

            task.created_at = datetime.now()
            self._tasks[task.id] = task

            # 优先级队列: (-priority, counter, task_id)
            # 注：Pydantic会将Enum存储为其底层int值，需兼容处理
            priority_value = int(task.priority)
            heapq.heappush(
                self._heap,
                (-priority_value, self._task_counter, task.id),
            )
            self._task_counter += 1
            return len(self._tasks)

    def dequeue(self) -> Optional[OrchestrationTask]:
        """
        取出最高优先级任务
        
        Returns:
            优先级最高的Pending任务，如果没有则返回None
        """
        with self._lock:
            while self._heap:
                _, _, task_id = heapq.heappop(self._heap)
                task = self._tasks.get(task_id)
                if task and task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.RUNNING
                    task.started_at = datetime.now()
                    return task
                elif task and task.status != TaskStatus.PENDING:
                    # 已转移的任务跳过
                    continue
            return None

    def assign_task(self, task_id: str, agent_id: str) -> bool:
        """
        分配任务给Agent
        
        Args:
            task_id: 任务ID
            agent_id: Agent ID
            
        Returns:
            是否成功分配
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.can_transition_to(TaskStatus.RUNNING):
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now()
                task.assigned_agent_id = agent_id
                return True
            return False

    def get_task(self, task_id: str) -> Optional[OrchestrationTask]:
        """获取指定任务"""
        return self._tasks.get(task_id)

    def mark_completed(self, task_id: str, result: Optional[Dict[str, Any]] = None):
        """
        标记任务完成
        
        Args:
            task_id: 任务ID
            result: 执行结果
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now()
                elapsed = (
                    (task.completed_at - task.started_at).total_seconds()
                    if task.started_at else 0.0
                )
                task.result = TaskResult(output=result or {}, duration=elapsed)

    def mark_failed(self, task_id: str, error: str):
        """
        标记任务失败
        
        Args:
            task_id: 任务ID  
            error: 错误信息
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now()
                elapsed = (
                    (task.completed_at - task.started_at).total_seconds()
                    if task.started_at else 0.0
                )
                task.result = TaskResult(output={}, duration=elapsed, error=error)

    def retry_task(self, task_id: str) -> bool:
        """
        重试任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            是否成功进入重试状态
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.retry_count < task.max_retries:
                task.status = TaskStatus.RETRYING
                task.retry_count += 1
                return True
            return False

    def schedule_retry(self, task_id: str) -> bool:
        """
        将任务重新放回队列等待执行
        
        Args:
            task_id: 任务ID
            
        Returns:
            是否成功
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.RETRYING:
                task.status = TaskStatus.PENDING
                # 降低优先级重试
                if int(task.priority) > 0:
                    task.priority = TaskPriority(int(task.priority) - 1)
                heapq.heappush(
                    self._heap,
                    (-int(task.priority), self._task_counter, task.id),
                )
                self._task_counter += 1
                return True
            return False

    def cancel_task(self, task_id: str) -> bool:
        """
        取消任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            是否成功取消
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.can_transition_to(TaskStatus.CANCELLED):
                task.status = TaskStatus.CANCELLED
                return True
            return False

    def update_priority(self, task_id: str, new_priority: TaskPriority) -> bool:
        """
        调整任务优先级
        
        Args:
            task_id: 任务ID
            new_priority: 新优先级
            
        Returns:
            是否成功调整
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.PENDING:
                task.priority = new_priority
                return True
            return False

    def pause_task(self, task_id: str) -> bool:
        """
        暂停任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            是否成功暂停
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.can_transition_to(TaskStatus.PAUSED):
                task.status = TaskStatus.PAUSED
                return True
            return False

    def resume_task(self, task_id: str) -> bool:
        """
        恢复任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            是否成功恢复
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.PAUSED:
                task.status = TaskStatus.PENDING
                heapq.heappush(
                    self._heap,
                    (-int(task.priority), self._task_counter, task.id),
                )
                self._task_counter += 1
                return True
            return False

    def get_pending_count(self) -> int:
        """获取Pending任务数"""
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)

    def get_running_count(self) -> int:
        """获取Running任务数"""
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)

    def get_all_tasks(self) -> List[OrchestrationTask]:
        """获取所有任务"""
        return list(self._tasks.values())

    def get_tasks_by_status(self, status: TaskStatus) -> List[OrchestrationTask]:
        """获取指定状态的任务列表"""
        return [t for t in self._tasks.values() if t.status == status]

    def get_statistics(self) -> Dict[str, Any]:
        """获取调度统计数据"""
        stats = {
            "total": len(self._tasks),
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "paused": 0,
            "retrying": 0,
        }
        for task in self._tasks.values():
            stats[task.status.value] = stats.get(task.status.value, 0) + 1
        return stats


# 全局调度器实例
scheduler_instance = TaskScheduler()