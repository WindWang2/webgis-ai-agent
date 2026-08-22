"""Chat API 测试

审计 T1：之前用 importlib bypass broken __init__.py。__init__.py 现在已健康
（循环 import 已修），改为直接 import。
"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI

from app.api.routes import chat as _chat_mod
from app.api.routes.chat import router as _router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(_router, prefix="/api")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_list_tools(client):
    with patch.object(_chat_mod, "registry", MagicMock(get_schemas=lambda: [])):
        # A-7: the tool catalog (incl. tier-3 schemas) requires authentication.
        resp = await client.get("/api/chat/tools")
        assert resp.status_code == 401
        from app.core.auth import create_access_token
        token = create_access_token({"sub": "tools-user", "username": "t", "role": "viewer"})
        resp = await client.get(
            "/api/chat/tools", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data


@pytest.mark.asyncio
async def test_chat_completions(client):
    mock_msg = MagicMock()
    mock_msg.content = "你好！"
    mock_msg.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_msg)]

    mock_registry = MagicMock()
    mock_registry.get_schemas.return_value = []
    mock_registry.tools = {}

    mock_engine = MagicMock()
    mock_engine.chat = AsyncMock(return_value={"session_id": "test-sid", "content": "你好！"})

    with patch.object(_chat_mod, "registry", mock_registry), \
         patch.object(_chat_mod, "engine", mock_engine):
        resp = await client.post("/api/chat/completions", json={"message": "你好"})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["content"]


@pytest.mark.asyncio
async def test_clear_session(client, app):
    # clear_session is now guarded by require_owned_session (SEC-08 ownership
    # check), which queries the DB and returns 404 when the session is absent.
    # FastAPI resolves Depends() at route-registration time (module import),
    # so patching the module attribute does not affect the already-registered
    # dependency. Use FastAPI's dependency_overrides to replace it.
    from app.api.routes.chat import require_owned_session
    from app.models.db_model import Conversation

    mock_engine = MagicMock()
    mock_engine.clear_session = AsyncMock(return_value=True)  # A2: clear_session 返回 bool
    mock_conv = MagicMock(spec=Conversation)

    async def _override_owned_session():
        return mock_conv

    app.dependency_overrides[require_owned_session] = _override_owned_session
    try:
        with patch("app.api.routes.chat.get_engine", return_value=mock_engine):
            resp = await client.delete("/api/chat/sessions/test-session")
            assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()


# ── #791 (F-E-3): session-lock contention -> scoped 503, never a 500 ──────


class _BusyLock:
    """Async CM whose acquire always times out — mirrors distributed_lock's
    10s acquire deadline raising TimeoutError under cross-pod contention."""

    async def __aenter__(self):
        raise TimeoutError("session lock contention: could not acquire in 10s")

    async def __aexit__(self, *exc):
        return False


class _BusyLockRegistry:
    def lock(self, session_id: str) -> _BusyLock:
        return _BusyLock()


def _assert_busy_503(exc_info) -> None:
    from fastapi import HTTPException

    assert isinstance(exc_info.value, HTTPException)
    assert exc_info.value.status_code == 503, (
        "#791: lock contention must surface as a scoped 503 (session busy), "
        f"got {exc_info.value.status_code}"
    )
    assert "busy" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_cartographic_observation_lock_timeout_returns_503(monkeypatch):
    """#791: the observation POST endpoint must translate a session-lock
    TimeoutError into a 503 'session busy' body, not a generic 500."""
    from fastapi import HTTPException

    from app.api.routes.chat import (
        CartographicRuntimeObservationRequest,
        push_cartographic_runtime_observation,
    )

    monkeypatch.setattr(_chat_mod, "session_lock_registry", _BusyLockRegistry())
    req = CartographicRuntimeObservationRequest(
        client_generation=1,
        mapspec_fingerprint="f" * 64,
        layers=[],
        viewport={},
        style_loaded=True,
    )
    with pytest.raises(HTTPException) as exc_info:
        await push_cartographic_runtime_observation("sess-busy", req, _conv=object())
    _assert_busy_503(exc_info)


@pytest.mark.asyncio
async def test_map_action_ack_lock_timeout_returns_503(monkeypatch):
    """#791: the map-action-ACK endpoint must translate a session-lock
    TimeoutError into a 503 (client retries; first-terminal-wins idempotency
    makes the retry safe)."""
    from fastapi import HTTPException

    from app.api.routes.chat import MapActionAckRequest, push_map_action_acks

    monkeypatch.setattr(_chat_mod, "session_lock_registry", _BusyLockRegistry())

    limiter = MagicMock()

    async def _is_allowed(key, max_requests, window_seconds):
        return True

    limiter.is_allowed = _is_allowed

    async def _get_limiter():
        return limiter

    monkeypatch.setattr(_chat_mod, "get_rate_limiter", _get_limiter)

    request = MagicMock()
    request.headers = {}
    request.client = None
    acks = MapActionAckRequest(acks=[{
        "action_id": "ma-1", "command": "fly_to", "status": "succeeded",
    }])
    with pytest.raises(HTTPException) as exc_info:
        await push_map_action_acks("sess-busy", acks, request=request, _conv=object())
    _assert_busy_503(exc_info)


@pytest.mark.asyncio
async def test_clear_session_lock_timeout_returns_503(monkeypatch):
    """#791: DELETE /sessions/{id} under lock contention must return a scoped
    503 (nothing was deleted — the tombstone write happens under the lock)."""
    from fastapi import HTTPException

    monkeypatch.setattr(_chat_mod, "session_lock_registry", _BusyLockRegistry())
    with pytest.raises(HTTPException) as exc_info:
        await _chat_mod.clear_session(
            "sess-busy", _user={}, owner_token=None, _conv=object()
        )
    _assert_busy_503(exc_info)


def _patch_turn_start_busy(monkeypatch):
    """Make the Pi-path turn-start observation write time out on the lock."""

    async def _busy(session_id, map_state):
        raise TimeoutError("session lock contention: could not acquire in 10s")

    async def _noop_guard(db, session_id, user_id, owner_token):
        return None

    monkeypatch.setattr(_chat_mod, "_record_frontend_cartographic_observation", _busy)
    monkeypatch.setattr(_chat_mod, "_guard_body_session", _noop_guard)
    monkeypatch.setattr(_chat_mod, "USE_NEW_AGENT", True)
    fake_bridge = MagicMock()
    fake_bridge._process_died = False
    fake_bridge.prompt = AsyncMock()
    monkeypatch.setattr(_chat_mod, "pi_bridge", fake_bridge, raising=False)
    return fake_bridge


@pytest.mark.asyncio
async def test_chat_completions_turn_start_observation_lock_timeout_503(monkeypatch):
    """#791: POST /chat/completions must scope a turn-start observation lock
    TimeoutError to 503 — and never start the Pi turn."""
    from fastapi import HTTPException

    fake_bridge = _patch_turn_start_busy(monkeypatch)
    req = _chat_mod.ChatRequest(message="hi", session_id="sess-busy")
    with pytest.raises(HTTPException) as exc_info:
        await _chat_mod.chat_completions(
            req, request=MagicMock(), _user={}, owner_token=None, db=None
        )
    _assert_busy_503(exc_info)
    fake_bridge.prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_stream_turn_start_observation_lock_timeout_503(monkeypatch):
    """#791: POST /chat/stream must scope a turn-start observation lock
    TimeoutError to 503 before any SSE stream starts."""
    from fastapi import HTTPException

    fake_bridge = _patch_turn_start_busy(monkeypatch)
    req = _chat_mod.ChatRequest(message="hi", session_id="sess-busy")
    with pytest.raises(HTTPException) as exc_info:
        await _chat_mod.chat_stream(req, _user={}, owner_token=None, db=None)
    _assert_busy_503(exc_info)
    fake_bridge.stream_prompt.assert_not_called()
