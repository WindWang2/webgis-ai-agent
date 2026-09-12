"""V9 P3/P5 —— data-lifecycle REST 面测试（objects/policies/assess/gc 闭环）。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, Engine, SessionLocal
from app.main import app
from app.models.data_lifecycle import GcPlan, LifecycleObject, LifecyclePolicy
from app.models.db_model import GeoComputeWorkerCache, User
from app.services.data_lifecycle import adapters

client = TestClient(app)


def _token(role="viewer", sub="lc-user"):
    from app.core.auth import create_access_token
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": sub, "username": sub, "role": role})}


@pytest.fixture(autouse=True)
def _setup():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        db.query(GcPlan).delete()
        db.query(LifecycleObject).delete()
        db.query(LifecyclePolicy).delete()
        db.query(GeoComputeWorkerCache).delete()
        db.merge(User(id="lc-user", username="lc-user", email="lc-user@example.com",
                      password_hash="x", role="viewer", is_active=True))
        db.merge(User(id="lc-admin", username="lc-admin", email="lc-admin@example.com",
                      password_hash="x", role="admin", is_active=True))
        db.commit()
    yield


@pytest.fixture
def roots(tmp_path, monkeypatch):
    art = tmp_path / "artifacts"
    cog = tmp_path / "cog"
    data = tmp_path / "data"
    for p in (art, cog, data):
        p.mkdir(parents=True)
    monkeypatch.setattr(adapters, "_artifact_root", lambda: art)
    monkeypatch.setattr(adapters, "_cog_root", lambda: cog)
    monkeypatch.setattr(adapters, "_data_root", lambda: data)
    monkeypatch.setattr(adapters, "_spill_root", lambda: data / "ref_spill")
    monkeypatch.setattr("app.services.data_lifecycle.gc_plan.STAGING_DIR",
                        data / ".gc-staging")
    return {"artifacts": art, "cog": cog, "data": data}


def _enable_cog_policy_admin():
    res = client.put("/api/v1/data-lifecycle/policies/default.cog_output", json={
        "kind": "cog_output", "action": "stage_delete", "enabled": True,
        "staging_hours": 0,
        "tier_thresholds": {"warm_after_s": 3600, "cold_after_s": 7200},
    }, headers=_token("admin", "lc-admin"))
    assert res.status_code == 200
    return res.json()


def test_objects_and_policies_endpoints():
    listed = client.get("/api/v1/data-lifecycle/objects")
    assert listed.status_code == 200
    assert {"items", "total", "limit", "offset", "has_more"} <= set(listed.json().keys())

    pol = client.get("/api/v1/data-lifecycle/policies")
    assert pol.status_code == 200
    names = [p["name"] for p in pol.json()["policies"]]
    assert len(names) >= 5 and "default.cog_output" in names


def test_assess_requires_auth():
    res = client.post("/api/v1/data-lifecycle/assess", json={"persist": False})
    assert res.status_code in (401, 403)
    res2 = client.post("/api/v1/data-lifecycle/assess", json={"persist": False},
                       headers=_token())
    assert res2.status_code == 200
    assert set(res2.json()["summary"]["kinds"].keys()) >= {"cog_output"}


def test_policy_upsert_requires_admin():
    res = client.put("/api/v1/data-lifecycle/policies/x", json={
        "kind": "cog_output", "action": "stage_delete"}, headers=_token())
    assert res.status_code in (401, 403)
    bad = client.put("/api/v1/data-lifecycle/policies/y", json={
        "kind": "lakehouse_dataset", "action": "delete"},
        headers=_token("admin", "lc-admin"))
    assert bad.status_code == 400, "observe-only 类拒绝删除动作"


def test_gc_loop_via_api(roots):
    _enable_cog_policy_admin()
    target = roots["cog"] / "sess" / "old.tif"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"q" * 8)
    old = time.time() - 10 * 86400
    import os
    os.utime(target, (old, old))

    created = client.post("/api/v1/data-lifecycle/gc/plans",
                          json={"kinds": ["cog_output"]},
                          headers=_token("admin", "lc-admin"))
    assert created.status_code == 200
    plan = created.json()["plan"]
    plan_id = plan["id"]
    assert plan["status"] == "pending_approval"
    assert plan["candidate_count"] == 1

    # 非 admin 不可审批
    denied = client.post(f"/api/v1/data-lifecycle/gc/plans/{plan_id}/approve",
                         headers=_token())
    assert denied.status_code in (401, 403)

    # 未 approve 不可执行
    early = client.post(f"/api/v1/data-lifecycle/gc/plans/{plan_id}/execute",
                        headers=_token("admin", "lc-admin"))
    assert early.status_code == 409

    approved = client.post(f"/api/v1/data-lifecycle/gc/plans/{plan_id}/approve",
                           headers=_token("admin", "lc-admin"))
    assert approved.json()["plan"]["status"] == "approved"

    executed = client.post(f"/api/v1/data-lifecycle/gc/plans/{plan_id}/execute",
                           headers=_token("admin", "lc-admin"))
    assert executed.status_code == 200
    assert not target.exists(), "执行后源文件进 staging"

    detail = client.get(f"/api/v1/data-lifecycle/gc/plans/{plan_id}")
    assert detail.json()["plan"]["status"] in ("done", "executing")

    rolled = client.post(f"/api/v1/data-lifecycle/gc/plans/{plan_id}/rollback",
                         headers=_token("admin", "lc-admin"))
    assert rolled.status_code == 200
    assert target.exists(), "回滚后文件应还原"


def test_execute_via_durable_job_eager(roots):
    """eager 模式下 execute 走 submit_durable_job → celery 内联 → 完成。"""
    _enable_cog_policy_admin()
    target = roots["cog"] / "sess" / "job.tif"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"j")
    old = time.time() - 10 * 86400
    import os
    os.utime(target, (old, old))
    plan = client.post("/api/v1/data-lifecycle/gc/plans",
                       json={"kinds": ["cog_output"]},
                       headers=_token("admin", "lc-admin")).json()["plan"]
    client.post(f"/api/v1/data-lifecycle/gc/plans/{plan['id']}/approve",
                headers=_token("admin", "lc-admin"))
    res = client.post(f"/api/v1/data-lifecycle/gc/plans/{plan['id']}/execute",
                      headers=_token("admin", "lc-admin"))
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in ("analysis_task_started", "analysis_task_reused")
    detail = client.get(f"/api/v1/data-lifecycle/gc/plans/{plan['id']}")
    assert detail.json()["plan"]["status"] in ("done", "executing")
