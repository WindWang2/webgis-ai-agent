"""Worker 生命周期与平台指标测试（ADR-0131 D4）。

覆盖：register/draining/offline 迁移、有界 drain（deadline 到点诚实退出）、
活跃任务计数与 gauge 同步、celery 信号防御性挂接、InflightGaugeMiddleware
的进/出/异常计数。
"""
from __future__ import annotations

import asyncio

import pytest

from app.lib.observability.metrics import InflightGaugeMiddleware
from app.services.jobs.worker_lifecycle import (
    WorkerLifecycle,
    install_celery_lifecycle_signals,
    reset_worker_lifecycle_for_tests,
)


# ── 生命周期迁移 ─────────────────────────────────────────────────────────


def test_register_then_offline_snapshot():
    lc = WorkerLifecycle(worker_id="w1", drain_timeout_s=0.5)
    assert lc.snapshot()["status"] == "offline"
    lc.register()
    snap = lc.snapshot()
    assert snap["status"] == "online"
    assert snap["started_at"] is not None
    summary = lc.deregister(wait_active=False, reason="test")
    assert summary["status"] == "offline"
    assert summary["drained"] is True
    assert summary["reason"] == "test"


def test_bounded_drain_waits_for_active_tasks():
    lc = WorkerLifecycle(worker_id="w2", drain_timeout_s=5.0)
    lc.register()
    lc.mark_task_started()
    lc.mark_task_started()

    sleeps: list = []

    def fake_sleep(s):
        sleeps.append(s)
        # 第一次轮询后完成任务 1，第二次后完成任务 2（确定性排空）
        if len(sleeps) == 1:
            lc.mark_task_finished()
        elif len(sleeps) == 2:
            lc.mark_task_finished()

    summary = lc.deregister(sleep=fake_sleep, monotonic=lambda: 0.0)
    assert summary["drained"] is True
    assert summary["active_left"] == 0
    assert len(sleeps) == 2


def test_drain_deadline_forces_honest_exit():
    """deadline 到点：drained=False + active_left 如实上报，绝不挂死。"""
    lc = WorkerLifecycle(worker_id="w3", drain_timeout_s=0.0)
    lc.register()
    lc.mark_task_started()
    summary = lc.deregister(
        sleep=lambda s: None, monotonic=lambda: 10.0  # 时钟已超 deadline
    )
    assert summary["drained"] is False
    assert summary["active_left"] == 1
    assert summary["status"] == "offline"


def test_active_count_never_negative():
    lc = WorkerLifecycle(worker_id="w4")
    lc.mark_task_finished()  # 无在跑任务时多减不减
    assert lc.snapshot()["active_tasks"] == 0


def test_lifecycle_gauge_synced():
    from prometheus_client import REGISTRY

    lc = WorkerLifecycle(worker_id="w5")
    lc.mark_task_started()
    assert REGISTRY.get_sample_value("jobs_worker_active_tasks") == 1
    lc.mark_task_finished()
    assert REGISTRY.get_sample_value("jobs_worker_active_tasks") == 0


# ── celery 信号挂接（防御性）─────────────────────────────────────────────


def test_install_celery_signals_returns_bool():
    result = install_celery_lifecycle_signals()
    assert isinstance(result, bool)


def test_lifecycle_singleton_reusable():
    lc = reset_worker_lifecycle_for_tests(worker_id="t1", drain_timeout_s=0.2)
    lc.register()
    from app.services.jobs.worker_lifecycle import get_worker_lifecycle

    assert get_worker_lifecycle().snapshot()["worker_id"] == "t1"
    reset_worker_lifecycle_for_tests(worker_id="t2")


