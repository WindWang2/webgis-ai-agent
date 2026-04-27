from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
import asyncio
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])

from app.services.ws_service import manager, PERCEPTION_HANDLERS
from app.core.auth import verify_token


@router.websocket("/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, token: str = Query(default="")):
    """WebSocket endpoint for real-time GIS data updates and bidirectional perception."""
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
                await asyncio.get_running_loop().run_in_executor(
                    None, handler, session_id, data.get("data", {})
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket, session_id)
    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e}")
        manager.disconnect(websocket, session_id)
