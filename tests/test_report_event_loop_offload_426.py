"""Regression tests for #426: report generation must not block the event loop.

Offload probes assert the actual execution thread, independent of scheduler
latency. Dedicated throughput/timeout tests keep their explicit time budgets.
"""
import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.database as core_db_mod
from app.models.api_response import ErrCode
from app.models.db_model import Conversation
from app.models.report import Report
from app.services import report_service as report_service_mod
from app.services.report_service import ReportService

_main_thread = threading.get_ident()


async def _run_offload_probe(awaitable_factory, delay: float = 0.8):
    """Run the probe; callers assert the actual worker thread identity.

    A fixed sleep/task-pending race depends on machine scheduling and can
    fail when correctly offloaded work finishes before the test resumes.
    """
    return await awaitable_factory()


# ─── Fakes ───────────────────────────────────────────────────────────────────


def _msg(role: str = "user", content: str = "hello") -> MagicMock:
    m = MagicMock()
    m.role = role
    m.content = content
    m.tool_calls = None
    m.tool_result = None
    return m


def _make_async_db(conversation, messages, created: dict) -> AsyncMock:
    """AsyncSession double for the saga's phase-1 reads + 'generating' insert."""
    db = AsyncMock()

    async def _get(model, pk):
        if model is Conversation:
            return conversation
        return None

    db.get = AsyncMock(side_effect=_get)

    exec_result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = messages
    exec_result.scalars.return_value = scalars
    db.execute = AsyncMock(return_value=exec_result)

    db.add = MagicMock(side_effect=lambda row: created.__setitem__("report", row))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.expunge = MagicMock()
    return db


def _make_session_factory(created: dict):
    """AsyncSessionLocal double: phase-2 terminal-status update session."""

    @asynccontextmanager
    async def factory():
        db2 = AsyncMock()

        async def _get2(model, pk):
            return created.get("report")

        db2.get = AsyncMock(side_effect=_get2)
        db2.commit = AsyncMock()
        yield db2

    return factory


def _fake_weasyprint(observed: dict, delay: float = 0.8, error: Exception | None = None):
    """WeasyPrint double: write_pdf stalls `delay` then writes a stub PDF."""

    class _SlowHTML:
        def __init__(self, string=None, **kwargs):
            pass

        def write_pdf(self, output_path):
            observed["thread"] = threading.get_ident()
            time.sleep(delay)
            if error is not None:
                raise error
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(b"%PDF-1.4 fake-report")

    mod = MagicMock()
    mod.HTML = _SlowHTML
    return mod


# ─── Site 1: ReportService.generate_report (sync render body in async def) ──


@pytest.mark.asyncio
async def test_service_pdf_render_off_loop(monkeypatch, tmp_path):
    """WeasyPrint write_pdf (multi-second on long sessions) must run in a
    worker thread; the saga must still complete with status 'completed'."""
    observed = {}
    monkeypatch.setattr(report_service_mod, "weasyprint", _fake_weasyprint(observed))
    monkeypatch.setattr(report_service_mod, "REPORT_DIR", str(tmp_path))

    created: dict = {}
    db = _make_async_db(
        Conversation(id="sess-1", title="测试会话"),
        [_msg(), _msg("assistant", "分析完成")],
        created,
    )

    svc = ReportService()
    res = await _run_offload_probe(
        lambda: svc.create_and_generate(
            db, "sess-1", format="pdf", session_factory=_make_session_factory(created)
        )
    )
    assert observed["thread"] != _main_thread, "WeasyPrint render ran on the event loop thread"
    assert res.success, res.message
    assert created["report"].status == "completed"
    assert created["report"].file_size > 0


