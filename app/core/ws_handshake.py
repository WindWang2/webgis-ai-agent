"""Bounded first-frame credentials for WebSockets without credential headers."""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from starlette.websockets import WebSocket

AUTH_FRAME_PROTOCOL = "auth-frame-v1"


async def receive_auth_frame(websocket: WebSocket) -> Optional[tuple[str, str]]:
    """Accept only the auth protocol, then authenticate before any subscription."""
    await websocket.accept(subprotocol=AUTH_FRAME_PROTOCOL)
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
        if len(raw) > 4096:
            raise ValueError("authentication frame too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("type") != "auth":
            raise ValueError("authentication frame required")
        mode, token = payload.get("mode"), payload.get("token")
        if mode not in {"bearer", "session"} or not isinstance(token, str) or not token or len(token) > 3072:
            raise ValueError("invalid authentication credentials")
        return mode, token
    except (ValueError, TypeError, asyncio.TimeoutError):
        await websocket.close(code=4001, reason="Invalid authentication frame")
        return None
