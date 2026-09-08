"""V6 wave 12 — cluster REST 面：submit(202/413/429/401/404) / list /
get 三级回退 / cancel 跨进程旗标 / metrics(admin)。

与既有 geocompute routes 测试同一纪律：强制认证、owner 域隔离（他人/未知
一律 404，无存在性预言机）。store 全部指向临时 SQLite（TestClient 进程内）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app

client = TestClient(app)

def _auth(user_id: str, role: str = "editor") -> dict[str, str]:
    # org_id 进 JWT claim（get_current_user 从 token 读 org —— 不查 DB）
    from app.core.auth import create_access_token

    return {
        "Authorization": f"Bearer {create_access_token({'sub': user_id, 'role': role, 'org_id': 1})}"
    }


_AUTH = _auth("gc-v6-user")
_AUTH_ADMIN = _auth("gc-v6-admin", role="admin")


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture()
def v6_env(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'v6-rest.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    from app.services.geocompute import reuse_index, run_evidence
    from app.services.geocompute.cluster import store as cluster_store_mod

    monkeypatch.setattr(run_evidence, "session_factory", factory)
    monkeypatch.setattr(reuse_index, "session_factory", factory)
    monkeypatch.setattr(cluster_store_mod, "session_factory", factory)
    # 认证：真实 JWT + 全局库 seed 用户（与 authz/v5 routes 测试同一惯例；
    # require_admin 实时读 DB role —— admin 用户必须真落库）
    from datetime import datetime, timezone

    from app.core.database import SessionLocal
    from app.models.db_model import User

    db = SessionLocal()
    try:
        for uid, uname, role in (
            ("gc-v6-user", "gc-v6-user", "editor"),
            ("gc-v6-admin", "gc-v6-admin", "admin"),
            ("gc-v6-other", "gc-v6-other", "editor"),
        ):
            u = db.get(User, uid)
            if u is None:
                db.add(User(
                    id=uid, username=uname, email=f"{uname}@example.com",
                    password_hash="scrypt$16384$8$1$00$00", role=role,
                    is_active=True, token_version=0, org_id=1,
                    created_at=datetime.now(timezone.utc),
                ))
            else:
                # dev 库残留的同名用户可能是旧 seed（无 org_id）——
                # 本文件背压/隔离用例依赖 org_id=1 的确定性
                u.org_id = 1
                u.role = role
                u.is_active = True
        db.commit()
    finally:
        db.close()
    yield factory
    eng.dispose()


def _plan_body(unique: str = "rest") -> dict:
    return {
        "plan": {
            "plan_id": "v6-rest",
            "nodes": [
                {
                    "node_id": "scan", "category": "source_scan",
                    "operation": "inline", "inputs": [],
                    "parameters": {"features": [
                        {"type": "Feature",
                         "geometry": {"type": "Point",
                                      "coordinates": [116.0, 39.0]},
                         "properties": {"kind": "a", "u": unique}}
                    ]},
                },
            ],
            "budget": {},
        },
        "session_id": None,
    }


class TestSubmit:
    def test_submit_returns_202_with_persistent_row(self, v6_env):
        resp = client.post("/api/v1/geocompute/plans/runs", json=_plan_body(),
                           headers=_AUTH)
        assert resp.status_code == 202
        data = resp.json()
        assert data["run_id"].startswith("gexec-")
        assert data["status"] == "queued"
        assert data["source"] == "cluster"
        # 持久行确实存在
        from app.services.geocompute.cluster.store import ClusterRunStore

        row = ClusterRunStore().get_run(data["run_id"])
        assert row is not None and row["status"] == "queued"

    def test_submit_requires_auth(self, v6_env):
        resp = client.post("/api/v1/geocompute/plans/runs", json=_plan_body())
        assert resp.status_code in (401, 403)

    def test_submit_invalid_plan_422(self, v6_env):
        body = _plan_body()
        body["plan"]["nodes"] = [{"node_id": "x", "category": "nope"}]
        resp = client.post("/api/v1/geocompute/plans/runs", json=body,
                           headers=_AUTH)
        assert resp.status_code == 422

    def test_submit_oversized_plan_413(self, v6_env):
        body = _plan_body()
        # 合法契约但快照超预算：source_scan 内联大 features（>256KB）
        body["plan"]["nodes"][0]["parameters"]["features"] = [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"blob": "x" * 2048}}
            for _ in range(200)
        ]
        resp = client.post("/api/v1/geocompute/plans/runs", json=body,
                           headers=_AUTH)
        assert resp.status_code == 413
        assert resp.json()["detail"]["code"] == "PLAN_SNAPSHOT_TOO_LARGE"

    def test_submit_backpressure_429(self, v6_env):
        from app.services.geocompute.cluster.errors import (
            ClusterBackpressureError,
        )
        from app.services.geocompute.cluster.store import ClusterRunStore

        # 直接塞满租户队列（REST 路径用默认上限 32）
        for _ in range(32):
            ClusterRunStore().create_run(
                plan_snapshot=_plan_body()["plan"], plan_fingerprint="fp",
                owner_scope="u:x", tenant_raw="1",
            )
        resp = client.post("/api/v1/geocompute/plans/runs", json=_plan_body(),
                           headers=_AUTH)
        assert resp.status_code == 429


class TestRunReadPaths:
    def test_get_run_cluster_projection(self, v6_env):
        resp = client.post("/api/v1/geocompute/plans/runs", json=_plan_body(),
                           headers=_AUTH)
        run_id = resp.json()["run_id"]
        got = client.get(f"/api/v1/geocompute/runs/{run_id}", headers=_AUTH)
        assert got.status_code == 200
        data = got.json()
        assert data["status"] == "queued"
        assert data["source"] == "cluster"

    def test_get_run_isolated_across_owners(self, v6_env):
        resp = client.post("/api/v1/geocompute/plans/runs", json=_plan_body(),
                           headers=_AUTH)
        run_id = resp.json()["run_id"]
        got = client.get(f"/api/v1/geocompute/runs/{run_id}",
                         headers=_auth("gc-v6-other"))
        assert got.status_code == 404  # 他人 run：不区分存在性

    def test_list_runs_merges_cluster_and_snapshots(self, v6_env):
        resp = client.post("/api/v1/geocompute/plans/runs", json=_plan_body(),
                           headers=_AUTH)
        cluster_id = resp.json()["run_id"]
        # 造一条纯快照 run（进程内同步执行路径的痕迹），owner = 当前用户域
        from app.services.geocompute import run_evidence
        from app.services.geocompute.executor import owner_scope_for
        from app.services.geocompute.plan import ExecutionPlan, ExecutionRun, ExecutionRunStatus

        owner = owner_scope_for({"user_id": "gc-v6-user"})
        plan = ExecutionPlan(plan_id="p", nodes=[])
        snap_run = ExecutionRun(
            run_id="gexec-snapshot01", plan_id="p",
            plan_fingerprint=plan.graph_fingerprint(),
            status=ExecutionRunStatus.COMPLETED,
        )
        run_evidence.save_snapshot(snap_run, owner)
        listing = client.get("/api/v1/geocompute/runs?limit=50", headers=_AUTH)
        assert listing.status_code == 200
        data = listing.json()
        ids = {r["run_id"] for r in data["runs"]}
        snap_ids = {r["run_id"] for r in data["terminal_snapshots"]}
        assert cluster_id in ids
        assert "gexec-snapshot01" in snap_ids
        assert "gexec-snapshot01" not in ids  # 去重：行域优先

    def test_list_requires_auth(self, v6_env):
        resp = client.get("/api/v1/geocompute/runs")
        assert resp.status_code in (401, 403)


class TestCancelUpgrade:
    def test_cancel_queued_cluster_run_via_flag(self, v6_env):
        resp = client.post("/api/v1/geocompute/plans/runs", json=_plan_body(),
                           headers=_AUTH)
        run_id = resp.json()["run_id"]
        ok = client.post(f"/api/v1/geocompute/plans/runs/{run_id}/cancel",
                         headers=_AUTH)
        assert ok.status_code == 200
        data = ok.json()
        assert data["cancelled"] is True
        assert data["source"] == "cluster"
        # 幂等：重复取消
        ok2 = client.post(f"/api/v1/geocompute/plans/runs/{run_id}/cancel",
                          headers=_AUTH)
        assert ok2.status_code == 200
        assert ok2.json()["cancelled"] is False

    def test_cancel_unknown_run_404(self, v6_env):
        ok = client.post("/api/v1/geocompute/plans/runs/gexec-nonexistent/cancel",
                         headers=_AUTH)
        # 未知 run：404（与 V5 语义一致）
        assert ok.status_code == 404


class TestClusterMetrics:
    def test_metrics_admin_only_shape(self, v6_env):
        resp = client.get("/api/v1/geocompute/cluster/metrics",
                          headers=_AUTH_ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        # 有界基数：封闭词表 + 有界投影
        assert set(data["runs_by_status"]) <= {
            "queued", "leased", "running", "completed", "failed",
            "cancelled", "preempted",
        }
        assert len(data["ledger"]) <= 20
        assert data["cancel_latency"]["samples"] <= 128
        assert "workers" in data and "leader" in data
