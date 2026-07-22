"""Chat API Route - SSE 流式对话"""
import logging
import os
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from app.core.auth import get_current_user_optional, require_admin
from app.services.chat_engine import ChatEngine
from app.services.history_service_async import AsyncHistoryService
from app.tools._utils import async_db_session
from app.tools.registry import ToolRegistry

from app.utils.sse import sse_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])

# 由 lifespan 在启动时注入
registry: ToolRegistry = None  # type: ignore[assignment]
engine: ChatEngine = None  # type: ignore[assignment]

# Feature flag: 通过环境变量切换新旧 Agent 系统
# true/1/yes 时使用 Pi 开源 agent (vendor/pi) 通过 RPC 调用
USE_NEW_AGENT = os.getenv("USE_NEW_AGENT", "").lower() in ("true", "1", "yes")
pi_bridge = None  # type: ignore[assignment]  # 由 lifespan 初始化


def get_engine() -> ChatEngine:
    """Return the ChatEngine instance, raising 503 if not yet initialized by lifespan.

    审计 S47：之前 raise RuntimeError -> 全局 exception handler 返回 500 +
    可能泄漏内部模块名。改为 503 让客户端知道是临时不可用（启动窗口）。
    """
    if engine is None:
        raise HTTPException(status_code=503, detail="Service starting up, please retry")
    return engine


