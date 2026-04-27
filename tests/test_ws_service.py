"""WebSocket ConnectionManager tests"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.ws_service import ConnectionManager, handle_viewport_change, handle_layer_toggled


@pytest.fixture
def manager():
    return ConnectionManager()


@pytest.mark.asyncio
async def test_connect_adds_connection(manager):
    ws = AsyncMock()
    ws.accept = AsyncMock()
    await manager.connect(ws, "session-1")
    assert "session-1" in manager.active_connections
    assert ws in manager.active_connections["session-1"]


@pytest.mark.asyncio
async def test_disconnect_removes_connection(manager):
    ws = AsyncMock()
    ws.accept = AsyncMock()
    await manager.connect(ws, "session-1")
    manager.disconnect(ws, "session-1")
    assert "session-1" not in manager.active_connections


@pytest.mark.asyncio
async def test_disconnect_nonexistent_no_error(manager):
    ws = AsyncMock()
    manager.disconnect(ws, "nonexistent")
    assert "nonexistent" not in manager.active_connections


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_in_session(manager):
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    ws1.accept = AsyncMock()
    ws2.accept = AsyncMock()
    await manager.connect(ws1, "session-1")
    await manager.connect(ws2, "session-1")

    msg = {"event": "test", "data": "hello"}
    await manager.broadcast("session-1", msg)
    ws1.send_json.assert_awaited_once_with(msg)
    ws2.send_json.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_broadcast_nonexistent_session_no_error(manager):
    await manager.broadcast("nonexistent", {"event": "test"})


def test_handle_viewport_change():
    mock_manager = MagicMock()
    with patch("app.services.ws_service.session_data_manager", mock_manager):
        handle_viewport_change("sess-1", {"center": [116.4, 39.9], "zoom": 12, "bearing": 0, "pitch": 0})
        mock_manager.set_map_state.assert_called_once_with("sess-1", "viewport", {
            "center": [116.4, 39.9], "zoom": 12, "bearing": 0, "pitch": 0
        })


def test_handle_layer_toggled():
    mock_manager = MagicMock()
    with patch("app.services.ws_service.session_data_manager", mock_manager):
        handle_layer_toggled("sess-1", {"layer_id": "layer-1", "visible": True})
        mock_manager.update_layer_in_state.assert_called_once_with("sess-1", "layer-1", {"visible": True})
        mock_manager.append_event.assert_called_once()
