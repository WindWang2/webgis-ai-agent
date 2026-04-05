"""
Agent编排核心数据模型
"""
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Set
from datetime import datetime


class TaskType(str, Enum):
    """任务类型枚举"""
    DEVELOP = "develop"
    TEST_REVIEW = "test_review"
    DEPLOY = "deploy"


class TaskPriority(int, Enum):
    """任务优先级枚举(数值越大优先级越高)"""
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class AgentRole(str, Enum):
    """Agent角色枚举"""
    CODER = "coder"
    TEST_REVIEWER = "test_reviewer"
    PM = "pm"


class AgentCapability(BaseModel):
    """Agent能力配置"""
    coder: Set[str] = Field(default_factory=set)
    test_reviewer: Set[str] = Field(default_factory=set)
    pm: Set[str] = Field(default_factory=set)


class AgentInfo(BaseModel):
    """Agent信息"""
    id: str
    role: AgentRole
    name: str
    capabilities: AgentCapability = Field(default_factory=AgentCapability)
    status: str = "idle"
    current_task_id: Optional[str] = None

    model_config = {"use_enum_values": True}


class TaskResult(BaseModel):
    """任务执行结果"""
    output: Dict[str, Any] = {}
    duration: float = 0.0
    error: Optional[str] = None


class OrchestrationTask(BaseModel):
    """编排任务模型"""
    id: str
    task_type: TaskType
    priority: TaskPriority
    status: TaskStatus = TaskStatus.PENDING
    payload: Dict[str, Any] = {}

    assigned_agent_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0
    max_retries: int = 3
    timeout_seconds: int = 3600
    result: Optional[TaskResult] = None

    model_config = {"use_enum_values": True}

    def can_transition_to(self, new_status: TaskStatus) -> bool:
        """
        验证状态转换合法性
        
        Args:
            new_status: 目标状态
            
        Returns:
            是否允许此转换
        """
        valid_transition = {
            TaskStatus.PENDING: [TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.PAUSED],
            TaskStatus.RUNNING: [TaskStatus.COMPLETED, TaskStatus.FAILED],
            TaskStatus.FAILED: [TaskStatus.RETRYING, TaskStatus.CANCELLED],
            TaskStatus.RETRYING: [TaskStatus.PENDING, TaskStatus.CANCELLED],
            TaskStatus.COMPLETED: [],
            TaskStatus.CANCELLED: [],
            TaskStatus.PAUSED: [TaskStatus.PENDING, TaskStatus.CANCELLED],
        }
        return new_status in valid_transition.get(self.status, [])