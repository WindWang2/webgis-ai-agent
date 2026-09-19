"""SEC-04 regression: Mission attribution + owner scoping.

Deep-review swarm 2026-09-19 (SEC-04): ``_uid()`` read ``id``/``sub`` while the
auth dependency returns ``user_id`` — every mission was persisted with an empty
owner, so org members could list/transition each other's missions. Reads and
transitions are now owner-scoped for non-admins; admins keep the org-wide view.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app

client = TestClient(app)


def _auth(user_id: str, role: str = "editor", org: int = 1) -> dict[str, str]:
    from app.core.auth import create_access_token

    return {
        "Authorization": "Bearer " + create_access_token(
            {"sub": user_id, "username": user_id, "role": role, "org_id": org},
            expires_delta=timedelta(hours=12),
        )
    }


@pytest.fixture()
def mission_env(monkeypatch):
    from app.api.routes import cockpit as cockpit_mod
    from app.api.routes import mission_runtime as mr_mod
    from app.core.auth import get_current_user_with_version
    from app.core.database import Base
    import app.models.mission as _mission_models  # noqa: F401 — register tables
    from app.services.mission_runtime.service import MissionRuntimeService
    from app.services.mission_runtime.store import MissionStore

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    svc = MissionRuntimeService(store=MissionStore(factory=factory))
    monkeypatch.setattr(mr_mod, "get_mission_runtime", lambda: svc)
    monkeypatch.setattr(cockpit_mod, "get_mission_runtime", lambda: svc)

    # require_scope() depends on get_current_user_with_version (token_version
    # DB lookup); hermetic tests inject the identity explicitly.
    holder = {"user": {"user_id": "user-a", "role": "editor", "org_id": 1}}
    app.dependency_overrides[get_current_user_with_version] = lambda: holder["user"]

    def _as_user(user_id: str, role: str = "editor") -> None:
        holder["user"] = {"user_id": user_id, "role": role, "org_id": 1}

    try:
        yield {"svc": svc, "as_user": _as_user}
    finally:
        app.dependency_overrides.pop(get_current_user_with_version, None)


def _create(svc, user_id: str, *, org_id: str = "1") -> str:
    rec = svc.create(org_id=org_id, user_id=user_id, root_goal="goal")
    return rec.mission_id


# ── attribution ───────────────────────────────────────────────────────────────


def test_route_create_persists_real_user_id(mission_env):
    mission_env["as_user"]("user-a")
    resp = client.post(
        "/api/v1/mission-runtime/missions",
        json={"root_goal": "analyze"},
    )
    assert resp.status_code == 200
    rec = mission_env["svc"].store.get_mission(resp.json()["mission_id"])
    assert rec is not None and rec.user_id == "user-a"


# ── owner scoping (non-admin) ────────────────────────────────────────────────


def test_non_admin_cannot_read_other_users_mission(mission_env):
    mid = _create(mission_env["svc"], "user-a")

    mission_env["as_user"]("user-a")
    assert client.get(f"/api/v1/mission-runtime/missions/{mid}").status_code == 200

    mission_env["as_user"]("user-b")
    assert client.get(f"/api/v1/mission-runtime/missions/{mid}").status_code == 404
    assert client.get(
        f"/api/v1/mission-runtime/missions/{mid}/diagnostics"
    ).status_code == 404


@pytest.mark.parametrize("op", ["start", "suspend", "resume", "cancel"])
def test_non_admin_cannot_transition_other_users_mission(mission_env, op):
    mid = _create(mission_env["svc"], "user-a")
    mission_env["as_user"]("user-b")
    resp = client.post(
        f"/api/v1/mission-runtime/missions/{mid}/{op}",
        json={"worker_id": "w-b"},
    )
    assert resp.status_code == 404
    rec = mission_env["svc"].store.get_mission(mid)
    assert rec is not None and rec.state.value == "created"


def test_admin_reads_and_transitions_any_org_mission(mission_env):
    mid = _create(mission_env["svc"], "user-a")
    mission_env["as_user"]("root", role="admin")

    assert client.get(f"/api/v1/mission-runtime/missions/{mid}").status_code == 200

    started = client.post(
        f"/api/v1/mission-runtime/missions/{mid}/start",
        json={"worker_id": "w-admin"},
    )
    assert started.status_code == 200
    assert started.json()["state"] == "running"


# ── cockpit enumeration ──────────────────────────────────────────────────────


def test_cockpit_list_is_owner_scoped_for_non_admin(mission_env):
    svc = mission_env["svc"]
    mid_a = _create(svc, "user-a")
    mid_b = _create(svc, "user-b")

    body_b = client.get("/api/v1/cockpit/missions", headers=_auth("user-b")).json()
    ids_b = {m["mission_id"] for m in body_b["missions"]}
    assert mid_b in ids_b
    assert mid_a not in ids_b

    body_admin = client.get(
        "/api/v1/cockpit/missions", headers=_auth("root", role="admin")
    ).json()
    ids_admin = {m["mission_id"] for m in body_admin["missions"]}
    assert {mid_a, mid_b} <= ids_admin


def test_cockpit_detail_is_owner_scoped_for_non_admin(mission_env):
    mid = _create(mission_env["svc"], "user-a")

    assert client.get(f"/api/v1/cockpit/missions/{mid}",
                      headers=_auth("user-b")).status_code == 404
    assert client.get(f"/api/v1/cockpit/missions/{mid}",
                      headers=_auth("root", role="admin")).status_code == 200
