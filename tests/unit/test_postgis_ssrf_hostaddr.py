"""PostGIS must connect to the IP that passed SSRF validation, not a second lookup."""

from unittest.mock import Mock

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.adapters import postgis_adapter as pga
from app.services.data_fabric.security import DataFabricSecurity, DataFabricSecurityError


def _adapter(host: str = "db.example", allow_private: bool = False) -> pga.PostGISAdapter:
    return pga.PostGISAdapter(ConnectionProfile(
        source_type="postgis",
        host=host,
        port=5432,
        database="gis",
        username="reader",
        password="secret",
        allow_private=allow_private,
    ))


def test_validated_hostaddrs_pins_public_lookup_and_rejects_private(monkeypatch):
    monkeypatch.setattr(
        DataFabricSecurity, "_resolve_all", lambda host: ["8.8.8.8", "1.1.1.1"],
    )
    assert DataFabricSecurity.validated_hostaddrs("db.example") == ["8.8.8.8", "1.1.1.1"]
    host, hostaddr = pga._pinned_libpq_target("db.example", allow_private=False)
    assert host == "db.example,db.example"
    assert hostaddr == "8.8.8.8,1.1.1.1"

    monkeypatch.setattr(
        DataFabricSecurity, "_resolve_all", lambda host: ["8.8.8.8", "10.1.2.3"],
    )
    with pytest.raises(DataFabricSecurityError, match="10.1.2.3"):
        DataFabricSecurity.validated_hostaddrs("db.example")
    with pytest.raises(DataFabricSecurityError):
        DataFabricSecurity.validated_hostaddrs("127.0.0.1")
    assert DataFabricSecurity.validated_hostaddrs("8.8.8.8") == ["8.8.8.8"]


def test_postgis_connect_uses_pinned_hostaddr_not_a_rebound_address(monkeypatch):
    answers = [["8.8.8.8"], ["10.9.9.9"]]

    def resolve(_host):
        return list(answers.pop(0))

    monkeypatch.setattr(DataFabricSecurity, "_resolve_all", resolve)
    captured = {}

    def fake_pool(minconn, maxconn, **kwargs):
        captured["pool"] = kwargs
        raise RuntimeError("pool unavailable")

    connect = Mock(side_effect=AssertionError("direct connect must not run"))
    monkeypatch.setattr("psycopg2.pool.ThreadedConnectionPool", fake_pool)
    monkeypatch.setattr("psycopg2.connect", connect)
    adapter = _adapter()
    try:
        with pytest.raises(DataFabricSecurityError, match="10.9.9.9"):
            adapter._get_connection()
    finally:
        pga.reset_postgis_pools()
    assert captured["pool"]["host"] == "db.example"
    assert captured["pool"]["hostaddr"] == "8.8.8.8"
    connect.assert_not_called()
    assert answers == []


def test_postgis_pool_pins_single_public_address(monkeypatch):
    monkeypatch.setattr(
        DataFabricSecurity, "_resolve_all", lambda host: ["1.1.1.1"],
    )
    captured = {}
    sentinel = object()

    class FakePool:
        def __init__(self, minconn, maxconn, **kwargs):
            captured.update(kwargs)

        def getconn(self):
            return sentinel

        def closeall(self):
            return None

    monkeypatch.setattr("psycopg2.pool.ThreadedConnectionPool", FakePool)
    connect = Mock()
    monkeypatch.setattr("psycopg2.connect", connect)
    adapter = _adapter()
    try:
        assert adapter._get_connection() is sentinel
    finally:
        pga.reset_postgis_pools()
    assert captured["host"] == "db.example"
    assert captured["hostaddr"] == "1.1.1.1"
    connect.assert_not_called()
