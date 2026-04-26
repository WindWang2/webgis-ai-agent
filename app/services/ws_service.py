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


# ═══════════════════════════════════════════
# Perception Handlers — Agent-Everything
# ═══════════════════════════════════════════

from app.services.session_data import session_data_manager


def handle_viewport_change(session_id: str, data: dict):
    viewport = {
        "center": data.get("center", [0, 0]),
        "zoom": data.get("zoom", 0),
        "bearing": data.get("bearing", 0),
        "pitch": data.get("pitch", 0),
    }
    session_data_manager.set_map_state(session_id, "viewport", viewport)


def handle_layer_toggled(session_id: str, data: dict):
    layer_id = data.get("layer_id")
    visible = data.get("visible")
    if layer_id is not None:
        session_data_manager.update_layer_in_state(session_id, layer_id, {"visible": visible})
        session_data_manager.append_event(session_id, "layer_toggled", data)


def handle_layer_opacity(session_id: str, data: dict):
    layer_id = data.get("layer_id")
    opacity = data.get("opacity")
    if layer_id is not None and opacity is not None:
        session_data_manager.update_layer_in_state(session_id, layer_id, {"opacity": opacity})


def handle_layer_removed(session_id: str, data: dict):
    layer_id = data.get("layer_id")
    if layer_id:
        session_data_manager.remove_layer_from_state(session_id, layer_id)
        session_data_manager.append_event(session_id, "layer_removed", data)


def handle_base_layer_changed(session_id: str, data: dict):
    name = data.get("name")
    if name:
        session_data_manager.set_map_state(session_id, "base_layer", name)
        session_data_manager.append_event(session_id, "base_layer_changed", data)


def handle_mode_changed(session_id: str, data: dict):
    is_3d = data.get("is_3d")
    if is_3d is not None:
        session_data_manager.set_map_state(session_id, "is_3d", is_3d)
        session_data_manager.append_event(session_id, "mode_changed", data)


def handle_upload(session_id: str, data: dict):
    session_data_manager.append_event(session_id, "upload_completed", data)


def handle_state_snapshot(session_id: str, data: dict):
    for k, v in data.items():
        session_data_manager.set_map_state(session_id, k, v)


def handle_layers_changed(session_id: str, data: dict):
    layers = data.get("layers")
    if layers is not None:
        session_data_manager.set_map_state(session_id, "layers", layers)


def handle_layers_reordered(session_id: str, data: dict):
    order = data.get("order")
    if order:
        session_data_manager.set_map_state(session_id, "layer_order", order)
        session_data_manager.append_event(session_id, "layers_reordered", data)


PERCEPTION_HANDLERS = {
    "viewport_change": handle_viewport_change,
    "layer_toggled": handle_layer_toggled,
    "layer_opacity_changed": handle_layer_opacity,
    "layer_removed": handle_layer_removed,
    "base_layer_changed": handle_base_layer_changed,
    "mode_changed": handle_mode_changed,
    "upload_completed": handle_upload,
    "state_snapshot": handle_state_snapshot,
    "layers_changed": handle_layers_changed,
    "layers_reordered": handle_layers_reordered,
}
