"""G07 controlled recovery experiments on existing production decision seams."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.lib.harness.goal_verification import reconcile_goal_verification
from app.services.gis_harness.recovery_policy.contracts import (
    DiagnosticAttribution,
    GISRecoveryDiagnostic,
    GISRecoveryReason,
)
from app.services.gis_harness.recovery_policy.diagnose import (
    diagnose_eligibility,
    diagnose_goal_gap,
    diagnose_tool_failure,
)
from app.services.gis_harness.recovery_policy.policy import decide_recovery
from app.services.gis_harness.scope_ref_guard import product_scope_geography
from app.services.session_data import session_data_manager


def _goal():
    return {
        "verdict": "satisfied",
        "requirements": [
            {"id": "r-map", "kind": "map", "verdict": "fulfilled"},
            {"id": "r-sci", "kind": "analysis", "verdict": "fulfilled"},
        ],
        "missing_summary": [],
    }


def _chapter():
    return {"workflow_contract": {"roles": [
        {"role": "schools", "required": True, "status": "bound"},
    ]}}


def test_missing_observation_reobserve_then_verify():
    diagnosis = diagnose_goal_gap(
        {"verdict": "blocked", "missing_summary": ["render_observation"]},
        attribution=DiagnosticAttribution(session_id="rel-recovery-observe"),
    )
    assert diagnosis is not None
    decision = decide_recovery(
        diagnosis, descriptor={"side_effect": "pure", "tier": 1},
    )
    assert decision.auto_retry is False
    assert "reobserve" in decision.allowed_actions

    before = reconcile_goal_verification(
        goal_report=_goal(), observation_health=None, chapter=_chapter(),
        task_complete=True, quality_review_present=True,
    )
    assert before.overall != "pass"
    after = reconcile_goal_verification(
        goal_report=_goal(),
        observation_health={
            "layers": [{"id": "l1", "state": "semantically_correct"}],
            "aggregate": "semantically_correct", "health": "ok",
            "observed_revision": 4,
        },
        chapter=_chapter(), task_complete=True, quality_review_present=True,
    )
    assert after.overall == "pass"
    assert after.contradictions == []


@pytest.mark.asyncio
async def test_transient_read_failure_retries_then_succeeds(monkeypatch, tmp_path):
    sid = "rel-recovery-transient"
    await session_data_manager.clear_session(sid)
    calls = {"count": 0}

    async def flaky(tool_name, tool_args_raw, session_id=None, executed_tools=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"success": False, "error": "HTTP 503 Service Unavailable"}
        return {"summary": "recovered", "count": 7}

    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path / "mapspec"))
    from app.services.tool_dispatch_service import ToolDispatchService

    registry = MagicMock(dispatch=AsyncMock(side_effect=flaky))
    registry.descriptor.return_value = {"side_effect": "cacheable_read", "tier": 1}
    service = ToolDispatchService(registry=registry)
    first = await service.dispatch({
        "id": "call-transient", "function": {"name": "query_pois", "arguments": {}},
    }, sid, set())
    recovery = first.raw_result["recovery"]
    assert recovery["diagnostic"]["reason"] == "upstream_server_error"
    assert recovery["decision"]["auto_retry"] is True
    assert "retry_with_backoff" in recovery["decision"]["allowed_actions"]

    second = await service.dispatch({
        "id": "call-transient", "function": {"name": "query_pois", "arguments": {}},
    }, sid, set())
    assert second.status == "ok"
    assert calls["count"] == 2
    await session_data_manager.clear_session(sid)


def test_cross_city_inconsistency_requires_new_data_not_blind_retry():
    mismatched = product_scope_geography(
        "北京市", descriptor={"district": "成都市"},
    )
    assert mismatched["verdict"] == "mismatched"
    diagnosis = diagnose_eligibility(
        state="blocked", reason_code="scope_ref_mismatch",
        attribution=DiagnosticAttribution(session_id="rel-recovery-scope"),
    )
    assert diagnosis is not None
    decision = decide_recovery(
        diagnosis, descriptor={"side_effect": "pure", "tier": 1},
    )
    assert decision.auto_retry is False
    assert decision.allowed_actions == ("ask_user_data",)

    matched = product_scope_geography(
        "北京市", descriptor={"district": "北京市"},
    )
    assert matched["verdict"] == "matched"


def test_persistent_edit_without_idempotency_cannot_auto_replay():
    diagnosis = diagnose_tool_failure(
        message="HTTP 503 Service Unavailable",
        tool_name="feature_edit_commit",
        harness_failure={"class": "timeout"},
        session_id="rel-recovery-edit",
    )
    decision = decide_recovery(
        diagnosis,
        descriptor={"side_effect": "state_mutation", "tier": 2},
        idempotency_key_present=False,
    )
    assert decision.auto_retry is False
    assert decision.required_confirmation == "owner_write"
    assert "retry" not in decision.allowed_actions
    assert "retry_with_backoff" not in decision.allowed_actions

    cancelled = decide_recovery(
        GISRecoveryDiagnostic(reason=GISRecoveryReason.CANCELLED),
        descriptor={"side_effect": "state_mutation", "tier": 2},
        cancelled=True,
    )
    assert cancelled.terminal is True
    assert cancelled.allowed_actions == ()
