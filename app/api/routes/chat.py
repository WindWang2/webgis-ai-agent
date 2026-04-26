"""Chat API Route - SSE 流式对话"""
import json
import logging
from collections import OrderedDict
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from app.core.auth import get_current_user, get_current_user_optional
from app.services.chat_engine import ChatEngine
from app.services.history_service import HistoryService
from app.core.database import SessionLocal
from app.tools.registry import ToolRegistry
from app.tools.osm import register_osm_tools
from app.tools.geocoding import register_geocoding_tools
from app.tools.spatial import register_spatial_tools
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.layer_manager import register_layer_management_tools
from app.tools.remote_sensing import register_rs_tools
from app.tools.chart import register_chart_tools
from app.tools.cartography import register_cartography_tools
from app.tools.nature_resources import register_nature_resource_tools
from app.tools.upload_tools import register_upload_tools
from app.tools.web_crawler import register_crawler_tools
from app.tools.chinese_maps import register_chinese_map_tools
from app.tools.report import register_report_tools
from app.tools.skills import load_skills, register_skill_tools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])

# 全局工具注册中心和对话引擎
registry = ToolRegistry()
register_geocoding_tools(registry)
register_osm_tools(registry)
register_spatial_tools(registry)
register_advanced_spatial_tools(registry)
register_layer_management_tools(registry)
register_rs_tools(registry)
register_chart_tools(registry)
register_cartography_tools(registry)
register_nature_resource_tools(registry)
register_upload_tools(registry)
register_crawler_tools(registry)
register_chinese_map_tools(registry)
register_report_tools(registry)
register_skill_tools(registry)

# 加载动态技能 (app/skills/*.py)
load_skills(registry)

engine = ChatEngine(registry)


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None
    map_state: Optional[dict] = Field(None, description="当前的地图状态（视角、图层等）")


class ChatResponse(BaseModel):
    """聊天响应"""
    session_id: str
    content: str


@router.post("/completions", response_model=ChatResponse)
async def chat_completions(req: ChatRequest, _user: dict = Depends(get_current_user_optional)):
    """非流式对话接口"""
    try:
        result = await engine.chat(req.message, session_id=req.session_id, map_state=req.map_state)
        return ChatResponse(**result)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream")
async def chat_stream(req: ChatRequest, _user: dict = Depends(get_current_user_optional)):
    """SSE 流式对话接口"""
    async def event_generator():
        try:
            async for event in engine.chat_stream(req.message, session_id=req.session_id, map_state=req.map_state):
                yield event
        except Exception as e:
            logger.error(f"Stream error: {e}")
            error_data = json.dumps({"error": str(e)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n"

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
    db = SessionLocal()
    try:
        sessions = HistoryService(db).list_sessions()
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
    finally:
        db.close()


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str, _user: dict = Depends(get_current_user_optional)):
    """获取会话详情（只读）"""
    db = SessionLocal()
    try:
        conv = HistoryService(db).get_session(session_id)
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
    finally:
        db.close()


@router.delete("/sessions/{session_id}")
async def clear_session(session_id: str, _user: dict = Depends(get_current_user_optional)):
    """清除会话（内存 + DB）"""
    engine.clear_session(session_id)
    return {"status": "ok"}


@router.get("/tools")
async def list_tools():
    """列出可用工具"""
    return {"tools": registry.get_schemas()}


# 工具结果存储（内存中，按 session 隔离）
_tool_results_by_session: dict = {}


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