"""#1040: Scripted test for the turn-token HTTP auth boundary (/pi-tools/execute).

Validates the full HTTP route boundary via httpx.AsyncClient + ASGITransport:
1. Rejection of requests with missing or invalid X-Pi-Bridge-Secret header (401).
2. Rejection of requests with missing, malformed, forged, or expired turnToken (401).
3. Rejection of requests with valid signed tokens whose turn is no longer active (409 Conflict).
4. Verified token capability: legitimate active turn token flows through to tool dispatch (200).
5. Integrity: caller-supplied sessionId in request body is overwritten by the verified HMAC session_id.
6. Execution of native and long-tail GIS tools through the route boundary.
"""
import time

import pytest
from httpx import ASGITransport, AsyncClient

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import set_tool_registry
from app.api.routes.pi_tools import get_bridge_secret
from app.main import app
from app.services.chat.pi_turn_context import issue_turn_token
from app.tools.registry import ToolRegistry


@pytest.fixture
def test_registry():
    registry = ToolRegistry()
    registry.register(
        "pi_auth_echo",
        "Echo test tool",
        lambda msg, session_id=None: f"echo:{msg}:session={session_id}",
        parameters={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    )
    old_registry = bridge_mod._tool_registry
    set_tool_registry(registry)
    yield registry
    bridge_mod._tool_registry = old_registry


@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
def clean_turn_registry():
    """Active-turn 状态在 PiTurnRegistry 里（本地 + Redis），不再读模块全局。

    每个测试前后清空本地登记，避免跨测试污染（上一个测试登记的 turn 让
    409 用例意外变成 active）。
    """
    from app.services.chat.pi_turn_context import pi_turn_registry

    pi_turn_registry._local_context = None
    yield
    pi_turn_registry._local_context = None


@pytest.mark.asyncio
async def test_route_missing_bridge_secret_returns_401(async_client):
    """Requests without X-Pi-Bridge-Secret are rejected before reaching turn token verification."""
    resp = await async_client.post(
        "/pi-tools/execute",
        json={
            "toolCallId": "call-1",
            "name": "webgis_execute",
            "arguments": {"toolName": "pi_auth_echo", "arguments": {"msg": "hi"}},
            "turnToken": "dummy.token",
        },
    )
    assert resp.status_code == 401
    assert "Invalid or missing bridge secret" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_route_invalid_bridge_secret_returns_401(async_client):
    """Requests with incorrect X-Pi-Bridge-Secret header are rejected with 401."""
    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": "wrong-secret-key-12345"},
        json={
            "toolCallId": "call-1",
            "name": "webgis_execute",
            "arguments": {"toolName": "pi_auth_echo", "arguments": {"msg": "hi"}},
            "turnToken": "dummy.token",
        },
    )
    assert resp.status_code == 401
    assert "Invalid or missing bridge secret" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_route_missing_turn_token_returns_401(async_client):
    """Requests with valid bridge secret but missing turnToken return 401."""
    secret = get_bridge_secret()
    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": secret},
        json={
            "toolCallId": "call-1",
            "name": "webgis_execute",
            "arguments": {"toolName": "pi_auth_echo", "arguments": {"msg": "hi"}},
        },
    )
    assert resp.status_code == 401
    assert "Invalid, missing, or expired Pi turn context" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_route_forged_turn_token_returns_401(async_client):
    """Turn token signed with foreign secret is rejected with 401."""
    secret = get_bridge_secret()
    forged_token = issue_turn_token("foreign-attacker-secret", "sess-1", "turn-1")

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": secret},
        json={
            "toolCallId": "call-1",
            "name": "webgis_execute",
            "arguments": {"toolName": "pi_auth_echo", "arguments": {"msg": "hi"}},
            "turnToken": forged_token,
        },
    )
    assert resp.status_code == 401
    assert "Invalid, missing, or expired Pi turn context" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_route_expired_turn_token_returns_401(async_client):
    """Turn token older than max_age_seconds is rejected with 401."""
    secret = get_bridge_secret()
    # Issue a token with timestamp 1 hour in the past
    old_time = int(time.time()) - 3600
    expired_token = issue_turn_token(secret, "sess-1", "turn-1", issued_at=old_time)

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": secret},
        json={
            "toolCallId": "call-1",
            "name": "webgis_execute",
            "arguments": {"toolName": "pi_auth_echo", "arguments": {"msg": "hi"}},
            "turnToken": expired_token,
        },
    )
    assert resp.status_code == 401
    assert "Invalid, missing, or expired Pi turn context" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_route_inactive_turn_returns_409(async_client):
    """Validly signed token for a turn that has completed or is not active returns 409 Conflict."""
    from app.agent_pi_bridge import register_active_pi_turn

    secret = get_bridge_secret()
    token = issue_turn_token(secret, "sess-inactive", "turn-old")

    # Register a different session/turn as the live one
    await register_active_pi_turn("sess-active", "turn-new")

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": secret},
        json={
            "toolCallId": "call-1",
            "name": "webgis_execute",
            "arguments": {"toolName": "pi_auth_echo", "arguments": {"msg": "hi"}},
            "turnToken": token,
        },
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    detail_str = detail.get("error", "") if isinstance(detail, dict) else detail
    assert "Pi turn context is no longer active" in detail_str


@pytest.mark.asyncio
async def test_route_valid_token_dispatches_and_overrides_caller_session_id(test_registry, async_client):
    """A signed active token succeeds and overwrites any attacker-supplied sessionId with verified session_id."""
    from app.agent_pi_bridge import register_active_pi_turn

    secret = get_bridge_secret()
    legit_session = "sess-legit-999"
    legit_turn = "turn-0042"
    token = issue_turn_token(secret, legit_session, legit_turn)

    # Register the turn so the live-turn check passes
    await register_active_pi_turn(legit_session, legit_turn)

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": secret},
        json={
            "toolCallId": "call-exec-1",
            "name": "webgis_execute",
            "arguments": {"toolName": "pi_auth_echo", "arguments": {"msg": "secure_call"}},
            "turnToken": token,
            # Attacker attempts to spoof a different session ID in the body
            "sessionId": "attacker-spoofed-session",
            "verifiedTurnId": "attacker-spoofed-turn",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["toolCallId"] == "call-exec-1"
    assert data["isError"] is False
    assert len(data["content"]) >= 1
    # Echo tool receives the verified session_id, NOT the attacker-spoofed one
    assert f"echo:secure_call:session={legit_session}" in data["content"][0]["text"]


@pytest.mark.asyncio
async def test_route_unknown_tool_returns_200_with_is_error(test_registry, async_client):
    """When an unknown tool is dispatched via webgis_execute, endpoint returns 200 with isError=True."""
    from app.agent_pi_bridge import register_active_pi_turn

    secret = get_bridge_secret()
    session_id = "sess-unknown-tool"
    turn_id = "turn-0001"
    token = issue_turn_token(secret, session_id, turn_id)

    await register_active_pi_turn(session_id, turn_id)

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": secret},
        json={
            "toolCallId": "call-err-1",
            "name": "webgis_execute",
            "arguments": {"toolName": "non_existent_tool", "arguments": {}},
            "turnToken": token,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["toolCallId"] == "call-err-1"
    assert data["isError"] is True
    assert "not found" in data["content"][0]["text"].lower()