def get_registry() -> ToolRegistry:
    """Return the ToolRegistry instance, raising 503 if not yet initialized."""
    if registry is None:
        raise HTTPException(status_code=503, detail="Service starting up, please retry")
    return registry


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
    user_id = _user.get("user_id")

    # Feature flag: 使用 Pi agent (vendor/pi) 通过 RPC 调用
    if USE_NEW_AGENT and pi_bridge is not None:
        try:
            result = await pi_bridge.prompt(req.message, session_id=req.session_id)
            return ChatResponse(session_id=result.get("sessionId", req.session_id or ""), content=result.get("content", ""))
        except Exception as e:
            logger.error(f"Pi bridge error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Agent error")

    # Legacy path: 使用 ChatEngine
    try:
        result = await get_engine().chat(
            req.message,
            session_id=req.session_id,
            map_state=req.map_state,
            skill_name=req.skill_name,
            user_id=user_id,
        )
        return ChatResponse(**result)
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/stream", response_model=None)
async def chat_stream(req: ChatRequest, _user: dict = Depends(get_current_user_optional)):
    """SSE 流式对话接口"""
    user_id = _user.get("user_id")

    # Feature flag: 使用 Pi agent (vendor/pi) 通过 RPC 调用
    if USE_NEW_AGENT and pi_bridge is not None:
        async def pi_event_generator():
            try:
                # TODO: implement streaming via Pi RPC
                result = await pi_bridge.prompt(req.message, session_id=req.session_id)
                yield sse_event("content", {"content": result.get("content", ""), "session_id": result.get("sessionId", req.session_id or "")})
                yield sse_event("done", {"session_id": result.get("sessionId", req.session_id or "")})
            except Exception as e:
                logger.error(f"Pi bridge stream error: {e}", exc_info=True)
                yield sse_event("error", {"error": "Internal server error"})

        return StreamingResponse(
            pi_event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    # Legacy path: 使用 ChatEngine
    async def event_generator():
        try:
            async for event in get_engine().chat_stream(
                req.message,
                session_id=req.session_id,
                map_state=req.map_state,
                skill_name=req.skill_name,
                user_id=user_id,
            ):
                yield event
        except Exception as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            yield sse_event("error", {"error": "Internal server error"})

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
async def list_sessions(
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    _user: dict = Depends(get_current_user_optional),
):
    """列出当前用户的历史会话；匿名调用方返回空列表（A2）。

    审计 A5：之前无 pagination，活跃用户可能积累数千 session 全量返回。
    加 limit (默认 50, 上限 200) + offset。注意：list_sessions 内部按
    updated_at desc，但当前 AsyncHistoryService.list_sessions 不支持 offset；
    简化处理：客户端用 limit 控制批次，offset 由客户端切页。
    """
    user_id = _user.get("user_id")
    async with async_db_session() as db:
        sessions = await AsyncHistoryService(db).list_sessions(limit=limit, user_id=user_id)
        # 审计 P1：offset=0 是合法的分页请求，不应跳过切片。
        # 之前 `if offset` 把 0 当 falsy 处理，导致第一页返回全量数据。
        paginated = sessions[offset:offset + limit]
        return {
            "total": len(sessions),  # 本次 query 的总数（不含 offset）
            "limit": limit,
            "offset": offset,
            "sessions": [
                {
                    "id": s.id,
                    "title": s.title,
                    "createdAt": s.created_at.timestamp() * 1000,
                    "updatedAt": s.updated_at.timestamp() * 1000,
                }
                for s in paginated
            ],
        }


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str, _user: dict = Depends(get_current_user_optional)):
    """获取会话详情（只读）— 受所有权检查保护（A2）。"""
    user_id = _user.get("user_id")
    async with async_db_session() as db:
        conv = await AsyncHistoryService(db).get_session(session_id, user_id=user_id)
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
async def get_session_map_state(
    session_id: str,
    _user: dict = Depends(get_current_user_optional),
):
    """Return persisted map state (viewport, layers) for session restoration.

    审计 S31：之前 _user 注入但未做所有权校验 —— 任何认证用户知道 session_id
    就能读取他人的 viewport/layers。复用 get_session_detail 的同款检查。
    """
    user_id = _user.get("user_id")
    async with async_db_session() as db:
        conv = await AsyncHistoryService(db).get_session(session_id, user_id=user_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Session not found")
    from app.services.session_data import session_data_manager
    state = await session_data_manager.get_map_state(session_id)
    return {"session_id": session_id, "map_state": state}


class MapStatePushRequest(BaseModel):
    viewport: Optional[dict] = None
    layers: Optional[list] = None
    base_layer: Optional[str] = None


@router.post("/sessions/{session_id}/map-state", status_code=204)
async def push_session_map_state(
    session_id: str,
    req: MapStatePushRequest,
    _user: dict = Depends(get_current_user_optional),
):
    """Persist live map state pushed by the frontend during agent execution.

    审计 S31：同 get_session_map_state，跨租户写入必须拒绝。
    """
    user_id = _user.get("user_id")
    async with async_db_session() as db:
        conv = await AsyncHistoryService(db).get_session(session_id, user_id=user_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Session not found")
    from app.services.session_data import session_data_manager
    if req.viewport:
        await session_data_manager.set_map_state(session_id, "viewport", req.viewport)
    if req.layers is not None:
        await session_data_manager.set_map_state(session_id, "layers", req.layers)
    if req.base_layer:
        await session_data_manager.set_map_state(session_id, "base_layer", req.base_layer)


@router.get("/skills")
async def list_skills_api(_user: dict = Depends(get_current_user_optional)):
    """列出可用的 .md 技能 — 需要认证（技能列表属于内部元数据）。"""
    from app.tools.skills import list_md_skills
    return {"skills": list_md_skills()}


@router.delete("/sessions/{session_id}")
async def clear_session(session_id: str, _user: dict = Depends(get_current_user_optional)):
    """清除会话（内存 + DB）— 受所有权检查保护（A2）。"""
    user_id = _user.get("user_id")

    # Feature flag: 使用 Pi agent
    if USE_NEW_AGENT and pi_bridge is not None:
        # TODO: implement session clearing via Pi RPC
        pass

    # Legacy path
    ok = await get_engine().clear_session(session_id, user_id=user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}


@router.get("/tools")
async def list_tools(_user: dict = Depends(get_current_user_optional)):
    """列出可用工具 — 需要认证（工具 schema 含 tier-3 危险工具）。"""
    return {"tools": get_registry().get_schemas()}


class ToolExecuteRequest(BaseModel):
    tool: str
    arguments: dict = {}
    session_id: Optional[str] = None
    # tier-3 工具（如 create_new_skill —— 写盘 + importlib.exec_module 等同 RCE）
    # 必须显式确认才执行（审计 S30）。
    confirm_destructive: bool = False


@router.post("/tools/execute", response_model=None)
async def execute_tool_direct(req: ToolExecuteRequest, _user: dict = Depends(require_admin)):
    """直接执行单个工具（非流式通过 chat）

    审计 S30：原本任何登录用户都能 dispatch 任意工具，包括 tier-3 的
    `create_new_skill`（写盘 + importlib.exec_module 等同 RCE）和
    `what_if_simulate`。改为 admin-only + tier-3 必须显式 confirm_destructive。
    """
    tool_name = req.tool
    args = req.arguments
    if not tool_name:
        raise HTTPException(status_code=400, detail="missing tool name")

    # tier 校验：catalog 把工具分为 1/2/3 层，3 = rare / heavy / destructive
    registry = get_registry()
    tier = registry.metadata(tool_name).get("tier", 1)
    if tier >= 3 and not req.confirm_destructive:
        raise HTTPException(
            status_code=403,
            detail=f"Tier-{tier} 工具 {tool_name} 需要显式 confirm_destructive=true",
        )

    try:
        result = await registry.dispatch(tool_name, args, session_id=req.session_id)
        return result
    except Exception as e:
        logger.error(f"Tool execute error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Tool execution failed")
