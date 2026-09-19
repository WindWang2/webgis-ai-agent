"""RUN-11: SpatialWatch state CAS (concurrent drainers must not lose fires).

Old flow: read state → evaluate → blind-write state. Two drainers processing
consecutive events for the same watch both read ``consecutive_hits=0`` and
both wrote ``1``, so a ``consecutive_n=2`` trigger never fired. Fix: writes
CAS on the row's ``updated_at`` revision and re-evaluate on conflict.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.spatial_events import contracts as C
from app.services.spatial_events.contracts import WatchState
from app.services.spatial_events.service import SpatialEventService


def _evt(event_id: str, *, minute: int = 0) -> C.SpatialEventEnvelope:
    return C.SpatialEventEnvelope(
        kind="observation.spatial",
        org_id="org-a",
        subject_type="feature",
        subject_key="device:1",
        occurred_at=datetime(2026, 9, 16, 12, minute, 0, tzinfo=timezone.utc),
        payload={"value": 1},
        event_id=event_id,
    )


def _make_watch(ledger) -> None:
    ledger.upsert_watch(C.SpatialWatch(
        watch_id="w-cas", org_id="org-a", name="cas",
        kinds=["observation.spatial"],
        condition=C.WatchCondition(consecutive_n=2),
        actions=["notify_only"],
        cooldown_s=0,
    ))


def test_compare_and_swap_rejects_stale_and_cross_tenant(ledger):
    _make_watch(ledger)
    _, rev0 = ledger.get_watch_state_with_revision("w-cas", org_id="org-a")

    assert ledger.compare_and_swap_watch_state(
        "w-cas", WatchState(consecutive_hits=1),
        org_id="org-a", expected_revision=rev0,
    ) is True

    # A stale writer still holding rev0 must lose.
    assert ledger.compare_and_swap_watch_state(
        "w-cas", WatchState(consecutive_hits=99),
        org_id="org-a", expected_revision=rev0,
    ) is False

    # Cross-tenant writes are rejected outright.
    _, rev1 = ledger.get_watch_state_with_revision("w-cas", org_id="org-a")
    assert ledger.compare_and_swap_watch_state(
        "w-cas", WatchState(), org_id="org-b", expected_revision=rev1,
    ) is False


@pytest.mark.asyncio
async def test_cas_recheck_recovers_lost_fire(
    ledger, mission_runtime, event_env, monkeypatch
):
    service = SpatialEventService(ledger, mission_runtime=mission_runtime)
    _make_watch(ledger)
    service.ingest_sync(_evt("e1", minute=0))
    service.ingest_sync(_evt("e2", minute=1))

    rows = ledger.claim_batch("w-cas-worker", limit=2)
    by_id = {r["event_id"]: r for r in rows}
    assert set(by_id) == {"e1", "e2"}

    # Snapshot the state/revision BEFORE the first event (this is what a
    # concurrent drainer that started earlier would have read).
    stale_state, stale_revision = ledger.get_watch_state_with_revision(
        "w-cas", org_id="org-a"
    )

    out1 = await service._process_event(by_id["e1"])
    assert out1["fires"] == 0
    state1, _ = ledger.get_watch_state_with_revision("w-cas", org_id="org-a")
    assert state1.consecutive_hits == 1

    real_get = ledger.get_watch_state_with_revision
    calls = {"n": 0}

    def stale_once(watch_id, *, org_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return stale_state, stale_revision
        return real_get(watch_id, org_id=org_id)

    monkeypatch.setattr(ledger, "get_watch_state_with_revision", stale_once)

    out2 = await service._process_event(by_id["e2"])

    assert out2.get("deferred") is not True
    assert calls["n"] >= 2, "stale revision must trigger a re-read"
    assert out2["fires"] == 1, "second event must complete consecutive_n=2"
    assert ledger.get_fire("w-cas", "e2") is not None
