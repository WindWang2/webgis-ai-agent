"""#565 regression: async data_fabric / project routes must not run sync
SQLAlchemy ORM on the event loop.

Offload probes assert the actual execution thread, independent of scheduler
latency. Dedicated throughput/timeout tests keep their explicit time budgets.
"""
import ast
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import data_fabric as df
from app.api.routes import project as project_mod
from app.schemas.project_schema import WorkflowRunRequest

_main_thread = threading.get_ident()


async def _run_offload_probe(awaitable_factory, delay: float = 2.0):
    """Run the probe; callers assert the actual worker thread identity.

    A fixed sleep/task-pending race depends on machine scheduling and can
    fail when correctly offloaded work finishes before the test resumes.
    """
    return await awaitable_factory()


# ─── Static contract: no direct sync ORM in async route bodies ────────────


def _direct_sync_db_calls(source: str, path: str = "<probe>") -> list[str]:
    """Inspect async lexical bodies, not deferred nested synchronous bodies.

    Function defaults/decorators still execute in the enclosing scope, while
    a nested async body gets its own contract even inside a synchronous helper.
    Runtime offload probes separately verify where production helpers execute.
    """
    offenders: list[str] = []

    class Visitor(ast.NodeVisitor):
        async_function: str | None = None

        def _header(self, node):
            for expression in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
                if expression is not None:
                    self.visit(expression)

        def visit_FunctionDef(self, node):
            self._header(node)
            enclosing = self.async_function
            self.async_function = None
            for statement in node.body:
                self.visit(statement)
            self.async_function = enclosing

        def visit_AsyncFunctionDef(self, node):
            self._header(node)
            enclosing = self.async_function
            self.async_function = node.name
            for statement in node.body:
                self.visit(statement)
            self.async_function = enclosing

        def visit_Lambda(self, node):
            for expression in (*node.args.defaults, *node.args.kw_defaults):
                if expression is not None:
                    self.visit(expression)

        def visit_Call(self, node):
            if (
                self.async_function is not None
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "db"
                and node.func.attr in {
                    "query", "execute", "add", "delete", "commit",
                    "rollback", "flush", "scalars", "scalar_one_or_none",
                    "refresh", "get",
                }
            ):
                offenders.append(f"{path}:{node.lineno} {self.async_function} → db.{node.func.attr}")
            self.generic_visit(node)

    Visitor().visit(ast.parse(source))
    return offenders


@pytest.mark.parametrize("method", ["get", "execute", "commit", "query"])
def test_sync_db_guard_rejects_direct_async_orm_calls(method):
    offenders = _direct_sync_db_calls(f"async def route():\n    db.{method}()\n")
    assert len(offenders) == 1
    assert f"route → db.{method}" in offenders[0]


def test_sync_db_guard_accepts_deferred_sync_worker_body():
    assert not _direct_sync_db_calls("""
async def route():
    def worker():
        return db.get(Project, project_id)
    return await asyncio.to_thread(worker)
""")


def test_sync_db_guard_still_checks_nested_async_and_eager_defaults():
    offenders = _direct_sync_db_calls("""
async def route():
    def worker(value=db.get(Project, project_id)):
        async def nested():
            db.commit()
        return value
    return await asyncio.to_thread(worker)
""")
    assert len(offenders) == 2
    assert "route → db.get" in offenders[0]
    assert "nested → db.commit" in offenders[1]


def test_async_routes_have_no_direct_sync_db_calls():
    """Every async route must route its sync SQLAlchemy work through
    asyncio.to_thread / thread-local sessions (offload helpers), never call
    db.query/execute/commit directly."""
    from pathlib import Path

    offenders = []
    for rel in ("app/api/routes/data_fabric.py", "app/api/routes/project.py"):
        offenders.extend(_direct_sync_db_calls(Path(rel).read_text(encoding="utf-8"), rel))
    assert not offenders, (
        "async routes still call sync SQLAlchemy directly on the event loop "
        "(pool_timeout=30 worst-case loop freeze):\n" + "\n".join(offenders)
    )


