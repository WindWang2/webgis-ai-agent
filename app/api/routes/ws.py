"""WebSocket endpoint for real-time GIS data updates.

审计 SEC-03：之前 WS 端点完全无认证 + 无 session 所有权校验 —— 任意客户端
知道 session_id 就能连接并写入他人的 map state（layer toggle/remove/snapshot）。

现在：1) 要求合法 access token；2) 校验 session 属于该用户；3) rate limit
keyed on client IP 而非 session_id（攻击者无法通过轮换 session_id 绕过）。

WS 感知通道在前端是死代码（useWebSocket 从未挂载），所以收紧认证不会破坏
任何活跃功能。
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
import logging

from app.services.ws_service import manager, PERCEPTION_HANDLERS
from app.core.auth import verify_token
from app.core.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])

_WS_RATE_LIMIT_MAX = 5
_WS_RATE_LIMIT_WINDOW = 60


@router.websocket("/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
):
    """WebSocket endpoint — requires valid access token + session ownership.

    审计 SEC-03：
    - 必须提供合法 access token（不再允许匿名连接）
    - session_id 必须属于 token 中的 user（防 IDOR）
    - rate limit per client IP（不是 per session_id）
    """
    # Rate limit per client IP
    from app.core.client_ip import client_ip_from

    client_ip = client_ip_from(websocket)
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"ws_connect:{client_ip}", _WS_RATE_LIMIT_MAX, _WS_RATE_LIMIT_WINDOW
    ):
        await websocket.close(code=4029, reason="Rate limit exceeded")
        return

    # SEC-03: 必须有合法 access token
    if not token:
        await websocket.close(code=4001, reason="Access token required")
        return

    payload = verify_token(token)
    if payload is None:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # 拒绝 refresh token 被当 access 用
    tok_type = payload.get("type")
    if tok_type is not None and tok_type != "access":
        await websocket.close(code=4001, reason="Wrong token type")
        return

    user_id = payload.get("sub")
    if not user_id:
        await websocket.close(code=4001, reason="Invalid token payload")
        return

    # SEC-03: 校验 session 所有权
    from app.tools._utils import async_db_session
    from app.services.history_service_async import AsyncHistoryService

    try:
        async with async_db_session() as db:
            # #525: ownership-only variant — WS connect must not load the full
            # message collection for every reconnect.
            conv = await AsyncHistoryService(db).get_session_meta(session_id, user_id)
        if conv is None:
            await websocket.close(code=4003, reason="Session not found or not owned by user")
            return
    except Exception as e:
        logger.error(f"WS session ownership check failed for {session_id}: {e}")
        # DB 不可用时拒绝连接（fail-closed）
        await websocket.close(code=4500, reason="Internal error during session validation")
        return

    await manager.connect(websocket, session_id)
    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("event")
            if event_type == "ping":
                await websocket.send_json({"event": "pong"})
            elif event_type in PERCEPTION_HANDLERS:
                handler = PERCEPTION_HANDLERS[event_type]
                await handler(session_id, data.get("data", {}))
    except WebSocketDisconnect:
        manager.disconnect(websocket, session_id)
    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e}")
        manager.disconnect(websocket, session_id)
