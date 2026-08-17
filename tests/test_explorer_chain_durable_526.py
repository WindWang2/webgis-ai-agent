"""Regression tests for #526: the explorer chain-run registry must survive a
process restart.

The pre-fix defect: `_chain_runs` is a process-local OrderedDict. On
restart/pod-swap the fallbacks degraded to polling/revoking ONLY the final
stage id — mid-chain-failed runs stayed PENDING forever (the SSE stream never
terminates), abort became a no-op, and the owner-verified endpoints 404'd.

The fix: the chain-run record (stage_ids + owner) is ALSO written to Redis
(`explorer:chain:<final_id>`, TTL 24h); `_chain_runs` stays as the L1 fast
path and the durable record is the authority on L1 miss. Status/abort/stream/
ownership all read through it.

Tests simulate restart by clearing the in-process registry (and the
TaskQueueService owner map) while keeping the durable store — exactly what a
pod swap does to the L1 state.
"""
import json

import pytest
from unittest.mock import MagicMock

from app.services.explorer import orchestrator as orch_mod
from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.models import SearchContext
from app.services.task_queue import TaskQueueService

_redis_mod = pytest.importorskip("redis")


@pytest.fixture(autouse=True)
def _durable_redis(monkeypatch):
    """Point the durable chain-run registry at in-process fakeredis."""
    import fakeredis

    fake = fakeredis.FakeRedis(decode_responses=True)
    fake.flushall()

    def _get_fake():
        return fake

    monkeypatch.setattr(orch_mod, "_get_chain_redis", _get_fake)
    # Isolate the in-process registries between tests.
    orch_mod.clear_chain_registry()
    TaskQueueService._task_owners.clear()
    yield fake
    orch_mod.clear_chain_registry()
    TaskQueueService._task_owners.clear()


def _simulate_restart():
    """Drop every in-process registry — the L1 state a pod swap loses."""
    orch_mod.clear_chain_registry()
    TaskQueueService._task_owners.clear()


def _register_run(final_id, stage_ids, owner="user-526"):
    """Register a chain run through the REAL production path: L1 + durable."""
    orch_mod.register_chain_run(final_id, stage_ids, owner=owner)
    orch_mod.persist_chain_run_sync(final_id, stage_ids, owner=owner)


def _parse_sse(event_str: str) -> dict:
    for line in event_str.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"no data line in {event_str!r}")


@pytest.mark.asyncio
async def test_status_aggregates_durable_stages_after_restart(monkeypatch):
    """After restart (L1 registry gone), a mid-chain FAILURE must still read
    as the run's status — the durable stage list re-enables whole-chain
    aggregation instead of degrading to single-id polling (PENDING forever)."""
    final_id = "fin-526"
    stage_ids = ["d-526", "f-526", "p-526", "g-526", final_id]
    _register_run(final_id, stage_ids)
    _simulate_restart()

    by_id = {
        "d-526": {"status": "SUCCESS", "progress": 100, "result": {}},
        "f-526": {"status": "FAILURE", "progress": 50, "result": None},
        "p-526": {"status": "PENDING", "progress": 0, "result": None},
        "g-526": {"status": "PENDING", "progress": 0, "result": None},
        final_id: {"status": "PENDING", "progress": 0, "result": None},
    }
    monkeypatch.setattr(
        TaskQueueService, "get_task_status",
        staticmethod(lambda tid: dict(by_id.get(tid, {"status": "PENDING", "progress": 0, "result": None}))),
    )

    orch = ExplorerOrchestrator()
    status = await orch.get_task_status(final_id)
    assert status["status"] == "FAILURE", (
        f"post-restart aggregate must see the mid-chain failure, got {status!r}"
    )
    assert status.get("stage") == "fetch"
    # The final-id-only fallback (the bug) reports PENDING — pin that contrast.
    assert TaskQueueService.get_task_status(final_id)["status"] == "PENDING"


@pytest.mark.asyncio
async def test_status_success_after_restart(monkeypatch):
    """A completed chain reads SUCCESS after restart."""
    final_id = "fin-ok-526"
    _register_run(final_id, ["d", "f", "p", "g", final_id])
    _simulate_restart()

    monkeypatch.setattr(
        TaskQueueService, "get_task_status",
        staticmethod(lambda tid: {"status": "SUCCESS", "progress": 100, "result": {"ok": True}}),
    )
    status = await ExplorerOrchestrator().get_task_status(final_id)
    assert status["status"] == "SUCCESS"
    assert status.get("result") == {"ok": True}


