"""Regression tests for #386: async routes must not do blocking sync work on the event loop.

The surviving sites (baseline 971eb05) block every concurrent SSE chat stream:

  1. 50MB GeoJSON parsed with ijson in ``get_upload_geojson``
  2. reportlab PDF render + sync file write in ``export_map_as_pdf``
  3. scrypt KDF (N=2^14 ≈ 50-150ms) in register/login
  4. sync SQLAlchemy Session driven by WorkflowEngine in run/replay/resume
  5. Celery broker/backend socket I/O (status polling every 3s, send_task,
     apply_async, revoke)

Each test fakes the slow work with a sync time.sleep (or an async sleep inside
the worker-thread loop for the engine) that records the thread id it ran on,
and asserts the main event loop stays responsive *while* the work is running —
with the work on the loop, the fake's sleep would block everything and the
final ``assert not task.done()`` fails deterministically (same technique as
tests/unit/test_execution_offload.py).

Run cost: ~1s per test (0.8s fake sleep), no network, no heavy deps.
"""
import asyncio
import threading
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

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


# ─── Site 1: upload.py get_upload_geojson (ijson parse of ≤50MB GeoJSON) ─────


@pytest.mark.asyncio
async def test_upload_geojson_parse_off_loop(monkeypatch, tmp_path):
    """ijson features parse must run in a worker thread, not on the loop."""
    from app.api.routes import upload as upload_mod

    observed = {}

    class _SlowIjson:
        def items(self, f, prefix):
            def _gen():
                observed["thread"] = threading.get_ident()
                time.sleep(0.8)
                yield {"type": "Feature", "properties": {}, "geometry": None}
            return _gen()

    monkeypatch.setattr(upload_mod, "ijson", _SlowIjson())
    monkeypatch.setattr(upload_mod.settings, "DATA_DIR", str(tmp_path))

    geojson_path = tmp_path / "data.geojson"
    geojson_path.write_text('{"type": "FeatureCollection", "features": []}')

    fake_record = MagicMock()
    fake_record.id = 1
    fake_record.session_id = None  # 匿名会话，跳过所有权校验
    fake_record.file_type = "vector"
    fake_record.filename = str(geojson_path)
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = fake_record
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(upload_mod, "_verify_session_owner", AsyncMock())

    @asynccontextmanager
    async def fake_async_db_session():
        yield fake_db

    monkeypatch.setattr(upload_mod, "async_db_session", fake_async_db_session)

    result = await _assert_loop_responsive_while(
        lambda: upload_mod.get_upload_geojson(1, {"user_id": "test-user"})
    )
    assert observed["thread"] != _main_thread, "ijson parse ran on the event loop thread"
    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) == 1


# ─── Site 2: map.py export_map_as_pdf (reportlab render + sync file write) ────


