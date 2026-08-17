"""Explorer task chain integration tests"""
import json
import pytest
from unittest.mock import patch, MagicMock
from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.models import SearchContext
from app.services.explorer.intent_detector import IntentDetector

# task_chain.py imports celery at module load; skip the task-body tests where
# celery isn't installed (CI installs it via requirements.txt).
task_chain = pytest.importorskip("app.tasks.explorer.task_chain", reason="celery not installed")


@pytest.mark.asyncio
async def test_orchestrator_start_and_status():
    """测试编排器启动任务和查询状态"""
    orchestrator = ExplorerOrchestrator()

    with patch("app.services.explorer.orchestrator.chain") as mock_chain:
        # Shape the mock like a REAL 5-stage chain result: the leaf result
        # carries a 4-deep .parent chain back to the first task (issue #481;
        # shape pinned by test_chain_apply_async_returns_final_stage_result).
        # A bare MagicMock would auto-create truthy .parent forever.
        mock_result = MagicMock()
        mock_result.id = "final_task_id"
        node = mock_result
        for _ in range(4):
            parent = MagicMock()
            parent.parent = None
            node.parent = parent
            node = parent
        node.id = "first_task_id"
        mock_chain.return_value.apply_async.return_value = mock_result

        task_id = await orchestrator.start_exploration(
            query="海淀区学校",
            context=SearchContext(query="海淀区学校"),
        )

        # The whole-chain handle handed to the client is the FINAL stage's
        # id, not the first task's.
        assert task_id == "final_task_id"
        run = orchestrator_mod.get_chain_run(task_id)
        assert run is not None
        assert run.stage_ids[0] == "first_task_id"
        assert run.stage_ids[-1] == "final_task_id"
        assert len(run.stage_ids) == 5


@pytest.mark.asyncio
async def test_intent_detector_triggers_exploration():
    """测试意图检测器正确触发探索"""
    detector = IntentDetector()
    result = detector.detect(
        user_query="深度搜索北京医院",
        current_layers=[],
        session_history=[],
    )

    assert result.decision == "auto_execute"
    assert result.confidence == 1.0


def test_explore_decision_validation():
    """测试 ExploreDecision 模型验证"""
    from app.services.explorer.intent_detector import ExploreDecision

    decision = ExploreDecision(decision="auto_execute", confidence=0.8)
    assert decision.decision == "auto_execute"
    assert decision.confidence == 0.8

    with pytest.raises(ValueError):
        ExploreDecision(decision="invalid", confidence=0.5)


# ─── Session-store seam routing (review §3 item 3a) ───────────────────────
#
# _store_ref/_load_ref previously imported the module-level session_data_manager
# singleton (a per-process MemorySessionStore under USE_REDIS=false), so a ref
# stored in one prefork worker was invisible to the next stage running in a
# different worker. They now route through get_session_store(), the config-gated
# seam — which returns the Redis-backed store under USE_REDIS=true (shared across
# workers) and the memory store under eager mode. These tests pin that routing.


def test_store_load_ref_round_trip_through_seam(monkeypatch):
    """_store_ref then _load_ref round-trips data via get_session_store()."""
    from app.services.session_data_protocol import (
        get_session_store,
        set_active_session_store,
    )
    from app.services.session_data import MemorySessionStore

    # Inject a fresh memory store so the test is isolated from other tests' state.
    fake = MemorySessionStore()
    set_active_session_store(fake)
    try:
        ref_id = task_chain._store_ref({"hello": "world"}, task_id="t-seam", prefix="explorer")
        assert ref_id.startswith("ref:explorer-")
        loaded = task_chain._load_ref(ref_id, task_id="t-seam")
        assert loaded == {"hello": "world"}
        # Confirm the data landed in the injected store under the explorer namespace.
        assert get_session_store() is fake
    finally:
        set_active_session_store(None)


