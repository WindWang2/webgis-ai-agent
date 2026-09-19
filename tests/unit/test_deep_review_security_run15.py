"""RUN-15 regression: mission budget charge must be revision-CAS'd.

Deep-review swarm 2026-09-19 (RUN-15): ``commit_consumption`` read the budget,
mutated it, and patched without ``expected_revision`` — concurrent charges
could overwrite each other (lost billing). The charge now CASes on revision
and retries on ``REVISION_CONFLICT``.
"""
from __future__ import annotations

import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services.mission_runtime.resources import MissionResourceLedger
from app.services.mission_runtime.service import MissionRuntimeService
from app.services.mission_runtime.store import MissionStore, TransitionRejected


def _make_store(tmp_path) -> MissionStore:
    import app.models.mission as _mission_models  # noqa: F401 — register tables

    engine = create_engine(
        f"sqlite:///{tmp_path / 'missions.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    return MissionStore(factory=sessionmaker(bind=engine, expire_on_commit=False))


def _leased_mission(tmp_path):
    store = _make_store(tmp_path)
    svc = MissionRuntimeService(store=store)
    rec = svc.create(org_id="org", user_id="u1", quota={"tokens": 1000})
    epoch = store.acquire_lease(rec.mission_id, owner="w1")
    assert epoch is not None
    return store, rec.mission_id, epoch[0]


def test_commit_passes_expected_revision(tmp_path, monkeypatch):
    store, mission_id, epoch = _leased_mission(tmp_path)
    ledger = MissionResourceLedger(store)
    seen = {}
    real = store.patch_mission

    def wrapper(mid, **kwargs):
        seen.update(kwargs)
        return real(mid, **kwargs)

    monkeypatch.setattr(store, "patch_mission", wrapper)
    current = store.get_mission(mission_id)
    assert current is not None

    ledger.commit_consumption(
        mission_id, lease_epoch=epoch, owner="w1", dim="tokens", amount=3.0
    )
    assert seen.get("expected_revision") == current.revision


def test_revision_conflict_retries_and_applies_charge(tmp_path, monkeypatch):
    store, mission_id, epoch = _leased_mission(tmp_path)
    ledger = MissionResourceLedger(store)
    real = store.patch_mission
    calls = {"n": 0}

    def flaky(mid, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransitionRejected("REVISION_CONFLICT")
        return real(mid, **kwargs)

    monkeypatch.setattr(store, "patch_mission", flaky)
    ledger.commit_consumption(
        mission_id, lease_epoch=epoch, owner="w1", dim="tokens", amount=3.0
    )
    assert calls["n"] == 2
    final = store.get_mission(mission_id)
    assert final.resource_budget.consumed["tokens"] == 3.0


def test_concurrent_charges_do_not_lose_updates(tmp_path):
    store, mission_id, epoch = _leased_mission(tmp_path)
    ledger = MissionResourceLedger(store)
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait(timeout=10)
            for _ in range(5):
                ledger.commit_consumption(
                    mission_id, lease_epoch=epoch, owner="w1",
                    dim="tokens", amount=1.0,
                )
        except BaseException as exc:  # noqa: BLE001 — surface in main thread
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    final = store.get_mission(mission_id)
    assert final.resource_budget.consumed["tokens"] == 10.0
