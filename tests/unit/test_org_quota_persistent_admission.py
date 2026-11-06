"""S3d-003: actual producer transactions arbitrate shared organization quota."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker, Session

from app.core.database import Base
from app.core.errors import PlatformError
from app.models.db_model import OrgQuota, Organization, GeoComputeClusterRun, WorkflowInstanceRow, GeoComputeArtifact
from app.models.lakehouse_catalog import LakehouseCatalogItem
from app.models.lakehouse_datasets import LakehouseDatasetVersion
from app.services import org_quota
from app.services.org_quota_admission import admit_persistent_write, lock_org_transaction
from app.services.geocompute.cluster.store import ClusterRunStore
from app.services.geocompute.cluster.exchange import ArtifactExchange
from app.services.workflow_runtime.store import InstanceStore
from app.services.lakehouse.catalog_service import upsert_catalog_entry


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'quota.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as db:
        db.add(Organization(id=1, name="one", slug="default"))
        db.add(OrgQuota(org_id="1", max_concurrent_tasks=1, max_storage_bytes=100))
        db.commit()
    yield maker
    engine.dispose()


def _workflow(factory):
    return InstanceStore(factory=factory).create_instance(package_id="p", package_version="1", package_fingerprint="fp",
        owner_scope="owner", org_id="1", node_specs=[{"node_id": "n", "optional": False}])


def _cluster(factory):
    return ClusterRunStore(factory=factory).create_run(plan_snapshot={}, plan_fingerprint="fp", owner_scope="owner", org_id="1")


def _fields(sha="a", size=70, **changes):
    return {"object_id": sha * 64, "owner_type": "project", "owner_id": "project", "org_id": "1",
            "kind": "cog_raster", "content_sha256": sha * 64, "byte_size": size, **changes}


def _race(left, right):
    barrier = threading.Barrier(2)

    def run(operation):
        barrier.wait(5)
        try:
            operation()
            return "accepted"
        except org_quota.QuotaExceededError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, operation) for operation in (left, right)]
        return [future.result(timeout=15) for future in futures]


def test_cluster_and_workflow_race_for_one_shared_slot(factory):
    assert sorted(_race(lambda: _cluster(factory), lambda: _workflow(factory))) == ["accepted", "denied"]
    with factory() as db:
        used = db.execute(select(func.count()).select_from(GeoComputeClusterRun)).scalar_one()
        used += db.execute(select(func.count()).select_from(WorkflowInstanceRow)).scalar_one()
        assert used == 1


def test_preempted_run_consumes_slot_until_terminal(factory):
    with factory() as db:
        db.add(GeoComputeClusterRun(run_id="existing", owner_scope="owner", org_id="1", status="preempted", plan_fingerprint="fp", plan_snapshot={}))
        db.commit()
    with pytest.raises(org_quota.QuotaExceededError):
        _workflow(factory)
    with factory() as db:
        db.execute(select(GeoComputeClusterRun).where(GeoComputeClusterRun.run_id == "existing")).scalar_one().status = "completed"
        db.commit()
    assert _workflow(factory)["instance_id"]


def test_storage_race_between_catalog_and_spill_never_allocates_denied_bytes(factory, tmp_path, monkeypatch):
    with factory() as db:
        db.add(GeoComputeClusterRun(run_id="completed", owner_scope="owner", org_id="1", status="completed", plan_fingerprint="fp", plan_snapshot={}))
        db.commit()
    exchange = ArtifactExchange(root=str(tmp_path / "blobs"), factory=factory)
    put = MagicMock(wraps=exchange._store.put_blob)
    monkeypatch.setattr(exchange._store, "put_blob", put)

    def catalog():
        with factory() as db:
            upsert_catalog_entry(db, _fields())
            db.commit()

    def spill():
        exchange.put_bytes(b"x" * 70, owner_scope="owner", run_id="completed", compress=False)

    results = _race(catalog, spill)
    assert sorted(results) == ["accepted", "denied"]
    if results[1] == "denied":
        put.assert_not_called()
    with factory() as db:
        used = db.execute(select(func.coalesce(func.sum(LakehouseCatalogItem.byte_size), 0))).scalar_one()
        used += db.execute(select(func.coalesce(func.sum(GeoComputeArtifact.size_bytes), 0))).scalar_one()
        assert used == 70


def test_catalog_replacement_credit_is_same_org_active_delta(factory):
    with factory() as db:
        created = upsert_catalog_entry(db, _fields(size=80))
        db.add(GeoComputeArtifact(artifact_key="b" * 64, owner_scope="owner", org_id="1", kind="spill", codec="raw", size_bytes=20))
        db.commit()
    with factory() as db:
        assert upsert_catalog_entry(db, _fields(size=80))["id"] == created["id"]
        db.commit()
    with factory() as db:
        with pytest.raises(org_quota.QuotaExceededError):
            upsert_catalog_entry(db, _fields(size=81))
    with factory() as db:
        upsert_catalog_entry(db, _fields(size=60))
        db.commit()
        upsert_catalog_entry(db, _fields(size=60, status="revoked"))
        db.commit()
        upsert_catalog_entry(db, _fields(size=80, status="active"))
        db.commit()
        assert db.get(LakehouseCatalogItem, created["id"]).byte_size == 80
    with factory() as db:
        db.add(OrgQuota(org_id="2", max_storage_bytes=100))
        upsert_catalog_entry(db, _fields(sha="c", size=60, org_id="2", owner_id="foreign-project"))
        db.commit()
    with factory() as db:
        with pytest.raises(org_quota.QuotaExceededError):
            # The old org's 80 bytes cannot be credited to org 2.
            upsert_catalog_entry(db, _fields(size=80, org_id="2"))


def test_dataset_commit_dedup_does_not_double_charge_and_denial_precedes_manifest(factory, tmp_path, monkeypatch):
    from app.services.lakehouse import dataset_registry as registry
    from app.services.durable_blob_store import FilesystemBlobStore
    monkeypatch.setattr(registry, "_object_store", lambda: FilesystemBlobStore(str(tmp_path / "cas")))
    monkeypatch.setattr(registry, "_resolved_content", lambda *a, **kw: {"byte_size": 50, "content_sha256": "f" * 64})
    publish = MagicMock(wraps=registry.publish_commit_record)
    monkeypatch.setattr(registry, "publish_commit_record", publish)
    with factory() as db:
        row, _ = registry.create_dataset(db, name="dataset", session_id="session")
        first = registry.commit_version(db, row, branch="main", data_object_id="d" * 64)
        db.commit()
        registry.create_branch(db, row, name="a", from_version_id=first.version_id)
        registry.create_branch(db, row, name="b", from_version_id=first.version_id)
        db.commit()
        second = registry.commit_version(db, row, branch="a", data_object_id="e" * 64)
        db.commit()
        duplicate = registry.commit_version(db, row, branch="b", data_object_id="e" * 64)
        db.commit()
        assert second.version_id == duplicate.version_id and duplicate.version_deduped
        assert db.execute(select(func.count()).select_from(LakehouseDatasetVersion)).scalar_one() == 2
        publish.reset_mock()
        with pytest.raises(org_quota.QuotaExceededError):
            registry.commit_version(db, row, branch="main", data_object_id="0" * 64)
        publish.assert_not_called()


@pytest.mark.parametrize("broken", ["org_quotas", "geocompute_runs"])
def test_authoritative_policy_or_usage_failure_rejects_internal_producer(factory, broken):
    from sqlalchemy import text
    with factory() as db:
        db.execute(text(f"DROP TABLE {broken}"))
        db.commit()
    with factory() as db:
        with pytest.raises(PlatformError) as error:
            admit_persistent_write(db, "1", task_increment=1)
        assert error.value.http_status == 503


def test_unlimited_policy_keeps_existing_zero_semantics(factory):
    with factory() as db:
        row = db.get(OrgQuota, "1")
        row.max_concurrent_tasks = 0
        row.max_storage_bytes = 0
        db.commit()
    assert _cluster(factory)
    assert _workflow(factory)["instance_id"]
    with factory() as db:
        upsert_catalog_entry(db, _fields(size=10**12))
        db.commit()


@pytest.mark.asyncio
async def test_redis_rate_failure_never_grants_a_local_quota(monkeypatch):
    from app.core.rate_limiter import RedisRateLimiter
    client = MagicMock()
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    client.pipeline.return_value = pipeline
    limiter = RedisRateLimiter(client)
    monkeypatch.setattr(limiter, "_ensure_client", AsyncMock(return_value=client))
    local = AsyncMock(return_value=True)
    monkeypatch.setattr(limiter._fallback, "is_allowed", local)
    monkeypatch.setattr("app.core.rate_limiter.get_rate_limiter", AsyncMock(return_value=limiter))
    assert await org_quota.check_rate("1") is False
    local.assert_not_called()


@pytest.mark.asyncio
async def test_production_missing_shared_rate_ledger_rejects(monkeypatch):
    from app.core.config import settings
    from app.core.rate_limiter import MemoryRateLimiter
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr("app.core.rate_limiter.get_rate_limiter", AsyncMock(return_value=MemoryRateLimiter()))
    with pytest.raises(PlatformError) as error:
        await org_quota.check_rate("1")
    assert error.value.http_status == 503


def test_postgres_lock_contention_is_bounded_retryable_admission():
    db = MagicMock()
    db.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    db.no_autoflush = nullcontext()
    db.connection.return_value.get_isolation_level.return_value = "READ COMMITTED"
    db.connection.return_value.connection.dbapi_connection.autocommit = False
    db.execute.return_value.scalar_one.return_value = False
    with pytest.raises(PlatformError) as error:
        lock_org_transaction(db, "1")
    assert error.value.http_status == 503
    assert "pg_try_advisory_xact_lock" in str(db.execute.call_args.args[0])
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.parametrize("level,autocommit", [("REPEATABLE READ", False), ("SERIALIZABLE", False), ("READ COMMITTED", True)])
def test_postgres_stale_snapshot_or_autocommit_is_rejected_before_lock(level, autocommit):
    db = MagicMock()
    db.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    db.no_autoflush = nullcontext()
    db.connection.return_value.get_isolation_level.return_value = level
    db.connection.return_value.connection.dbapi_connection.autocommit = autocommit
    with pytest.raises(PlatformError, match="READ COMMITTED") as error:
        lock_org_transaction(db, "1")
    assert error.value.http_status == 503
    db.execute.assert_not_called()


def test_sqlite_autocommit_cannot_release_the_admission_lock_before_write(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'autocommit.db'}", isolation_level="AUTOCOMMIT")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            with pytest.raises(PlatformError, match="transactional SQLite") as error:
                lock_org_transaction(db, "1")
            assert error.value.http_status == 503
    finally:
        engine.dispose()


def test_rollback_releases_actual_row_usage_without_a_reservation_counter(factory):
    with factory() as db:
        admit_persistent_write(db, "1", task_increment=1)
        db.add(WorkflowInstanceRow(instance_id="rolled-back", package_id="p", package_version="1", package_fingerprint="fp", owner_scope="owner", org_id="1"))
        db.flush()
        db.rollback()
    assert _cluster(factory)


def test_spill_dedup_at_limit_and_denial_before_file_allocation(factory, tmp_path, monkeypatch):
    exchange = ArtifactExchange(root=str(tmp_path / "spills"), factory=factory)
    first = exchange.put_bytes(b"x" * 100, owner_scope="owner", compress=False)
    assert exchange.put_bytes(b"x" * 100, owner_scope="owner", compress=False).key == first.key
    put = MagicMock(wraps=exchange._store.put_blob)
    monkeypatch.setattr(exchange._store, "put_blob", put)
    with pytest.raises(org_quota.QuotaExceededError):
        exchange.put_bytes(b"different", owner_scope="owner", compress=False)
    put.assert_not_called()
    assert exchange.get_bytes(first, owner_scope="owner") == b"x" * 100
    with factory() as db:
        assert db.execute(select(func.count()).select_from(GeoComputeArtifact)).scalar_one() == 1


def test_spill_failed_commit_compensates_new_bytes(factory, tmp_path):
    class BrokenCommit(Session):
        def commit(self):
            if any(isinstance(row, GeoComputeArtifact) for row in self.new):
                raise OSError("commit unavailable")
            return super().commit()

    broken = sessionmaker(bind=factory.kw["bind"], class_=BrokenCommit)
    exchange = ArtifactExchange(root=str(tmp_path / "failed-spills"), factory=broken)
    with pytest.raises(OSError, match="commit unavailable"):
        exchange.put_bytes(b"new-payload", owner_scope="owner", compress=False)
    with factory() as db:
        assert db.execute(select(func.count()).select_from(GeoComputeArtifact)).scalar_one() == 0
    assert list((tmp_path / "failed-spills").rglob("*.bin")) == []
