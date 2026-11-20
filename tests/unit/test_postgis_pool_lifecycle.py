"""Process-wide PostGIS pools close on evict/revoke and stay bounded."""

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.adapters import postgis_adapter as pga
from app.services.data_fabric.connection_manager import DataFabricConnectionManager
from app.services.data_fabric.fabric.connection_registry import (
    ConnectionRegistry,
    InMemorySecretStore,
    TenantScope,
)


class _Pool:
    def __init__(self, host):
        self.host = host
        self.closed = False

    def closeall(self):
        self.closed = True


def _profile(password: str, pid: str = "pg-src") -> ConnectionProfile:
    return ConnectionProfile(
        id=pid,
        name="pg",
        source_type="postgis",
        host="8.8.8.8",
        port=5432,
        database="gis",
        username="reader",
        password=password,
        options={"application_name": "webgis"},
    )


def _key(password: str) -> str:
    return pga._pool_key(
        "8.8.8.8", 5432, "gis", "reader",
        password=password, options={"application_name": "webgis"},
    )


@pytest.fixture
def clean_pools():
    pga.reset_postgis_pools()
    yield
    pga.reset_postgis_pools()


def test_revision_change_and_revoke_dispose_pool(clean_pools):
    reg = ConnectionRegistry(secret_store=InMemorySecretStore())
    scope = TenantScope(owner="alice")
    reg.attach(_profile("secret-a"), scope, build_adapter=False)
    key = _key("secret-a")
    pool = _Pool("8.8.8.8")
    pga._POSTGIS_POOLS[key] = pool

    reg.attach(_profile("secret-b"), scope, build_adapter=False)
    assert pool.closed is True
    assert key not in pga._POSTGIS_POOLS

    key_b = _key("secret-b")
    pool_b = _Pool("8.8.8.8")
    pga._POSTGIS_POOLS[key_b] = pool_b
    assert reg.revoke_profile("pg-src") == 1
    assert pool_b.closed is True
    assert key_b not in pga._POSTGIS_POOLS


def test_manager_disconnect_disposes_pool(clean_pools):
    mgr = DataFabricConnectionManager()
    profile = _profile("secret-a", pid="pg-legacy")
    mgr._profiles[(None, profile.id)] = profile
    mgr._adapters[(None, profile.id)] = object()
    mgr._last_access[(None, profile.id)] = 0.0
    key = _key("secret-a")
    pool = _Pool("8.8.8.8")
    pga._POSTGIS_POOLS[key] = pool

    assert mgr.disconnect(profile.id) is True
    assert pool.closed is True
    assert key not in pga._POSTGIS_POOLS


def test_pool_map_lru_and_idle(clean_pools, monkeypatch):
    monkeypatch.setattr(pga, "_POSTGIS_POOL_MAX", 2)
    monkeypatch.setattr(pga, "_POOL_IDLE_TTL_S", 10.0)
    monkeypatch.setattr(
        pga, "_pinned_libpq_target",
        lambda host, allow_private=False: (host, "8.8.8.8"),
    )
    clock = {"now": 1_000.0}
    monkeypatch.setattr(pga.time, "monotonic", lambda: clock["now"])

    def fake_pool(minconn, maxconn, **kwargs):
        return _Pool(kwargs["host"])

    monkeypatch.setattr("psycopg2.pool.ThreadedConnectionPool", fake_pool)
    hosts = ("8.8.8.8", "1.1.1.1", "8.8.4.4")
    created = [
        pga._get_or_create_postgis_pool(host, 5432, "gis", "reader", "pw")
        for host in hosts
    ]
    assert created[0].closed is True
    assert created[1].closed is False and created[2].closed is False
    assert len(pga._POSTGIS_POOLS) == 2

    clock["now"] = 2_000.0
    newest = pga._get_or_create_postgis_pool("9.9.9.9", 5432, "gis", "reader", "pw")
    assert created[1].closed is True and created[2].closed is True
    assert newest.closed is False
    assert len(pga._POSTGIS_POOLS) == 1