@pytest.mark.asyncio
async def test_pdf_render_off_loop(monkeypatch, tmp_path):
    """generate_map_pdf + file write must run in a worker thread."""
    import io

    from fastapi import UploadFile

    from app.api.routes import map as map_mod

    observed = {}

    def _slow_render(img_bytes, **kwargs):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(
        "app.lib.cartography.pdf_renderer.generate_map_pdf", _slow_render
    )
    monkeypatch.setattr(map_mod, "EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(map_mod, "_set_export_owner", lambda *a, **k: None)

    file = UploadFile(file=io.BytesIO(b"png-bytes"), filename="map.png")
    try:
        res = await _assert_loop_responsive_while(
            lambda: map_mod.export_map_as_pdf(file, title="t", _user={"user_id": "u1"})
        )
        assert res["format"] == "pdf"
        assert observed["thread"] != _main_thread, "PDF render ran on the event loop thread"
        assert (tmp_path / res["filename"]).exists()
    finally:
        await file.close()


@pytest.mark.asyncio
async def test_pdf_render_value_error_still_400(monkeypatch, tmp_path):
    """Renderer ValueError must still map to HTTP 400 after offloading."""
    import io

    from fastapi import HTTPException, UploadFile

    from app.api.routes import map as map_mod

    def _raise_value_error(img_bytes, **kwargs):
        raise ValueError("无法解析图片数据")

    monkeypatch.setattr(
        "app.lib.cartography.pdf_renderer.generate_map_pdf", _raise_value_error
    )
    monkeypatch.setattr(map_mod, "EXPORT_DIR", str(tmp_path))

    file = UploadFile(file=io.BytesIO(b"not-an-image"), filename="bad.png")
    try:
        with pytest.raises(HTTPException) as exc_info:
            await map_mod.export_map_as_pdf(file, _user={"user_id": "u1"})
        assert exc_info.value.status_code == 400
    finally:
        await file.close()


# ─── Site 3: auth.py scrypt KDF (hash_password / verify_password) ────────────


@pytest.mark.asyncio
async def test_register_kdf_off_loop(monkeypatch):
    """hash_password (scrypt N=2^14) must run in a worker thread."""
    from app.api.routes import auth as auth_mod

    observed = {}

    def _slow_hash(pw):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return "scrypt$16384$8$1$00$00"

    monkeypatch.setattr(auth_mod, "hash_password", _slow_hash)
    monkeypatch.setattr(auth_mod, "_allow_public_register", lambda: True)
    monkeypatch.setattr(auth_mod, "_get_client_ip", lambda request: "1.2.3.4")

    class _Limiter:
        async def is_allowed(self, *a, **k):
            return True

    monkeypatch.setattr(auth_mod, "get_rate_limiter", AsyncMock(return_value=_Limiter()))

    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = None  # 无重名用户
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)
    fake_db.add = MagicMock()  # 同步调用点，避免未 await 的 coroutine 告警

    req = auth_mod.RegisterRequest(
        username="tester", email="t@example.com", password="secret123"
    )
    resp = await _assert_loop_responsive_while(
        lambda: auth_mod.register(req, MagicMock(), db=fake_db)
    )
    assert observed["thread"] != _main_thread, "scrypt hash ran on the event loop thread"
    assert resp.access_token


@pytest.mark.asyncio
async def test_login_kdf_off_loop(monkeypatch):
    """verify_password (scrypt, dummy-hash path doubled) must run in a worker thread."""

    from app.api.routes import auth as auth_mod
    from app.models.db_model import User

    observed = {}

    def _slow_verify(pw, stored):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return True

    monkeypatch.setattr(auth_mod, "verify_password", _slow_verify)
    monkeypatch.setattr(auth_mod, "_get_client_ip", lambda request: "1.2.3.4")

    class _Limiter:
        async def is_allowed(self, *a, **k):
            return True

    monkeypatch.setattr(auth_mod, "get_rate_limiter", AsyncMock(return_value=_Limiter()))

    user = User(
        id="u1",
        username="tester",
        email="t@example.com",
        full_name=None,
        role="viewer",
        is_active=True,
        token_version=0,
        password_hash="scrypt$16384$8$1$00$00",
        last_login=None,
        login_count=None,
    )
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = user
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)

    req = auth_mod.LoginRequest(identifier="tester", password="secret123")
    resp = await _assert_loop_responsive_while(
        lambda: auth_mod.login(req, MagicMock(), db=fake_db)
    )
    assert observed["thread"] != _main_thread, "scrypt verify ran on the event loop thread"
    assert resp.access_token
    assert user.login_count == 1
    assert user.last_login is not None


# ─── Site 4: project.py workflow engine (sync Session driver) ────────────────


