"""Regression tests for #426: report generation must not block the event loop.

Sibling of the #386 sweep (d8c0ab1 / tests/test_event_loop_offload_386.py),
which never touched the report path:

  1. ``ReportService.generate_report`` is an ``async def`` whose body is fully
     synchronous — Jinja2 templating of the full session history, MapSpec→SVG
     compilation, sync file writes and ``weasyprint.HTML(...).write_pdf()``
     all execute inline on the loop, from ``POST /api/v1/reports``.
  2. The ``generate_analysis_report`` agent tool (async def → ASYNC policy →
     awaited on the loop) opens a sync ``db_session()`` (blocking SQLAlchemy)
     before/after rendering.

Each test fakes the slow work with a sync ``time.sleep`` that records the
thread id it ran on, and asserts the main event loop stays responsive
*while* the work is running — with the work on the loop, the fake's sleep
blocks everything and the ticker assertion fails deterministically (same
technique as tests/test_event_loop_offload_386.py).

Run cost: ~1s per lag test (0.8s fake sleep), no network, no heavy deps.
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


async def _assert_loop_responsive_while(awaitable_factory, delay: float = 0.8):
    """Run awaitable_factory() and assert a 0.05s timer fires mid-flight.

    Deterministic: with the work offloaded the task is still running when the
    timer completes; with the work on the loop the task finishes before the
    test's own sleep resumes, so ``assert not task.done()`` fails.
    """
    task = asyncio.create_task(awaitable_factory())
    await asyncio.sleep(0.15)          # let it enter the slow work
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
    res = await _assert_loop_responsive_while(
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
    resp = await _assert_loop_responsive_while(
        lambda: route_mod.create_report(req, db=db, _user={"user_id": "u1"})
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
    ok = await _assert_loop_responsive_while(
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

    result = await _assert_loop_responsive_while(
        lambda: tool_fn(format="markdown", _session_id="sess-tool")
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
    resp = await route_mod.create_report(req, db=db, _user={"user_id": "u1"})

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

    result = await tool_fn(format="markdown", _session_id="sess-tool")
    assert "error" in result
    (report_row,) = report_rows.values()
    assert report_row.status == "failed"
    assert report_row.error_message == "生成过程未产出文件"


@pytest.mark.asyncio
async def test_concurrent_report_generation_parallel(monkeypatch, tmp_path):
    """Two concurrent report requests must render in parallel in worker
    threads, not serialize on the event loop."""
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
