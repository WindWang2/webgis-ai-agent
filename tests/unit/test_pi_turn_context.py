from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.agent_pi_bridge import PiToolRequest, PiToolResponse
from app.api.routes.pi_tools import execute_tool
from app.services.chat.pi_turn_context import (
    TURN_CONTEXT_MARKER,
    attach_turn_context,
    issue_turn_token,
    verify_turn_token,
)


def test_turn_context_is_session_bound_tamper_evident_and_expiring():
    token = issue_turn_token("secret", "session-a", "turn-1", issued_at=1_000)

    assert verify_turn_token("secret", token, now=1_001) == {
        "session_id": "session-a",
        "turn_id": "turn-1",
        "issued_at": 1_000,
    }
    assert verify_turn_token("wrong-secret", token, now=1_001) is None
    assert verify_turn_token("secret", token + "tampered", now=1_001) is None
    assert verify_turn_token("secret", token, now=2_000, max_age_seconds=10) is None


def test_attach_turn_context_keeps_marker_last_with_session_plan():
    token = issue_turn_token("secret", "s1", "turn-1", issued_at=1_000)
    text = attach_turn_context(
        "成都市小学分布情况",
        token,
        "[CARTOGRAPHY_VERDICT] pass",
        "[SessionPlan] recipe=poi_distribution_overview open=poi_query replaced=false superseded=false",
    )
    marker = f"[{TURN_CONTEXT_MARKER}:{token}]"
    assert text.index("[CARTOGRAPHY_VERDICT]") < text.index("[SessionPlan]")
    assert text.index("[SessionPlan]") < text.index(marker)
    assert text.rstrip().endswith(
        "(Internal routing context; do not quote or modify this marker.)"
    )


@pytest.mark.asyncio
async def test_pi_route_uses_signed_session_and_ignores_caller_session_id():
    secret = "route-secret"
    request = PiToolRequest(
        toolCallId="call-1",
        name="webgis_state_get",
        arguments={},
        sessionId="attacker-session",
        turnToken=issue_turn_token(secret, "owned-session", "turn-1"),
    )
    response = PiToolResponse(
        toolCallId="call-1", content=[{"type": "text", "text": "ok"}]
    )
    dispatcher = AsyncMock(return_value=response)

    with (
        patch("app.api.routes.pi_tools.get_bridge_secret", return_value=secret),
        patch("app.agent_pi_bridge.is_active_pi_turn", return_value=True),
        patch("app.api.routes.pi_tools.dispatch_tool", new=dispatcher),
    ):
        actual = await execute_tool(request, _secret=None)

    assert actual is response
    assert request.sessionId == "owned-session"
    assert request.verifiedTurnId == "turn-1"
    dispatcher.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_pi_route_rejects_missing_turn_context_before_dispatch():
    request = PiToolRequest(
        toolCallId="call-2", name="webgis_state_get", arguments={}
    )
    dispatcher = AsyncMock()

    with (
        patch("app.api.routes.pi_tools.get_bridge_secret", return_value="secret"),
        patch("app.api.routes.pi_tools.dispatch_tool", new=dispatcher),
        pytest.raises(HTTPException) as exc,
    ):
        await execute_tool(request, _secret=None)

    assert exc.value.status_code == 401
    dispatcher.assert_not_awaited()


@pytest.mark.asyncio
async def test_pi_route_rejects_valid_but_completed_turn_token():
    secret = "route-secret"
    request = PiToolRequest(
        toolCallId="call-late",
        name="webgis_state_get",
        arguments={},
        turnToken=issue_turn_token(secret, "owned-session", "turn-old"),
    )
    dispatcher = AsyncMock()

    with (
        patch("app.api.routes.pi_tools.get_bridge_secret", return_value=secret),
        patch("app.agent_pi_bridge.is_active_pi_turn", return_value=False),
        patch("app.api.routes.pi_tools.dispatch_tool", new=dispatcher),
        pytest.raises(HTTPException) as exc,
    ):
        await execute_tool(request, _secret=None)

    assert exc.value.status_code == 409
    dispatcher.assert_not_awaited()
