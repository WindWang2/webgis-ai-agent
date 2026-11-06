"""GC critical review: actual process death, live-owner exclusion and legacy bytes."""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.data_lifecycle import GcPlan, LifecycleObject
from app.services.data_lifecycle import gc_plan as gp


def _configure_roots(root: Path) -> None:
    from app.services.data_lifecycle import adapters
    gp.STAGING_DIR = root / 'staging'
    for name, folder in [('_artifact_root', 'artifacts'), ('_cog_root', 'cog'),
                         ('_data_root', 'data'), ('_spill_root', 'spill')]:
        path = root / folder
        path.mkdir(exist_ok=True)
        setattr(adapters, name, lambda path=path: path)


def _crash_after_staging(root: str, plan_id: str) -> None:
    from app.services.data_lifecycle import gc_revalidation
    root_path = Path(root)
    _configure_roots(root_path)
    gc_revalidation.candidate_still_cold = lambda *args: (True, 'cold')
    original = gp._stage_file
    def stage_and_die(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(71)
    gp._stage_file = stage_and_die
    engine = create_engine(f'sqlite:///{root_path / "gc.db"}')
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        gp.execute_plan(db, db.get(GcPlan, plan_id))


@pytest.fixture
def factory(tmp_path, monkeypatch):
    from app.services.data_lifecycle import adapters
    monkeypatch.setattr(gp, 'STAGING_DIR', tmp_path / 'staging')
    for name, folder in [('_artifact_root', 'artifacts'), ('_cog_root', 'cog'),
                         ('_data_root', 'data'), ('_spill_root', 'spill')]:
        path = tmp_path / folder
        path.mkdir()
        monkeypatch.setattr(adapters, name, lambda path=path: path)
    monkeypatch.setattr('app.services.data_lifecycle.gc_revalidation.candidate_still_cold', lambda *args: (True, 'cold'))
    engine = create_engine(f'sqlite:///{tmp_path / "gc.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _plan(factory, objects, status='approved'):
    with factory() as db:
        plan = GcPlan(status=status, scope={'staging_hours': 0}, plan_tree={'objects': objects})
        db.add(plan)
        db.commit()
        return plan.id


def test_process_death_after_move_can_resume_and_restore_same_plan(factory, tmp_path):
    source = tmp_path / 'cog' / 'old.tif'
    source.write_bytes(b'irreplaceable pixels')
    plan_id = _plan(factory, [{'kind': 'cog_output', 'object_id': 'old.tif'}])
    process = multiprocessing.get_context('spawn').Process(target=_crash_after_staging, args=(str(tmp_path), plan_id))
    process.start()
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join()
    assert process.exitcode == 71
    with factory() as db:
        plan = db.get(GcPlan, plan_id)
        assert plan.status == 'executing' and not source.exists()
        previous_token = plan.result['_execution_token']
        result = gp.execute_plan(db, plan)
        assert result['ok'] and result['recovered_execution']
        assert plan.result['_execution_token'] != previous_token
        restored = gp.rollback_plan(db, plan)
        assert restored['restored_count'] == 1 and plan.status == 'rolled_back'
    assert source.read_bytes() == b'irreplaceable pixels'


def test_live_executor_excludes_retry_rollback_and_purge(factory, tmp_path, monkeypatch):
    source = tmp_path / 'cog' / 'live.tif'
    source.write_bytes(b'live')
    plan_id = _plan(factory, [{'kind': 'cog_output', 'object_id': 'live.tif'}])
    entered, release = threading.Event(), threading.Event()
    original = gp._stage_file
    def stage(*args, **kwargs):
        original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(gp, '_stage_file', stage)
    def execute():
        with factory() as db:
            return gp.execute_plan(db, db.get(GcPlan, plan_id))
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(execute)
        try:
            assert entered.wait(3)
            for operation in [gp.execute_plan, gp.rollback_plan, lambda db, plan: gp.purge_staging(plan, db=db)]:
                with factory() as db:
                    with pytest.raises(gp.GcPlanError, match='live executor'):
                        operation(db, db.get(GcPlan, plan_id))
        finally:
            release.set()
        assert work.result(5)['ok']
    with factory() as db:
        assert gp.rollback_plan(db, db.get(GcPlan, plan_id))['restored_count'] == 1


def test_legacy_fabric_recovers_using_trusted_origin_metadata(factory, tmp_path):
    object_id = 'session/old.json'
    plan_id = _plan(factory, [{'kind': 'fabric_materialization', 'object_id': object_id}], status='done')
    staged = tmp_path / 'staging' / plan_id / 'fabric_materialization' / object_id
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b'pre-upgrade data')
    with factory() as db:
        db.add(LifecycleObject(kind='fabric_materialization', object_id=object_id,
            owner_scope='session:session', info={'lane': 'ref_spill'}))
        db.commit()
        result = gp.rollback_plan(db, db.get(GcPlan, plan_id))
        assert result['restored_count'] == 1 and not result['errors']
    assert (tmp_path / 'spill' / object_id).read_bytes() == b'pre-upgrade data'
    assert not (tmp_path / 'data' / object_id).exists()


def test_ambiguous_legacy_origin_preserved_and_reconciled_without_new_plan(factory, tmp_path):
    object_id = 'session/unknown.json'
    plan_id = _plan(factory, [{'kind': 'fabric_materialization', 'object_id': object_id}], status='done')
    staged = tmp_path / 'staging' / plan_id / 'fabric_materialization' / object_id
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b'preserve unknown origin')
    with factory() as db:
        plan = db.get(GcPlan, plan_id)
        result = gp.rollback_plan(db, plan)
        assert result['errors'] and result['rollback_partial'] and plan.status == 'failed'
        assert staged.read_bytes() == b'preserve unknown origin'
        with pytest.raises(gp.GcPlanError, match='partial recovery'):
            gp.purge_staging(plan, db=db)
        db.rollback()
        db.add(LifecycleObject(kind='fabric_materialization', object_id=object_id,
            owner_scope='session:session', info={'lane': 'ref_spill'}))
        db.commit()
        result = gp.rollback_plan(db, plan)
        assert not result['errors'] and not result['rollback_partial'] and plan.status == 'rolled_back'
    assert (tmp_path / 'spill' / object_id).read_bytes() == b'preserve unknown origin'


