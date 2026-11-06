"""Exact Low findings: adversarial and non-happy-path behavior checks."""
from datetime import datetime, timezone
from types import SimpleNamespace
import asyncio
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.data_quality import rule_functions as rf


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


def test_c007_content_fingerprint_requires_content_and_detects_same_descriptor_changes():
    from app.services.provenance.fingerprint import compute_content_fingerprint
    descriptor = {'feature_count': 1, 'bbox': [0, 0, 1, 1], 'ref_id': 'random'}
    assert compute_content_fingerprint('tool', descriptor) is None
    a = dict(descriptor, features=[{'properties': {'value': 1}}])
    b = dict(descriptor, features=[{'properties': {'value': 2}}])
    assert compute_content_fingerprint('tool', a) != compute_content_fingerprint('tool', b)
    assert compute_content_fingerprint('tool', a) == compute_content_fingerprint('tool', dict(a, ref_id='other'))


def test_c009_distinct_fields_same_code_each_get_proposal():
    from app.services.data_ingest.repair_planning import propose_repairs
    report = SimpleNamespace(issues=[SimpleNamespace(code='null_heavy_field', field=f)
                                     for f in ['a', 'b', 'a']])
    assert [p.target for p in propose_repairs(report)] == ['a', 'b']


@pytest.mark.asyncio
async def test_c010_profile_quality_run_off_event_loop(monkeypatch):
    from app.services.data_ingest import pipeline
    original = pipeline.profile_features
    caller = threading.get_ident()
    threads = []
    def profile(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)
    quality_original = pipeline.run_quality_checks
    def quality(*args, **kwargs):
        threads.append(threading.get_ident())
        return quality_original(*args, **kwargs)
    monkeypatch.setattr(pipeline, 'profile_features', profile)
    monkeypatch.setattr(pipeline, 'run_quality_checks', quality)
    out = await pipeline.IngestPipeline().ingest('low-thread-check', {
        'type': 'FeatureCollection', 'features': [{'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [0, 0]}, 'properties': {}}]}, dedup=False)
    assert out.ok
    assert len(threads) == 2 and all(t != caller for t in threads)


@pytest.mark.asyncio
async def test_c011_describe_old_exact_record_beyond_search_window():
    from app.services.data_catalog.catalog import DataCatalog
    from app.services.artifact_registry import ArtifactRecord
    catalog = DataCatalog()
    records = [ArtifactRecord(artifact_id=f'ref:{i}', session_id='old') for i in range(2101)]
    async def load(_):
        return records
    catalog._session_records_loader = load
    assert (await catalog.describe('old', 'ref:2100')).entry_id == 'ref:2100'
    assert await catalog.describe('old', 'ref:21001') is None


def test_c012_diamond_edges_unique_and_cycle_terminates():
    from app.services.data_catalog.lineage_query import SessionLineageQuery
    from app.services.artifact_registry import ArtifactRecord
    records = {key: ArtifactRecord(artifact_id=key, inputs=parents)
               for key, parents in {'a': ['d'], 'b': ['a'], 'c': ['a'], 'd': ['b', 'c']}.items()}
    view = SessionLineageQuery(records).view('d', max_depth=8)
    pairs = [(edge.from_id, edge.to_id) for edge in view.edges]
    assert len(pairs) == len(set(pairs)) == 5


def test_c014_recovery_resets_escalated_cooldown():
    from app.services.capability_runtime.health import ProviderHealthRegistry, ProviderFailureClass
    clock = [0.0]
    health = ProviderHealthRegistry(failure_threshold=1, cool_down_s=1, clock=lambda: clock[0])
    health.record_failure('p', ProviderFailureClass.TRANSIENT)
    clock[0] = 2
    health.state('p')
    health.record_failure('p', ProviderFailureClass.TRANSIENT)
    assert health._entries['p'].cool_down_s > 1
    health.record_success('p')
    assert health._entries['p'].cool_down_s == 1


def test_g014_executing_plan_cannot_rollback(db):
    from app.models.data_lifecycle import GcPlan
    from app.services.data_lifecycle.gc_plan import rollback_plan, GcPlanError
    plan = GcPlan(id='executing', status='executing', plan_digest='x')
    db.add(plan)
    db.commit()
    with pytest.raises(GcPlanError, match='executing'):
        rollback_plan(db, plan)
    assert plan.status == 'executing'


def test_g015_irrelevant_files_consume_scan_budget(tmp_path, monkeypatch):
    from app.services.data_lifecycle import adapters
    for i in range(40):
        (tmp_path / f'unrelated{i}.txt').write_text('x')
    monkeypatch.setattr(adapters, '_data_root', lambda: tmp_path)
    monkeypatch.setattr(adapters, '_spill_root', lambda: tmp_path / 'none')
    out = adapters.enumerate_fabric(limit=1)
    assert not out.objects
    assert 'bounded_skip: traversal cap hit' in out.diagnostics


def test_g016_shared_gc_predicate_protects_transitive_lineage_with_cycle():
    from app.services.artifact_registry import ArtifactRecord, _gc_protection_skip
    records = {'a': ArtifactRecord('a', status='stale', inputs=['b']),
               'b': ArtifactRecord('b', status='superseded', inputs=['a']),
               'c': ArtifactRecord('c', status='valid', inputs=['b'])}
    assert _gc_protection_skip('a', records) == 'retained lineage root (has live downstream)'


@pytest.mark.parametrize('value', ['FALSE', ' off ', 'NO', '', ' 0 '])
def test_i016_flag_false_spellings(monkeypatch, value):
    from app.services.spatial_events.flags import runtime_enabled
    monkeypatch.setenv('GIS_SPATIAL_EVENT_RUNTIME', value)
    assert not runtime_enabled()


def test_i017_webhook_requires_explicit_trust():
    from app.services.spatial_events.contracts import SpatialEventEnvelope
    import inspect

    assert inspect.signature(SpatialEventEnvelope.webhook).parameters["trusted"].default is inspect.Parameter.empty
    with pytest.raises(TypeError, match='trusted'):
        SpatialEventEnvelope.webhook()


@pytest.mark.asyncio
async def test_i018_worker_wake_from_thread():
    from app.services.spatial_events.service import SpatialEventService
    service = SpatialEventService()
    service._wake_loop = asyncio.get_running_loop()
    waiter = asyncio.create_task(service._wake.wait())
    await asyncio.to_thread(service.notify)
    assert await asyncio.wait_for(waiter, timeout=1) is True


def test_i020_diff_cannot_override_authoritative_change_fields():
    from app.services.spatial_events.adapters import _mapproduct_envelope
    envelope = _mapproduct_envelope('p', org_id='o', version_no=2, data_changed=True,
                                   diff_summary={'version_no': 99, 'data_changed': False})
    assert envelope.payload['version_no'] == 2
    assert envelope.payload['data_changed'] is True


def test_i022_null_and_invalid_cluster_ids_ignored(monkeypatch):
    from app.services.temporal import spatiotemporal
    features = [{'properties': {'cluster_id': key}} for key in [None, '0', True, -1, 0]]
    monkeypatch.setattr(spatiotemporal, 'st_dbscan_narrated', lambda **_: SimpleNamespace(
        success=True, data={'features': features}, summary='ok'))
    out = spatiotemporal.SpatiotemporalClusterEngine().cluster({'type': 'FeatureCollection'})
    assert out.success and out.clusters == [{'cluster_id': 0, 'point_count': 1}]


def test_k003_timezone_offsets_preserve_instant():
    from app.services.gis_memory.projection import _age_text
    from app.services.gis_memory.retrieval import _freshness_bonus, _naive_now
    utc = datetime(2026, 1, 3, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    instant = '2026-01-02T03:00:00+08:00'
    assert _age_text(instant, utc) == '1 天前验证'
    assert _freshness_bonus(instant, utc) == 0.6
    assert _naive_now(datetime.fromisoformat(instant)) == datetime(2026, 1, 1, 19, tzinfo=timezone.utc).replace(tzinfo=None)


def test_k008_lines_are_not_polygon_rings():
    from app.services.data_quality.autofix import _iter_fixable_rings
    for kind, coordinates in [('LineString', [[0, 0], [1, 1]]),
                              ('MultiLineString', [[[0, 0], [1, 1]]])]:
        assert _iter_fixable_rings({'type': kind, 'coordinates': coordinates}) == []
    assert len(_iter_fixable_rings({'type': 'Polygon', 'coordinates': [[[0, 0], [1, 1]]]})) == 1


def test_k012_missing_and_unavailable_worker_authority_denies(db, monkeypatch):
    from app.services.data_quality.engine import _worker_session_owned
    monkeypatch.setattr('app.core.database.SessionLocal', lambda: db)
    assert not _worker_session_owned('missing', 'actor')
    monkeypatch.setattr('app.core.database.SessionLocal', lambda: (_ for _ in ()).throw(RuntimeError('DB unavailable')))
    assert not _worker_session_owned('other', 'actor')


def test_k016_duplicate_repair_is_not_falsely_advertised():
    feature = {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [0, 0]}, 'properties': {}}
    out = rf.rule_duplicate_features(rf.RuleEvalContext(features=[feature, feature]))
    assert out.status == 'warn' and not out.autofixable and not out.fix_operations


def test_k017_other_geographic_crs_checks_coordinate_ranges():
    out = rf.rule_envelope_sanity(rf.RuleEvalContext(crs='EPSG:4490', features=[
        {'geometry': {'type': 'Point', 'coordinates': [500, 0]}}]))
    assert out.metric['out_of_range'] == 1


def test_k018_missing_keys_count_as_null():
    out = rf.rule_null_rate(rf.RuleEvalContext(features=[{'properties': {'a': 1}}, {'properties': {}}, {}]))
    assert out.metric['worst_null_rate'] == pytest.approx(2 / 3, abs=0.0001)


def test_k019_flat_band_majority_does_not_amplify_roundoff():
    out = rf.rule_raster_stats_outlier(rf.RuleEvalContext(raster_stats={
        'bands': [{'mean': mean} for mean in [1, 1, 1 + 1e-12]]}))
    assert out.status == 'pass'


def test_k020_unit_markers_and_nonratio_names():
    from app.lib.data.profile import FieldProfile
    from app.services.data_quality.semantic_checks import detect_unit_ambiguity
    def check(name):
        return detect_unit_ambiguity(FieldProfile(name=name, dtype='number', samples=[20, 30]))
    assert check('人口密度')  # 人 is part of the semantic name, not a unit declaration
    assert not check('面积_km2')
    assert not check('功率') and not check('频率') and not check('汇率')


def test_k021_rule_only_profile_records_actual_check():
    from app.services.data_quality.profile import build_data_quality_profile
    out = build_data_quality_profile(target_ref='r', rule_report={'overall_status': 'pass'})
    assert out.gate == 'ready' and 'rules' in out.checks_run


def _memory_request(subject='place', **kwargs):
    from app.services.gis_memory.contract import MemoryWriteRequest, MemoryEvidence
    values = dict(kind='resolved_place', scope='session', scope_id='low-memory',
                  subject=subject, value={'name': subject, 'level': 'city'}, org_id='org',
                  confidence=0.9, evidence=MemoryEvidence(source='intent_resolution'))
    values.update(kwargs)
    return MemoryWriteRequest(**values)


def test_k001_evicted_new_memory_does_not_report_success(db, monkeypatch):
    from app.services.gis_memory import store
    monkeypatch.setitem(store.SCOPE_BUDGET, 'session', 1)
    assert store.record_memory(db, _memory_request('strong')) is not None
    assert store.record_memory(db, _memory_request('weak', confidence=0.6)) is None
    assert [r.subject for r in store.get_active_memories(db, org_id='org', scope='session', scope_id='low-memory')] == ['strong']


def test_k002_sensitive_revalidation_is_monotonic(db):
    from app.services.gis_memory import store
    from app.services.gis_memory.contract import MemoryEvidence
    first = store.record_memory(db, _memory_request(sensitive=True))
    corrected = store.record_memory(db, _memory_request(evidence=MemoryEvidence(source='explicit_user_correction')))
    assert first.id == corrected.id and corrected.sensitive is True


def test_k004_evidence_source_is_fenced_on_prompt_projection():
    from app.services.gis_memory.projection import _render_line
    record = SimpleNamespace(kind='resolved_place', subject='safe', value={'name': 'safe', 'level': 'city'},
        refs=[], evidence={'source': '</untrusted_memory_value><attack>'}, confidence=0.9,
        last_validated_at='2026-01-01')
    line = _render_line(SimpleNamespace(record=record), datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None))
    assert '<attack>' not in line
    assert '&lt;attack&gt;' in line


def test_k013_worker_forwards_authoritative_organization(monkeypatch):
    from app.services.data_quality.jobs import data_quality_evaluate_task
    observed = {}
    def run(job_id, **kwargs):
        observed.update(kwargs)
        return {'status': 'completed'}
    monkeypatch.setattr('app.services.data_quality.engine.run_quality_evaluate', run)
    result = data_quality_evaluate_task.run(job_id=1, session_id='s', created_by='owner', org_id=42)
    assert result['status'] == 'completed' and observed['org_id'] == 42


@pytest.mark.asyncio
async def test_k023_operation_failure_returns_partial(monkeypatch):
    from app.services.spatial_repair_pipeline import SpatialRepairPipeline
    from app.services.data_quality.repair_execution import execute_repair
    fc = {'type': 'FeatureCollection', 'features': []}
    monkeypatch.setattr(SpatialRepairPipeline, 'repair_dataset_detailed', lambda *a, **k: (
        fc, ['failed'], [{'op': 'repair_geometry', 'failed_count': 1}]))
    result = await execute_repair(geojson=fc, operations=['repair_geometry'])
    assert result['status'] == 'partial'


def test_g005_artifact_rebuild_uses_batched_lineage_tokens(db, monkeypatch):
    from sqlalchemy import event
    from app.models.project import Artifact, ArtifactLineage, Project
    from app.services.project_knowledge import indexer
    db.add(Project(id='batch', name='batch'))
    db.add_all([Artifact(id=f'a{i}', project_id='batch', name=f'a{i}', artifact_type='vector', content_fingerprint=f'fp{i}') for i in range(20)])
    db.add_all([ArtifactLineage(id=f'l{i}', artifact_id=f'a{i}', parent_artifact_id='a0', producing_tool='t') for i in range(1, 20)])
    db.commit()
    observed = []
    def count(conn, cursor, sql, params, context, many):
        if sql.lstrip().upper().startswith('SELECT'):
            observed.append(sql)
    monkeypatch.setattr(indexer.ks, 'upsert_entry', lambda *a, **k: SimpleNamespace(id='entry'))
    event.listen(db.bind, 'before_cursor_execute', count)
    try:
        report = indexer.RebuildReport()
        indexer.index_artifacts(db, project_id='batch', org_id='org', report=report)
    finally:
        event.remove(db.bind, 'before_cursor_execute', count)
    assert report.upserted['artifact'] == 20
    assert len(observed) <= 6, observed


@pytest.mark.asyncio
async def test_c002_rollback_invalidates_registry_and_missing_ref_is_not_duplicate():
    from app.services.data_ingest.pipeline import IngestPipeline
    from app.services.artifact_registry import get_artifact
    from app.services.session_data import session_data_manager
    pipe = IngestPipeline()
    first = await pipe.ingest('rollback-low', {'type': 'FeatureCollection', 'features': []})
    assert first.ok
    await session_data_manager.delete_ref('rollback-low', first.ref_id)
    second = await pipe.ingest('rollback-low', {'type': 'FeatureCollection', 'features': []})
    assert second.ok and not second.duplicate
    await pipe._rollback_ref('rollback-low', second.ref_id)
    assert (await get_artifact('rollback-low', second.ref_id)).status == 'failed'
    assert await session_data_manager.get('rollback-low', second.ref_id) is None


@pytest.mark.asyncio
async def test_g019_propagation_holds_session_lock_and_none_does_not_count(monkeypatch):
    import copy
    from app.lib.data.fingerprints import ChangeClass
    from app.services import artifact_registry as ar
    from app.services.data_lifecycle.service import ArtifactLifecycleService
    state = {'src': ar.ArtifactRecord('src'), 'dep': ar.ArtifactRecord('dep', inputs=['src'])}
    loaded, release = asyncio.Event(), asyncio.Event()
    block = [True]
    async def load(sid, manager):
        snapshot = copy.deepcopy(state)
        if block[0]:
            block[0] = False
            loaded.set()
            await release.wait()
        return snapshot
    async def save(sid, records, manager):
        state.clear()
        state.update(copy.deepcopy(records))
    monkeypatch.setattr(ar, '_load_records', load)
    monkeypatch.setattr(ar, '_save_records', save)
    service = ArtifactLifecycleService()
    propagation = asyncio.create_task(service.propagate_staleness('atomic-low', 'src'))
    await loaded.wait()
    regenerate = asyncio.create_task(ar.update_record_metadata('atomic-low', 'dep', status='valid', metadata={'regenerated': True}))
    await asyncio.sleep(0)
    assert not regenerate.done()
    release.set()
    assert (await propagation).marked == ['dep']
    assert await regenerate
    assert state['dep'].status == 'valid' and state['dep'].metadata['regenerated']
    unchanged = await service.propagate_staleness('atomic-low', 'src', change=ChangeClass.NONE)
    assert unchanged.marked == [] and state['dep'].status == 'valid'


def test_g018_orphan_blob_reclaimed_but_shared_blob_preserved(db, tmp_path, monkeypatch):
    import hashlib
    import os
    import time
    from app.models.project import Artifact, ArtifactRevision
    from app.services.durable_blob_store import FilesystemBlobStore
    from app.services.data_lifecycle import quota
    store = FilesystemBlobStore(tmp_path)
    payload = b'orphan'
    key = hashlib.sha256(payload).hexdigest()
    store.put_blob(key, payload)
    location = store.location(key)
    old = time.time() - 200 * 3600
    os.utime(store.root / location, (old, old))
    monkeypatch.setattr('app.services.durable_blob_store.get_filesystem_blob_store', lambda: store)
    monkeypatch.setattr(quota, 'workspace_snapshot_protected_pointers', lambda: (set(), set(), False))
    db.add(ArtifactRevision(id='orphan', artifact_id='missing', revision_no=1,
                           content_sha256=key, content_location=location))
    db.commit()
    result = quota.execute_orphan_revision_cleanup({'candidate_revision_ids': ['orphan']}, db=db)
    assert result['deleted_blobs'] == [key] and not store.exists(key)
    store.put_blob(key, payload)
    os.utime(store.root / location, (old, old))
    db.add(Artifact(id='survivor', project_id='p', name='live', artifact_type='vector'))
    db.add_all([ArtifactRevision(id=name, artifact_id=aid, revision_no=1,
                                content_sha256=key, content_location=location)
                for name, aid in [('again', 'missing'), ('live', 'survivor')]])
    db.commit()
    result = quota.execute_orphan_revision_cleanup({'candidate_revision_ids': ['again']}, db=db)
    assert result['deleted_blobs'] == [] and store.exists(key)


@pytest.mark.asyncio
async def test_c008_storymap_uses_recent_200_messages_with_tie_break(db, monkeypatch):
    from app.models.db_model import Conversation, Message
    from app.services.storymap import story_compiler
    conv = Conversation(id='story-window', title='story')
    db.add(conv)
    instant = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.add_all([Message(conversation_id=conv.id, role='user', content=str(i), created_at=instant)
                for i in range(205)])
    db.commit()
    class AsyncDB:
        async def execute(self, statement):
            return db.execute(statement)
    observed = {}
    def compile_story(**kwargs):
        observed.update(kwargs)
        return kwargs
    async def no_mapspec(_):
        return None
    monkeypatch.setattr(story_compiler, 'compile_story_map', compile_story)
    monkeypatch.setattr('app.services.mapspec.store.mapspec_store_instance.get_mapspec', no_mapspec)
    await story_compiler.compile_for_session(AsyncDB(), conv)
    assert [m['content'] for m in observed['messages']] == [str(i) for i in range(5, 205)]


def test_g008_invalidation_sql_matches_exact_ref_and_session(db):
    from sqlalchemy import event, select
    from app.models.project_knowledge import ProjectKnowledgeEntry
    from app.services.project_knowledge.invalidation import handle_ref_invalidation_for_session
    rows = []
    for i in range(301):
        rows.append(ProjectKnowledgeEntry(id=f'pk-{i}', org_id='org', project_id='p',
            entity_kind='artifact', subject=str(i), authority_store='artifact', authority_id=f'a{i}',
            version_token='v', refs=[{'authority': 'session_ref', 'id': 'ref:match' if i == 300 else 'ref:match-other',
                                     'session_id': 's'}]))
    db.add_all(rows)
    db.commit()
    selects = []
    def record(conn, cursor, sql, *args):
        if sql.lstrip().upper().startswith('SELECT'):
            selects.append(sql)
    event.listen(db.bind, 'before_cursor_execute', record)
    try:
        assert handle_ref_invalidation_for_session(db, org_id='org', session_id='s', ref_id='ref:match') == 1
    finally:
        event.remove(db.bind, 'before_cursor_execute', record)
    assert len(selects) == 1 and 'json_each' in selects[0]
    assert db.get(ProjectKnowledgeEntry, 'pk-300').status == 'stale'
    assert sum(r.status == 'active' for r in db.execute(select(ProjectKnowledgeEntry)).scalars()) == 300


def test_g017_retention_pushes_pin_head_scope_and_bound_into_sql(db, tmp_path, monkeypatch):
    from datetime import timedelta
    from sqlalchemy import event
    from app.models.project import Artifact, ArtifactRevision
    from app.services.data_lifecycle.quota import plan_retention_cleanup, RetentionPolicy
    from app.services.durable_blob_store import FilesystemBlobStore
    old = datetime.now(timezone.utc) - timedelta(days=100)
    for aid, project in [('own', 'p'), ('foreign', 'q')]:
        db.add(Artifact(id=aid, project_id=project, name=aid, artifact_type='vector'))
        db.add_all([ArtifactRevision(id=f'{aid}-{i}', artifact_id=aid, revision_no=i,
            content_sha256=str(i) * 64, content_location=f'{i}/{i}.json', created_at=old,
            pinned_at=old if i == 1 else None) for i in range(1, 12)])
    db.commit()
    monkeypatch.setattr('app.services.durable_blob_store.get_filesystem_blob_store', lambda: FilesystemBlobStore(tmp_path))
    sqls = []
    def record(conn, cursor, sql, *args):
        sqls.append(sql)
    event.listen(db.bind, 'before_cursor_execute', record)
    try:
        result = plan_retention_cleanup(db, 'p', policy=RetentionPolicy(max_age_days=1), limit=2)
    finally:
        event.remove(db.bind, 'before_cursor_execute', record)
    assert [r['revision_id'] for r in result['candidate_revisions']] == ['own-10', 'own-2']
    candidate_sql = next(sql for sql in sqls if 'ORDER BY artifact_revisions.created_at' in sql)
    assert all(part in candidate_sql for part in ['pinned_at IS NULL', 'EXISTS', 'LIMIT', 'project_id ='])
    head_sql = next(sql for sql in sqls if 'GROUP BY artifact_revisions.artifact_id' in sql)
    assert 'artifact_revisions.artifact_id IN' in head_sql
