"""Sync HTTP waits never retain org admission locks or refund live work."""
import asyncio
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from sqlalchemy import select

from app.models.db_model import AnalysisTask
from app.services.geocompute import sync_admission as sync
from app.services.geocompute.plan import ExecutionNode, ExecutionPlan, ExecutionRun, ExecutionRunStatus, NodeCategory
from app.services import org_quota
from tests.unit.test_org_quota_persistent_admission import factory as factory, _workflow, _cluster


def _plan():
    return ExecutionPlan(plan_id='sync-review', nodes=[ExecutionNode(node_id='f', category=NodeCategory.FILTER, parameters={'predicate': {'op': 'eq', 'field': 'v', 'value': 1}, 'features': []})])


def _reserve(factory):
    return sync.reserve_sync_execution(_plan(), org_id='1', caller={'user_id': None}, session_id=None, factory=factory)


def _result(run_id):
    return ExecutionRun(run_id=run_id, plan_id='sync-review', plan_fingerprint='fp', status=ExecutionRunStatus.COMPLETED)


def test_committed_sync_occupancy_blocks_other_producers_until_actual_finish(factory):
    reservation = _reserve(factory)
    with factory() as db:
        row = db.get(AnalysisTask, reservation.job_id)
        assert row.status == 'running' and row.worker_id == reservation.worker_id
    for producer in (_workflow, _cluster):
        with pytest.raises(org_quota.QuotaExceededError):
            producer(factory)
    result = sync.execute_reserved(reservation, lambda token, run_id: _result(run_id))
    assert result.status == ExecutionRunStatus.COMPLETED
    assert _workflow(factory)['instance_id']


def test_exception_releases_only_after_operation_has_stopped(factory):
    reservation = _reserve(factory)
    def fail(token, run_id):
        with pytest.raises(org_quota.QuotaExceededError):
            _workflow(factory)
        raise ValueError('operation failed')
    with pytest.raises(ValueError, match='operation failed'):
        sync.execute_reserved(reservation, fail)
    with factory() as db:
        assert db.get(AnalysisTask, reservation.job_id).status == 'failed'
    assert _cluster(factory)


def test_two_sync_reservations_compete_for_one_durable_slot(factory):
    barrier = threading.Barrier(2)
    def reserve():
        barrier.wait(3)
        try:
            return _reserve(factory)
        except org_quota.QuotaExceededError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(result is not None for result in results) == 1
    with factory() as db:
        assert len(db.execute(select(AnalysisTask)).scalars().all()) == 1


@pytest.mark.asyncio
async def test_http_cancellation_keeps_live_worker_counted_and_org_lock_released(factory, monkeypatch):
    from app.api.routes import geocompute as route
    from app.services.geocompute import api
    entered, release = threading.Event(), threading.Event()
    lock_held = False
    @asynccontextmanager
    async def admission(user):
        nonlocal lock_held
        lock_held = True
        try:
            yield '1'
        finally:
            lock_held = False
    monkeypatch.setattr(org_quota, 'serialized_admission', admission)
    async def enforce(user):
        assert lock_held
    monkeypatch.setattr(route, '_enforce_org_quota', enforce)
    reserve = sync.reserve_sync_execution
    monkeypatch.setattr(sync, 'reserve_sync_execution', lambda *a, **kw: reserve(*a, **kw, factory=factory))
    def run(plan, **kwargs):
        assert not lock_held
        entered.set()
        assert release.wait(5)
        return _result(kwargs['run_id'])
    monkeypatch.setattr(api, 'run_plan_sync', run)
    waiter = asyncio.create_task(route.execute_execution_plan(
        route.ExecutePlanRequest(plan=_plan().model_dump(mode='json')), user={'user_id': None}, owner_token=None))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert not lock_held
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        with pytest.raises(org_quota.QuotaExceededError):
            await asyncio.to_thread(_workflow, factory)
    finally:
        release.set()
    for _ in range(100):
        with factory() as db:
            terminal = db.execute(select(AnalysisTask.status)).scalar_one() == 'completed'
        if terminal:
            break
        await asyncio.sleep(.01)
    assert terminal
    assert await asyncio.to_thread(_workflow, factory)