@pytest.mark.asyncio
async def test_abort_revokes_all_durable_stages_after_restart(monkeypatch):
    """After restart, abort must revoke EVERY stage id (the pre-fix fallback
    revoked only the final id — a no-op while earlier stages run)."""
    revoked: list[str] = []

    def fake_revoke(task_id):
        revoked.append(task_id)
        return True

    monkeypatch.setattr(TaskQueueService, "revoke_task", staticmethod(fake_revoke))

    final_id = "fin-abort-526"
    stage_ids = ["da", "fa", "pa", "ga", final_id]
    _register_run(final_id, stage_ids)
    _simulate_restart()

    ok = await ExplorerOrchestrator().abort_task(final_id)
    assert ok is True
    assert set(revoked) == set(stage_ids), (
        f"post-restart abort must revoke all stage ids, got {revoked}"
    )


@pytest.mark.asyncio
async def test_abort_unknown_task_falls_back_to_single_revoke(monkeypatch):
    """A task with NO durable record keeps the single-id fallback (honest
    degradation, no behavior change for unknown ids)."""
    revoked: list[str] = []

    def fake_revoke(task_id):
        revoked.append(task_id)
        return True

    monkeypatch.setattr(TaskQueueService, "revoke_task", staticmethod(fake_revoke))
    _simulate_restart()

    ok = await ExplorerOrchestrator().abort_task("never-registered")
    assert ok is True
    assert revoked == ["never-registered"]


@pytest.mark.asyncio
async def test_stream_terminates_on_durable_failure_after_restart(monkeypatch):
    """After restart, the SSE stream for a mid-chain-failed run must emit the
    failed terminal event and CLOSE — not stream PENDING progress forever."""
    final_id = "fin-stream-526"
    _register_run(final_id, ["s1", "s2", "s3", "s4", final_id])
    _simulate_restart()

    by_id = {
        "s1": {"status": "SUCCESS", "progress": 100, "result": {}},
        "s2": {"status": "FAILURE", "progress": 60, "result": None},
        "s3": {"status": "PENDING", "progress": 0, "result": None},
        "s4": {"status": "PENDING", "progress": 0, "result": None},
        final_id: {"status": "PENDING", "progress": 0, "result": None},
    }
    monkeypatch.setattr(
        TaskQueueService, "get_task_status",
        staticmethod(lambda tid: dict(by_id.get(tid, {"status": "PENDING", "progress": 0, "result": None}))),
    )

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr("app.services.explorer.orchestrator.asyncio.sleep", _no_sleep)

    events = [e async for e in ExplorerOrchestrator().stream_progress(final_id)]
    payloads = [_parse_sse(e) for e in events if e.startswith("event: explorer_progress")]
    assert payloads[-1]["status"] == "failed", f"stream must terminate truthfully: {payloads[-1]}"
    assert payloads[-1]["stage"] == "fetch"
    assert payloads[-1]["context"].get("final_status") == "FAILURE"
    # The generator returned — no infinite stream.
    assert events


@pytest.mark.asyncio
async def test_owner_check_survives_restart():
    """The owner-verified endpoints must keep working after restart: the
    durable record carries the owner."""
    final_id = "fin-owner-526"
    _register_run(final_id, ["o1", "o2", "o3", "o4", final_id], owner="user-526")
    _simulate_restart()

    orch = ExplorerOrchestrator()
    assert await orch.verify_chain_owner(final_id, "user-526") is True
    assert await orch.verify_chain_owner(final_id, "user-other") is False
    assert await orch.verify_chain_owner("never-registered", "user-526") is False


@pytest.mark.asyncio
async def test_start_exploration_writes_durable_record(monkeypatch):
    """The submit path must write the durable record (stage_ids + owner) so a
    later restart can recover it."""
    final_id = "fin-submit-526"
    mock_result = MagicMock()
    mock_result.id = final_id
    node = mock_result
    for i in range(4):
        parent = MagicMock()
        parent.parent = None
        parent.id = f"stage-{i}-526"
        node.parent = parent
        node = parent
    node.id = "first-526"
    mock_chain = MagicMock()
    mock_chain.return_value.apply_async.return_value = mock_result
    monkeypatch.setattr("app.services.explorer.orchestrator.chain", mock_chain)

    orch = ExplorerOrchestrator()
    returned = await orch.start_exploration(
        query="q",
        context=SearchContext(query="q"),
        session_id="s-526",
        user_id="user-526",
    )
    assert returned == final_id

    # The durable record exists independently of the in-process registry.
    _simulate_restart()
    run = orch_mod.load_chain_run_sync(final_id)
    assert run is not None, "start_exploration must persist the chain run durably"
    assert run.stage_ids[0] == "first-526"
    assert run.stage_ids[-1] == final_id
    assert run.owner == "user-526"
    assert len(run.stage_ids) == 5
