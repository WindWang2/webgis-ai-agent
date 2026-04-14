import logging
from typing import Dict, List, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class ConnectionManager:
    """Handles active WebSocket connections for real-time GIS data broadcasting"""
    def __init__(self):
        # session_id -> list of active websockets
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append(websocket)
        logger.info(f"WebSocket connected for session: {session_id}")

    def disconnect(self, websocket: WebSocket, session_id: str):
        if session_id in self.active_connections:
            if websocket in self.active_connections[session_id]:
                self.active_connections[session_id].remove(websocket)
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]
        logger.info(f"WebSocket disconnected for session: {session_id}")

    async def broadcast(self, session_id: str, message: dict):
        if session_id in self.active_connections:
            for connection in self.active_connections[session_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Error sending WebSocket message: {e}")

# Global instance for the entire application
manager = ConnectionManager()

async def broadcast_ws_event(session_id: str, event_type: str, data: Any):
    """Entry point for other services to broadcast events to the frontend"""
    await manager.broadcast(session_id, {
        "event": event_type,
        "data": data
    })
