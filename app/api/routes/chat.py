"""Chat API Route - SSE 流式对话"""
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
from app.tools.chart import register_chart_tools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])

# 全局工具注册中心和对话引擎
registry = ToolRegistry()
register_geocoding_tools(registry)
register_osm_tools(registry)
register_spatial_tools(registry)
register_rs_tools(registry)
register_chart_tools(registry)
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


@router.get("/sessions")
async def list_sessions():
    """列出所有历史会话（最多1000条，按最近更新排序）"""
    sessions = engine._history.list_sessions()
    return {
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at.timestamp() * 1000,
                "updated_at": s.updated_at.timestamp() * 1000,
            }
            for s in sessions
        ]
    }


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """获取会话详情（只读）"""
    conv = engine._history.get_session(session_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": conv.id,
        "title": conv.title,
        "created_at": conv.created_at.timestamp() * 1000,
        "updated_at": conv.updated_at.timestamp() * 1000,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.timestamp() * 1000,
            }
            for m in conv.messages
            if m.role in ("user", "assistant")
        ],
    }


@router.delete("/sessions/{session_id}")
async def clear_session(session_id: str):
    """清除会话（内存 + DB）"""
    engine.clear_session(session_id)
    return {"status": "ok"}


@router.get("/tools")
async def list_tools():
    """列出可用工具"""
    return {"tools": registry.get_schemas()}


# 简单的工具结果存储（内存中）
_latest_tool_results: dict = {}


class ToolExecuteRequest(BaseModel):
    tool: str
    argument: dict = {}


@router.post("/tools/execute")
async def execute_tool_direct(req: ToolExecuteRequest):
    """直接执行单个工具（非流式通过 chat）"""
    tool_name = req.tool
    args = req.argument
    if not tool_name:
        return {"error": "missing tool name"}

    try:
        # 使用 registry.dispatch 自动处理参数
        result = await registry.dispatch(tool_name, args)
        # 保存最新结果供轮询
        _latest_tool_results[tool_name] = result
        return result
    except Exception as e:
        logger.error(f"Tool execute error: {e}")
        return {"error": str(e)}


@router.get("/tools/results")
async def get_latest_result(tool: str = ""):
    """获取最新的工具执行结果"""
    if tool and tool in _latest_tool_results:
        return _latest_tool_results[tool]
    return _latest_tool_results