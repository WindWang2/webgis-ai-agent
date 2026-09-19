"""SEC-03 regression: server-side tier-3 confirmation artifact.

Deep-review swarm 2026-09-19 (SEC-03): ``execute_plan`` accepted a
model-provided ``confirm_destructive`` boolean, so an LLM could self-confirm
destructive tier-3 steps. Execution now requires a server-side, session-owner
approved challenge stored in the plan payload; the boolean parameter is gone.
"""
from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from app.services import plan_mode as plan_svc
from app.tools.registry import ToolRegistry

SID = "sec03-session"


def _tier3_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(name="sec03_danger", description="x", tier=3)
    def danger(x: str = "1"):
        return {"success": True, "value": "ran"}

    return reg


async def _store_tier3_plan(session_id: str) -> str:
    plan = plan_svc.PlanProposal(
        title="boom",
        steps=[plan_svc.PlanStep(id="d1", tool="sec03_danger", args={})],
    )
    return await plan_svc.store_plan(session_id, plan)


def test_model_boolean_parameter_is_gone():
    assert "confirm_destructive" not in inspect.signature(
        plan_svc.execute_plan_async
    ).parameters
    assert "confirm_destructive" not in inspect.signature(
        plan_svc._execute_plan_locked
    ).parameters


async def test_execute_without_approval_returns_challenge():
    reg = _tier3_registry()
    plan_id = await _store_tier3_plan(SID)

    result = await plan_svc.execute_plan_async(SID, plan_id, reg)
    assert result["success"] is False
    assert result["code"] == "CONFIRMATION_REQUIRED"
    assert result["challenge_id"]
    assert result["destructive_steps"] == ["d1"]

    # Nothing ran: the tool result is not present.
    plan = await plan_svc.load_plan(SID, plan_id)
    assert not (plan.get("__step_results__") or {})


async def test_wrong_or_stale_challenge_never_approves():
    reg = _tier3_registry()
    plan_id = await _store_tier3_plan(SID)
    first = await plan_svc.execute_plan_async(SID, plan_id, reg)

    assert (await plan_svc.approve_destructive_confirmation(
        SID, plan_id, "wrong-challenge"
    ))["code"] == "CONFIRMATION_MISMATCH"

    # Re-issuing rotates the challenge; the old one is dead.
    second = await plan_svc.execute_plan_async(SID, plan_id, reg)
    assert "challenge_id" in second, second
    assert second["challenge_id"] != first["challenge_id"]
    assert (await plan_svc.approve_destructive_confirmation(
        SID, plan_id, first["challenge_id"]
    ))["code"] == "CONFIRMATION_MISMATCH"

    assert (await plan_svc.execute_plan_async(SID, plan_id, reg))["code"] == \
        "CONFIRMATION_REQUIRED"


async def test_approval_then_execution_works():
    reg = _tier3_registry()
    plan_id = await _store_tier3_plan(SID)
    pending = await plan_svc.execute_plan_async(SID, plan_id, reg)

    approved = await plan_svc.approve_destructive_confirmation(
        SID, plan_id, pending["challenge_id"]
    )
    assert approved["success"] is True
    assert approved["approved_steps"] == ["d1"]

    result = await plan_svc.execute_plan_async(SID, plan_id, reg)
    assert result["success"] is True, result
    assert result["results"]["d1"]["value"] == "ran"


def test_confirm_endpoint_is_session_owner_guarded():
    import app.api.routes.chat as chat_mod
    from app.core.auth import require_owned_session

    src = inspect.getsource(chat_mod.confirm_destructive_plan)
    assert "require_owned_session" in src or require_owned_session.__name__ in src


async def test_confirm_endpoint_approves_and_execution_runs(monkeypatch):
    from app.core.auth import require_owned_session
    from app.main import app

    reg = _tier3_registry()
    plan_id = await _store_tier3_plan(SID)
    pending = await plan_svc.execute_plan_async(SID, plan_id, reg)

    app.dependency_overrides[require_owned_session] = lambda session_id: object()
    try:
        with TestClient(app) as client:
            bad = client.post(
                f"/api/v1/chat/sessions/{SID}/plans/{plan_id}/confirm",
                json={"challenge_id": "wrong"},
            )
            assert bad.status_code == 409

            ok = client.post(
                f"/api/v1/chat/sessions/{SID}/plans/{plan_id}/confirm",
                json={"challenge_id": pending["challenge_id"]},
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["success"] is True
    finally:
        app.dependency_overrides.pop(require_owned_session, None)

    result = await plan_svc.execute_plan_async(SID, plan_id, reg)
    assert result["success"] is True, result
    assert result["results"]["d1"]["value"] == "ran"


async def test_execute_plan_tool_returns_confirmation_required():
    from app.tools.plan_mode import register_plan_mode_tools

    reg = _tier3_registry()
    register_plan_mode_tools(reg)
    tool = reg._tools["execute_plan"]

    plan_id = await _store_tier3_plan(SID)
    result = await tool(plan_id=plan_id, session_id=SID)
    assert result.get("code") == "CONFIRMATION_REQUIRED", result
    assert result["challenge_id"]
    assert "confirm" in result["next_action"]
