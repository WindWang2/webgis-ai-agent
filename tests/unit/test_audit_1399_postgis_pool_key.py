"""#1399: PostGIS pool key includes credential digest."""
from __future__ import annotations

from app.services.data_fabric.adapters import postgis_adapter as pga


def test_pool_key_differs_when_password_differs():
    a = pga._pool_key("h", 5432, "db", "u", password="pass-a")
    b = pga._pool_key("h", 5432, "db", "u", password="pass-b")
    assert a != b
    assert a.startswith("u@h:5432/db#")
    assert "pass-a" not in a and "pass-b" not in b


def test_pool_key_differs_when_options_differ():
    a = pga._pool_key("h", 5432, "db", "u", password="x", options={"sslmode": "require"})
    b = pga._pool_key("h", 5432, "db", "u", password="x", options={"sslmode": "disable"})
    assert a != b


def test_pool_key_stable_for_same_credentials():
    a = pga._pool_key("h", 5432, "db", "u", password="secret", options={"a": 1})
    b = pga._pool_key("h", 5432, "db", "u", password="secret", options={"a": 1})
    assert a == b


def test_pool_failures_bounded(monkeypatch):
    pga._POOL_FAILURES.clear()
    monkeypatch.setattr(pga, "_POOL_FAILURES_MAX", 8)
    for i in range(20):
        pga._POOL_FAILURES[f"k{i}"] = float(i)
        pga._bound_pool_failures()
    assert len(pga._POOL_FAILURES) <= 8
    # oldest evicted
    assert "k0" not in pga._POOL_FAILURES
    pga._POOL_FAILURES.clear()


def test_dispose_removes_pool():
    key = pga._pool_key("h", 5432, "db", "u", password="p")

    class FakePool:
        def __init__(self):
            self.closed = False

        def closeall(self):
            self.closed = True

    fake = FakePool()
    pga._POSTGIS_POOLS[key] = fake
    assert pga.dispose_postgis_pool("h", 5432, "db", "u", password="p") is True
    assert key not in pga._POSTGIS_POOLS
    assert fake.closed is True
    assert pga.dispose_postgis_pool("h", 5432, "db", "u", password="p") is False
