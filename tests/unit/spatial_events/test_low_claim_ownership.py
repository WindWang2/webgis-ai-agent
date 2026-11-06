"""S4i019/021: stale attempts cannot settle or replay another worker's claim."""
import asyncio
from datetime import datetime, timezone

import pytest

from app.services.spatial_events.contracts import SpatialEventEnvelope
from app.services.spatial_events.service import SpatialEventService


def _append(ledger, key='event'):
    return ledger.append(SpatialEventEnvelope(kind='dataset.version_changed', org_id='org-a',
        subject_type='dataset', subject_key=key, occurred_at=datetime.now(timezone.utc), payload={}))


def test_stale_worker_cannot_settle_new_owner(ledger):
    row_id = _append(ledger).row_id
    ledger.claim_batch('old', limit=1)
    ledger.requeue_stale_processing(older_than_s=0)
    ledger.claim_batch('new', limit=1)
    assert not ledger.mark_processed(row_id, worker_id='old')
    assert not ledger.mark_failed(row_id, worker_id='old', error_code='old-error')
    assert not ledger.mark_skipped(row_id, worker_id='old', reason='old-skip')
    assert ledger.get_event(row_id, org_id='org-a')['claimed_by'] == 'new'
    assert ledger.mark_processed(row_id, worker_id='new')
    assert not ledger.mark_failed(row_id, worker_id='new', error_code='late')


@pytest.mark.asyncio
async def test_slow_batch_keeps_live_claim_renewed(ledger, monkeypatch):
    monkeypatch.setenv('GIS_SPATIAL_EVENT_STALE_CLAIM_S', '0.15')
    _append(ledger)
    service = SpatialEventService(ledger)
    started, finish = asyncio.Event(), asyncio.Event()
    async def slow(row):
        started.set()
        await finish.wait()
        return {'fires': 0}
    monkeypatch.setattr(service, '_process_event', slow)
    task = asyncio.create_task(service.drain_once('worker'))
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.sleep(.35)
    assert ledger.requeue_stale_processing(older_than_s=.15) == 0
    finish.set()
    assert (await task)['processed'] == 1


@pytest.mark.asyncio
async def test_force_replay_refuses_active_claim(ledger, monkeypatch):
    row_id = _append(ledger).row_id
    ledger.claim_batch('worker', limit=1)
    service = SpatialEventService(ledger)
    called = []
    async def process(row):
        called.append(row['id'])
        return {}
    monkeypatch.setattr(service, '_process_event', process)
    result = await service.replay(org_id='org-a', from_id=row_id, to_id=row_id, force=True)
    assert result['replayed'] == 0 and not called
    assert ledger.get_event(row_id, org_id='org-a')['claimed_by'] == 'worker'


@pytest.mark.asyncio
async def test_force_replay_runs_with_its_claim_and_settles(ledger, monkeypatch):
    row_id = _append(ledger).row_id
    ledger.claim_batch('worker', limit=1)
    assert ledger.mark_processed(row_id, worker_id='worker')
    service = SpatialEventService(ledger)
    observed = []
    async def process(row):
        observed.append((row['claimed_by'], row['status']))
        return {'fires': 0}
    monkeypatch.setattr(service, '_process_event', process)
    result = await service.replay(org_id='org-a', from_id=row_id, to_id=row_id, force=True)
    assert result['replayed'] == 1
    assert observed[0][0].startswith('replay:') and observed[0][1] == 'processing'
    assert ledger.get_event(row_id, org_id='org-a')['status'] == 'processed'
