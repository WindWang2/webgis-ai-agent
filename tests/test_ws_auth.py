"""WebSocket authentication tests — token 是 optional（审计 S50 还原 CHANGELOG 原意）。

之前硬强制 token 导致前端无 token 的 WS 连接全部被拒（前端无登录基础设施）。
现在：空 token 放行（匿名），带 token 时验证合法性，无效 token 拒绝。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.routes.ws import router as ws_router
from app.core.auth import create_access_token


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(ws_router, prefix="/api/v1")
    return app


def test_ws_connect_without_token_is_accepted(app):
    """No token = 匿名连接允许（审计 S50：与前端无登录基础设施的现状对齐）。"""
    client = TestClient(app)
    with client.websocket_connect("/api/v1/ws/sess-no-token") as websocket:
        websocket.send_json({"event": "ping"})
        resp = websocket.receive_json()
        assert resp == {"event": "pong"}


def test_ws_connect_with_empty_token_is_accepted(app):
    """Empty token = 视同未带 token，匿名连接允许。"""
    client = TestClient(app)
    with client.websocket_connect("/api/v1/ws/sess-empty-token?token=") as websocket:
        websocket.send_json({"event": "ping"})
        resp = websocket.receive_json()
        assert resp == {"event": "pong"}


def test_ws_connect_with_invalid_token_is_rejected(app):
    """带 token 但签名无效 = 拒绝（code 4001）。"""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws/sess-invalid?token=invalid_jwt_signature"):
            pass
    assert exc_info.value.code == 4001


def test_ws_connect_with_valid_token_is_accepted(app):
    """Valid JWT = connection accepted, ping/pong works."""
    client = TestClient(app)
    valid_token = create_access_token({"sub": "user-123"})
    with client.websocket_connect(f"/api/v1/ws/sess-valid?token={valid_token}") as websocket:
        websocket.send_json({"event": "ping"})
        resp = websocket.receive_json()
        assert resp == {"event": "pong"}