def test_store_ref_uses_seam_not_module_singleton(monkeypatch):
    """_store_ref must resolve the store via get_session_store() each call.

    Regression for the hard-coded `from app.services.session_data import
    session_data_manager` import: if _store_ref bound the singleton at import
    time, swapping the active store via set_active_session_store() would have
    no effect.
    """
    from app.services.session_data_protocol import set_active_session_store
    from app.services.session_data import MemorySessionStore

    seen_stores = []

    class TrackingStore(MemorySessionStore):
        async def store(self, session_id, data, prefix="data"):
            seen_stores.append(self)
            return await super().store(session_id, data, prefix=prefix)

    fake = TrackingStore()
    set_active_session_store(fake)
    try:
        task_chain._store_ref({"x": 1}, task_id="t-track", prefix="explorer")
        assert seen_stores == [fake], "_store_ref did not route through get_session_store()"
    finally:
        set_active_session_store(None)


# ─── Issue #481: whole-chain handle semantics ──────────────────────────────
#
# The SSE stream / status / abort endpoints must track the WHOLE 5-stage
# chain, not its first task. Celery semantics this pins (verified against the
# installed Celery 5.6.3): ``chain(a..e).apply_async()`` returns the LAST
# task's AsyncResult (SUCCESS only when the whole chain finished) and
# ``.parent`` walks backward toward the first task. Polling the FIRST task's
# id reports SUCCESS seconds in while stages 2-5 still run; a mid-chain
# failure leaves downstream ids PENDING forever (never FAILURE), so honest
# status requires aggregating every stage id.

from app.services.explorer import orchestrator as orchestrator_mod
from app.services.task_queue import TaskQueueService, celery_app
from celery import chain as celery_chain

_STAGE_ATTRS = (
    "explorer_discover_task",
    "explorer_fetch_task",
    "explorer_parse_task",
    "explorer_geocode_task",
    "explorer_validate_task",
)

_stub_seq = {"n": 0}


def _make_stub_chain_tasks():
    """Build 5 real Celery tasks shaped like the explorer stage tasks.

    Real ``celery_app.task`` objects (real signatures, real chain semantics,
    real backend marks) whose bodies just return the next stage's handoff
    dict — so tests exercise the canvas machinery without the real stages'
    network/store touchpoints.
    """
    _stub_seq["n"] += 1
    n = _stub_seq["n"]

    @celery_app.task(bind=True, name=f"stub.explorer.{n}.discover")
    def stub_discover(self, task_id, query, context):
        return {"task_id": task_id, "selected_sources": []}

    @celery_app.task(bind=True, name=f"stub.explorer.{n}.fetch")
    def stub_fetch(self, prev):
        return {"task_id": prev["task_id"], "fetch_results": []}

    @celery_app.task(bind=True, name=f"stub.explorer.{n}.parse")
    def stub_parse(self, prev):
        return {"task_id": prev["task_id"], "parsed_results": []}

    @celery_app.task(bind=True, name=f"stub.explorer.{n}.geocode")
    def stub_geocode(self, prev):
        return {
            "task_id": prev["task_id"],
            "geocoded_ref_id": None,
            "total_rows": 0,
            "success_rate": 0.0,
        }

    @celery_app.task(bind=True, name=f"stub.explorer.{n}.validate")
    def stub_validate(self, prev):
        return {"task_id": prev["task_id"], "status": "completed"}

    return (stub_discover, stub_fetch, stub_parse, stub_geocode, stub_validate)


def _build_real_stub_chain():
    stubs = _make_stub_chain_tasks()
    return celery_chain(
        stubs[0].s("t-stub", "q", {}),
        stubs[1].s(),
        stubs[2].s(),
        stubs[3].s(),
        stubs[4].s(),
    )


def _parse_sse(event_str: str) -> dict:
    for line in event_str.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"no data line in {event_str!r}")


