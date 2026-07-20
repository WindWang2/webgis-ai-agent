from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
import logging

from app.services.ws_service import manager, PERCEPTION_HANDLERS
from app.core.auth import verify_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])


@router.websocket("/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, token: str = Query(default="")):
    """WebSocket endpoint for real-time GIS data updates and bidirectional perception.

    Auth 是 optional：带 token 时验证（合法才放行），不带 token 也允许匿名连接。
    CHANGELOG 0.1.2 曾记载 "WebSocket connections support optional JWT token
    validation; anonymous connections allowed for compatibility" —— 之前的硬
    强制改动（空 token 直接 4001）与前端无 token 的事实冲突，导致所有 WS 连接
    都被拒绝。还原为 optional，待前端补齐登录后可再收紧（或改走短期 session ticket）。
    """
    if token:
        payload = verify_token(token)
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
