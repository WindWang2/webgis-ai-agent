from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from enum import Enum

class TaskType(str, Enum):
    DATA_QUERY = "data_query"
    SPATIAL_ANALYSIS = "spatial_analysis"
    VISUALIZATION = "visualization"
    GENERAL_QA = "general_qa"
    COMPLEX_ANALYSIS = "complex_analysis"
    UNKNOWN = "unknown"

class SubTask(BaseModel):
    id: int
    type: TaskType
    parameters: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[int] = Field(default_factory=list)
    status: str = "pending"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: int = 0
    fallback_attempted: bool = False

class ParsedTask(BaseModel):
    original_request: str
    task_type: TaskType
    subtasks: List[SubTask] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)

class AgentInfo(BaseModel):
    agent_type: str
    endpoint: str
    timeout: int = 30
    priority: int = 1

class OrchestrationResult(BaseModel):
    status: str
    data: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    execution_time: float = 0.0
