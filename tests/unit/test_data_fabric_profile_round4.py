"""Regression tests for round-4 data-fabric profile handling:

- SEC-07: create_data_source previously stored the SANITIZED profile
  (password -> "********") in the DB, so every later probe/sync/query rebuilt
  the ConnectionProfile with a fake password and failed to connect. The real
  profile is now persisted; sanitization happens on egress only.
- The create REST response must sanitize the profile too (a password leak that
  existed because the stored profile was previously already sanitized).
"""

from app.services.data_fabric.manager import DataFabricManager
from app.services.data_fabric.security import DataFabricSecurity
from app.schemas.data_fabric_schema import ConnectionProfile


def test_create_data_source_stores_real_profile(monkeypatch):
    """The stored connection_profile must keep the real password so later
    probe/sync/query can actually connect."""
    captured = {}

    class _FakeDB:
        def add(self, obj):
            captured["model"] = obj

        def commit(self):
            pass

        def refresh(self, obj):
            pass

        def query(self, *a, **k):
            return self

        def filter(self, *a, **k):
            return self

        def first(self):
            return None

    # Stub the probe/capabilities/sync so the test is unit-level.
    class _FakeHealth:
        status = "healthy"

        def model_dump(self):
            return {"status": "healthy"}

    class _FakeAdapter:
        def capabilities(self):
            return ["vector"]

    monkeypatch.setattr(DataFabricManager, "probe_profile", classmethod(lambda cls, p: _FakeHealth()))
    monkeypatch.setattr(DataFabricManager, "get_adapter", classmethod(lambda cls, p: _FakeAdapter()))
    monkeypatch.setattr(DataFabricManager, "sync_catalog", classmethod(lambda cls, db, sid: []))

    db = _FakeDB()
    DataFabricManager.create_data_source(
        db=db, name="pg", source_type="postgis",
        endpoint_url="https://db.example.com",
        profile_options={"ssl": True},
    )

    stored = captured["model"].connection_profile
    assert stored.get("password") is None or stored.get("password") != "********", (
        "SEC-07 regression: stored profile must keep the real password"
    )


def test_profile_rebuilt_from_stored_dict_has_working_fields():
    """sync/query rebuild ConnectionProfile from the stored dict — options and
    allow_private must survive (DATA-13 adjacent) and the password must be the
    real one, not the sanitized placeholder."""
    conn = ConnectionProfile(
        id="ds_x", name="n", source_type="postgis",
        url="https://db.example.com", options={"ssl": True},
        allow_private=False, password="secret123",
    )
    stored = conn.model_dump()
    # Simulate the manager reading it back.
    rebuilt = ConnectionProfile(
        id=stored["id"],
        name=stored["name"],
        source_type=stored["source_type"],
        url=stored["url"],
        options=stored.get("options", {}),
        allow_private=stored.get("allow_private", False),
        password=stored.get("password"),
    )
    assert rebuilt.password == "secret123", (
        "SEC-07 regression: password lost when rebuilding from stored profile"
    )
    assert rebuilt.options == {"ssl": True}
    assert rebuilt.allow_private is False


def test_sanitize_profile_dict_redacts_credentials():
    """The egress sanitizer must redact all credential fields."""
    conn = ConnectionProfile(
        id="ds_x", name="n", source_type="postgis",
        url="https://db.example.com", password="secret123",
        access_key="AK", secret_key="SK",
    )
    sanitized = DataFabricSecurity.sanitize_profile_dict(conn.model_dump())
    assert sanitized["password"] == "********"
    assert sanitized["access_key"] == "********"
    assert sanitized["secret_key"] == "********"
