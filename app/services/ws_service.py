import asyncio
import logging
from typing import Dict, List, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)

# F16: 单个 socket 的 send 有界等待上限（秒）。卡死（不报错但也不 flush）的连接
# 不能无限期拖住同 session 的其它连接；超时后按死连接处理并驱逐。
WS_SEND_TIMEOUT = 2.0

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
        connections = self.active_connections.get(session_id)
        if not connections:
            return
        # 快照迭代 + 并发发送（P2 round-2）：所有连接同一轮内并行 send，每路
        # send 各自被 WS_SEND_TIMEOUT 封顶 —— N 个卡死连接的总耗时约等于一个
        # 超时（顺序发送时是 N×WS_SEND_TIMEOUT）。发送失败/超时的连接记入
        # dead，本轮结束后统一驱逐（快照迭代语义不变：在循环里 remove 会跳过
        # 元素）。
        dead: List[WebSocket] = []

        async def _send(connection: WebSocket) -> None:
            try:
                await asyncio.wait_for(
                    connection.send_json(message), timeout=WS_SEND_TIMEOUT
                )
            except Exception as e:  # noqa: BLE001 — 含 TimeoutError；坏连接不能影响其余连接
                logger.error(f"Error sending WebSocket message: {e}")
                dead.append(connection)

        await asyncio.gather(*(_send(c) for c in list(connections)))
        for connection in dead:
            self.disconnect(connection, session_id)
            # P2 (round-2 review): 驱逐后尽力关闭底层 socket。路由可能正阻塞在
            # receive_json() 上等下一条消息 —— 只从 active_connections 移除而
            # 不 close，那条路由会一直挂在那里（僵尸连接，接收循环永不退出）。
            # close 本身也可能卡在坏连接上，所以同样加超时；失败仅记日志
            # （best-effort，关闭失败不阻塞广播）。
            try:
                await asyncio.wait_for(
                    connection.close(), timeout=WS_SEND_TIMEOUT
                )
            except Exception as e:  # noqa: BLE001 — close 是 best-effort
                logger.warning(f"Error closing evicted WebSocket: {e}")

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


async def handle_viewport_change(session_id: str, data: dict):
    viewport = {
        "center": data.get("center", [0, 0]),
        "zoom": data.get("zoom", 0),
        "bearing": data.get("bearing", 0),
        "pitch": data.get("pitch", 0),
    }
    await session_data_manager.set_map_state(session_id, "viewport", viewport)
    # Round 3: 后台预热视口地名，加 rate limit（每 session 每 5 秒最多 1 次）保护 Nominatim
    from app.services.viewport_naming import schedule_populate
    from app.core.rate_limiter import get_rate_limiter
    center = viewport.get("center") or [0, 0]
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        try:
            limiter = await get_rate_limiter()
            if await limiter.is_allowed(f"ws_viewport_populate:{session_id}", max_requests=1, window_seconds=5):
                schedule_populate(float(center[0]), float(center[1]))
        except (ValueError, TypeError):
            pass


async def handle_layer_toggled(session_id: str, data: dict):
    layer_id = data.get("layer_id")
    visible = data.get("visible")
    if layer_id is not None:
        await session_data_manager.update_layer_in_state(session_id, layer_id, {"visible": visible})
        await session_data_manager.append_event(session_id, "layer_toggled", data)


async def handle_layer_opacity(session_id: str, data: dict):
    layer_id = data.get("layer_id")
    opacity = data.get("opacity")
    if layer_id is not None and opacity is not None:
        await session_data_manager.update_layer_in_state(session_id, layer_id, {"opacity": opacity})


async def handle_layer_removed(session_id: str, data: dict):
    layer_id = data.get("layer_id")
    if layer_id:
        await session_data_manager.remove_layer_from_state(session_id, layer_id)
        await session_data_manager.append_event(session_id, "layer_removed", data)


async def handle_base_layer_changed(session_id: str, data: dict):
    name = data.get("name")
    if name:
        await session_data_manager.set_map_state(session_id, "base_layer", name)
        await session_data_manager.append_event(session_id, "base_layer_changed", data)


async def handle_mode_changed(session_id: str, data: dict):
    is_3d = data.get("is_3d")
    if is_3d is not None:
        await session_data_manager.set_map_state(session_id, "is_3d", is_3d)
        await session_data_manager.append_event(session_id, "mode_changed", data)


async def handle_upload(session_id: str, data: dict):
    await session_data_manager.append_event(session_id, "upload_completed", data)


async def handle_state_snapshot(session_id: str, data: dict):
    for k, v in data.items():
        await session_data_manager.set_map_state(session_id, k, v)


async def handle_layers_changed(session_id: str, data: dict):
    layers = data.get("layers")
    if layers is not None:
        await session_data_manager.set_map_state(session_id, "layers", layers)


async def handle_layers_reordered(session_id: str, data: dict):
    order = data.get("order")
    if order:
        await session_data_manager.set_map_state(session_id, "layer_order", order)
        await session_data_manager.append_event(session_id, "layers_reordered", data)


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
