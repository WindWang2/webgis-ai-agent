"""Deterministic fault injection for the GeoCompute plane (ADR-0096).

Every fault is injected in-process (fake catalog fn / registry handler /
monkeypatched durable bridge / byte-level file corruption) with Events and
monotonic deadlines — no sleeps > 1s, no network, no docker, no marks.

Only chaos windows not already pinned in test_geocompute_execution.py
(happy path, reuse, generic failure-skip, transient retry, single-node
budget, mid-node cancel) and test_geocompute_durable.py (durable happy
path, pre-cancelled token).
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.lib.cancellation import CancellationToken
from app.lib.geo_raster.cog import validate_cog
from app.services.geocompute import (
    ExecutionNode,
    ExecutionPlan,
    ExecutionPolicyKind,
    ExecutionRunStatus,
    GeoExecutionEngine,
    NodeCategory,
    NodeExecutionError,
    ResourceBudget,
    RetryPolicy,
    ops,
)


def _engine() -> GeoExecutionEngine:
    return GeoExecutionEngine(max_workers=2)


def _fc(n: int) -> list[dict]:
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]},
            "properties": {"v": i, "kind": "a" if i % 2 == 0 else "b"},
        }
        for i in range(n)
    ]


def _filter_node(node_id: str, inputs: list[str] | None = None, n: int = 4) -> ExecutionNode:
    return ExecutionNode(
        node_id=node_id,
        category=NodeCategory.FILTER,
        inputs=inputs or [],
        parameters={
            "predicate": {"op": "eq", "field": "kind", "value": "a"},
            "features": _fc(n),
        },
    )


# ── 1. DB disconnect on the QUERY node → typed, no retry, no leaked state ──


def test_db_disconnect_on_query_fails_typed_skips_descendants(monkeypatch):
    """A sqlalchemy-ish disconnect (injected query_catalog_fn raising
    "connection reset") is NOT transient-safe by default: exactly one
    attempt, NODE_FAILED with retry_safe=False, descendants skipped, run
    FAILED, and no leaked node output for the failed subtree."""
    calls = {"n": 0}

    def reset_connection(db, item_id, spec):
        calls["n"] += 1
        raise RuntimeError("connection reset")

    monkeypatch.setattr(ops, "query_catalog_fn", reset_connection)

    query = ExecutionNode(
        node_id="q",
        category=NodeCategory.QUERY,
        retry=RetryPolicy(max_attempts=3),
        parameters={"dataset_id": "cat-1", "query": {"limit": 10}},
    )
    child = _filter_node("child", inputs=["q"])
    grandchild = ExecutionNode(
        node_id="agg",
        category=NodeCategory.AGGREGATE,
        inputs=["child"],
        parameters={"aggregates": [{"func": "count", "field": "v"}]},
    )
    engine = _engine()
    run = engine.execute_plan(ExecutionPlan(plan_id="chaos-db", nodes=[query, child, grandchild]))

    assert calls["n"] == 1, "retry_safe=False failure must not be retried"
    assert run.status is ExecutionRunStatus.FAILED
    ev = run.evidence["q"]
    assert ev.status == "failed"
    assert ev.error_code == "NODE_FAILED"
    assert ev.retry_safe is False
    assert ev.attempts == 1
    assert run.evidence["child"].status == "skipped"
    assert run.evidence["agg"].status == "skipped"
    for node_id in ("q", "child", "agg"):
        assert engine.get_node_output(run.run_id, node_id) is None, (
            f"failed run leaked output for node '{node_id}'"
        )


# ── 2. deadline expiration mid-run ──────────────────────────────────────────


def test_deadline_expiration_midrun_fails_node_and_bounds_wall_time(monkeypatch):
    """budget.deadline_s=0.2 with an operator that loops on 10ms sleeps and
    cooperative checkpoints: the checkpoint must convert the expired deadline
    into a typed DEADLINE_EXCEEDED failure, the descendant must never run,
    and the run must terminate promptly (bounded wall clock)."""

    def slow(ctx, node, payloads):
        for _ in range(100):
            time.sleep(0.01)
            ctx.checkpoint()
        return {"rows": [], "metadata": {}}

    monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, slow)

    slow_node = ExecutionNode(
        node_id="slow", category=NodeCategory.SOURCE_SCAN, parameters={"dataset_id": "d"}
    )
    child = _filter_node("child", inputs=["slow"])
    plan = ExecutionPlan(
        plan_id="chaos-deadline",
        nodes=[slow_node, child],
        budget=ResourceBudget(max_rows=200_000, deadline_s=0.2),
    )

    engine = _engine()
    run = engine.execute_plan(plan)

    ev = run.evidence["slow"]
    assert ev.status == "failed"
    assert ev.error_code == "DEADLINE_EXCEEDED"
    # Wave-level deadline check precedes ancestor-skip, so the pending
    # descendant is marked cancelled with the deadline reason (never executed).
    child_ev = run.evidence["child"]
    assert child_ev.status in {"skipped", "cancelled"}
    assert "deadline" in (child_ev.error_message or "")
    # Run converges to CANCELLED (deadline evidence) or FAILED (node failure);
    # both mean "did not complete".
    assert run.status in {ExecutionRunStatus.FAILED, ExecutionRunStatus.CANCELLED}
    assert run.status is not ExecutionRunStatus.COMPLETED
    assert run.wall_time_s is not None and run.wall_time_s < 2.0, "deadline did not bound the run"
    assert run.wall_time_s >= 0.15, "deadline fired before it expired (too early)"
    assert engine.get_node_output(run.run_id, "child") is None


# ── 3. budget exhaustion at node 2 of a chain; engine stays reusable ───────


def test_budget_exceeded_on_second_node_skips_third_and_engine_reusable(monkeypatch):
    """Combined chaos angle: node 1 succeeds, node 2 blows the plan budget,
    node 3 is skipped — and the SAME engine instance completes a subsequent
    run (no poisoned state in the run cache / result store)."""

    def producer(ctx, node, payloads):
        n = 5 if node.parameters.get("dataset_id") == "small" else 10
        rows = [{"i": i} for i in range(n)]
        ops._check_row_budget(rows, ctx, node)  # the real guard, not a fake raise
        return {"rows": rows, "metadata": {"n": n}}

    monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, producer)

    n1 = ExecutionNode(
        node_id="n1", category=NodeCategory.SOURCE_SCAN, parameters={"dataset_id": "small"}
    )
    n2 = ExecutionNode(
        node_id="n2",
        category=NodeCategory.SOURCE_SCAN,
        inputs=["n1"],
        parameters={"dataset_id": "burst"},
    )
    n3 = _filter_node("n3", inputs=["n2"])
    budget = ResourceBudget(max_rows=5, deadline_s=30)
    engine = _engine()

    run = engine.execute_plan(
        ExecutionPlan(plan_id="chaos-budget", nodes=[n1, n2, n3], budget=budget)
    )

    assert run.status is ExecutionRunStatus.FAILED
    assert run.evidence["n1"].status == "completed"
    assert run.evidence["n1"].rows_emitted == 5
    ev2 = run.evidence["n2"]
    assert ev2.status == "failed"
    assert ev2.error_code == "RESOURCE_BUDGET_EXCEEDED"
    assert "budget" in (ev2.error_message or "")
    assert run.evidence["n3"].status == "skipped"
    assert engine.get_node_output(run.run_id, "n2") is None

    # Engine reusable for the next run (fresh plan, within budget).
    ok_plan = ExecutionPlan(
        plan_id="chaos-budget-next",
        nodes=[_filter_node("fine", n=2)],
        budget=ResourceBudget(max_rows=5, deadline_s=30),
    )
    run2 = engine.execute_plan(ok_plan)
    assert run2.status is ExecutionRunStatus.COMPLETED
    assert run2.evidence["fine"].status in {"completed", "reused"}


# ── 4. worker-killed: durable job swept to stale ────────────────────────────


def _naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def job_db(tmp_path):
    """Sync engine + sessionmaker over a temp SQLite jobs DB (worker-style)."""
    from app.models.db_model import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'chaos-jobs.db'}")
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    yield Sess
    engine.dispose()


def _make_running_job(Sess) -> int:
    from app.services.jobs import DurableJobStore, JobKind, JobStatus

    with Sess() as db:
        job = DurableJobStore.create_sync(
            db,
            task_type="geocompute_node",
            kind=JobKind.analysis,
            owner_id="user-a",
            session_id="sess-a",
            parameters={},
            dispatch_spec={"task": "t", "args": [], "kwargs": {}},
        )
        assert DurableJobStore.transition_sync(
            db, job.id, JobStatus.queued, expected=[JobStatus.pending]
        )
        assert DurableJobStore.mark_running_sync(db, job.id)
        db.commit()
        return int(job.id)


def _sweep_stale_via_store(db_path: str) -> int:
    """Drive the REAL async sweep_stale over the same SQLite file."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services.jobs import DurableJobStore

    async def _run() -> int:
        eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        SF = async_sessionmaker(bind=eng, expire_on_commit=False)
        try:
            async with SF() as s:
                swept = await DurableJobStore.sweep_stale(s, stale_after_s=300)
                await s.commit()
            return swept
        finally:
            await eng.dispose()

    return asyncio.run(_run())


