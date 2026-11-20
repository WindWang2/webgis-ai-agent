"""PostGIS checkout ownership must not mutate driver connection objects."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import psycopg2
import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.adapters import postgis_adapter as pga
from app.services.data_fabric.errors import SourceUnreachableError


class SlottedConnection:
    __slots__ = ("close_calls", "rollback_calls")

    def __init__(self):
        self.close_calls = 0
        self.rollback_calls = 0

    def close(self):
        self.close_calls += 1

    def rollback(self):
        self.rollback_calls += 1


@pytest.fixture
def adapter():
    return pga.PostGISAdapter(ConnectionProfile(source_type="postgis", name="ownership"))


def test_native_psycopg2_connection_needs_no_custom_attributes(adapter, monkeypatch):
    # Allocate the real extension type without connecting to any database. It
    # has the same attribute restrictions as a live psycopg2 connection.
    conn = psycopg2.extensions.connection.__new__(psycopg2.extensions.connection)
    pool = Mock()
    pool.getconn.return_value = conn
    monkeypatch.setattr(pga, "_get_or_create_postgis_pool", lambda *a, **kw: pool)

    assert adapter._get_connection() is conn
    adapter._release_connection(conn)

    pool.putconn.assert_called_once_with(conn)
    assert not hasattr(conn, "_is_pooled")
    assert adapter._connection_pools == {}


def test_release_uses_original_pool_after_replacement(adapter, monkeypatch):
    conn = SlottedConnection()
    original, replacement = Mock(), Mock()
    original.getconn.return_value = conn
    lookup = Mock(return_value=original)
    monkeypatch.setattr(pga, "_get_or_create_postgis_pool", lookup)

    adapter._get_connection()
    lookup.return_value = replacement
    adapter._release_connection(conn)

    original.putconn.assert_called_once_with(conn)
    replacement.putconn.assert_not_called()
    assert lookup.call_count == 1  # No lookup or new pool creation on release.
    assert conn.close_calls == 0
    assert adapter._connection_pools == {}


def test_direct_connection_is_closed_without_pool_lookup(adapter, monkeypatch):
    conn = SlottedConnection()
    lookup = Mock(return_value=None)
    monkeypatch.setattr(pga, "_get_or_create_postgis_pool", lookup)
    # Direct connect pins the SSRF-checked address before calling the driver.
    monkeypatch.setattr(
        pga, "_pinned_libpq_target", lambda host, allow_private=False: (host, "127.0.0.1"),
    )
    monkeypatch.setattr(psycopg2, "connect", lambda **kw: conn)

    with adapter._connection_context() as acquired:
        assert acquired is conn

    assert lookup.call_count == 1
    assert conn.close_calls == 1
    assert conn.rollback_calls == 2
    assert adapter._connection_pools == {}


def test_release_reclaims_pool_slot_after_first_putconn_failure(adapter, monkeypatch):
    conn = SlottedConnection()
    pool = Mock()
    pool.getconn.return_value = conn
    pool.putconn.side_effect = [RuntimeError("return failed"), None]
    monkeypatch.setattr(pga, "_get_or_create_postgis_pool", lambda *a, **kw: pool)

    adapter._release_connection(adapter._get_connection())

    assert pool.putconn.call_args_list == [
        ((conn,), {}), ((conn,), {"close": True}),
    ]
    assert conn.close_calls == 0
    assert adapter._connection_pools == {}


def test_pool_checkout_failure_is_typed_and_does_not_connect_directly(adapter, monkeypatch):
    pool = Mock()
    error = RuntimeError("pool exhausted")
    pool.getconn.side_effect = error
    monkeypatch.setattr(pga, "_get_or_create_postgis_pool", lambda *a, **kw: pool)
    connect = Mock()
    monkeypatch.setattr(psycopg2, "connect", connect)

    with pytest.raises(SourceUnreachableError) as caught:
        adapter._get_connection()

    assert caught.value.__cause__ is error
    connect.assert_not_called()
    pool.putconn.assert_not_called()
    assert adapter._connection_pools == {}


@pytest.mark.parametrize("query_fails", [False, True])
def test_overlapping_contexts_return_each_checkout(adapter, monkeypatch, query_fails):
    pool = Mock()
    pool.getconn.side_effect = SlottedConnection
    monkeypatch.setattr(pga, "_get_or_create_postgis_pool", lambda *a, **kw: pool)
    barrier = Barrier(4)

    def query():
        try:
            with adapter._connection_context() as conn:
                barrier.wait(timeout=5)
                if query_fails:
                    raise ValueError("query failed")
        except ValueError:
            if not query_fails:
                raise
        return conn

    with ThreadPoolExecutor(max_workers=4) as workers:
        connections = list(workers.map(lambda _: query(), range(4)))

    assert len({id(conn) for conn in connections}) == 4
    assert {call.args[0] for call in pool.putconn.call_args_list} == set(connections)
    assert pool.putconn.call_count == 4
    assert all(conn.close_calls == 0 for conn in connections)
    assert adapter._connection_pools == {}
