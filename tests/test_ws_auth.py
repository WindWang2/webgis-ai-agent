"""WebSocket authentication tightening integration tests.

Part of P1-3 Phase-2: WS 认证收紧 (要求 verify_token).
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


def test_ws_connect_without_token_is_rejected(app):
    """If no token query parameter is provided, the connection must be rejected with code 4001."""
    client = TestClient(app)
    # Connecting to the WS endpoint without any token parameter
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws/sess-no-token"):
            pass
    assert exc_info.value.code == 4001


def test_ws_connect_with_invalid_token_is_rejected(app):
    """If an invalid token is provided, the connection must be rejected with code 4001."""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws/sess-invalid-token?token=invalid_jwt_signature"):
            pass
    assert exc_info.value.code == 4001


def test_ws_connect_with_valid_token_is_accepted(app):
    """If a valid JWT token is provided, the connection must be accepted successfully."""
    client = TestClient(app)
    valid_token = create_access_token({"sub": "user-123"})
    with client.websocket_connect(f"/api/v1/ws/sess-valid-token?token={valid_token}") as websocket:
        # A valid connection can receive and send messages
        websocket.send_json({"event": "ping"})
        resp = websocket.receive_json()
        assert resp == {"event": "pong"}
