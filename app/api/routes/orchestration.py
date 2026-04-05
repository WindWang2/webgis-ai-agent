from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from app.services.orchestration import AgentOrchestrator

router = APIRouter()

# 初始化编排器单例
orchestrator = AgentOrchestrator()

class OrchestrationRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None

class OrchestrationResponse(BaseModel):
    status: str
    data: Dict[str, Any]
    warnings: list[str] = []
    errors: list[str] = []
    execution_time: float = 0.0

@router.post("/execute", response_model=OrchestrationResponse, summary="执行GIS查询任务")
async def execute_task(request: OrchestrationRequest):
    """
    接收用户自然语言GIS查询请求，自动解析、调度多Agent执行并返回结果
    - **query**: 用户自然语言查询
    - **session_id**: 会话ID，用于多轮对话上下文管理
    - **context**: 额外上下文参数
    """
    try:
        result = await orchestrator.execute_async(request.query, request.session_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"任务执行失败: {str(e)}")

@router.delete("/context/{session_id}", summary="清除会话上下文")
async def clear_session_context(session_id: str):
    """清除指定会话的上下文信息"""
    orchestrator.clear_context(session_id)
    return {"status": "success", "message": f"会话 {session_id} 上下文已清除"}