def test_no_unused_depends_get_db_in_offloaded_routes():
    """#932: async routes that offload via SessionLocal must not also hold
    Depends(get_db) — that holds two pool connections and exhausts QueuePool."""
    import ast
    from pathlib import Path

    offenders: list[str] = []
    for rel in ("app/api/routes/data_fabric.py", "app/api/routes/project.py"):
        src = Path(rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            # Does this async def declare `db: Session = Depends(get_db)`?
            has_depends = False
            for arg in node.args.args:
                if arg.arg == "db":
                    # inspect default via source slice
                    has_depends = True
            if not has_depends:
                continue
            # Check if body actually uses `db` (Load)
            used = any(
                isinstance(n, ast.Name) and n.id == "db" and isinstance(n.ctx, ast.Load)
                for n in ast.walk(node)
                if getattr(n, "lineno", 0) > node.lineno
            )
            if not used:
                offenders.append(f"{rel}:{node.lineno} {node.name} holds unused Depends(get_db)")
    assert not offenders, (
        "async routes hold unused Depends(get_db) (dual connection → pool exhaustion #932):\n"
        + "\n".join(offenders)
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

    res = await _run_offload_probe(
        lambda: df.list_data_sources(source_type=None, user=None)
    )
    assert observed.get("thread") != _main_thread, (
        "list_data_sources ran its DB read on the event loop thread"
    )
    assert res["sources"] == []


@pytest.mark.asyncio
async def test_list_spatial_catalog_db_read_off_loop(monkeypatch):
    observed: dict = {}
    monkeypatch.setattr(df, "SessionLocal", lambda: _SlowSession([], observed))

    res = await _run_offload_probe(
        lambda: df.list_spatial_catalog(user=None, q=None, source_id=None, geometry_type=None, feature_type=None, availability=None, limit=50, offset=0, summary=True)
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

    res = await _run_offload_probe(
        lambda: df.probe_data_source("src-1", user={"user_id": "u-admin", "role": "admin"})
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
        await _run_offload_probe(
            lambda: project_mod.run_workflow(
                "proj-1", "wf-1", req=req, user={"user_id": "u1"}
            )
        )
    assert exc_info.value.status_code == 404
    assert observed.get("thread") != _main_thread, (
        "run_workflow's project auth lookup ran on the event loop thread"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy,user_id", [(True, "owner"), (False, "owner"), (True, "other")])
async def test_workflow_continuation_preflight_and_owner_reads_stay_off_loop(monkeypatch, legacy, user_id):
    """Exercise the actual owner helper and offload seam, including rejection.

    Each context-managed Session is used sequentially by its owning worker;
    neither the preflight nor the engine worker may read ORM state on the loop.
    """
    from app.models.db_model import Conversation, User
    from app.models.project import Project, WorkflowRun
    from app.services import workflow_session_scope

    loop_thread = threading.get_ident()
    reads: list[tuple[type, int]] = []
    sessions = []
    created_sessions: list[str] = []
    engine_calls: list[tuple[str, str]] = []
    prior = SimpleNamespace(
        id="prior", project_id="project", workflow_id="workflow",
        session_id=None if legacy else "evidence", status="failed", input_bindings={},
    )
    rows = {
        WorkflowRun: prior,
        Project: SimpleNamespace(id="project", owner_id="owner", status="active"),
        User: SimpleNamespace(id=user_id, is_active=True),
        Conversation: SimpleNamespace(user_id="owner"),
    }

    class WorkerSession:
        def __enter__(self):
            self.thread = threading.get_ident()
            assert self.thread != loop_thread
            sessions.append(self)
            return self

        def __exit__(self, *args):
            assert threading.get_ident() == self.thread

        def get(self, model, key):
            assert threading.get_ident() == self.thread
            assert self.thread != loop_thread
            reads.append((model, self.thread))
            return rows.get(model)

    async def create_session(owner):
        created_sessions.append(owner)
        return "fresh-evidence"

    async def resume(session, **kwargs):
        assert threading.get_ident() == session.thread != loop_thread
        engine_calls.append(("resume", kwargs["session_id"]))
        return WorkflowRun(id="resumed")

    async def execute(session, **kwargs):
        assert threading.get_ident() == session.thread != loop_thread
        engine_calls.append(("execute", kwargs["session_id"]))
        assert kwargs["input_bindings"] == prior.input_bindings
        return WorkflowRun(id="rerun")

    monkeypatch.setattr(project_mod, "SessionLocal", WorkerSession)
    monkeypatch.setattr(workflow_session_scope, "create_workflow_session", create_session)
    monkeypatch.setattr(project_mod.WorkflowEngine, "resume_run", staticmethod(resume))
    monkeypatch.setattr(project_mod.WorkflowEngine, "execute_workflow_run", staticmethod(execute))
    kwargs = dict(prior_run_id="prior", expected_project_id="project", user_id=user_id,
                  allow_rerun=legacy, tool_registry=object())
    if user_id == "other":
        with pytest.raises(HTTPException) as error:
            await project_mod._run_workflow_engine(project_mod.WorkflowEngine.resume_run, **kwargs)
        assert error.value.status_code == 404
        assert not created_sessions and not engine_calls
        assert len(sessions) == 1
    else:
        result = await project_mod._run_workflow_engine(project_mod.WorkflowEngine.resume_run, **kwargs)
        assert result.id == ("rerun" if legacy else "resumed")
        assert len(sessions) == 2
        assert created_sessions == (["owner"] if legacy else [])
        assert engine_calls == ([("execute", "fresh-evidence")] if legacy else [("resume", "evidence")])
    assert {model for model, _ in reads} == (
        {WorkflowRun, Project, User} if legacy else {WorkflowRun, Conversation, User}
    )
    assert all(thread != loop_thread for _, thread in reads)
