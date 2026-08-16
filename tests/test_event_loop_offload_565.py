"""#565 regression: async data_fabric / project routes must not run sync
SQLAlchemy ORM on the event loop.

Before the fix, 12 async data_fabric routes and 3 async project routes ran
sync Session queries/commits directly on the loop — under DB latency or pool
contention a sync pool acquire stalls the loop up to pool_timeout=30s,
freezing every concurrent SSE/WS stream (the same failure mode #386/#421/#425
eliminated for the workflow engine body and remote fetches).

Two guards:
  1. static AST contract — no async route body calls sync `db.<orm>` directly;
  2. dynamic loop-responsiveness — the DB reads/commits run in a worker
     thread while a ticker proves the main event loop keeps ticking
     (technique mirrors tests/test_event_loop_offload_427.py).
"""
import asyncio
import threading
import time

import pytest
from fastapi import HTTPException

from app.api.routes import data_fabric as df
from app.api.routes import project as project_mod
from app.schemas.project_schema import WorkflowRunRequest

_main_thread = threading.get_ident()


async def _assert_loop_responsive_while(awaitable_factory, delay: float = 2.0):
    """Run awaitable_factory() and assert a timer fires mid-flight.

    Deterministic: with the work offloaded to a thread the task is still
    running when the test's timer completes; with the work on the loop the
    task finishes before the timer resumes (or the timer never fires).
    """
    task = asyncio.create_task(awaitable_factory())
    await asyncio.sleep(0.15)  # let it enter the slow work
    assert not task.done(), "work finished before the test could observe it"

    ticks = []

    async def _tick():
        await asyncio.sleep(0.05)
        ticks.append(True)

    tick = asyncio.create_task(_tick())
    await asyncio.sleep(0.15)
    assert tick.done() and ticks, "event loop was blocked during the work"
    assert not task.done(), "event loop was blocked during the work"
    return await task


# ─── Static contract: no direct sync ORM in async route bodies ────────────


def test_async_routes_have_no_direct_sync_db_calls():
    """Every async route must route its sync SQLAlchemy work through
    asyncio.to_thread / thread-local sessions (offload helpers), never call
    db.query/execute/commit directly."""
    import ast
    from pathlib import Path

    offenders = []
    for rel in ("app/api/routes/data_fabric.py", "app/api/routes/project.py"):
        tree = ast.parse(Path(rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "db"
                    and sub.attr in {
                        "query", "execute", "add", "delete", "commit",
                        "rollback", "flush", "scalars", "scalar_one_or_none",
                        "refresh", "get",
                    }
                ):
                    offenders.append(f"{rel}:{node.lineno} {node.name} → db.{sub.attr}")
    assert not offenders, (
        "async routes still call sync SQLAlchemy directly on the event loop "
        "(pool_timeout=30 worst-case loop freeze):\n" + "\n".join(offenders)
    )


# ─── Dynamic loop-responsiveness: slow DB work must stay off the loop ──────


class _SlowQuery:
    """Chainable query stand-in whose terminal calls sleep (simulating a slow /
    contended DB), recording the thread they ran on."""

    def __init__(self, result, observed: dict):
        self._result = result
        self._observed = observed

    def _note_thread(self):
        self._observed["thread"] = threading.get_ident()

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def offset(self, n):
        return self

    def limit(self, n):
        return self

    def options(self, *a, **k):
        return self

    def join(self, *a, **k):
        return self

    def all(self):
        self._note_thread()
        time.sleep(2.0)
        return self._result

    def first(self):
        self._note_thread()
        time.sleep(2.0)
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def count(self):
        self._note_thread()
        time.sleep(2.0)
        if isinstance(self._result, list):
            return len(self._result)
        return 1


class _SlowSession:
    """SessionLocal() stand-in: query() sleeps at the terminal calls."""

    def __init__(self, result=None, observed: dict | None = None):
        self._result = result if result is not None else []
        self._observed = observed or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def query(self, *a, **k):
        return _SlowQuery(self._result, self._observed)

    def execute(self, stmt):
        self._observed["thread"] = threading.get_ident()
        return _SlowResult()

    def add(self, *a, **k):
        pass

    def delete(self, *a, **k):
        pass

    def commit(self):
        self._observed["thread"] = threading.get_ident()
        time.sleep(2.0)

    def close(self):
        pass


class _SlowResult:
    def scalar_one_or_none(self):
        time.sleep(2.0)
        return None


class _StubRow:
    """Minimal DataSourceModel stand-in for the probe path (tenant guard reads
    org_id/owner_id; the route reads scalar columns only)."""

    id = "src-1"
    name = "stub"
    source_type = "wms"
    endpoint_url = "http://example.test/wms"
    status = "unknown"
    capabilities_json = {}
    connection_profile = {"options": {}}
    org_id = None
    owner_id = None
    last_health_check = None


class _FakeHealth:
    status = "healthy"

    def model_dump(self):
        return {"status": "healthy"}


@pytest.mark.asyncio
async def test_list_data_sources_db_read_off_loop(monkeypatch):
    observed: dict = {}
    monkeypatch.setattr(df, "SessionLocal", lambda: _SlowSession([], observed))

    res = await _assert_loop_responsive_while(
        lambda: df.list_data_sources(source_type=None, db=None, user=None)
    )
    assert observed.get("thread") != _main_thread, (
        "list_data_sources ran its DB read on the event loop thread"
    )
    assert res["sources"] == []


@pytest.mark.asyncio
async def test_list_spatial_catalog_db_read_off_loop(monkeypatch):
    observed: dict = {}
    monkeypatch.setattr(df, "SessionLocal", lambda: _SlowSession([], observed))

    res = await _assert_loop_responsive_while(
        lambda: df.list_spatial_catalog(db=None, user=None)
    )
    assert observed.get("thread") != _main_thread
    assert res["total"] == 0 and res["items"] == []


@pytest.mark.asyncio
async def test_probe_data_source_read_and_commit_off_loop(monkeypatch):
    observed: dict = {}
    monkeypatch.setattr(df, "SessionLocal", lambda: _SlowSession(_StubRow(), observed))
    # Patch the CLASS method (not the singleton instance): an instance-level
    # patch would linger as an own attribute and shadow later tests' class
    # patches of probe_profile.
    from app.services.data_fabric.manager import DataFabricManager

    monkeypatch.setattr(
        DataFabricManager, "probe_profile", staticmethod(lambda profile: _FakeHealth())
    )

    res = await _assert_loop_responsive_while(
        lambda: df.probe_data_source("src-1", db=None, user=None)
    )
    assert observed.get("thread") != _main_thread, (
        "probe_data_source ran its DB read/commit on the event loop thread"
    )
    assert res == {"status": "healthy"}


@pytest.mark.asyncio
async def test_run_workflow_auth_lookup_off_loop(monkeypatch):
    """The pre-engine get_project_with_auth precondition must also run in a
    worker thread — a slow pool acquire there previously froze the loop before
    the engine was ever offloaded."""
    observed: dict = {}
    monkeypatch.setattr(project_mod, "SessionLocal", lambda: _SlowSession([], observed))

    req = WorkflowRunRequest()
    with pytest.raises(HTTPException) as exc_info:
        await _assert_loop_responsive_while(
            lambda: project_mod.run_workflow(
                "proj-1", "wf-1", req=req, db=None, user={"user_id": "u1"}
            )
        )
    assert exc_info.value.status_code == 404
    assert observed.get("thread") != _main_thread, (
        "run_workflow's project auth lookup ran on the event loop thread"
    )