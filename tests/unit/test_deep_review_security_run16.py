"""RUN-16 regression: recovery must release the lease on early failure.

Deep-review swarm 2026-09-19 (RUN-16): ``recover`` acquired a mission lease
and returned on rejected transitions / exceptions without releasing it,
blocking other workers until TTL expiry. A single try/finally now releases
the lease on every non-success path.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.mission_runtime.service import MissionRuntimeService
from app.services.mission_runtime.store import MissionStore, TransitionRejected


@pytest.fixture()
def service():
    import app.models.mission as _mission_models  # noqa: F401 — register tables

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return MissionRuntimeService(store=MissionStore(factory=factory))


def _lease_holder(svc, mission_id: str):
    rec = svc.store.get_mission(mission_id)
    assert rec is not None
    return rec.lease_owner, rec.lease_expires_at


def test_rejected_transition_releases_lease(service, monkeypatch):
    rec = service.create(org_id="org", user_id="u1")
    real_transition = service.store.transition

    def reject(*args, **kwargs):
        raise TransitionRejected("ILLEGAL_TRANSITION:test")

    monkeypatch.setattr(service.store, "transition", reject)
    result = service.recovery.recover(rec.mission_id, worker_id="w1")
    assert result["ok"] is False
    assert "ILLEGAL_TRANSITION" in result["reason"]
    monkeypatch.setattr(service.store, "transition", real_transition)

    owner, expires_at = _lease_holder(service, rec.mission_id)
    assert owner == ""
    assert not expires_at


def test_unexpected_error_also_releases_lease(service, monkeypatch):
    rec = service.create(org_id="org", user_id="u1")

    def boom(*args, **kwargs):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(service.store, "transition", boom)
    with pytest.raises(RuntimeError, match="store exploded"):
        service.recovery.recover(rec.mission_id, worker_id="w1")

    owner, expires_at = _lease_holder(service, rec.mission_id)
    assert owner == ""
    assert not expires_at


def test_successful_recovery_keeps_lease(service):
    rec = service.create(org_id="org", user_id="u1")
    result = service.recovery.recover(rec.mission_id, worker_id="w1")
    assert result["ok"] is True
    owner, expires_at = _lease_holder(service, rec.mission_id)
    assert owner == "w1"
    assert expires_at