@pytest.mark.asyncio
async def test_start_exploration_returns_whole_chain_handle(monkeypatch):
    """start_exploration must return the FINAL stage's task id (the durable
    whole-chain handle), and record every stage id for status/abort."""
    stubs = _make_stub_chain_tasks()
    for attr, stub in zip(_STAGE_ATTRS, stubs):
        monkeypatch.setattr(task_chain, attr, stub)

    orch = ExplorerOrchestrator()
    returned = await orch.start_exploration(
        query="海淀区学校",
        context=SearchContext(query="海淀区学校"),
        session_id="s-481",
        user_id="user-481",
    )

    run = orchestrator_mod.get_chain_run(returned)
    assert run is not None, "no chain-run record registered under the returned id"
    assert len(run.stage_ids) == 5, "run record must know all 5 stage task ids"
    assert len(set(run.stage_ids)) == 5
    # The handle handed to the client is the LAST stage (validate), not the
    # first (discover): Celery's returned result is the chain-completion
    # signal; the root id goes SUCCESS seconds in and fakes completion.
    assert returned == run.stage_ids[-1]
    # Ownership is registered on the same id the client will poll/abort.
    assert TaskQueueService.verify_owner(returned, "user-481")


def test_chain_apply_async_returns_final_stage_result():
    """Pin real Celery canvas semantics: apply_async() returns the LAST task's
    result and .parent walks back to the FIRST task."""
    result = _build_real_stub_chain().apply_async()

    node, root, depth = result, result, 0
    while getattr(node, "parent", None) is not None:
        node = node.parent
        root = node
        depth += 1

    assert depth == 4, "leaf result must have 4 ancestors (5-stage chain)"
    # The returned handle is the final stage; the ROOT (what the buggy code
    # returned) is a different task that reports SUCCESS after stage 1 only.
    assert result.id != root.id