@pytest.mark.asyncio
async def test_route_post_reports_render_off_loop(monkeypatch, tmp_path):
    """POST /api/v1/reports must not stall the loop for the whole render."""
    from app.api.routes import report as route_mod

    observed = {}
    monkeypatch.setattr(route_mod, "verify_session_owner", AsyncMock())
    monkeypatch.setattr(
        route_mod, "mapspec_store", MagicMock(get_mapspec=AsyncMock(return_value=None))
    )
    monkeypatch.setattr(report_service_mod, "weasyprint", _fake_weasyprint(observed))
    monkeypatch.setattr(report_service_mod, "REPORT_DIR", str(tmp_path))

    created: dict = {}
    monkeypatch.setattr(
        core_db_mod, "AsyncSessionLocal", _make_session_factory(created)
    )
    db = _make_async_db(Conversation(id="sess-1", title="测试会话"), [_msg()], created)

    req = route_mod.GenerateReportRequest(session_id="sess-1", format="pdf")
    resp = await _run_offload_probe(
        lambda: route_mod.create_report(
            req, db=db, _user={"user_id": "u1"}, owner_token=None
        )
    )
    assert observed["thread"] != _main_thread, "WeasyPrint render ran on the event loop thread"
    assert resp.success
    assert resp.data["status"] == "completed"


@pytest.mark.asyncio
async def test_large_report_markdown_render_off_loop(monkeypatch, tmp_path):
    """Full-history Jinja2/markdown render of a long session must be offloaded."""
    svc = ReportService()
    observed = {}

    real_render = ReportService._render_markdown

    def slow_render(self, data):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)  # simulated multi-second render of the full history
        return real_render(self, data)

    monkeypatch.setattr(ReportService, "_render_markdown", slow_render)

    messages = [
        {"role": "user" if i % 2 else "assistant", "content": f"消息 {i} " * 40}
        for i in range(2000)
    ]
    out = tmp_path / "large.md"
    ok = await _run_offload_probe(
        lambda: svc.generate_report(
            session_id="s-large",
            session_title="长会话",
            messages=messages,
            output_path=str(out),
            format="markdown",
        )
    )
    assert ok
    assert observed["thread"] != _main_thread, "markdown render ran on the event loop thread"
    assert out.stat().st_size > 10_000


# ─── Site 2: generate_analysis_report tool (sync db_session on the loop) ─────


