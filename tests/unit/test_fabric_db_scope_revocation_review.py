"""#1936 S57-007: authoritative DB scope and credential cache revocation."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.data_fabric import DataSource, SourceFactsRecordModel
from app.services.data_fabric.fabric.connection_registry import get_connection_registry, reset_connection_registry, TenantScope
from app.services.data_fabric.fabric.runtime import FabricRuntime
from app.services.data_fabric.manager import DataFabricManager


@pytest.fixture
def db(monkeypatch):
    from app.services.data_fabric.security import DataFabricSecurity
    from app.services.data_fabric.connection_manager import connection_manager
    from app.services.data_fabric.fabric.source_facts import reset_source_facts_service
    from app.services.data_fabric.fabric.probing import reset_capability_probe_service
    # Adapter tests use an explicit trusted in-process endpoint seam, never IO.
    monkeypatch.setattr(DataFabricSecurity, 'validate_url', lambda url, **kw: url)
    reset_connection_registry()
    reset_source_facts_service()
    reset_capability_probe_service()
    connection_manager.clear()
    monkeypatch.setattr(DataFabricManager, 'get_adapter', lambda _: SimpleNamespace())
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()
    reset_connection_registry()
    reset_source_facts_service()
    reset_capability_probe_service()
    connection_manager.clear()


def _source(db, **over):
    args = dict(id='ds_scope_review', name='source', source_type='wfs', endpoint_url='https://1.1.1.1/wfs',
                org_id=1, owner_id=None, connection_profile={})
    args.update(over)
    source = DataSource(**args)
    db.add(source)
    db.commit()
    return source


def test_org_only_source_never_registers_global_or_foreign_scope(db):
    source = _source(db)
    adapter = DataFabricManager._governed_adapter(source)
    registry = get_connection_registry()
    assert registry.resolve(source.id, TenantScope(org_id=1)) is adapter
    assert registry.peek(source.id, TenantScope()) is None
    assert registry.resolve(source.id, TenantScope(org_id=2)) is None
    assert registry.resolve(source.id, TenantScope(owner='foreign-session')) is None
    # DB cache identity never substitutes for live DB authorization in tools.
    assert FabricRuntime().resolve(source.id, owner='foreign-session') is None


def test_db_update_revokes_cached_adapter_secret_and_persisted_facts(db):
    source = _source(db, connection_profile={'password': 'private-secret'})
    DataFabricManager._governed_adapter(source)
    registry = get_connection_registry()
    record = registry.peek(source.id, TenantScope(org_id=1))
    secret_ref = record.secret_ref
    assert secret_ref and registry._secret_store.get(secret_ref)
    db.add(SourceFactsRecordModel(scope_key='org:1', dataset_fingerprint='a' * 64,
        profile_id=source.id, collector='test', facts_json={}, collected_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    db.commit()
    source.endpoint_url = 'https://1.1.1.1/new-wfs'
    db.commit()
    assert registry.peek(source.id, TenantScope(org_id=1)) is None
    assert registry._secret_store.get(secret_ref) is None
    assert list(db.execute(select(SourceFactsRecordModel)).scalars()) == []
    assert FabricRuntime().resolve(source.id, owner='foreign-session') is None


def test_db_delete_revokes_all_historical_scopes_and_legacy_cache(db):
    from app.schemas.data_fabric_schema import ConnectionProfile
    from app.services.data_fabric.connection_manager import connection_manager
    source = _source(db)
    DataFabricManager._governed_adapter(source)
    profile = ConnectionProfile(id=source.id, name='legacy', source_type='wfs', url=source.endpoint_url)
    registry = get_connection_registry()
    registry.attach(profile, TenantScope(), build_adapter=False, prebuilt_adapter=SimpleNamespace())
    registry.attach(profile, TenantScope(owner='historical-session'), build_adapter=False, prebuilt_adapter=SimpleNamespace())
    # Real legacy namespaces are populated without connecting a network adapter.
    with connection_manager._lock:
        connection_manager._profiles[(None, source.id)] = profile
        connection_manager._adapters[(None, source.id)] = SimpleNamespace()
    db.delete(source)
    db.commit()
    for scope in [TenantScope(), TenantScope(org_id=1), TenantScope(owner='historical-session')]:
        assert registry.peek(source.id, scope) is None
    assert connection_manager.get_profile(source.id) is None
    assert FabricRuntime().resolve(source.id, owner='historical-session') is None
