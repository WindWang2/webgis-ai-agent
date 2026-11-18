"""#2213: image-revocation regeneration (new_session) reverts the vendor
session model to the spawn default — the bridge must reset its applied-model
tracking, invalidate the vision runtime cache, and re-apply the session
model for the current turn before prompt bytes go out."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent_pi_bridge import PiBridge, PiRpcError
from tests.unit.test_pi_issue2212 import _FakeLedger


def _make_bridge(monkeypatch, *, ledger, epoch=7):
    from app.services.visual_context import resolver as resolver_mod

    async def _fake_resolve(turn_sid, attachments, *, images_allowed, allowed_reason):
        return SimpleNamespace(images=[], receipts=[], any_sent=False)

    monkeypatch.setattr(resolver_mod, "resolve_attachments", _fake_resolve)
    monkeypatch.setattr(resolver_mod, "get_sent_image_ledger", lambda: ledger)

    class _Rpc:
        def __init__(self):
            self.new_sessions = 0
            self.spawn_model_id = "base-m"
            self.spawn_declared_image_input = False
            self.spawn_epoch = epoch

        async def request(self, command, data=None):
            if command == "new_session":
                self.new_sessions += 1
                return {"cancelled": False}
            return {}

    async def _drain():
        return None

    pi = PiBridge.__new__(PiBridge)
    pi._drain_stale_events = _drain
    pi._rpc = _Rpc()
    pi._vision_gate = None
    return pi


@pytest.mark.asyncio
async def test_revoked_regeneration_resets_model_state_gate_and_reapplies(monkeypatch):
    from app.services.visual_context.profile import VisionGate

    ledger = _FakeLedger(revoked=["sha-revoked"])
    pi = _make_bridge(monkeypatch, ledger=ledger)
    pi._vendor_model_state = ("sid-1", "webgis", "vision-m", 7)
    # Simulate a populated runtime cache from the pre-revocation model.
    pi._vision_gate = VisionGate()
    pi._vision_gate._state_cache = {"model": {"input": ["text", "image"]}}
    pi._vision_gate._state_epoch = 7

    reapply = AsyncMock(return_value=SimpleNamespace(
        ok=True, status="applied", effective_provider="webgis",
        effective_model="vision-m",
    ))
    pi._apply_session_model_for_turn = reapply

    outcome = await pi._resolve_turn_images(
        "sid-1", None, turn_id="t1", run_id="r1", bound_message="hello")

    assert outcome["regenerated"] is True
    assert outcome["blocked_reason"] == ""
    assert pi._rpc.new_sessions == 1
    assert ledger.bumped == 1
    # The stale applied-model claim and the stale runtime cache are gone.
    assert pi._vision_gate._state_cache is None
    assert pi._vision_gate._state_epoch is None
    # The session model was re-applied for THIS turn (not just the next one).
    reapply.assert_awaited_once_with("sid-1", "t1", "r1", "hello")


@pytest.mark.asyncio
async def test_revoked_regeneration_reapply_failure_is_not_silent(monkeypatch):
    ledger = _FakeLedger(revoked=["sha-revoked"])
    pi = _make_bridge(monkeypatch, ledger=ledger)
    pi._vendor_model_state = ("sid-1", "webgis", "vision-m", 7)
    reapply = AsyncMock(return_value=SimpleNamespace(
        ok=False, status="failed", reason_detail="set_model failed: down",
    ))
    pi._apply_session_model_for_turn = reapply

    with pytest.raises(PiRpcError, match="set_model failed: down"):
        await pi._resolve_turn_images(
            "sid-1", None, turn_id="t1", run_id="r1", bound_message="hello")
    # Regeneration did happen; the failure is surfaced, not masked as a
    # plain image block on a silently-defaulted model.
    assert pi._rpc.new_sessions == 1


@pytest.mark.asyncio
async def test_revoked_without_turn_context_does_not_reapply(monkeypatch):
    """Steer path (no turn context, allow_regeneration=False) keeps its
    contract: no regeneration, no re-apply, images blocked for this turn."""
    ledger = _FakeLedger(revoked=["sha-revoked"])
    pi = _make_bridge(monkeypatch, ledger=ledger)
    pi._vendor_model_state = ("sid-1", "webgis", "vision-m", 7)
    reapply = AsyncMock()
    pi._apply_session_model_for_turn = reapply

    outcome = await pi._resolve_turn_images(
        "sid-1", [{"kind": "image"}], allow_regeneration=False)
    assert outcome["regenerated"] is False
    assert outcome["blocked_reason"] == "revoked_history_pending_regeneration"
    assert pi._rpc.new_sessions == 0
    reapply.assert_not_awaited()
