"""Final planning persistence and lexical-boundary review regressions."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import gc
import weakref

import pytest

from app.services.planning.models import CanonicalPlan, CanonicalStep, PlanStatus, StepStatus
from app.services.planning.store import PlanStore
from app.services.session_data import MemorySessionStore


@pytest.mark.parametrize('failed, expected', [(StepStatus.failed, PlanStatus.failed),
    (StepStatus.skipped, PlanStatus.failed), (StepStatus.cancelled, PlanStatus.cancelled)])
def test_blocked_dependency_chain_terminates_without_mutating_steps(failed, expected):
    plan = CanonicalPlan(plan_id='p', session_id='s', intent='work', status=PlanStatus.running,
        steps=[CanonicalStep(id='s1', n=1, goal='first', status=failed),
               CanonicalStep(id='s2', n=2, goal='second', depends_on=['s1']),
               CanonicalStep(id='s3', n=3, goal='third', depends_on=['s2'])])
    assert plan.recompute_status() == expected
    assert plan.next_pending_steps() == []
    assert plan.steps[1].status == StepStatus.pending


def test_followup_and_recovery_keywords_do_not_match_unrelated_words_or_counts():
    from app.services.planning.followup import classify_followup, FollowUpKind
    from app.services.planning.recovery import classify_error, FailureClass
    kwargs = dict(has_active_plan=False, active_domains=[], session_has_refs=True, domain_keywords={})
    assert classify_followup('那个区域有医院吗', **kwargs) != FollowUpKind.ref_reuse
    assert classify_followup('那个结果继续用', **kwargs) == FollowUpKind.ref_reuse
    assert classify_error(message='nonempty result with 10 条 records') == FailureClass.internal
    assert classify_error(message='返回 0 条') == FailureClass.empty_result
    assert classify_error(message='request was notempty') == FailureClass.internal


def test_generalization_date_and_threshold_roles_require_real_shapes_and_tokens():
    from app.services.gis_skills.induction.parameter_generalizer import generalize_parameters
    step = SimpleNamespace(tool_name='tool', seq=1, observations={}, arguments={
        'start_time': 'morning', 'signal_strength': .4, 'unlimited_count': 10,
        'maxThreshold': 2, 'max_p_value': .05, 'date': '2026-11-06'})
    result = generalize_parameters(SimpleNamespace(steps=[step]))
    by_name = {param.source_key: param for param in result.parameters}
    assert by_name['start_time'].semantic_role == 'generic'
    assert by_name['signal_strength'].semantic_role == 'generic'
    assert by_name['unlimited_count'].semantic_role == 'generic'
    assert by_name['maxThreshold'].semantic_role == by_name['max_p_value'].semantic_role == 'threshold'
    assert by_name['date'].semantic_role == 'date'


@pytest.mark.asyncio
async def test_save_failure_cannot_poison_cache_or_mint_an_unreachable_replacement():
    backend = MemorySessionStore()
    store = PlanStore(session_store=backend)
    plan = CanonicalPlan(plan_id='p', session_id='s', intent='saved', revision=1)
    await store.save(plan)
    reference = await backend.resolve_alias('s', 'plan-current')
    old = await backend.get('s', reference)
    backend.overwrite = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError, match='changed or unavailable'):
        await store.save(plan.model_copy(update={'revision': 2, 'intent': 'unsaved'}))
    assert store.peek('s') is None
    assert await backend.resolve_alias('s', 'plan-current') == reference
    assert await backend.get('s', reference) == old


@pytest.mark.asyncio
async def test_clear_marker_can_accept_new_plan_but_rejects_old_plan_resurrection():
    backend = MemorySessionStore()
    store = PlanStore(session_store=backend)
    plan = CanonicalPlan(plan_id='old', session_id='s', intent='old', revision=5)
    await store.save(plan)
    await store.clear('s')
    with pytest.raises(RuntimeError, match='cleared plan'):
        await store.save(plan)
    await store.save(CanonicalPlan(plan_id='new', session_id='s', intent='new'))
    await store.save(plan.model_copy(update={'revision': 99}))
    assert (await store.load_current('s')).plan_id == 'new'
    loaded = await store.load_current('s')
    loaded.intent = 'mutated caller copy'
    assert (await store.load_current('s')).intent == 'new'


def test_stac_cache_keys_url_and_ttl_without_retaining_primitive_or_closing_live_catalog(monkeypatch):
    import pystac_client
    from app.services.rs import stac_client as module

    class Session:
        def __init__(self):
            self.closed = False
        def close(self):
            self.closed = True

    class Catalog:
        pass

    sessions = []
    def make_session():
        session = Session()
        sessions.append(session)
        return session

    calls = []
    def open_catalog(url, **kwargs):
        calls.append(url)
        return Catalog()

    url, clock = ['https://catalog-a.example'], [0.0]
    monkeypatch.setattr('app.services.data_fabric.security.make_safe_session', make_session)
    monkeypatch.setattr(pystac_client.Client, 'open', open_catalog)
    monkeypatch.setattr(module, '_catalog_url', lambda: url[0])
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    module._catalog_for_url.cache_clear()
    primitive = module.StacClientPrimitive()
    primitive_ref = weakref.ref(primitive)
    old = primitive._get_catalog()
    assert primitive._get_catalog() is old
    del primitive
    gc.collect()
    assert primitive_ref() is None
    clock[0] = 61
    newer = module.StacClientPrimitive()._get_catalog()
    assert newer is not old and not sessions[0].closed
    del old
    gc.collect()
    assert sessions[0].closed
    url[0] = 'https://catalog-b.example'
    other = module.StacClientPrimitive()._get_catalog()
    assert calls == ['https://catalog-a.example', 'https://catalog-a.example', 'https://catalog-b.example']
    assert newer is not other
    module._catalog_for_url.cache_clear()
    del newer, other
    gc.collect()
    assert all(session.closed for session in sessions)
