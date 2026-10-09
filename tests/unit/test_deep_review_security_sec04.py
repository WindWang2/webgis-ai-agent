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
def mission_env(monkeypatch, tmp_path):
    from app.api.routes import cockpit as cockpit_mod
    from app.api.routes import mission_runtime as mr_mod
    from app.core.auth import get_current_user_with_version
    from app.core.database import Base, get_async_db
    from app.models.db_model import User
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

    # cockpit admin 通道复核 DB role（深度评审：降级即时生效）——注入文件型
    # sqlite + seeded root 行，避免落到仓内 ./data/webgis.db。async 侧用
    # NullPool（TestClient portal loop 与测试 loop 互斥，连接不得跨 loop 复用）。
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "sec04-users.db"
    user_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(user_engine)
    with user_engine.begin() as conn:
        conn.execute(User.__table__.insert().values(
            id="root", username="root", email="root@test",
            password_hash="x", role="admin",
        ))
    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool
    )
    async_session = async_sessionmaker(async_engine, expire_on_commit=False)

    async def _override_async_db():
        async with async_session() as db:
            yield db

    app.dependency_overrides[get_async_db] = _override_async_db

    def _as_user(user_id: str, role: str = "editor") -> None:
        holder["user"] = {"user_id": user_id, "role": role, "org_id": 1}

    def _set_db_role(user_id: str, role: str) -> None:
        with user_engine.begin() as conn:
            conn.execute(
                User.__table__.update()
                .where(User.__table__.c.id == user_id)
                .values(role=role)
            )

    try:
        yield {"svc": svc, "as_user": _as_user, "set_db_role": _set_db_role}
    finally:
        app.dependency_overrides.pop(get_current_user_with_version, None)
        app.dependency_overrides.pop(get_async_db, None)
        user_engine.dispose()


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


def test_cockpit_downgraded_admin_jwt_loses_org_wide_view(mission_env):
    """深度评审 High：DB 已把 root 降为 viewer 后，剩余 TTL 内持旧 admin
    claim 的 token 不得再见 org 全量列表 / 他人 mission（admin 判定复核
    DB role，而非只信 JWT claim）。"""
    svc = mission_env["svc"]
    mid_a = _create(svc, "user-a")

    mission_env["set_db_role"]("root", "viewer")

    headers = _auth("root", role="admin")
    body = client.get("/api/v1/cockpit/missions", headers=headers).json()
    # root 名下无 mission —— org 全量视图被收回后应为空集，见不到 user-a 的
    assert [m["mission_id"] for m in body["missions"]] == []
    assert client.get(
        f"/api/v1/cockpit/missions/{mid_a}", headers=headers
    ).status_code == 404

    # pre-landing review：降级收口不止 role —— 返回 dict 的 scopes 也要钳制
    # 到 DB 角色基线（claim 派生的 admin 专属 scope 不得残留，否则未来任何
    # has_scope 消费方都绕过降级）。
    import asyncio as _asyncio

    from app.api.routes import cockpit as _cockpit_mod
    from app.core.database import get_async_db
    from app.core.scopes import ROLE_SCOPES

    token_user = {
        "user_id": "root", "role": "admin", "org_id": 1,
        "scopes": sorted(ROLE_SCOPES["admin"]),
    }

    async def _downgraded():
        # mission_env 已把 get_async_db 指到 seeded 临时库（root 已降 viewer）
        db_gen = app.dependency_overrides[get_async_db]()
        db = await db_gen.__anext__()
        try:
            return await _cockpit_mod._user_with_db_role(
                dict(token_user), db)
        finally:
            await db_gen.aclose()

    degraded = _asyncio.run(_downgraded())
    assert degraded["role"] == "viewer"
    assert degraded["scopes"] == sorted(ROLE_SCOPES["viewer"]), (
        "降级后 scopes 必须钳制到 DB 角色基线（claim∩角色集）"
    )
    assert not (set(degraded["scopes"]) & {
        "admin:read", "admin:write", "geocompute:admin", "metrics:read",
    }), "admin 专属 scope 不得在降级后残留"