def test_durable_job_counts_active_task():
    """durable_job 与 lifecycle 计数联动（用假 store 走完整路径）。"""
    import unittest.mock
    from types import SimpleNamespace

    from app.services.jobs import worker as worker_mod

    lc = reset_worker_lifecycle_for_tests(worker_id="w6", drain_timeout_s=0.2)
    lc.register()

    job = SimpleNamespace(
        status="queued", cancel_requested_at=None, session_id=None,
        run_id=None, turn_id=None, agent_task_id=None, tool_call_id=None,
    )

    class _FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def commit(self):
            pass

    executed = {"ran": False}

    @worker_mod.contextlib.contextmanager
    def fake_factory():
        yield _FakeDb()

    with unittest.mock.patch.object(
        worker_mod.DurableJobStore, "get_sync", staticmethod(lambda db, jid: job)
    ), unittest.mock.patch.object(
        worker_mod.DurableJobStore, "mark_running_sync",
        staticmethod(lambda db, jid, worker_id=None: True),
    ), unittest.mock.patch.object(
        worker_mod, "_default_session_factory", fake_factory
    ):
        with worker_mod.durable_job(1, session_factory=fake_factory):
            executed["ran"] = True
            assert lc.snapshot()["active_tasks"] == 1
    assert executed["ran"] is True
    assert lc.snapshot()["active_tasks"] == 0


def test_durable_job_active_count_released_on_failure():
    """任务体抛异常也必须释放计数（finally 语义）。"""
    import unittest.mock
    from types import SimpleNamespace

    from app.services.jobs import worker as worker_mod

    lc = reset_worker_lifecycle_for_tests(worker_id="w7", drain_timeout_s=0.2)
    lc.register()
    job = SimpleNamespace(
        status="queued", cancel_requested_at=None, session_id=None,
        run_id=None, turn_id=None, agent_task_id=None, tool_call_id=None,
    )

    class _FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def commit(self):
            pass

    @worker_mod.contextlib.contextmanager
    def fake_factory():
        yield _FakeDb()

    with unittest.mock.patch.object(
        worker_mod.DurableJobStore, "get_sync", staticmethod(lambda db, jid: job)
    ), unittest.mock.patch.object(
        worker_mod.DurableJobStore, "mark_running_sync",
        staticmethod(lambda db, jid, worker_id=None: True),
    ), unittest.mock.patch.object(
        worker_mod.DurableJobStore, "mark_failed_sync",
        staticmethod(lambda db, jid, error=None, message=None: None),
    ), unittest.mock.patch.object(
        worker_mod, "_default_session_factory", fake_factory
    ):
        with pytest.raises(RuntimeError):
            with worker_mod.durable_job(2, session_factory=fake_factory):
                raise RuntimeError("task body failed")
    assert lc.snapshot()["active_tasks"] == 0


# ── InflightGaugeMiddleware ──────────────────────────────────────────────


def _run_asgi(app, scope):
    async def run():
        received = []

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            received.append(message)

        await app(scope, receive, send)
        return received

    return asyncio.run(run())


def test_inflight_gauge_tracks_request(monkeypatch):
    from prometheus_client import REGISTRY

    calls = {"n": 0}

    async def inner(scope, receive, send):
        calls["n"] += 1
        # 请求处理中：gauge 应为 1
        assert REGISTRY.get_sample_value("app_inflight_requests") == 1
        await send({"type": "http.response.start"})

    mw = InflightGaugeMiddleware(inner)
    _run_asgi(mw, {"type": "http"})
    assert calls["n"] == 1
    # 请求结束后归零
    assert REGISTRY.get_sample_value("app_inflight_requests") == 0


def test_inflight_gauge_releases_on_error():
    from prometheus_client import REGISTRY

    async def inner(scope, receive, send):
        raise RuntimeError("handler boom")

    mw = InflightGaugeMiddleware(inner)
    with pytest.raises(RuntimeError):
        _run_asgi(mw, {"type": "http"})
    assert REGISTRY.get_sample_value("app_inflight_requests") == 0


def test_inflight_gauge_ignores_lifespan_scope():
    from prometheus_client import REGISTRY

    async def inner(scope, receive, send):
        return None

    mw = InflightGaugeMiddleware(inner)
    _run_asgi(mw, {"type": "lifespan"})
    # lifespan scope 直通，计数未动
    assert REGISTRY.get_sample_value("app_inflight_requests") == 0
