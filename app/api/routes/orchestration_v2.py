"""
Agent编排API路由 - 任务管理与Agent调度RESTful接口
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
from app.services.orchestration.models import (
    OrchestrationTask,
    TaskType,
    TaskPriority,
    TaskStatus,
    AgentRole,
    AgentInfo,
)
from app.services.orchestration.task_scheduler import (
    TaskScheduler,
    scheduler_instance,
)
from app.services.orchestration.agent_pool import (
    AgentPool,
    agent_pool_instance,
)

router = APIRouter()

# 依赖注入获取服务实例
def get_scheduler() -> TaskScheduler:
    return scheduler_instance


def get_agent_pool() -> AgentPool:
    return agent_pool_instance


# ========== Request Models ==========
class CreateTaskRequest(BaseModel):
    """创建任务请求"""
    task_type: TaskType
    priority: TaskPriority = TaskPriority.MEDIUM
    payload: Dict[str, Any] = {}
    max_retries: int = 3
    timeout_seconds: int = 3600
    created_by: Optional[str] = None


class UpdatePriorityRequest(BaseModel):
    """更新优先级请求"""
    priority: TaskPriority


class RetryTaskRequest(BaseModel):
    """重试任务请求"""
    max_additional_retries: int = 1


class RegisterAgentRequest(BaseModel):
    """注册Agent请求"""
    role: AgentRole
    name: str
    capabilities_coder: List[str] = []
    capabilities_test_reviewer: List[str] = []
    capabilities_pm: List[str] = []

    def to_agent_info(self, agent_id: str) -> AgentInfo:
        from app.services.orchestration.models import AgentCapability
        return AgentInfo(
            id=agent_id,
            role=self.role,
            name=self.name,
            capabilities=AgentCapability(
                coder=set(self.capabilities_coder),
                test_reviewer=set(self.capabilities_test_reviewer),
                pm=set(self.capabilities_pm),
            ),
        )


# ========== Response Models ==========
class TaskResponse(BaseModel):
    """任务响应"""
    id: str
    task_type: str
    priority: int
    status: str
    payload: Dict[str, Any]
    assigned_agent_id: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retry_count: int
    max_retries: int
    timeout_seconds: int
    result: Optional[Dict[str, Any]] = None

    @classmethod
    def from_task(cls, task: OrchestrationTask) -> "TaskResponse":
        return cls(
            id=task.id,
            task_type=str(task.task_type),
            priority=int(task.priority),
            status=str(task.status),
            payload=task.payload,
            assigned_agent_id=task.assigned_agent_id,
            created_at=task.created_at.isoformat(),
            started_at=task.started_at.isoformat() if task.started_at else None,
            completed_at=task.completed_at.isoformat() if task.completed_at else None,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            timeout_seconds=task.timeout_seconds,
            result=dict(task.result) if task.result else None,
        )


class TaskListResponse(BaseModel):
    """任务列表响应"""
    total: int
    tasks: List[TaskResponse]


class AgentResponse(BaseModel):
    """Agent响应"""
    id: str
    role: str
    name: str
    status: str
    current_task_id: Optional[str] = None


class StatsResponse(BaseModel):
    """统计响应"""
    tasks: Dict[str, int]
    agents: Dict[str, Any]


# ========== Task APIs ==========
@router.post(
    "/tasks",
    response_model=TaskResponse,
    summary="创建任务",
)
async def create_task(
    request: CreateTaskRequest,
    scheduler: TaskScheduler = Depends(get_scheduler),
):
    """
    创建新任务并加入调度队列
    
    - **task_type**: 任务类型 (develop/test_review/deploy)
    - **priority**: 优先级 (0=LOW,1=MEDIUM,2=HIGH,3=CRITICAL)
    - **payload**: 任务载荷，包含具体指令和数据
    - **max_retries**: 最大重试次数，默认3次
    - **timeout_seconds**: 超时时间默认3600秒
    """
    task = OrchestrationTask(
        id=f"task-{uuid.uuid4().hex[:12]}",
        task_type=request.task_type,
        priority=request.priority,
        payload=request.payload,
        max_retries=request.max_retries,
        timeout_seconds=request.timeout_second,
        created_by=request.created_by,
    )
    
    scheduler.enqueue(task)
    return TaskResponse.from_task(task)


@router.get("/tasks", response_model=TaskListResponse, summary="任务列表")
async def list_tasks(
    status_filter: Optional[TaskStatus] = None,
    limit: int = 50,
    scheduler: TaskScheduler = Depends(get_scheduler),
):
    """
    获取任务列表，可按状态过滤
    """
    all_tasks = scheduler.get_all_tasks()
    
    if status_filter:
        all_tasks = [t for t in all_tasks if t.status == status_filter]
    
    tasks_response = [
        TaskResponse.from_task(t) for t in all_tasks[-limit:]
    ]
    
    return TaskListResponse(total=len(all_tasks), tasks=tasks_response)


@router.get("/tasks/{task_id}", response_model=TaskResponse, summary="任务详情")
async def get_task(
    task_id: str,
    scheduler: TaskScheduler = Depends(get_scheduler),
):
    """
    获取指定任务详情
    """
    task = scheduler.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskResponse.from_task(task)


@router.patch("/tasks/{task_id}/cancel", response_model=TaskResponse, summary="取消任务")
async def cancel_task(
    task_id: str,
    scheduler: TaskScheduler = Depends(get_scheduler),
):
    """取消指定任务"""
    success = scheduler.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="无法取消该任务")
    
    task = scheduler.get_task(task_id)
    return TaskResponse.from_task(task)


@router.patch("/tasks/{task_id}/priority", response_model=TaskResponse, summary="调整优先级")
async def update_priority(
    task_id: str,
    request: UpdatePriorityRequest,
    scheduler: TaskScheduler = Depends(get_scheduler),
):
    """
    调整任务优先级，仅对待处理任务有效
    """
    success = scheduler.update_priority(task_id, request.priority)
    if not success:
        raise HTTPException(status_code=400, detail="无法调整该任务优先级")
    
    task = scheduler.get_task(task_id)
    return TaskResponse.from_task(task)


@router.post("/tasks/{task_id}/retry", response_model=TaskResponse, summary="重试任务")
async def retry_task(
    task_id: str,
    request: RetryTaskRequest = None,
    scheduler: TaskScheduler = Depends(get_scheduler),
):
    """
    重试失败的任务
    """
    task = scheduler.get_task(task_id)
    if not task or task.status not in [TaskStatus.FAILED]:
        raise HTTPException(status_code=400, detail="只有失败任务才能重试")

    # Update max retries if specified
    if request and request.max_additional_retries > 0:
        task.max_retries += request.max_additional_retries
    
    success = scheduler.schedule_retry(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="无法重试该任务")
    
    task = scheduler.get_task(task_id)
    return TaskResponse.from_task(task)


@router.delete("/tasks/{task_id}", summary="删除任务")
async def delete_task(
    task_id: str,
    scheduler: TaskScheduler = Depends(get_scheduler),
):
    """强制删除任务（仅限于取消/失败状态）"""
    task = scheduler.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.status not in [TaskStatus.CANCELLED, TaskStatus.FAILED]:
        raise HTTPException(
            status_code=400,
            detail="只能删除已取消或失败的任务",
        )
    
    # 从调度器删除
    scheduler._tasks.pop(task_id, None)
    return {"status": "deleted", "task_id": task_id}


# ========== Agent APIs ==========
@router.post("/agents", response_model=AgentResponse, summary="注册Agent")
async def register_agent(
    request: RegisterAgentRequest,
    pool: AgentPool = Depends(get_agent_pool),
):
    """注册新的Agent"""
    agent_id = f"agent-{uuid.uuid4().hex[:12]}"
    agent_info = request.to_agent_info(agent_id)
    
    success = pool.register(agent_info)
    if not success:
        raise HTTPException(status_code=400, detail="Agent ID冲突")
    
    return AgentResponse(
        id=agent_info.id,
        role=str(agent_info.role),
        name=agent_info.name,
        status=agent_info.status,
    )


@router.get("/agents", response_model=List[AgentResponse], summary="Agent列表")
async def list_agents(
    role_filter: Optional[AgentRole] = None,
    pool: AgentPool = Depends(get_agent_pool),
):
    """获取Agent列表"""
    if role_filter:
        agents = pool.get_agents_by_role(role_filter)
    else:
        agents = pool.get_all_agents()
    
    return [
        AgentResponse(
            id=a.id,
            role=str(a.role),
            name=a.name,
            status=a.status,
            current_task_id=a.current_task_id,
        )
        for a in agents
    ]


@router.get("/agents/{agent_id}", response_model=AgentResponse, summary="Agent详情")
async def get_agent(
    agent_id: str,
    pool: AgentPool = Depends(get_agent_pool),
):
    """获取指定Agent详情"""
    agent = pool.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent不存在")
    
    return AgentResponse(
        id=agent.id,
        role=str(agent.role),
        name=agent.name,
        status=agent.status,
        current_task_id=agent.current_task_id,
    )


@router.delete("/agents/{agent_id}", summary="注销Agent")
async def unregister_agent(
    agent_id: str,
    pool: AgentPool = Depends(get_agent_pool),
):
    """注销指定Agent"""
    success = pool.unregister(agent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Agent不存在")
    
    return {"status": "unregistered", "agent_id": agent_id}


# ========== Monitor APIs ==========
@router.get("/stats", response_model=StatsResponse, summary="系统统计")
async def get_stats(
    scheduler: TaskScheduler = Depends(get_scheduler),
    pool: AgentPool = Depends(get_agent_pool),
):
    """获取系统和调度统计"""
    return StatsResponse(
        tasks=scheduler.get_statistics(),
        agents=pool.get_statistics(),
    )


@router.post("/agents/{agent_id}/heartbeat", summary="Agent心跳")
async def agent_heartbeat(
    agent_id: str,
    pool: AgentPool = Depends(get_agent_pool),
):
    """Agent定期发送心跳"""
    success = pool.heartbeat(agent_id)
    return {"alive": success}