def test_await_node_job_raises_typed_on_stale_swept_job(job_db, monkeypatch):
    """Worker killed mid-node: the job's heartbeat ages out, the real
    ``DurableJobStore.sweep_stale`` flips it to stale, and
    ``durable.await_node_job`` (unit-tested directly with an injected
    session factory) surfaces a typed non-retryable NodeExecutionError."""
    from app.services.geocompute import durable as durable_mod
    from app.services.geocompute.durable import await_node_job
    from app.services.jobs import DurableJobStore, JobStatus, coerce_status

    Sess = job_db
    job_id = _make_running_job(Sess)

    # Worker dies: heartbeat ages back beyond the stale window (sweep semantics).
    with Sess() as db:
        row = DurableJobStore.get_sync(db, job_id)
        row.heartbeat_at = _naive_utc() - timedelta(seconds=900)
        db.commit()

    with Sess() as db:
        db_path = str(db.bind.url).replace("sqlite:///", "")
    assert _sweep_stale_via_store(db_path) == 1

    with Sess() as db:
        assert coerce_status(DurableJobStore.get_sync(db, job_id).status) is JobStatus.stale

    monkeypatch.setattr(durable_mod, "session_factory", Sess)
    with pytest.raises(NodeExecutionError) as ei:
        await_node_job(job_id, session_id="sess-a", deadline_ts=None, cancel_token=None)
    assert ei.value.code == "NODE_FAILED"
    assert ei.value.retry_safe is False
    assert "stale" in str(ei.value)
    assert "stale" in str(ei.value.details.get("job_status", ""))


