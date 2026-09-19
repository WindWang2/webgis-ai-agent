"""#1401: Retry/replan budgets fail-closed on ledger/session-state degradation."""
from __future__ import annotations

import asyncio

import pytest


def test_classify_and_remediate_fail_closed_when_ledger_path_none(monkeypatch):
    """_ledger_path → None must not look like attempts=0 (unbounded retry)."""
    from app.services.gis_harness import failure_taxonomy as ft
    from app.services.gis_harness import recovery_ledger as rl

    monkeypatch.setattr(rl, "_ledger_path", lambda _sid: None)
    monkeypatch.setattr(ft, "_global_ledger", ft.RemediationLedger())

    payload = ft.classify_and_remediate(
        code="TOOL_ERROR",
        message="boom",
        tool_name="clip_layer",
        session_id="sess-1401-ledger",
    )
    assert payload["retry_allowed"] is False
    assert payload["remediation"] == "abort_with_disclosure"
    assert payload["attempts"] < 0
    # Must NOT fall through to process ledger (would restart budget).
    assert ft._global_ledger.attempts(
        ("sess-1401-ledger", "clip_layer", payload["class"])) == 0


def test_classify_and_remediate_fail_closed_when_ledger_disabled(monkeypatch):
    from app.services.gis_harness import failure_taxonomy as ft

    monkeypatch.setenv("GIS_RECOVERY_LEDGER", "0")
    payload = ft.classify_and_remediate(
        code="TOOL_ERROR",
        message="boom",
        tool_name="clip_layer",
        session_id="sess-1401-off",
    )
    assert payload["retry_allowed"] is False
    assert payload["remediation"] == "abort_with_disclosure"


def test_request_replan_fail_closed_when_get_map_state_raises(monkeypatch):
    """get_map_state raise → unavailable recovery → no replan grant."""
    from app.services import session_data as sd
    from app.services.gis_harness.plan_runtime import request_replan

    async def _boom(_sid):
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        sd.session_data_manager, "get_map_state", _boom, raising=True)

    async def _run():
        result = await request_replan(
            "sess-1401-replan", reason="repair unreachable")
        assert result["verdict"] == "abort_with_disclosure"
        assert result.get("replan_pending") is False
        assert "unavailable" in str(result.get("reason") or "").lower() or \
            "fail-closed" in str(result.get("reason") or "").lower() or \
            "exhausted" in str(result.get("reason") or "").lower()

    asyncio.run(_run())


def test_load_recovery_state_unavailable_distinct_from_empty(monkeypatch):
    from app.services import session_data as sd
    from app.services.gis_harness.durable_context import (
        LOOP_BUDGETS,
        load_recovery_state,
        new_recovery_state,
    )

    async def _boom(_sid):
        raise RuntimeError("plane down")

    monkeypatch.setattr(
        sd.session_data_manager, "get_map_state", _boom, raising=True)

    async def _run():
        state = await load_recovery_state("sess-1401-load")
        assert state.get("_unavailable") is True
        empty = new_recovery_state()
        assert empty.get("_unavailable") is not True
        for k, budget in LOOP_BUDGETS.items():
            assert int(state["loops"][k]) >= budget

    asyncio.run(_run())


def test_update_recovery_state_raises_on_write_failure(monkeypatch):
    from app.services import session_data as sd
    from app.services.gis_harness.durable_context import (
        RecoveryStoreUnavailable,
        update_recovery_state,
    )

    async def _ok_get(_sid):
        return {}

    async def _boom_set(*_a, **_k):
        raise RuntimeError("write failed")

    monkeypatch.setattr(
        sd.session_data_manager, "get_map_state", _ok_get, raising=True)
    monkeypatch.setattr(
        sd.session_data_manager, "set_map_state", _boom_set, raising=True)

    async def _run():
        with pytest.raises(RecoveryStoreUnavailable):
            await update_recovery_state(
                "sess-1401-write", loop="replan", detail="x")

    asyncio.run(_run())