@pytest.mark.asyncio
async def test_status_reports_mid_chain_failure(monkeypatch):
    """A stage-3 FAILURE must surface as the run's status.

    Celery leaves downstream task ids PENDING forever when an upstream stage
    fails, so polling the final id alone shows PENDING; polling the FIRST id
    (the old behavior) shows SUCCESS. Only the aggregate over all stage ids
    is honest.
    """
    stubs = _make_stub_chain_tasks()
    for attr, stub in zip(_STAGE_ATTRS, stubs):
        monkeypatch.setattr(task_chain, attr, stub)

    # Non-eager so nothing executes at submit time; tasks sit on the in-memory
    # broker queue. We then drive the backend marks by hand.
    monkeypatch.setattr(celery_app.conf, "task_always_eager", False)

    orch = ExplorerOrchestrator()
    final_id = await orch.start_exploration(
        query="海淀区学校", context=SearchContext(query="海淀区学校"),
    )
    run = orchestrator_mod.get_chain_run(final_id)
    discover_id, fetch_id, parse_id, geocode_id, validate_id = run.stage_ids

    # Discover + fetch done, parse died (e.g. unresolvable refs), successors
    # never started (PENDING — exactly how a real worker leaves a broken chain).
    for tid in (discover_id, fetch_id):
        celery_app.backend.store_result(tid, {"task_id": "x"}, state="SUCCESS")
    celery_app.backend.store_result(
        parse_id, RuntimeError("parse boom"), state="FAILURE"
    )

    status = await orch.get_task_status(final_id)
    assert status["status"] == "FAILURE", (
        f"mid-chain parse failure must read as FAILURE, got {status!r}"
    )
    assert status.get("stage") == "parse"
    # The old bug: the polled id was discover's, which reads SUCCESS here —
    # a fake 'completed' while the chain is actually dead.
    assert TaskQueueService.get_task_status(discover_id)["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_status_pending_while_later_stages_run(monkeypatch):
    """While stage 1 succeeded but stages 2-5 are still PENDING/alive, the
    run status must stay PROGRESS — never SUCCESS (the fake-completion bug)."""
    stubs = _make_stub_chain_tasks()
    for attr, stub in zip(_STAGE_ATTRS, stubs):
        monkeypatch.setattr(task_chain, attr, stub)
    monkeypatch.setattr(celery_app.conf, "task_always_eager", False)

    orch = ExplorerOrchestrator()
    final_id = await orch.start_exploration(
        query="海淀区学校", context=SearchContext(query="海淀区学校"),
    )
    run = orchestrator_mod.get_chain_run(final_id)

    # Stage 1-3 finished; stages 4-5 not started.
    for tid in run.stage_ids[:3]:
        celery_app.backend.store_result(tid, {"task_id": "x"}, state="SUCCESS")
    # geocode mid-flight with a PROGRESS mark, validate still PENDING.
    celery_app.backend.store_result(
        run.stage_ids[3], {"stage": "geocode", "progress": 40}, state="PROGRESS"
    )

    status = await orch.get_task_status(final_id)
    assert status["status"] == "PROGRESS"
    assert status.get("stage") == "geocode"


@pytest.mark.asyncio
async def test_abort_revokes_every_stage(monkeypatch):
    """Abort must revoke ALL stage task ids, not just the polled one.

    Revoking only the first id is a no-op once discover succeeded; revoking
    only the final id is a no-op while earlier stages run (the final task
    hasn't been dispatched yet).
    """
    revoked: list[str] = []

    def fake_revoke(task_id):
        revoked.append(task_id)
        return True

    monkeypatch.setattr(TaskQueueService, "revoke_task", staticmethod(fake_revoke))

    final_id = "fin-481"
    stage_ids = ["id-disc", "id-fetch", "id-parse", "id-geo", final_id]
    orchestrator_mod.register_chain_run(final_id, stage_ids)

    orch = ExplorerOrchestrator()
    ok = await orch.abort_task(final_id)

    assert ok is True
    assert set(revoked) == set(stage_ids), (
        f"abort must revoke all 5 stage ids, revoked={revoked}"
    )


@pytest.mark.asyncio
async def test_stream_covers_all_stages_until_validate(monkeypatch):
    """The SSE stream must stay open and report real per-stage progress while
    geocode (stage 4) runs, and only emit 'completed' when validate finishes.

    Regression shape of the bug: the stream polled the FIRST task's id, whose
    SUCCESS fired seconds in and ended the stream with a hardcoded final
    'completed' event while fetch/parse/geocode/validate ran dark.
    """
    final_id = "fin-stream-481"
    stage_ids = ["sid-1", "sid-2", "sid-3", "sid-4", final_id]
    orchestrator_mod.register_chain_run(final_id, stage_ids)

    # Per-task backend view, evolving over aggregate polls:
    #   polls 1-2: discover/fetch/parse SUCCESS, geocode PROGRESS 40, validate PENDING
    #   poll 3+:   all SUCCESS (validate result ready)
    base = {
        "sid-1": {"status": "SUCCESS", "progress": 100, "result": {}},
        "sid-2": {"status": "SUCCESS", "progress": 100, "result": {}},
        "sid-3": {"status": "SUCCESS", "progress": 100, "result": {}},
        "sid-4": {"status": "PROGRESS", "progress": 40, "result": None},
        final_id: {"status": "PENDING", "progress": 0, "result": None},
    }
    polls = {"n": 0}

    def counting_get(task_id):
        if task_id == final_id:
            polls["n"] += 1
            if polls["n"] >= 3:
                return {"status": "SUCCESS", "progress": 100,
                        "result": {"task_id": "x", "status": "completed"}}
        if polls["n"] >= 3:
            return {"status": "SUCCESS", "progress": 100, "result": {}}
        return dict(base[task_id])

    monkeypatch.setattr(TaskQueueService, "get_task_status", staticmethod(counting_get))
    # Skip the 1s poll interval; heartbeat window (15s) never trips.
    async def _no_sleep(_s):
        return None
    monkeypatch.setattr("app.services.explorer.orchestrator.asyncio.sleep", _no_sleep)

    orch = ExplorerOrchestrator()
    events = [e async for e in orch.stream_progress(final_id)]

    progress_payloads = [_parse_sse(e) for e in events if e.startswith("event: explorer_progress")]
    stages_seen = [p["stage"] for p in progress_payloads]

    # Geocode progress was reported while the chain was still alive...
    assert "geocode" in stages_seen, f"stream went dark during stages 2-5: {stages_seen}"
    assert any(p["status"] == "progress" and p["stage"] == "geocode" for p in progress_payloads)
    # ...and 'completed' appears exactly once, as the FINAL event.
    completed = [p for p in progress_payloads if p["status"] == "completed"]
    assert len(completed) == 1
    assert progress_payloads[-1]["status"] == "completed"
    assert progress_payloads[-1]["stage"] == "validate"
    # The generator terminated (loop ended) — no hang.
    assert events, "stream produced no events"


@pytest.mark.asyncio
async def test_stream_reports_late_stage_failure(monkeypatch):
    """A geocode-stage FAILURE must end the stream with a failed event naming
    the failed stage — not a fake 'completed'."""
    final_id = "fin-fail-481"
    stage_ids = ["fid-1", "fid-2", "fid-3", "fid-4", final_id]
    orchestrator_mod.register_chain_run(final_id, stage_ids)

    by_id = {
        "fid-1": {"status": "SUCCESS", "progress": 100, "result": {}},
        "fid-2": {"status": "SUCCESS", "progress": 100, "result": {}},
        "fid-3": {"status": "SUCCESS", "progress": 100, "result": {}},
        "fid-4": {"status": "FAILURE", "progress": 90, "result": None},
        final_id: {"status": "PENDING", "progress": 0, "result": None},
    }
    monkeypatch.setattr(
        TaskQueueService, "get_task_status",
        staticmethod(lambda tid: dict(by_id.get(tid, {"status": "PENDING", "progress": 0, "result": None}))),
    )

    async def _no_sleep(_s):
        return None
    monkeypatch.setattr("app.services.explorer.orchestrator.asyncio.sleep", _no_sleep)

    orch = ExplorerOrchestrator()
    events = [e async for e in orch.stream_progress(final_id)]
    payloads = [_parse_sse(e) for e in events if e.startswith("event: explorer_progress")]

    assert payloads[-1]["status"] == "failed"
    assert payloads[-1]["stage"] == "geocode"
    assert payloads[-1]["context"].get("final_status") == "FAILURE"
    assert all(p["status"] != "completed" for p in payloads), (
        "stream must not report completed when a late stage failed"
    )


@pytest.mark.asyncio
async def test_concurrent_explores_keep_independent_runs(monkeypatch):
    """Two concurrent explores register independent runs; status of one never
    reflects the other's stages."""
    stubs = _make_stub_chain_tasks()
    for attr, stub in zip(_STAGE_ATTRS, stubs):
        monkeypatch.setattr(task_chain, attr, stub)
    monkeypatch.setattr(celery_app.conf, "task_always_eager", False)

    orch = ExplorerOrchestrator()
    id_a = await orch.start_exploration(query="a", context=SearchContext(query="a"))
    id_b = await orch.start_exploration(query="b", context=SearchContext(query="b"))

    run_a = orchestrator_mod.get_chain_run(id_a)
    run_b = orchestrator_mod.get_chain_run(id_b)
    assert id_a != id_b
    assert set(run_a.stage_ids).isdisjoint(run_b.stage_ids)

    # Run A fully done, run B untouched: A reports SUCCESS, B stays pending.
    for tid in run_a.stage_ids:
        celery_app.backend.store_result(tid, {"task_id": "a"}, state="SUCCESS")
    status_a = await orch.get_task_status(id_a)
    status_b = await orch.get_task_status(id_b)
    assert status_a["status"] == "SUCCESS"
    assert status_b["status"] == "PROGRESS"


@pytest.mark.asyncio
async def test_status_revoked_stage_is_terminal_failure(monkeypatch):
    """A REVOKED stage (post-abort) reads as a terminal run status — the
    stream/status must not hang on it forever."""
    final_id = "fin-revoke-481"
    stage_ids = ["rid-1", "rid-2", "rid-3", "rid-4", final_id]
    orchestrator_mod.register_chain_run(final_id, stage_ids)

    by_id = {
        "rid-1": {"status": "SUCCESS", "progress": 100, "result": {}},
        "rid-2": {"status": "REVOKED", "progress": 0, "result": None},
        "rid-3": {"status": "PENDING", "progress": 0, "result": None},
        "rid-4": {"status": "PENDING", "progress": 0, "result": None},
        final_id: {"status": "PENDING", "progress": 0, "result": None},
    }
    monkeypatch.setattr(
        TaskQueueService, "get_task_status",
        staticmethod(lambda tid: dict(by_id.get(tid, {"status": "PENDING", "progress": 0, "result": None}))),
    )

    async def _no_sleep(_s):
        return None
    monkeypatch.setattr("app.services.explorer.orchestrator.asyncio.sleep", _no_sleep)

    orch = ExplorerOrchestrator()
    status = await orch.get_task_status(final_id)
    assert status["status"] == "REVOKED"
    assert status["stage"] == "fetch"

    events = [e async for e in orch.stream_progress(final_id)]
    payloads = [_parse_sse(e) for e in events if e.startswith("event: explorer_progress")]
    assert payloads[-1]["status"] == "failed"
    assert payloads[-1]["context"].get("final_status") == "REVOKED"


@pytest.mark.asyncio
async def test_status_backend_unavailable_degrades_to_unknown(monkeypatch):
    """When the result backend is unreachable every stage read degrades to
    UNKNOWN — the aggregate must report UNKNOWN, never a fake completion."""
    final_id = "fin-unknown-481"
    orchestrator_mod.register_chain_run(final_id, ["uid-1", "uid-2", "uid-3", "uid-4", final_id])
    monkeypatch.setattr(
        TaskQueueService, "get_task_status",
        staticmethod(lambda tid: {"task_id": tid, "status": "UNKNOWN", "result": None, "progress": 0}),
    )

    orch = ExplorerOrchestrator()
    status = await orch.get_task_status(final_id)
    assert status["status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_stream_stuck_chain_closes_at_deadline(monkeypatch):
    """Issue #593: a chain stuck in UNKNOWN (broker restart / expired result /
    unreachable result backend) must NOT stream forever on the independent
    /explorer/stream endpoint — the generator closes with an explicit failed
    terminal event after the wall-clock deadline instead of polling at 1 Hz
    + heartbeat indefinitely."""
    final_id = "fin-deadline-593"
    stage_ids = ["dd-1", "dd-2", "dd-3", "dd-4", final_id]
    orchestrator_mod.register_chain_run(final_id, stage_ids)
    polls = {"n": 0}

    def stuck_status(task_id):
        polls["n"] += 1
        return {"task_id": task_id, "status": "UNKNOWN", "result": None, "progress": 0}

    monkeypatch.setattr(TaskQueueService, "get_task_status", staticmethod(stuck_status))
    # Polls as fast as the loop runs (no real 1 s sleeps). Drive the deadline
    # deterministically: running the wall-clock cap down to real 0.05 s makes
    # the fast-spin poll count machine-dependent, so instead advance
    # time.monotonic() artificially (asyncio itself calls it per loop turn,
    # so the exact count also has an asyncio component — the assertions only
    # pin the ORDER of magnitude).
    import time as _time_mod
    clock = {"t": 0.0}
    def _fake_monotonic():
        clock["t"] += 0.01
        return clock["t"]
    monkeypatch.setattr(_time_mod, "monotonic", _fake_monotonic)
    async def _no_sleep(_s):
        return None
    monkeypatch.setattr("app.services.explorer.orchestrator.asyncio.sleep", _no_sleep)
    # shrink the wall-clock cap so the deadline trips within the test
    monkeypatch.setattr(orchestrator_mod, "_EXPLORER_STREAM_MAX_SECONDS", 0.05)

    events = [e async for e in ExplorerOrchestrator().stream_progress(final_id)]
    payloads = [_parse_sse(e) for e in events if e.startswith("event: explorer_progress")]
    assert payloads, "stuck stream must emit events before closing"
    assert payloads[-1]["status"] == "failed", (
        f"stuck chain must close with an explicit failed event: {payloads[-1]}"
    )
    assert payloads[-1]["context"].get("error") == "stream timeout"
    assert payloads[-1]["context"].get("final_status") == "FAILURE"
    # Bounded: a deadline-less loop would never return (the async-for above
    # would hang). The simulated 0.05 s cap must stop it after a handful of
    # polls — nothing like the ~900 polls a real 900 s / 1 Hz run implies.
    assert 1 <= polls["n"] <= 60, f"deadline did not bound the polls: {polls['n']}"
