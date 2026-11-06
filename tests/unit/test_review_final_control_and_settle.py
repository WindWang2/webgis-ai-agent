"""Independent audit regressions: finite control state and recoverable claims."""
import asyncio
from collections import OrderedDict
from unittest.mock import AsyncMock

import pytest

from app.services.session_data import MemorySessionStore
from app.services.session_data_redis import RedisSessionStore
from app.services.session_plan import load_session_plan, save_session_plan


@pytest.fixture(params=['memory', 'redis'])
def backend(request):
    if request.param == 'memory':
        return MemorySessionStore(capacity=1)
    import fakeredis.aioredis
    return RedisSessionStore('redis://test/0', capacity=1, redis=fakeredis.aioredis.FakeRedis())


@pytest.mark.asyncio
async def test_control_history_is_bounded_and_current_checkpoint_and_data_survive(backend, monkeypatch):
    from app.services import session_control_budget as budget
    monkeypatch.setattr(budget, 'MAX_CONTROL_REFS', 6)
    data = await backend.store('s', {'user': 'data'})
    current = await backend.store('s', {'revision': 1}, prefix='sessionplan')
    await backend.set_alias('s', current, 'session-plan')
    checkpoint = await backend.store('s', {'checkpoint': 1}, prefix='checkpoint')
    # Current plan explicitly references an older recovery checkpoint.
    await backend.overwrite('s', current, {'revision': 2, 'checkpoint_ref': checkpoint})
    latest = await backend.store('s', {'checkpoint': 2}, prefix='checkpoint')
    history = []
    for i in range(20):
        ref = await backend.store('s', {'status': 'superseded', 'revision': i}, prefix='plan')
        await backend.set_alias('s', ref, f'plan-id:{i}')
        history.append(ref)
    refs = await backend.list_refs('s')
    assert len([ref for ref in refs if ref != data]) <= 6
    assert await backend.get('s', current) == {'revision': 2, 'checkpoint_ref': checkpoint}
    assert await backend.get('s', checkpoint) == {'checkpoint': 1}
    assert await backend.get('s', latest) == {'checkpoint': 2}
    assert await backend.get('s', data) == {'user': 'data'}
    assert await backend.get('s', history[0]) is None
    assert await backend.resolve_alias('s', 'plan-id:0') == 'plan-id:0'


@pytest.mark.asyncio
async def test_control_byte_budget_covers_overwrite_and_rejection_is_atomic(backend, monkeypatch):
    from app.services import session_control_budget as budget
    monkeypatch.setattr(budget, 'MAX_CONTROL_BYTES', 240)
    ref = await backend.store('s', {'revision': 1, 'value': 'old'}, prefix='sessionplan')
    await backend.set_alias('s', ref, 'session-plan')
    with pytest.raises(ValueError, match='control'):
        await backend.overwrite('s', ref, {'revision': 2, 'value': 'x' * 241}, expected_revision=1)
    assert await backend.get('s', ref) == {'revision': 1, 'value': 'old'}
    history = await backend.store('s', {'status': 'superseded', 'value': 'a' * 80}, prefix='plan')
    newer = await backend.store('s', {'status': 'superseded', 'value': 'b' * 80}, prefix='plan')
    assert await backend.get('s', history) is None
    assert await backend.get('s', newer) is not None
    assert await backend.resolve_alias('s', 'session-plan') == ref


@pytest.mark.asyncio
async def test_active_unaliased_plans_are_not_evicted_to_admit_more_controls(backend, monkeypatch):
    from app.services import session_control_budget as budget
    monkeypatch.setattr(budget, 'MAX_CONTROL_REFS', 2)
    first = await backend.store('s', {'__status__': 'running'}, prefix='plan')
    second = await backend.store('s', {'__status__': 'partially_completed'}, prefix='plan')
    with pytest.raises(ValueError, match='active session controls'):
        await backend.store('s', {'status': 'superseded'}, prefix='plan')
    assert await backend.get('s', first) == {'__status__': 'running'}
    assert await backend.get('s', second) == {'__status__': 'partially_completed'}
    assert len(await backend.list_refs('s')) == 2


