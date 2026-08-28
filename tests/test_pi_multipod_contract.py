"""Contract test for Issue #1042: Multi-pod Pi turn ownership & 409 recovery guidance."""
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.agent_pi_bridge import register_active_pi_turn
from app.core.bridge_secret import get_bridge_secret
from app.main import app
from app.services.chat.pi_turn_context import issue_turn_token, pi_turn_registry


@pytest.fixture
async def async_client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_multipod_turn_ownership_contract(async_client):
    """Verify that a turn token registered in Redis is accepted cross-pod and rejected when removed."""
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
    secret = get_bridge_secret()
    sid = "sess-contract-multipod"
    tid = "turn-contract-1"
    token = issue_turn_token(secret, sid, tid)

    with patch.object(pi_turn_registry, "_get_redis_client", return_value=fake_client):
        # 1. Pod A registers active turn in Redis
        await register_active_pi_turn(sid, tid)

        # 2. Simulate Pod B (empty in-process state) receiving callback
        pi_turn_registry._local_context = None

        mock_response = {
            "toolCallId": "call-1",
            "content": [{"type": "text", "text": "executed"}],
            "isError": False,
        }
        with patch("app.api.routes.pi_tools.dispatch_tool", new=AsyncMock(return_value=mock_response)):
            resp = await async_client.post(
                "/pi-tools/execute",
                headers={"X-Pi-Bridge-Secret": secret},
                json={
                    "toolCallId": "call-1",
                    "name": "webgis_state_get",
                    "arguments": {},
                    "turnToken": token,
                },
            )
            assert resp.status_code == 200

        # 3. When turn is removed from Redis, callback gets 409 Conflict with recovery guidance
        fake_redis_store.clear()
        resp409 = await async_client.post(
            "/pi-tools/execute",
            headers={"X-Pi-Bridge-Secret": secret},
            json={
                "toolCallId": "call-2",
                "name": "webgis_state_get",
                "arguments": {},
                "turnToken": token,
            },
        )
        assert resp409.status_code == 409
        detail = resp409.json()["detail"]
        assert detail["code"] == "TURN_CONTEXT_INACTIVE"
        assert "guidance" in detail