def test_legacy_parquet_format_label_does_not_guess_original_root(factory, tmp_path):
    object_id = 'session/fabric-geoparquet/old.parquet'
    plan_id = _plan(factory, [{'kind': 'fabric_materialization', 'object_id': object_id}], status='done')
    staged = tmp_path / 'staging' / plan_id / 'fabric_materialization' / object_id
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b'old parquet in either root')
    with factory() as db:
        origin = LifecycleObject(kind='fabric_materialization', object_id=object_id,
            owner_scope='session:session', info={'lane': 'fabric_geoparquet'})
        db.add(origin)
        db.commit()
        plan = db.get(GcPlan, plan_id)
        assert gp.rollback_plan(db, plan)['errors']
        assert staged.exists()
        origin.info = {'lane': 'fabric_geoparquet', 'origin_root': 'spill'}
        db.commit()
        assert gp.rollback_plan(db, plan)['restored_count'] == 1
    assert (tmp_path / 'spill' / object_id).read_bytes() == b'old parquet in either root'


def test_recovery_never_overwrites_new_bytes_at_original_path(factory, tmp_path):
    source = tmp_path / 'cog' / 'occupied.tif'
    source.write_bytes(b'approved old bytes')
    plan_id = _plan(factory, [{'kind': 'cog_output', 'object_id': 'occupied.tif'}])
    with factory() as db:
        plan = db.get(GcPlan, plan_id)
        assert gp.execute_plan(db, plan)['ok']
        source.write_bytes(b'newly produced bytes')
        result = gp.rollback_plan(db, plan)
        assert result['errors'] and result['rollback_partial']
        assert source.read_bytes() == b'newly produced bytes'
        assert (tmp_path / 'staging' / plan_id / 'cog_output/default/occupied.tif').read_bytes() == b'approved old bytes'


def test_abandoned_execution_without_its_storage_manifest_is_not_recreated(factory):
    plan_id = _plan(factory, [{'kind': 'cog_output', 'object_id': 'remote.tif'}], status='executing')
    with factory() as db:
        plan = db.get(GcPlan, plan_id)
        plan.result = {'_execution_protocol': 2, '_execution_token': 'remote-worker'}
        db.commit()
        for operation in (gp.execute_plan, gp.rollback_plan):
            with pytest.raises(gp.GcPlanError, match='original staging volume'):
                operation(db, plan)
            db.rollback()
        assert db.get(GcPlan, plan_id).status == 'executing'
