"""A Redis-active signature does not authorize borrowing another worker/turn's caller."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import app.agent_pi_bridge as bridge
from app.api.routes import pi_tools
from app.core.exception import unified_http_exception_handler
from app.services.chat.pi_turn_context import issue_turn_token, pi_turn_registry

SECRET = "synthetic-local-turn-test-secret"
SID = "local-turn-test-session"
TID = "original-turn"


@pytest.fixture
async def local_callback(monkeypatch):
    app = FastAPI()
    app.include_router(pi_tools.router)
    app.add_exception_handler(HTTPException, unified_http_exception_handler)
    monkeypatch.setattr(pi_tools, "get_bridge_secret", lambda: SECRET)
    monkeypatch.setattr(bridge, "get_tool_registry", lambda: object())
    monkeypatch.delitem(bridge._active_turns, SID, raising=False)
    remote_active = AsyncMock(return_value=True)
    monkeypatch.setattr(pi_turn_registry, "is_active", remote_active)
    bound = AsyncMock(return_value=bridge.PiToolResponse(toolCallId="local-call", content=[]))
    monkeypatch.setattr(bridge, "_dispatch_tool_bound", bound)
    token = issue_turn_token(SECRET, SID, TID)
    payload = {
        "toolCallId": "local-call", "name": "webgis_state_get", "arguments": {},
        "sessionId": "forged-session", "verifiedTurnId": "forged-turn", "turnToken": token,
    }
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(client=client, bound=bound, remote_active=remote_active,
                              payload=payload, token=token)


def successor():
    return bridge._ActiveTurnEntry(
        session_id=SID, turn_id="successor-turn", token=None,
        caller=bridge._PiCallerIdentity(user_id="successor-user", owner_token="private-successor-cap"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["absent", "successor", "handoff"])
@pytest.mark.parametrize("envelope", ["unified", "detail"])
async def test_signed_remote_active_callback_without_matching_local_scope_is_controlled_conflict(
    local_callback, monkeypatch, scenario, envelope,
):
    env = local_callback
    next_entry = successor()
    if scenario == "successor":
        monkeypatch.setitem(bridge._active_turns, SID, next_entry)
    elif scenario == "handoff":
        async def handoff(*args):
            monkeypatch.setitem(bridge._active_turns, SID, next_entry)
            return True
        env.remote_active.side_effect = handoff
    headers = {"X-Pi-Bridge-Secret": SECRET}
    if envelope == "detail":
        headers["X-Error-Envelope"] = "detail"
    response = await env.client.post("/pi-tools/execute", json=env.payload, headers=headers)
    assert response.status_code == 409, response.text
    payload = response.json()
    detail = payload["detail" if envelope == "detail" else "data"]
    assert detail["code"] == "TURN_NOT_LOCAL"
    assert detail["retryable"] is False
    assert "affinity" in detail["guidance"].lower()
    assert "do not" in detail["guidance"].lower()
    assert "retry-after" not in response.headers
    for private in (SECRET, env.token, "private-successor-cap", "successor-user"):
        assert private not in response.text
    env.bound.assert_not_awaited()
    env.remote_active.assert_awaited_once_with(SID, TID)
    if scenario != "absent":
        assert bridge.get_active_turn_entry(SID) is next_entry


@pytest.mark.asyncio
async def test_matching_local_scope_dispatches_once_with_verified_routing(local_callback, monkeypatch):
    env = local_callback
    entry = bridge._ActiveTurnEntry(
        session_id=SID, turn_id=TID, token=None,
        caller=bridge._PiCallerIdentity(user_id="original-user", owner_token="original-cap"),
    )
    monkeypatch.setitem(bridge._active_turns, SID, entry)
    response = await env.client.post("/pi-tools/execute", json=env.payload,
                                    headers={"X-Pi-Bridge-Secret": SECRET})
    assert response.status_code == 200, response.text
    assert not response.json()["isError"]
    env.remote_active.assert_not_awaited()
    env.bound.assert_awaited_once()
    request = env.bound.await_args.args[0]
    assert request.sessionId == SID and request.verifiedTurnId == TID
    assert env.bound.await_args.kwargs["trusted_entry"] is entry


@pytest.mark.asyncio
async def test_completed_or_replayed_turn_retains_existing_inactive_conflict(local_callback):
    env = local_callback
    env.remote_active.return_value = False
    response = await env.client.post("/pi-tools/execute", json=env.payload,
                                    headers={"X-Pi-Bridge-Secret": SECRET})
    assert response.status_code == 409
    assert response.json()["data"]["code"] == "TURN_CONTEXT_INACTIVE"
    env.bound.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "forged", "expired"])
async def test_unauthenticated_turn_context_never_reaches_local_admission(local_callback, kind):
    env = local_callback
    payload = dict(env.payload)
    if kind == "missing":
        payload.pop("turnToken")
    elif kind == "forged":
        payload["turnToken"] = issue_turn_token("forged-secret", SID, TID)
    else:
        payload["turnToken"] = issue_turn_token(SECRET, SID, TID, issued_at=int(time.time()) - 3600)
    response = await env.client.post("/pi-tools/execute", json=payload,
                                    headers={"X-Pi-Bridge-Secret": SECRET})
    assert response.status_code == 401
    env.remote_active.assert_not_awaited()
    env.bound.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_bridge_secret_still_rejected_before_turn_admission(local_callback):
    env = local_callback
    response = await env.client.post("/pi-tools/execute", json=env.payload,
                                    headers={"X-Pi-Bridge-Secret": "incorrect"})
    assert response.status_code == 401
    env.remote_active.assert_not_awaited()
    env.bound.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [bridge.PiRpcError("unrelated RPC failure"), ValueError("unrelated tool failure")])
async def test_unrelated_dispatch_failures_are_not_misclassified_as_turn_conflicts(local_callback, monkeypatch, failure):
    env = local_callback
    monkeypatch.setitem(bridge._active_turns, SID, bridge._ActiveTurnEntry(session_id=SID, turn_id=TID, token=None))
    env.bound.side_effect = failure
    response = await env.client.post("/pi-tools/execute", json=env.payload,
                                    headers={"X-Pi-Bridge-Secret": SECRET})
    assert response.status_code == 500
    env.bound.assert_awaited_once()