def test_executor_marks_node_failed_when_durable_job_dies_stale(monkeypatch):
    """The executor side of the same accident: a durable node whose job dies
    stale must mark the node failed (typed, retry_safe=False), skip its
    descendants, fail the run, and leave no node output behind."""
    from app.services.geocompute import durable as durable_mod

    def fake_dispatch(node, *, session_id, plan_fingerprint, deadline_s):
        return {"job_id": 4242}

    def stale_await(job_id, *, session_id, deadline_ts, cancel_token=None):
        raise NodeExecutionError(
            f"durable job {job_id} ended stale",
            retry_safe=False,
            details={"job_id": str(job_id), "job_status": "stale"},
        )

    monkeypatch.setattr(durable_mod, "dispatch_node", fake_dispatch)
    monkeypatch.setattr(durable_mod, "await_node_job", stale_await)

    node = ExecutionNode(
        node_id="dn",
        category=NodeCategory.FILTER,
        policy=ExecutionPolicyKind.DURABLE_JOB,
        parameters={
            "predicate": {"op": "eq", "field": "kind", "value": "a"},
            "features": _fc(2),
        },
    )
    child = _filter_node("child", inputs=["dn"])

    engine = _engine()
    run = engine.execute_plan(
        ExecutionPlan(plan_id="chaos-stale", nodes=[node, child]), session_id="sess-a"
    )

    assert run.status is ExecutionRunStatus.FAILED
    ev = run.evidence["dn"]
    assert ev.status == "failed"
    assert ev.error_code == "NODE_FAILED"
    assert ev.retry_safe is False
    assert run.evidence["child"].status == "skipped"
    assert engine.get_node_output(run.run_id, "dn") is None


# ── 5. corrupt artifact / invalid COG ───────────────────────────────────────


