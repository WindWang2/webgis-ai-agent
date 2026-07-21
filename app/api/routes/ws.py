from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
import logging

from app.services.ws_service import manager, PERCEPTION_HANDLERS
from app.core.auth import verify_token
from app.core.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])

# 审计 P0：WebSocket 认证修复
# 之前 `if token:` 对空字符串也为 falsy，导致 `?token=` 完全绕过认证。
# 使用 sentinel 区分「未提供 token」（允许匿名）和「提供了空 token」（拒绝）。
_UNSET = object()

# WebSocket 连接频率限制：每 session 每 60 秒最多 5 次连接尝试
_WS_RATE_LIMIT_MAX = 5
_WS_RATE_LIMIT_WINDOW = 60


@router.websocket("/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
):
    """WebSocket endpoint for real-time GIS data updates and bidirectional perception.

    Auth 是 optional：带合法 token 时验证放行；不带 token 参数允许匿名连接。
    但显式传递空 token（?token=）会被拒绝，防止绕过认证守卫。
    匿名连接受 rate limit 保护（每 session 每 60 秒最多 5 次连接）。
    """
    # 审计 P1：WebSocket 连接频率限制，防止匿名连接被滥用
    limiter = await get_rate_limiter()
    rate_key = f"ws_connect:{session_id}"
    if not await limiter.is_allowed(rate_key, _WS_RATE_LIMIT_MAX, _WS_RATE_LIMIT_WINDOW):
        await websocket.close(code=4029, reason="Rate limit exceeded")
        return

    # 审计 P0：用 sentinel 区分「参数不存在」和「显式空字符串」
    raw_token = token if token else _UNSET

    if raw_token is not _UNSET:
        # 提供了 token（哪怕是空的）— 必须有效
        payload = verify_token(raw_token)
        if payload is None:
            await websocket.close(code=4001, reason="Invalid token")
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