@pytest.mark.asyncio
async def test_tool_sync_db_off_loop(monkeypatch, tmp_path):
    """Both db_session() blocks (fetch+insert, terminal status) must run in a
    worker thread; the render must go through the offloaded service path."""
    from app.tools import report as tool_mod
    from app.tools.registry import ToolRegistry
    from app.tools.report import register_report_tools

    reg = ToolRegistry()
    register_report_tools(reg)
    tool_fn = reg._tools["generate_analysis_report"]

    observed_threads: list[int] = []
    report_rows: dict[str, Report] = {}
    conversation = Conversation(id="sess-tool", title="工具会话")

    class _FakeSyncDB:
        def get(self, model, pk):
            if model is Conversation:
                return conversation
            if model is Report:
                return report_rows.get(pk)
            return None

        def query(self, model):
            q = MagicMock()
            q.filter.return_value.order_by.return_value.all.return_value = [
                _msg(),
                _msg("assistant", "done"),
            ]
            return q

        def add(self, row):
            report_rows[row.id] = row

        def commit(self):
            pass

    @contextmanager
    def fake_db_session():
        observed_threads.append(threading.get_ident())
        time.sleep(0.8)  # blocking SQLAlchemy on a long session
        yield _FakeSyncDB()

    monkeypatch.setattr(tool_mod, "db_session", fake_db_session)
    monkeypatch.setattr(tool_mod, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(
        tool_mod, "mapspec_store", MagicMock(get_mapspec=AsyncMock(return_value=None))
    )

    async def fake_render(**kwargs):
        os.makedirs(os.path.dirname(kwargs["output_path"]), exist_ok=True)
        with open(kwargs["output_path"], "w", encoding="utf-8") as f:
            f.write("# fake report\n")
        return True

    monkeypatch.setattr(tool_mod.spatial_report_engine, "generate_report", fake_render)

    result = await _run_offload_probe(
        lambda: tool_fn(format="markdown", session_id="sess-tool")
    )
    assert observed_threads and all(
        t != _main_thread for t in observed_threads
    ), "sync db_session ran on the event loop thread"
    assert len(observed_threads) == 2, "both db_session blocks must be offloaded"
    assert result.get("type") == "report_generated"
    assert result["report_id"] in report_rows
    assert report_rows[result["report_id"]].status == "completed"


# ─── Adversarial: failure semantics + concurrency ────────────────────────────


@pytest.mark.asyncio
async def test_render_failure_marks_failed_not_500(monkeypatch, tmp_path):
    """A WeasyPrint crash must end the saga as status='failed' (ApiResponse
    failure), never an unhandled 500 — including after the thread offload."""
    from app.api.routes import report as route_mod

    observed = {}
    monkeypatch.setattr(route_mod, "verify_session_owner", AsyncMock())
    monkeypatch.setattr(
        route_mod, "mapspec_store", MagicMock(get_mapspec=AsyncMock(return_value=None))
    )
    monkeypatch.setattr(
        report_service_mod,
        "weasyprint",
        _fake_weasyprint(observed, error=RuntimeError("weasyprint exploded")),
    )
    monkeypatch.setattr(report_service_mod, "REPORT_DIR", str(tmp_path))

    created: dict = {}
    monkeypatch.setattr(
        core_db_mod, "AsyncSessionLocal", _make_session_factory(created)
    )
    db = _make_async_db(Conversation(id="sess-2", title="t"), [_msg()], created)

    req = route_mod.GenerateReportRequest(session_id="sess-2", format="pdf")
    resp = await route_mod.create_report(
        req, db=db, _user={"user_id": "u1"}, owner_token=None
    )

    assert resp.success is False
    assert resp.code == ErrCode.SERVER_ERROR
    assert created["report"].status == "failed"
    assert created["report"].error_message


@pytest.mark.asyncio
async def test_tool_render_failure_marks_failed(monkeypatch, tmp_path):
    """Tool path: render returning False must set status='failed' + error dict."""
    from app.tools import report as tool_mod
    from app.tools.registry import ToolRegistry
    from app.tools.report import register_report_tools

    reg = ToolRegistry()
    register_report_tools(reg)
    tool_fn = reg._tools["generate_analysis_report"]

    report_rows: dict[str, Report] = {}

    class _FakeSyncDB:
        def get(self, model, pk):
            if model is Conversation:
                return Conversation(id="sess-tool", title="t")
            if model is Report:
                return report_rows.get(pk)
            return None

        def query(self, model):
            q = MagicMock()
            q.filter.return_value.order_by.return_value.all.return_value = [_msg()]
            return q

        def add(self, row):
            report_rows[row.id] = row

        def commit(self):
            pass

    @contextmanager
    def fake_db_session():
        yield _FakeSyncDB()

    monkeypatch.setattr(tool_mod, "db_session", fake_db_session)
    monkeypatch.setattr(tool_mod, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(
        tool_mod, "mapspec_store", MagicMock(get_mapspec=AsyncMock(return_value=None))
    )

    async def failing_render(**kwargs):
        return False

    monkeypatch.setattr(tool_mod.spatial_report_engine, "generate_report", failing_render)

    result = await tool_fn(format="markdown", session_id="sess-tool")
    assert "error" in result
    (report_row,) = report_rows.values()
    assert report_row.status == "failed"
    assert report_row.error_message == "生成过程未产出文件"


@pytest.mark.asyncio
async def test_concurrent_report_generation_parallel(monkeypatch, tmp_path):
    """Two concurrent report requests must render in parallel in worker
    threads, not serialize on the event loop."""
    import contextlib
    from app.services import publication_export as pub_mod

    # V6 R1-M6 serializes write_pdf via _WEASYPRINT_LOCK; bypass the lock here to
    # verify that the async/threadpool offload layer itself allows parallel execution.
    monkeypatch.setattr(pub_mod, "render_pdf_exclusive", lambda fn: fn())
    monkeypatch.setattr(pub_mod, "_WEASYPRINT_LOCK", contextlib.nullcontext())
    monkeypatch.setattr(report_service_mod, "weasyprint", _fake_weasyprint({}))
    monkeypatch.setattr(report_service_mod, "REPORT_DIR", str(tmp_path))
    svc = ReportService()

    async def _one(i: int):
        created: dict = {}
        db = _make_async_db(Conversation(id=f"s{i}", title="t"), [_msg()], created)
        return await svc.create_and_generate(
            db, f"s{i}", format="pdf", session_factory=_make_session_factory(created)
        )

    t0 = time.monotonic()
    r1, r2 = await asyncio.gather(_one(1), _one(2))
    elapsed = time.monotonic() - t0

    assert r1.success and r2.success
    assert elapsed < 1.5, (
        f"concurrent 0.8s renders serialized (took {elapsed:.2f}s) — "
        "they are running on the event loop thread"
    )


async def _assert_real_terminal_retry(monkeypatch, tmp_path, failures=1) -> bool:
    from pathlib import Path
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.database import Base
    from app.models.db_model import Message

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'report-retry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as seed:
        seed.add(Conversation(id="retry-status", title="status"))
        seed.add(Message(conversation_id="retry-status", role="user", content="hello"))
        await seed.commit()
    monkeypatch.setattr(report_service_mod, "REPORT_DIR", str(tmp_path))
    async def render(**kwargs):
        Path(kwargs['output_path']).write_bytes(b'%PDF-test')
        return True
    service = ReportService()
    monkeypatch.setattr(service, 'generate_report', render)
    attempts = []
    reports = []

    @asynccontextmanager
    async def factory():
        async with maker() as terminal_db:
            attempts.append(terminal_db)
            original_get = terminal_db.get
            async def get(model, key):
                row = await original_get(model, key)
                reports.append(row)
                return row
            monkeypatch.setattr(terminal_db, 'get', get)
            if len(attempts) <= failures:
                monkeypatch.setattr(terminal_db, 'commit', AsyncMock(side_effect=RuntimeError('database unavailable')))
            yield terminal_db

    try:
        async with maker() as primary_db:
            result = await service.create_and_generate(
                primary_db, 'retry-status', format='pdf', session_factory=factory)
        assert len(attempts) == 2 and attempts[0] is not attempts[1]
        assert reports[0] is not reports[1], 'terminal transactions must not share an ORM object'
        async with maker() as independent_reader:
            persisted = await independent_reader.get(Report, result.report_data['id'])
            assert persisted is not reports[0] and persisted is not reports[1]
            if failures == 1:
                assert result.success and result.report_data['status'] == persisted.status == 'completed'
                assert result.report_data['file_size'] == persisted.file_size > 0
            else:
                assert not result.success and result.report_data['status'] == 'failed'
                assert persisted.status == report_service_mod.REPORT_STATUS_IN_PROGRESS
                assert 'could not be saved' in result.report_data['error_message']
        return True
    finally:
        await engine.dispose()


@pytest.mark.parametrize('failures', [1, 2])
async def test_terminal_commit_failure_retries_without_false_success(monkeypatch, tmp_path, failures):
    assert await _assert_real_terminal_retry(monkeypatch, tmp_path, failures)


async def test_terminal_retry_regression_detects_removed_detached_object_sync(monkeypatch, tmp_path):
    """Mutation proof: a distinct DB row cannot accidentally update the receipt."""
    import inspect
    import textwrap
    source = textwrap.dedent(inspect.getsource(ReportService.create_and_generate))
    sync = """            report.status = final_status
            report.error_message = final_error
            if final_size is not None:
                report.file_size = final_size
"""
    assert source.count(sync) == 1
    mutant = source.replace(sync, '')
    namespace = {}
    exec(compile(mutant, '<report-retry-no-detached-sync>', 'exec'), report_service_mod.__dict__, namespace)
    monkeypatch.setattr(ReportService, 'create_and_generate', namespace['create_and_generate'])
    with pytest.raises(AssertionError):
        await _assert_real_terminal_retry(monkeypatch, tmp_path, failures=1)