@pytest.mark.asyncio
async def test_run_workflow_engine_off_loop(monkeypatch):
    """WorkflowEngine.execute_workflow_run must run in a worker thread with a
    Session created in that same thread (never shared cross-thread)."""
    from app.api.routes import project as route_mod
    from app.schemas.project_schema import WorkflowRunRequest

    observed = {}

    async def _slow_engine(db, **kwargs):
        observed["thread"] = threading.get_ident()
        observed["session_id"] = id(db)
        await asyncio.sleep(0.8)  # 只阻塞 worker 线程自己的事件循环
        return {"id": "wfrun_test_1", "status": "completed"}

    monkeypatch.setattr(
        route_mod.WorkflowEngine, "execute_workflow_run", staticmethod(_slow_engine)
    )
    monkeypatch.setattr(route_mod, "get_tool_registry", lambda: object())

    class _FakeProjectService:
        @staticmethod
        def get_project_with_auth(**kwargs):
            return {"id": "proj_1"}

    monkeypatch.setattr(route_mod, "ProjectService", _FakeProjectService)

    injected_db = MagicMock()
    req = WorkflowRunRequest(input_bindings={"aoi": "Haidian"}, start_from_step=None)
    res = await _assert_loop_responsive_while(
        lambda: route_mod.run_workflow(
            "proj_1", "wf_1", req,
            db=injected_db,
            user={"user_id": "u1", "org_id": None},
        )
    )
    assert res["status"] == "completed"
    assert observed["thread"] != _main_thread, "workflow engine ran on the event loop thread"
    assert observed["session_id"] != id(injected_db), (
        "engine reused the request-injected Session across threads"
    )


# ─── Site 5: Celery broker/backend socket I/O ────────────────────────────────


@pytest.mark.asyncio
async def test_celery_status_poll_off_loop(monkeypatch):
    """AsyncResult.info/.ready() (frontend polls every 3s) must run off-loop."""
    from app.api.routes import task as task_mod

    observed = {}

    def _slow_status(tid):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return {"task_id": tid, "status": "SUCCESS", "result": None, "progress": 100}

    monkeypatch.setattr(
        task_mod.TaskQueueService, "get_task_status", staticmethod(_slow_status)
    )
    monkeypatch.setattr(task_mod, "_verify_celery_owner", AsyncMock())
    monkeypatch.setattr(
        task_mod.DurableJobStore, "get_by_celery_id", AsyncMock(return_value=None)
    )

    payload = await _assert_loop_responsive_while(
        lambda: task_mod.get_celery_task_status(
            "celery-1", db=AsyncMock(), _user={"user_id": "u1"}, owner_token=None
        )
    )
    assert payload["status"] == "SUCCESS"
    assert observed["thread"] != _main_thread, "Celery backend read ran on the event loop thread"


@pytest.mark.asyncio
async def test_explorer_submit_and_status_off_loop(monkeypatch):
    """Orchestrator apply_async + AsyncResult read (SSE stream polls 1s) off-loop."""
    import app.services.explorer.orchestrator as orch_mod
    from app.services.explorer.models import SearchContext
    from app.services.explorer.orchestrator import ExplorerOrchestrator

    orch = ExplorerOrchestrator()
    observed = {}

    def _slow_submit():
        observed["submit_thread"] = threading.get_ident()
        time.sleep(0.8)
        root = MagicMock()
        root.id = "celery_root_1"
        root.parent = None
        return root

    def _slow_status(tid):
        observed["status_thread"] = threading.get_ident()
        time.sleep(0.8)
        return {"task_id": tid, "status": "PROGRESS", "result": {"meta": {}}, "progress": 50}

    # start_exploration 内部 `chain(...)` 后 apply_async —— 用假 chain 注入慢提交。
    monkeypatch.setattr(
        orch_mod, "chain", lambda *tasks: MagicMock(apply_async=_slow_submit)
    )
    monkeypatch.setattr(
        orch, "task_queue",
        MagicMock(get_task_status=_slow_status, revoke_task=lambda tid: True),
    )

    # apply_async + parent 遍历必须 off-loop。
    task_id = await _assert_loop_responsive_while(
        lambda: orch.start_exploration(
            "query", SearchContext(query="q", expected_data_type="poi_list"),
            session_id="s1", user_id="u1",
        )
    )
    assert task_id == "celery_root_1"
    assert observed["submit_thread"] != _main_thread, "apply_async ran on the event loop thread"

    # get_task_status（SSE stream_progress 每秒轮询）同样 off-loop。
    status = await _assert_loop_responsive_while(
        lambda: orch.get_task_status(task_id)
    )
    assert status["progress"] == 50
    assert observed["status_thread"] != _main_thread, "AsyncResult read ran on the event loop thread"
