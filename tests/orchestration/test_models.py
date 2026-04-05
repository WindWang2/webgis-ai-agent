"""
数据模型测试
"""
import pytest
from datetime import datetime
from app.services.orchestration.models import (
    TaskType,
    TaskPriority,
    TaskStatus,
    AgentRole,
    AgentCapability,
    AgentInfo,
    OrchestrationTask,
    TaskResult,
)


class TestTaskType:
    """任务类型枚举测试"""

    def test_task_type_values(self):
        assert TaskType.DEVELOP.value == "develop"
        assert TaskType.TEST_REVIEW.value == "test_review"
        assert TaskType.DEPLOY.value == "deploy"

    def test_task_type_from_string(self):
        assert TaskType("develop") == TaskType.DEVELOP
        assert TaskType("test_review") == TaskType.TEST_REVIEW


class TestTaskPriority:
    """任务优先级测试"""

    def test_priority_order(self):
        """验证优先级数值大小关系"""
        assert TaskPriority.CRITICAL.value > TaskPriority.HIGH.value
        assert TaskPriority.HIGH.value > TaskPriority.MEDIUM.value
        assert TaskPriority.MEDIUM.value > TaskPriority.LOW.value

    def test_priority_comparison(self):
        assert TaskPriority.CRITICAL > TaskPriority.HIGH
        assert TaskPriority.HIGH > TaskPriority.MEDIUM
        assert TaskPriority.MEDIUM > TaskPriority.LOW
        assert TaskPriority.LOW < TaskPriority.MEDIUM


class TestTaskStatus:
    """任务状态测试"""

    def test_status_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"


class TestAgentCapability:
    """Agent能力配置测试"""

    def test_default_capabilities(self):
        caps = AgentCapability()
        assert caps.coder == set()
        assert caps.test_reviewer == set()
        assert caps.pm == set()

    def test_coder_capabilities(self):
        caps = AgentCapability(coder={"python", "javascript"})
        assert "python" in caps.coder
        assert "javascript" in caps.coder

    def test_all_roles_capabilities(self):
        caps = AgentCapability(
            coder={"python"},
            test_reviewer={"selenium", "pytest"},
            pm={"planning"}
        )
        assert "python" in caps.coder
        assert "selenium" in caps.test_reviewer
        assert "planning" in caps.pm


class TestAgentInfo:
    """Agent信息测试"""

    def test_agent_info_creation(self):
        agent = AgentInfo(
            id="agent-001",
            role=AgentRole.CODER,
            name="DevAgent1",
            capabilities=AgentCapability(coder={"python"})
        )
        assert agent.id == "agent-001"
        assert agent.role == AgentRole.CODER
        assert agent.status == "idle"
        assert agent.current_task_id is None

    def test_agent_info_assignment(self):
        agent = AgentInfo(
            id="agent-001",
            role=AgentRole.CODER,
            name="DevAgent1"
        )
        agent.status = "busy"
        agent.current_task_id = "task-123"
        assert agent.status == "busy"
        assert agent.current_task_id == "task-123"


class TestTaskResult:
    """任务结果测试"""

    def test_result_creation(self):
        result = TaskResult(output={"key": "value"}, duration=10.5)
        assert result.output["key"] == "value"
        assert result.duration == 10.5

    def test_result_error(self):
        result = TaskResult(output={}, duration=5.0, error="Something went wrong")
        assert result.error == "Something went wrong"


class TestOrchestrationTask:
    """编排任务测试"""

    @pytest.fixture
    def pending_task(self):
        return OrchestrationTask(
            id="test-task-1",
            task_type=TaskType.DEVELOP,
            priority=TaskPriority.MEDIUM,
            payload={"spec": "implement login API"},
            created_at=datetime.now(),
            status=TaskStatus.PENDING
        )

    def test_task_creation(self):
        task = OrchestrationTask(
            id="task-1",
            task_type=TaskType.TEST_REVIEW,
            priority=TaskPriority.HIGH,
            payload={"test_file": "test_main.py"}
        )
        assert task.id == "task-1"
        assert task.task_type == TaskType.TEST_REVIEW
        assert task.priority == TaskPriority.HIGH
        assert task.status == TaskStatus.PENDING

    def test_can_transition_pending_to_running(self, pending_task):
        assert pending_task.can_transition_to(TaskStatus.RUNNING)

    def test_can_transition_pending_to_cancelled(self, pending_task):
        assert pending_task.can_transition_to(TaskStatus.CANCELLED)

    def test_can_transition_running_to_completed(self):
        task = OrchestrationTask(
            id="task-1",
            task_type=TaskType.DEVELOP,
            priority=TaskPriority.MEDIUM,
            status=TaskStatus.RUNNING,
            created_at=datetime.now()
        )
        assert task.can_transition_to(TaskStatus.COMPLETED)

    def test_can_transition_running_to_failed(self):
        task = OrchestrationTask(
            id="task-1",
            task_type=TaskType.DEVELOP,
            priority=TaskPriority.MEDIUM,
            status=TaskStatus.RUNNING,
            created_at=datetime.now()
        )
        assert task.can_transition_to(TaskStatus.FAILED)

    def test_can_not_transition_to_invalid_state(self, pending_task):
        # Cannot transition from PENDING directly to COMPLETED
        assert not pending_task.can_transition_to(TaskStatus.COMPLETED)
        # Cannot transition from COMPLETED to anything
        completed_task = OrchestrationTask(
            id="task-1",
            task_type=TaskType.DEVELOP,
            priority=TaskPriority.MEDIUM,
            status=TaskStatus.COMPLETED,
            created_at=datetime.now()
        )
        assert not completed_task.can_transition_to(TaskStatus.RUNNING)

    def test_task_with_initial_timestamps(self):
        now = datetime.now()
        task = OrchestrationTask(
            id="task-1",
            task_type=TaskType.DEPLOY,
            priority=TaskPriority.LOW,
            created_at=now
        )
        assert task.created_at == now

    def test_task_default_values(self):
        task = OrchestrationTask(
            id="task-1",
            task_type=TaskType.DEVELOP,
            priority=TaskPriority.MEDIUM
        )
        assert task.retry_count == 0
        assert task.max_retries == 3
        assert task.timeout_seconds == 3600

    def test_task_priority_constants(self):
        assert TaskPriority.LOW.value == 0
        assert TaskPriority.MEDIUM.value == 1
        assert TaskPriority.HIGH.value == 2
        assert TaskPriority.CRITICAL.value == 3