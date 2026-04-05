"""
任务调度器测试
"""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from app.services.orchestration.models import (
    OrchestrationTask,
    TaskType,
    TaskPriority,
    TaskStatus,
)
from app.services.orchestration.task_scheduler import TaskScheduler


@pytest.fixture
def scheduler():
    """调度器Fixture"""
    return TaskScheduler()


@pytest.fixture
def develop_task():
    """开发任务Fixture"""
    return OrchestrationTask(
        id="dev-task-1",
        task_type=TaskType.DEVELOP,
        priority=TaskPriority.HIGH,
        payload={"spec": "implement login API"},
        created_at=datetime.now(),
    )


@pytest.fixture
def test_review_task():
    """测试审查任务Fixture"""
    return OrchestrationTask(
        id="test-task-1",
        task_type=TaskType.TEST_REVIEW,
        priority=TaskPriority.MEDIUM,
        payload={"pr_url": "https://github.com/test"},
        created_at=datetime.now(),
    )


class TestTaskScheduler:
    """任务调度器测试"""

    def test_enqueue_single_task(self, scheduler, develop_task):
        """测试单个任务入队"""
        count = scheduler.enqueue(develop_task)
        assert count == 1
        assert scheduler.get_pending_count() == 1

    def test_multiple_tasks_in_queue(self, scheduler, develop_task, test_review_task):
        """测试多任务入队"""
        scheduler.enqueue(develop_task)
        scheduler.enqueue(test_review_task)
        assert scheduler.get_pending_count() == 2
        assert len(scheduler.get_all_tasks()) == 2

    def test_priority_order_dequeue(self, scheduler):
        """测试优先级顺序出队"""
        low_task = OrchestrationTask(
            id="low", task_type=TaskType.DEVELOP, priority=TaskPriority.LOW,
            created_at=datetime.now()
        )
        high_task = OrchestrationTask(
            id="high", task_type=TaskType.DEVELOP, priority=TaskPriority.CRITICAL,
            created_at=datetime.now()
        )
        medium_task = OrchestrationTask(
            id="medium", task_type=TaskType.DEVELOP, priority=TaskPriority.MEDIUM,
            created_at=datetime.now()
        )

        scheduler.enqueue(low_task)
        scheduler.enqueue(high_task)
        scheduler.enqueue(medium_task)

        # Critical 应首先出队
        next_task = scheduler.dequeue()
        assert next_task.id == "high"
        assert next_task.priority == TaskPriority.CRITICAL

    def test_dequeue_changes_status_to_running(self, scheduler, develop_task):
        """测试出队后状态变为RUNNING"""
        scheduler.enqueue(develop_task)
        
        task = scheduler.dequeue()
        
        assert task.status == TaskStatus.RUNNING
        assert task.started_at is not None

    def test_empty_heap_returns_none(self, scheduler):
        """测试空队列返回None"""
        result = scheduler.dequeue()
        assert result is None

    def test_mark_completed(self, scheduler, develop_task):
        """测试标记任务完成"""
        scheduler.enqueue(develop_task)
        scheduler.assign_task(develop_task.id, "agent-001")
        
        scheduler.mark_completed(develop_task.id, {"output": "done"})
        
        task = scheduler.get_task(develop_task.id)
        assert task.status == TaskStatus.COMPLETED
        assert task.result is not None

    def test_mark_failed(self, scheduler, develop_task):
        """测试标记任务失败"""
        scheduler.enqueue(develop_task)
        scheduler.assign_task(develop_task.id, "agent-001")
        
        scheduler.mark_failed(develop_task.id, "Build failed")
        
        task = scheduler.get_task(develop_task.id)
        assert task.status == TaskStatus.FAILED
        assert task.result.error == "Build failed"

    def test_cancel_pending_task(self, scheduler, develop_task):
        """测试取消Pending任务"""
        scheduler.enqueue(develop_task)
        
        result = scheduler.cancel_task(develop_task.id)
        
        assert result is True
        task = scheduler.get_task(develop_task.id)
        assert task.status == TaskStatus.CANCELLED

    def test_cannot_cancel_completed_task(self, scheduler, develop_task):
        """测试无法取消已完成任务"""
        scheduler.enqueue(develop_task)
        scheduler.assign_task(develop_task.id, "agent-001")
        scheduler.mark_completed(develop_task.id)
        
        result = scheduler.cancel_task(develop_task.id)
        
        assert result is False

    def test_update_priority_success(self, scheduler, develop_task):
        """测试成功调整优先级"""
        scheduler.enqueue(develop_task)
        
        result = scheduler.update_priority(develop_task.id, TaskPriority.CRITICAL)
        
        assert result is True
        task = scheduler.get_task(develop_task.id)
        assert task.priority == TaskPriority.CRITICAL

    def test_update_priority_on_running_fails(self, scheduler, develop_task):
        """测试运行中任务无法调整优先级"""
        scheduler.enqueue(develop_task)
        scheduler.dequeue()  # 改为RUNNING状态
        
        result = scheduler.update_priority(develop_task.id, TaskPriority.CRITICAL)
        
        assert result is False

    def test_pause_resume_task(self, scheduler, develop_task):
        """测试暂停和恢复任务"""
        scheduler.enqueue(develop_task)
        
        # 暂停
        paused = scheduler.pause_task(develop_task.id)
        assert paused is True
        
        task = scheduler.get_task(develop_task.id)
        assert task.status == TaskStatus.PAUSED
        
        # 恢复
        resumed = scheduler.resume_task(develop_task.id)
        assert resumed is True
        
        task = scheduler.get_task(develop_task.id)
        assert task.status == TaskStatus.PENDING

    def test_retry_task(self, scheduler, develop_task):
        """测试任务重试"""
        develop_task.retry_count = 0
        develop_task.max_retries = 3
        scheduler.enqueue(develop_task)
        scheduler.assign_task(develop_task.id, "agent-001")
        scheduler.mark_failed(develop_task.id, "Test error")
        
        # 重试
        retried = scheduler.retry_task(develop_task.id)
        
        assert retried is True
        task = scheduler.get_task(develop_task.id)
        assert task.status == TaskStatus.RETRYING
        assert task.retry_count == 1

    def test_exceed_max_retries(self, scheduler, develop_task):
        """测试超出最大重试次数"""
        develop_task.retry_count = 3
        develop_task.max_retries = 3
        scheduler.enqueue(develop_task)
        
        retried = scheduler.retry_task(develop_task.id)
        
        assert retried is False

    def test_schedule_retry(self, scheduler, develop_task):
        """测试计划重试放回队列"""
        develop_task.retry_count = 1
        develop_task.status = TaskStatus.RETRYING
        scheduler.enqueue(develop_task)
        
        scheduled = scheduler.schedule_retry(develop_task.id)
        
        assert scheduled is True
        task = scheduler.get_task(develop_task.id)
        assert task.status == TaskStatus.PENDING

    def test_get_statistics(self, scheduler, develop_task, test_review_task):
        """测试统计信息"""
        scheduler.enqueue(develop_task)
        scheduler.enqueue(test_review_task)
        
        scheduler.dequeue()  # Running
        scheduler.assign_task(test_review_task.id, "agent-002")
        scheduler.mark_completed(test_review_task.id, {})
        
        stats = scheduler.get_statistics()
        
        assert stats["total"] == 2
        assert stats["pending"] >= 0
        assert stats["running"] >= 0
        assert stats["completed"] >= 0

    def test_assign_task_updates_agent_id(self, scheduler, develop_task):
        """测试分配任务时更新Agent ID"""
        scheduler.enqueue(develop_task)
        
        result = scheduler.assign_task(develop_task.id, "agent-coder-1")
        
        assert result is True
        task = scheduler.get_task(develop_task.id)
        assert task.assigned_agent_id == "agent-coder-1"


class TestTaskSchedulerEdgeCases:
    """边界情况测试"""

    def test_double_enqueue_same_task(self, scheduler, develop_task):
        """测试重复入队相同任务"""
        count1 = scheduler.enqueue(develop_task)
        count2 = scheduler.enqueue(develop_task)  # 再次入队
        
        # 应该只算一个任务
        assert count1 == count2
        assert scheduler.get_pending_count() == 1

    def test_get_nonexistent_task(self, scheduler):
        """测试获取不存在任务"""
        task = scheduler.get_task("non-existent")
        assert task is None

    def test_update_nonexistent_priority(self, scheduler):
        """测试调整不存在任务的优先级"""
        result = scheduler.update_priority("non-existent", TaskPriority.HIGH)
        assert result is False