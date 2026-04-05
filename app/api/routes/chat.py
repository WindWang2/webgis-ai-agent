"""Chat API Routes - SSE 流式对话"""
import json
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry
from app.tools.osm import register_osm_tools
from app.tools.geocoding import register_geocoding_tools
from app.tools.spatial import register_spatial_tools
from app.tools.remote_sensing import register_rs_tools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])

# 全局工具注册中心和对话引擎
registry = ToolRegistry()
register_geocoding_tools(registry)
register_osm_tools(registry)
register_spatial_tools(registry)
register_rs_tools(registry)
engine = ChatEngine(registry)


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    """聊天响应"""
    session_id: str
    content: str


@router.post("/completions", response_model=ChatResponse)
async def chat_completions(req: ChatRequest):
    """非流式对话接口"""
    try:
        result = await engine.chat(req.message, session_id=req.session_id)
        return ChatResponse(**result)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """SSE 流式对话接口"""
    async def event_generator():
        try:
            async for event in engine.chat_stream(req.message, session_id=req.session_id):
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
