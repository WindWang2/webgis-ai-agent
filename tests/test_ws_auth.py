"""WebSocket authentication tests — SEC-03: WS now requires auth + session ownership.

审计 SEC-03：WS 端点之前允许匿名连接（无 token），且不校验 session 所有权。
现在：必须有合法 access token，且 session_id 属于该用户。WS 感知通道在前端
是死代码（useWebSocket 从未挂载），所以收紧认证不破坏活跃功能。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.routes.ws import router as ws_router
from app.core.auth import create_access_token


def _make_app_with_session(session_id: str = "sess-valid", user_id: str = "user-123",
                           user_token_version: int = 0):
    """Create a FastAPI app + temp DB with a user and owned session.

    Uses sync sqlite3 for setup (avoids event loop conflicts with TestClient).
    """
    import sqlite3
    import tempfile
    import os
    from contextlib import asynccontextmanager
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    import app.tools._utils as _utils
    import app.api.routes.ws as ws_module

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "ws_test.db")

    # Create schema + seed data via sync sqlite3 (no event loop needed)
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, org_id INTEGER, username TEXT, email TEXT,
        password_hash TEXT, full_name TEXT, avatar_url TEXT, role TEXT,
        is_active INTEGER, email_verified INTEGER, last_login TEXT,
        login_count INTEGER, token_version INTEGER DEFAULT 0,
        created_at TEXT, updated_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, user_id TEXT, title TEXT, owner_token TEXT,
        created_at TEXT, updated_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT, content TEXT,
        reasoning_content TEXT, tool_calls TEXT, tool_call_id TEXT,
        tool_result TEXT, created_at TEXT
    )""")
    conn.execute("INSERT INTO users (id, username, email, role, is_active, token_version) VALUES (?, ?, ?, ?, 1, ?)",
                 (user_id, "testuser", "test@example.com", "viewer", user_token_version))
    conn.execute("INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
                 (session_id, user_id, "Test"))
    conn.commit()
    conn.close()

    # Set up async session factory for the WS route
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    test_session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    @asynccontextmanager
    async def _test_db_session():
        async with test_session_factory() as s:
            yield s

    # Patch the source module that ws.py does `from ... import async_db_session` from
    _utils.async_db_session = _test_db_session

    # Patch rate limiter to always allow (tests run fast, same IP)
    class _NoOpLimiter:
        async def is_allowed(self, *a, **kw):
            return True
    async def _stub():
        return _NoOpLimiter()
    ws_module.get_rate_limiter = _stub

    app = FastAPI()
    app.include_router(ws_router, prefix="/api/v1")
    return app


def test_ws_connect_without_token_is_rejected():
    """SEC-03: No token = 拒绝（不再允许匿名连接）。"""
    app = _make_app_with_session()
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws/sess-valid"):
            pass
    assert exc_info.value.code == 4001


def test_ws_connect_with_empty_token_is_rejected():
    """SEC-03: Empty token = 拒绝。"""
    app = _make_app_with_session()
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws/sess-valid?token="):
            pass
    assert exc_info.value.code == 4001


def test_ws_connect_with_invalid_token_is_rejected():
    """Invalid token = 拒绝 (code 4001)。"""
    app = _make_app_with_session()
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws/sess-valid?token=invalid_jwt"):
            pass
    assert exc_info.value.code == 4001


def test_ws_connect_with_valid_token_and_owned_session_is_accepted():
    """SEC-03: Valid token + owned session = 连接成功, ping/pong works."""
    app = _make_app_with_session()
    client = TestClient(app)
    valid_token = create_access_token({"sub": "user-123", "role": "viewer"})
    with client.websocket_connect(f"/api/v1/ws/sess-valid?token={valid_token}") as websocket:
        websocket.send_json({"event": "ping"})
        resp = websocket.receive_json()
        assert resp == {"event": "pong"}


def test_ws_connect_with_valid_token_but_unowned_session_rejected():
    """SEC-03: Valid token but session belongs to another user = 拒绝 (code 4003)."""
    app = _make_app_with_session()
    client = TestClient(app)
    other_token = create_access_token({"sub": "user-456", "role": "viewer"})
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/ws/sess-valid?token={other_token}"):
            pass
    assert exc_info.value.code == 4003


def test_ws_connect_with_nonexistent_session_rejected():
    """SEC-03: Session doesn't exist = 拒绝 (code 4003)."""
    app = _make_app_with_session()
    client = TestClient(app)
    valid_token = create_access_token({"sub": "user-123", "role": "viewer"})
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/ws/nonexistent?token={valid_token}"):
            pass
    assert exc_info.value.code == 4003


def test_ws_connect_via_subprotocol_token_accepted():
    """#757: token 走 Sec-WebSocket-Protocol（不落 URL/访问日志）也能通过认证。"""
    app = _make_app_with_session()
    client = TestClient(app)
    valid_token = create_access_token({"sub": "user-123", "role": "viewer"})
    with client.websocket_connect(
        "/api/v1/ws/sess-valid", subprotocols=["bearer", valid_token]
    ) as websocket:
        assert websocket.accepted_subprotocol == "bearer"
        websocket.send_json({"event": "ping"})
        resp = websocket.receive_json()
        assert resp == {"event": "pong"}


def test_ws_connect_with_revoked_token_version_rejected():
    """#758: logout（token_version bump）后旧 access token 不得继续开 WS。"""
    # user-123 已 bump 到 ver=1（如 logout-everywhere）；token 仍是旧 ver=0
    app = _make_app_with_session(user_token_version=1)
    stale_token = create_access_token({"sub": "user-123", "role": "viewer"}, token_version=0)
    client = TestClient(app)
    # 拒绝发生在 accept 之前 —— TestClient 在握手处即抛 WebSocketDisconnect(4001)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/ws/sess-valid?token={stale_token}"):
            pass
    assert exc_info.value.code == 4001


def test_ws_connect_with_current_token_version_accepted():
    """#758 对照：ver 与 DB 一致的 token 正常连接。"""
    app = _make_app_with_session(user_token_version=2)
    fresh_token = create_access_token({"sub": "user-123", "role": "viewer"}, token_version=2)
    client = TestClient(app)
    with client.websocket_connect(f"/api/v1/ws/sess-valid?token={fresh_token}") as websocket:
        websocket.send_json({"event": "ping"})
        assert websocket.receive_json() == {"event": "pong"}
