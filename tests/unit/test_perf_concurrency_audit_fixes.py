"""Unit tests verifying Performance, Concurrency & Resource Lifecycle fixes (#961-#965)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.ws_service import ConnectionManager


@pytest.mark.asyncio
async def test_ws_connection_manager_connect_and_disconnect():
    """ConnectionManager tracks active connections and cleans up properly on disconnect."""
    cm = ConnectionManager()
    ws = MagicMock()
    ws.accept = AsyncMock()
    
    await cm.connect(ws, "session-test")
    assert "session-test" in cm.active_connections
    assert ws in cm.active_connections["session-test"]

    cm.disconnect(ws, "session-test")
    assert "session-test" not in cm.active_connections


@pytest.mark.asyncio
async def test_ws_broadcast_evicts_dead_connections():
    """ConnectionManager.broadcast removes connections that timeout or raise errors."""
    cm = ConnectionManager()
    ws_alive = MagicMock()
    ws_alive.accept = AsyncMock()
    ws_alive.send_json = AsyncMock()

    ws_dead = MagicMock()
    ws_dead.accept = AsyncMock()
    ws_dead.send_json = AsyncMock(side_effect=RuntimeError("connection closed"))
    ws_dead.close = AsyncMock()

    await cm.connect(ws_alive, "session-broadcast")
    await cm.connect(ws_dead, "session-broadcast")
    assert len(cm.active_connections["session-broadcast"]) == 2

    await cm.broadcast("session-broadcast", {"msg": "hello"})
    assert len(cm.active_connections["session-broadcast"]) == 1
    assert ws_alive in cm.active_connections["session-broadcast"]
    assert ws_dead not in cm.active_connections["session-broadcast"]
