"""
AI 对话 API 路由 - 支持工具调用和地图联动
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, AsyncGenerator
import asyncio
import json
import uuid

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry
from app.tools.geocoding import register_geocoding_tools
from app.tools.osm import register_osm_tools
from app.tools.spatial import register_spatial_tools
from app.tools.remote_sensing import register_rs_tools
from app.tools.map_action import register_map_action_tools

logger = __import__("logging").getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])

# 全局工具注册中心和对话引擎
registry = ToolRegistry()
register_geocoding_tools(registry)
register_osm_tools(registry)
register_spatial_tools(registry)
register_rs_tools(registry)
register_map_action_tools(registry)  # 注册地图操作工具

engine = ChatEngine(registry)


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    """聊天响应"""
    session_id: str
    content: str
    map_action: Optional[dict] = None  # 地图操作指令


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    处理 AI 对话请求，识别意图并返回结果
    支持地图联动：工具调用结果包含 map_action 前端可解析
    """
    session_id = request.session_id or str(uuid.uuid4())
    
    try:
        result = await engine.chat(request.message, session_id=session_id)
        content = result.get("content", "")
        map_action = result.get("map_action")  # 提取地图操作指令
        
        return ChatResponse(
            session_id=session_id,
            content=content,
            map_action=map_action
        )
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """
    SSE 流式对话接口
    支持实时返回工具调用过程和最终结果
    """
    session_id = request.session_id or str(uuid.uuid4())

    async def event_generator():
        try:
            async for event in engine.chat_stream(request.message, session_id=session_id):
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


@router.delete("/sessions/{session_id}")
async def clear_session(session_id: str):
    """清除会话"""
    engine.clear_session(session_id)
    return {"status": "ok"}


@router.get("/tools")
async def list_tools():
    """列出可用工具"""
    return {"tools": registry.get_schemas()}