@pytest.mark.asyncio
async def test_cancelled_settlement_terminalizes_and_shared_ledger_replay_cannot_stick(backend, monkeypatch):
    from app.services.harness_kernel import runtime as runtime_module, settle
    runtime = runtime_module.GISSessionRuntime('settle-cancel', store=backend)
    await runtime.begin_turn('t', host='pi', message='q')
    monkeypatch.setattr(runtime_module, 'get_runtime', lambda sid: runtime)
    monkeypatch.setattr(settle, '_RECEIPTS', OrderedDict())
    monkeypatch.setattr(settle, '_TURN_LOCKS', OrderedDict())
    monkeypatch.setattr(settle, 'resolve_turn_refusal', AsyncMock(return_value=None))
    entered = asyncio.Event()
    async def effects(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(settle, 'run_settle_effects', effects)
    inp = settle.HarnessSettleInput(session_id=runtime.session_id, turn_id='t', cause='success')
    task = asyncio.create_task(settle.settle_turn(inp))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    plan = await load_session_plan(runtime.session_id, store=backend)
    assert plan.turns[-1].status == 'interrupted'
    assert plan.turns[-1].settle_finished_at > 0
    settle._RECEIPTS.clear()
    replay = await settle.settle_turn(inp)
    assert replay.disposition == 'stale'
    assert (await load_session_plan(runtime.session_id, store=backend)).turns[-1].status == 'interrupted'


@pytest.mark.asyncio
async def test_expired_claim_is_fenced_and_a_new_worker_can_finish(backend, monkeypatch):
    from app.services.harness_kernel import runtime as runtime_module, settle
    from app.services.harness_kernel import settle_claim
    runtime = runtime_module.GISSessionRuntime('settle-crash', store=backend)
    await runtime.begin_turn('t', host='pi', message='q')
    monkeypatch.setattr(runtime_module, 'get_runtime', lambda sid: runtime)
    monkeypatch.setattr(settle, '_RECEIPTS', OrderedDict())
    monkeypatch.setattr(settle, '_TURN_LOCKS', OrderedDict())
    monkeypatch.setattr(settle, 'resolve_turn_refusal', AsyncMock(return_value=None))
    first = await settle_claim.acquire_settlement_claim(runtime.session_id, 't', expected_status='running')
    assert first.state == 'claimed'
    busy = await settle.settle_turn(settle.HarnessSettleInput(session_id=runtime.session_id, turn_id='t'))
    assert busy.disposition == 'in_progress'
    plan = await load_session_plan(runtime.session_id, store=backend)
    plan.turns[-1].settle_started_at -= settle_claim.SETTLE_LEASE_S + 1
    assert await save_session_plan(plan, store=backend)
    # Simulate worker/process-local runtime loss while preserving shared truth.
    recovered_store = (RedisSessionStore('redis://test/0', redis=backend._r)
                       if isinstance(backend, RedisSessionStore) else backend)
    recovered = runtime_module.GISSessionRuntime(runtime.session_id, store=recovered_store)
    monkeypatch.setattr(runtime_module, 'get_runtime', lambda sid: recovered)
    replacement = await settle_claim.acquire_settlement_claim(runtime.session_id, 't', expected_status='running')
    assert replacement.state == 'claimed' and replacement.token != first.token
    await runtime.end_turn('t', host='pi', status='completed', settle_token=first.token)
    assert (await load_session_plan(runtime.session_id, store=backend)).turns[-1].status == 'running'
    assert not await settle_claim.complete_settlement(runtime.session_id, 't', first.token)
    await runtime.end_turn('t', host='pi', status='completed', settle_token=replacement.token)
    assert await settle_claim.complete_settlement(runtime.session_id, 't', replacement.token)
    replay = await settle.settle_turn(settle.HarnessSettleInput(session_id=runtime.session_id, turn_id='t'))
    assert replay.disposition == 'duplicate'


@pytest.mark.asyncio
async def test_control_byte_limit_measures_utf8_in_both_backends(backend, monkeypatch):
    from app.services import session_control_budget as budget
    monkeypatch.setattr(budget, 'MAX_CONTROL_BYTES', 80)
    with pytest.raises(ValueError, match='control'):
        await backend.store('s', {'text': '中' * 30}, prefix='plan')
    assert await backend.list_refs('s') == {}


@pytest.mark.asyncio
async def test_advisory_plan_save_inside_parent_turn_lock_does_not_reacquire_it():
    from app.services.distributed_lock import session_lock_registry
    from app.services.planning.models import CanonicalPlan
    from app.services.planning.store import PlanStore
    backend = MemorySessionStore()
    store = PlanStore(session_store=backend)
    plan = CanonicalPlan(plan_id='p', session_id='plan-lock-boundary', intent='test')
    async with session_lock_registry.lock(plan.session_id):
        await asyncio.wait_for(store.save(plan), timeout=2)
        plan.revision += 1
        await asyncio.wait_for(store.save(plan), timeout=2)
    assert (await store.load_current(plan.session_id)).revision == plan.revision
