"""Unit tests for #1042: Multi-pod topology Pi turn ownership & 409 recovery guidance.

Validates:
1. Multi-pod active-turn coordination via Redis (Pod A registers, Pod B verifies).
2. Atomic deregistration on turn completion.
3. Route-level 409 Conflict returns structured payload with actionable recovery guidance.
4. Local-only fallback when Redis is unavailable or unconfigured.
"""
from unittest.mock import patch
import pytest
from fastapi import HTTPException

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import (
    is_active_pi_turn,
    register_active_pi_turn,
    unregister_active_pi_turn,
    PiToolRequest,
)
from app.api.routes.pi_tools import execute_tool
from app.services.chat.pi_turn_context import issue_turn_token


@pytest.mark.asyncio
async def test_multi_pod_active_turn_redis_coordination():
    """Pod A registers active turn in Redis; simulated Pod B (with empty local state) verifies it."""
    fake_redis_store = {}

    class FakeRedisClient:
        async def set(self, key, value, ex=None, nx=False, px=None):
            fake_redis_store[key] = value
            return True

        async def get(self, key):
            return fake_redis_store.get(key)

        async def eval(self, script, numkeys, key, token, *args):
            if fake_redis_store.get(key) == token:
                del fake_redis_store[key]
                return 1
            return 0

    fake_client = FakeRedisClient()

    with patch("app.services.distributed_lock.session_lock_registry._get_client", return_value=fake_client):
        # 1. Pod A registers active turn
        sid = "sess-multipod-1"
        tid = "turn-pod-a"
        await register_active_pi_turn(sid, tid)

        assert fake_redis_store.get(f"webgis:pi:active_turn:{sid}") == tid

        # 2. Local check (Pod A) is True
        assert await is_active_pi_turn(sid, tid) is True

        # 3. Simulate Pod B (clear local memory, but Redis holds the record)
        bridge_mod._active_turn_context = None
        assert await is_active_pi_turn(sid, tid) is True

        # 4. Foreign/mismatched turn is False on Pod B
        assert await is_active_pi_turn(sid, "turn-foreign") is False

        # 5. Turn completion on Pod A unregisters Redis key
        await unregister_active_pi_turn(sid, tid)
        assert f"webgis:pi:active_turn:{sid}" not in fake_redis_store
        assert await is_active_pi_turn(sid, tid) is False


@pytest.mark.asyncio
async def test_route_409_returns_actionable_recovery_guidance():
    """Route 409 Conflict returns structured detail dictionary with guidance."""
    secret = "test-secret"
    sid = "sess-inactive"
    tid = "turn-stale"
    token = issue_turn_token(secret, sid, tid)

    request = PiToolRequest(
        toolCallId="call-409",
        name="webgis_state_get",
        arguments={},
        turnToken=token,
    )

    with (
        patch("app.api.routes.pi_tools.get_bridge_secret", return_value=secret),
        patch("app.agent_pi_bridge.is_active_pi_turn", return_value=False),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await execute_tool(request, _secret=None)

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "TURN_CONTEXT_INACTIVE"
    assert "guidance" in detail
    assert "session affinity" in detail["guidance"].lower() or "session" in detail["guidance"].lower()


@pytest.mark.asyncio
async def test_local_only_mode_when_redis_client_none():
    """When Redis is not configured, register/unregister/is_active works solely in-process."""
    with patch("app.services.distributed_lock.session_lock_registry._get_client", return_value=None):
        sid = "sess-local"
        tid = "turn-local"

        await register_active_pi_turn(sid, tid)
        assert await is_active_pi_turn(sid, tid) is True
        assert await is_active_pi_turn(sid, "other") is False

        await unregister_active_pi_turn(sid, tid)
        assert await is_active_pi_turn(sid, tid) is False