def test_validate_cog_rejects_garbage_and_truncated_bytes(tmp_path):
    """Corrupt inputs must fail CLOSED with a structured report (ok=False,
    named issues) — never a partial 'valid' verdict. Garbage bytes are
    unopenable; a truncated real GeoTIFF fails structurally."""
    garbage = tmp_path / "garbage.tif"
    garbage.write_bytes(b"this is not a tiff at all" * 8)
    report = validate_cog(str(garbage))
    assert report["ok"] is False
    assert report["issues"], "unopenable input must name its issue"
    assert any(str(i).startswith("unopenable") for i in report["issues"])

    plain = tmp_path / "small.tif"
    with rasterio.open(
        plain, "w", driver="GTiff", width=64, height=64, count=1, dtype="uint8",
        crs="EPSG:32648", transform=from_origin(500000, 4000000, 10, 10),
    ) as dst:
        dst.write(np.zeros((64, 64), dtype="uint8"), 1)

    truncated = tmp_path / "truncated.tif"
    truncated.write_bytes(plain.read_bytes()[:64])  # keep only the TIFF header
    report2 = validate_cog(str(truncated))
    assert report2["ok"] is False
    assert report2["issues"]


# ── 6. cancellation between waves: pending nodes cancelled, no zombies ─────


def test_cancellation_between_waves_cancels_pending_and_leaves_no_threads(monkeypatch):
    token = CancellationToken()
    executed: list[str] = []

    def dispatcher(ctx, node, payloads):
        if node.node_id == "a":
            token.cancel("between waves")  # deterministic: fires INSIDE wave 1
            return {"rows": [{"ok": 1}], "metadata": {}}
        executed.append(node.node_id)
        return {"rows": [], "metadata": {}}

    monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, dispatcher)

    baseline_threads = threading.active_count()
    engine = _engine()
    plan = ExecutionPlan(
        plan_id="chaos-cancel-waves",
        nodes=[
            ExecutionNode(node_id="a", category=NodeCategory.SOURCE_SCAN,
                          parameters={"dataset_id": "d"}),
            ExecutionNode(node_id="b1", category=NodeCategory.SOURCE_SCAN,
                          inputs=["a"], parameters={"dataset_id": "d"}),
            ExecutionNode(node_id="b2", category=NodeCategory.SOURCE_SCAN,
                          inputs=["a"], parameters={"dataset_id": "d"}),
        ],
    )
    run = engine.execute_plan(plan, cancel_token=token)

    assert run.status is ExecutionRunStatus.CANCELLED
    assert run.evidence["a"].status == "completed"
    assert run.evidence["b1"].status == "cancelled"
    assert run.evidence["b2"].status == "cancelled"
    assert run.evidence["b1"].error_message == "between waves"
    assert executed == [], "pending wave must never execute after cancellation"
    assert engine.get_node_output(run.run_id, "b1") is None

    # No zombie threads: bounded join window back to the thread baseline.
    deadline = time.monotonic() + 2.0
    while threading.active_count() > baseline_threads and time.monotonic() < deadline:
        time.sleep(0.02)
    assert threading.active_count() <= baseline_threads


# ── 7. singleflight under failure: cache stays empty, recovery works ───────


class _FakeRedis:
    """Minimal SET-NX/get/setex/exists/eval surface for the sync path."""

    def __init__(self):
        self.kv: dict = {}
        self.locks: dict = {}

    def set(self, name, value, nx=False, px=None):
        if nx:
            if name in self.locks:
                return False
            self.locks[name] = value
            return True
        self.kv[name] = value
        return True

    def setex(self, name, ttl, value):
        self.kv[name] = value

    def get(self, name):
        return self.kv.get(name)

    def exists(self, name):
        return 1 if name in self.locks else 0

    def eval(self, script, numkeys, key, token):
        if self.locks.get(key) == token:
            self.locks.pop(key)
            return 1
        return 0


def test_singleflight_builder_failure_keeps_cache_empty_then_recovers(monkeypatch):
    """A builder that RAISES must not poison the cache: waiters see the typed
    failure, the lock is released, nothing is written under the key, and the
    next build succeeds and is served from cache thereafter."""
    from app.lib import tool_cache

    fake = _FakeRedis()
    monkeypatch.setattr(tool_cache, "_get_redis_client", lambda: fake)

    calls = {"n": 0}

    @tool_cache.cached_tool(ttl=60)
    def flaky_builder(x: int):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("builder exploded")
        return {"r": x}

    with pytest.raises(RuntimeError, match="builder exploded"):
        flaky_builder(x=3)

    assert fake.kv == {}, "failed build must not write any cache value"
    assert fake.locks == {}, "failed builder must release its singleflight lock"

    assert flaky_builder(x=3) == {"r": 3}, "subsequent build must succeed"
    assert calls["n"] == 2
    assert fake.kv, "successful build must populate the cache"

    assert flaky_builder(x=3) == {"r": 3}
    assert calls["n"] == 2, "third call must be served from cache"
