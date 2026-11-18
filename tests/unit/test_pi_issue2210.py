"""Pi caller identity comes from the authenticated turn, never model input."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.agent_pi_bridge as bridge
from app.services.jobs.context import current_origin
from app.services.provenance.context import (
    ToolExecutionContext, get_tool_execution_context,
    reset_tool_execution_context, set_tool_execution_context,
)
from app.services.templates.composition_assets import tool_caller_identity
from app.services.tool_dispatch_service import ToolDispatchResult
from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("raise_in_tool", [False, True])
async def test_verified_callbacks_bind_both_identity_channels_without_leakage(monkeypatch, raise_in_tool):
    registry = ToolRegistry()

    async def identity_probe(owner_id: str = "", owner_token: str = "", org_id: int = 0,
                             project_id: str = "", session_id: str = ""):
        return {"success": True}

    registry.register(tier=1, name="identity_probe", description="identity probe", func=identity_probe)
    monkeypatch.setattr(bridge, "get_tool_registry", lambda: registry)
    captured = {}
    ready = asyncio.Event()

    async def dispatch(tc, sid, executed):
        caller = get_tool_execution_context()
        origin = current_origin()
        captured[sid] = (caller, origin, tool_caller_identity())
        if len(captured) == 2:
            ready.set()
        await ready.wait()  # force overlapping callback scopes
        assert get_tool_execution_context() is caller
        assert current_origin() is origin
        if raise_in_tool:
            raise RuntimeError("tool failed")
        return ToolDispatchResult("ok", "ok", {}, None, {"success": True}, None)

    monkeypatch.setattr(bridge, "_get_shared_dispatch_service", lambda: SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr("app.services.session_plan.ensure_session_plan_slot", AsyncMock())
    for suffix in ("a", "b"):
        sid = f"identity-{suffix}"
        caller = SimpleNamespace(user_id=f"user-{suffix}", owner_token=f"cap-{suffix}",
                                 org_id=1 if suffix == "a" else 2, project_id=f"project-{suffix}")
        monkeypatch.setitem(bridge._active_turns, sid, SimpleNamespace(
            session_id=sid, turn_id=f"turn-{suffix}", run_id=f"run-{suffix}",
            token=None, caller=caller,
        ))

    async def callback(suffix):
        try:
            return await bridge.dispatch_tool(bridge.PiToolRequest(
                toolCallId=f"identity-call-{suffix}-{raise_in_tool}", name="identity_probe",
                sessionId=f"identity-{suffix}", verifiedTurnId=f"turn-{suffix}",
                arguments={
                    "owner_id": "forged", "owner_token": "forged", "org_id": 999,
                    "project_id": "forged", "session_id": "identity-other",
                },
            ))
        finally:
            assert get_tool_execution_context() is inherited
            assert current_origin() is None

    # Even an inherited unrelated workflow identity must not bleed into Pi.
    inherited = ToolExecutionContext(user_id="unrelated", session_id="unrelated")
    token = set_tool_execution_context(inherited)
    try:
        results = await asyncio.gather(callback("a"), callback("b"), return_exceptions=True)
        if raise_in_tool:
            assert all(isinstance(result, RuntimeError) for result in results)
        else:
            assert all(not result.isError for result in results)
        assert get_tool_execution_context() is inherited
    finally:
        reset_tool_execution_context(token)
    for suffix in ("a", "b"):
        caller, origin, asset_identity = captured[f"identity-{suffix}"]
        assert caller.user_id == origin.owner_id == f"user-{suffix}"
        assert caller.org_id == origin.org_id == (1 if suffix == "a" else 2)
        assert caller.project_id == origin.project_id == f"project-{suffix}"
        assert caller.session_id == origin.session_id == f"identity-{suffix}"
        assert caller.run_id == origin.run_id == f"run-{suffix}"
        assert origin.owner_token == f"cap-{suffix}"
        assert asset_identity == (caller.user_id, caller.org_id)
    assert get_tool_execution_context() is None
    assert current_origin() is None


@pytest.mark.asyncio
async def test_stale_verified_turn_cannot_borrow_successor_identity(monkeypatch):
    monkeypatch.setattr(bridge, "get_tool_registry", lambda: object())
    monkeypatch.setitem(bridge._active_turns, "same-session", SimpleNamespace(
        turn_id="successor", caller=SimpleNamespace(user_id="successor-user"),
    ))
    with pytest.raises(bridge.PiRpcError, match="active turn"):
        await bridge.dispatch_tool(bridge.PiToolRequest(
            toolCallId="stale", name="irrelevant", sessionId="same-session",
            verifiedTurnId="old-turn", arguments={"owner_id": "successor-user"},
        ))


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,owner_token", [("auth-user", None), (None, "anonymous-cap")])
async def test_real_dispatch_delivers_server_identity_to_tool(monkeypatch, user_id, owner_token):
    registry = ToolRegistry()
    captured = []

    async def caller_probe(owner_id: str = "", org_id: int = 0):
        captured.append((get_tool_execution_context(), current_origin(), tool_caller_identity()))
        return {"success": True}

    registry.register(tier=1, name="caller_probe", description="probe caller identity", func=caller_probe)
    monkeypatch.setattr(bridge, "get_tool_registry", lambda: registry)
    sid = f"real-dispatch-identity-{user_id}"
    monkeypatch.setitem(bridge._active_turns, sid, bridge._ActiveTurnEntry(
        session_id=sid, turn_id="real-turn", token=None, run_id="real-run",
        caller=bridge._PiCallerIdentity(user_id=user_id, owner_token=owner_token,
                                       org_id=12, project_id="allowed-project"),
    ))
    result = await bridge.dispatch_tool(bridge.PiToolRequest(
        toolCallId=f"real-identity-{user_id}", name="caller_probe", sessionId=sid,
        verifiedTurnId="real-turn", arguments={"owner_id": "forged-user", "org_id": 999},
    ))
    assert not result.isError, result
    assert len(captured) == 1
    ctx, origin, asset_identity = captured[0]
    assert ctx.user_id == origin.owner_id == user_id
    assert ctx.org_id == origin.org_id == 12
    assert ctx.project_id == origin.project_id == "allowed-project"
    assert origin.owner_token == owner_token
    assert asset_identity == (user_id, 12)
    assert get_tool_execution_context() is None
    assert current_origin() is None
