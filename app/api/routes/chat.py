"""Chat API Route - SSE 流式对话"""
import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from app.core.auth import get_current_user, get_current_user_optional
from app.services.chat_engine import ChatEngine
from app.services.history_service import HistoryService
from app.services.history_service_async import AsyncHistoryService
from app.tools._utils import async_db_session
from app.tools.registry import ToolRegistry

from app.utils.sse import sse_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])

# 由 lifespan 在启动时注入
registry: ToolRegistry = None  # type: ignore[assignment]
engine: ChatEngine = None  # type: ignore[assignment]


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None
    map_state: Optional[dict] = Field(None, description="当前的地图状态（视角、图层等）")
    skill_name: Optional[str] = Field(None, description="要激活的技能名称")


class ChatResponse(BaseModel):
    """聊天响应"""
    session_id: str
    content: str


@router.post("/completions", response_model=ChatResponse)
async def chat_completions(req: ChatRequest, _user: dict = Depends(get_current_user_optional)):
    """非流式对话接口"""
    try:
        result = await engine.chat(req.message, session_id=req.session_id, map_state=req.map_state, skill_name=req.skill_name)
        return ChatResponse(**result)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream")
async def chat_stream(req: ChatRequest, _user: dict = Depends(get_current_user_optional)):
    """SSE 流式对话接口"""
    async def event_generator():
        try:
            async for event in engine.chat_stream(req.message, session_id=req.session_id, map_state=req.map_state, skill_name=req.skill_name):
                yield event
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield sse_event("error", {"error": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/sessions")
async def list_sessions(_user: dict = Depends(get_current_user_optional)):
    """列出所有历史会话（最多1000条，按最近更新排序）"""
    async with async_db_session() as db:
        sessions = await AsyncHistoryService(db).list_sessions()
        return {
            "sessions": [
                {
                    "id": s.id,
                    "title": s.title,
                    "createdAt": s.created_at.timestamp() * 1000,
                    "updatedAt": s.updated_at.timestamp() * 1000,
                }
                for s in sessions
            ]
        }


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str, _user: dict = Depends(get_current_user_optional)):
    """获取会话详情（只读）"""
    async with async_db_session() as db:
        conv = await AsyncHistoryService(db).get_session(session_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Session not found")
        return {
            "id": conv.id,
            "title": conv.title,
            "createdAt": conv.created_at.timestamp() * 1000,
            "updatedAt": conv.updated_at.timestamp() * 1000,
            "messages": [
                {
                    "id": str(m.id),
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.created_at.timestamp() * 1000,
                }
                for m in conv.messages
                if m.role in ("user", "assistant")
            ],
        }


@router.get("/sessions/{session_id}/map-state")
async def get_session_map_state(session_id: str):
    """Return persisted map state (viewport, layers) for session restoration."""
    from app.services.session_data import session_data_manager
    state = session_data_manager.get_map_state(session_id)
    return {"session_id": session_id, "map_state": state}


@router.get("/skills")
async def list_skills_api():
    """列出可用的 .md 技能"""
    from app.tools.skills import list_md_skills
    return {"skills": list_md_skills()}


@router.delete("/sessions/{session_id}")
async def clear_session(session_id: str, _user: dict = Depends(get_current_user_optional)):
    """清除会话（内存 + DB）"""
    await engine.clear_session(session_id)
    return {"status": "ok"}


@router.get("/tools")
async def list_tools():
    """列出可用工具"""
    return {"tools": registry.get_schemas()}


class ToolExecuteRequest(BaseModel):
    tool: str
    arguments: dict = {}
    session_id: Optional[str] = None


@router.post("/tools/execute")
async def execute_tool_direct(req: ToolExecuteRequest, _user: dict = Depends(get_current_user)):
    """直接执行单个工具（非流式通过 chat）"""
    tool_name = req.tool
    args = req.arguments
    if not tool_name:
        return {"error": "missing tool name"}

    try:
        result = await registry.dispatch(tool_name, args, session_id=req.session_id)
        return result
    except Exception as e:
        logger.error(f"Tool execute error: {e}")
        return {"error": str(e)}


@router.get("/tools/results")
async def get_latest_result(session_id: str = "", tool: str = "", _user: dict = Depends(get_current_user)):
    """获取指定 session 的工具执行结果（需提供 session_id）"""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    session_results = _tool_results_by_session.get(session_id, {})
    if tool and tool in session_results:
        return {tool: session_results[tool]}
    return session_results


# 模块级缓存（会话 -> 工具结果），由工具执行时写入
try:
    _tool_results_by_session: dict = {}
except NameError:
    _tool_results_by_session = {}
