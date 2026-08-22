"""WebSocket ConnectionManager tests"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.ws_service import (
    ConnectionManager, handle_viewport_change, handle_layer_toggled,
    handle_state_snapshot, handle_layers_changed,
)


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


@pytest.mark.asyncio
async def test_handle_viewport_change():
    mock_manager = MagicMock()
    mock_manager.set_map_state = AsyncMock()
    with patch("app.services.ws_service.session_data_manager", mock_manager):
        await handle_viewport_change("sess-1", {"center": [116.4, 39.9], "zoom": 12, "bearing": 0, "pitch": 0})
        mock_manager.set_map_state.assert_called_once_with("sess-1", "viewport", {
            "center": [116.4, 39.9], "zoom": 12, "bearing": 0, "pitch": 0
        })


@pytest.mark.asyncio
async def test_handle_layer_toggled():
    mock_manager = MagicMock()
    mock_manager.update_layer_in_state = AsyncMock()
    mock_manager.append_event = AsyncMock()
    with patch("app.services.ws_service.session_data_manager", mock_manager):
        await handle_layer_toggled("sess-1", {"layer_id": "layer-1", "visible": True})
        mock_manager.update_layer_in_state.assert_called_once_with("sess-1", "layer-1", {"visible": True})
        mock_manager.append_event.assert_called_once()


# ── #811: WS 感知通道与 REST 观测同守 #643 desired-MapSpec 门 ─────────────

@pytest.mark.asyncio
async def test_state_snapshot_drops_internal_and_unknown_keys_811():
    """#811: _cartographic_* 内部键与未知键不得经 WS 覆盖服务端真相。"""
    mock_manager = MagicMock()
    mock_manager.set_map_state = AsyncMock()
    with patch("app.services.ws_service.session_data_manager", mock_manager):
        await handle_state_snapshot("sess-1", {
            "_cartographic_deleted": True,
            "_cartographic_review": {"fake": "review"},
            "layers": [{"id": "x"}],
            "viewport": {"center": [116.4, 39.9], "zoom": 12, "bearing": 0, "pitch": 0},
        })
    written = {c.args[1] for c in mock_manager.set_map_state.call_args_list}
    assert written == {"viewport"}, "只有感知白名单键可写"
    # 内部真相未被触碰
    assert all(not c.args[1].startswith("_")
               for c in mock_manager.set_map_state.call_args_list)


@pytest.mark.asyncio
async def test_layers_changed_wholesale_write_rejected_811():
    """#811: wholesale layers 替换与 REST 路由同样拒绝（#643）。"""
    mock_manager = MagicMock()
    mock_manager.set_map_state = AsyncMock()
    with patch("app.services.ws_service.session_data_manager", mock_manager):
        await handle_layers_changed("sess-1", {"layers": [{"id": "a"}, {"id": "b"}]})
    mock_manager.set_map_state.assert_not_called()
