"""Real PostgreSQL cross-connection admission; deliberately not SQLite emulation.

Opt in via the existing REAL_SERVICES=1 lane. Each case owns a fresh schema;
ambient developer DATABASE_URL alone never authorizes database mutation.
"""
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.errors import PlatformError
from app.models.db_model import Organization, OrgQuota, GeoComputeClusterRun, WorkflowInstanceRow
from app.services import org_quota
from app.services.org_quota_admission import admit_persistent_write
from app.services.workflow_runtime.store import InstanceStore
from tests.test_real_services_smoke import _postgres_url

pytestmark = pytest.mark.real_services


@pytest.fixture
def pg_factory():
    url = make_url(_postgres_url()).set(drivername='postgresql+psycopg2')
    admin = create_engine(url, connect_args={'connect_timeout': 3})
    schema = '_quota_review_' + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    # Explicit schema translation prevents checkfirst or unqualified SQL from
    # finding/mutating a same-named table in public on an existing lane DB.
    engine = create_engine(url, connect_args={'connect_timeout': 3},
                           execution_options={'schema_translate_map': {None: schema}})
    try:
        Base.metadata.create_all(engine)
        maker = sessionmaker(bind=engine, expire_on_commit=False)
        with maker() as db:
            db.add(Organization(id=1, name='quota real lane', slug='default'))
            db.add(OrgQuota(org_id='1', max_concurrent_tasks=1))
            db.commit()
        yield maker
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _workflow(factory):
    return InstanceStore(factory=factory).create_instance(package_id='p', package_version='1',
        package_fingerprint='fp', owner_scope='owner', org_id='1',
        node_specs=[{'node_id': 'n', 'optional': False}])


@pytest.mark.parametrize('commit', [False, True], ids=['rollback-frees-slot', 'commit-consumes-slot'])
def test_pg_distinct_producer_waiter_observes_commit_or_rollback(pg_factory, commit):
    with pg_factory() as first, pg_factory() as witness:
        first_pid = first.execute(text('SELECT pg_backend_pid()')).scalar_one()
        assert first_pid != witness.execute(text('SELECT pg_backend_pid()')).scalar_one()
        witness.rollback()
        admit_persistent_write(first, '1', task_increment=1)
        first.add(GeoComputeClusterRun(run_id='pending-cluster', owner_scope='owner', org_id='1',
            status='queued', plan_fingerprint='fp', plan_snapshot={}))
        first.flush()
        # Actual second producer/connection races an uncommitted reservation.
        # pg_try_advisory_xact_lock must refuse, rather than count a snapshot
        # without the first producer and over-admit.
        with ThreadPoolExecutor(max_workers=1) as pool:
            blocked = pool.submit(_workflow, pg_factory)
            with pytest.raises(PlatformError) as error:
                blocked.result(timeout=5)
            assert error.value.http_status == 503
        if commit:
            first.commit()
            with pytest.raises(org_quota.QuotaExceededError):
                _workflow(pg_factory)
        else:
            first.rollback()
            assert _workflow(pg_factory)['instance_id']
    with pg_factory() as db:
        cluster_count = db.execute(select(func.count()).select_from(GeoComputeClusterRun)).scalar_one()
        workflow_count = db.execute(select(func.count()).select_from(WorkflowInstanceRow)).scalar_one()
        assert (cluster_count, workflow_count) == ((1, 0) if commit else (0, 1))
