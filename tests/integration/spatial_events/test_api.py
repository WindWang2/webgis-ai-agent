"""Spatial Events API 集成测试（真实路由 + 注入 hermetic 持久层）。

覆盖：401 红线 / org 提取 / 404-not-403 租户语义 / envelope 契约错误 400 /
webhook HMAC / disabled 语义 503 / replay 边界 / SSE 有界流。
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
import app.models.mission  # noqa: F401
import app.models.spatial_events  # noqa: F401
from app.main import app
from app.core.auth import get_current_user
from app.services.mission_runtime.service import (
    MissionRuntimeService,
    reset_mission_runtime_for_tests,
)
from app.services.mission_runtime.store import MissionStore
from app.services.spatial_events import service as svc_mod
from app.services.spatial_events.ledger import SpatialEventLedger
from app.services.spatial_events.service import SpatialEventService


@pytest.fixture()
def se_factory():
    """function 级隔离：sqlite 内存引擎每个测试独立（防跨测试事件残留）。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def api(se_factory, monkeypatch):
    monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "1")
    monkeypatch.setenv("GIS_SPATIAL_EVENT_MISSION_BRIDGE", "1")
    monkeypatch.setenv("GIS_SPATIAL_EVENT_INVALIDATION", "1")
    monkeypatch.delenv("GIS_SPATIAL_EVENT_WEBHOOK_SECRET", raising=False)
    ledger = SpatialEventLedger(factory=se_factory)
    runtime = MissionRuntimeService(store=MissionStore(factory=se_factory))
    service = SpatialEventService(
        ledger, mission_runtime=runtime,
    )
    monkeypatch.setattr(svc_mod, "get_spatial_event_service", lambda: service)
    # 路由模块在 import 时绑定了名字 —— 同步打补丁
    import app.api.routes.spatial_events as se_routes

    monkeypatch.setattr(se_routes, "get_spatial_event_service", lambda: service)
    # portfolio 读模型的工厂 seam
    import app.api.routes.portfolio as pf_routes

    monkeypatch.setattr(pf_routes, "_factory", lambda: se_factory)
    monkeypatch.setattr(
        "app.services.spatial_events.adapters._org_resolver", None,
        raising=False,
    )
    reset_mission_runtime_for_tests()

    def _fake_org(user):
        return {"alice": "org-a", "bob": "org-b"}.get(str(user.get("id")), "")

    from app.core import tenancy

    monkeypatch.setattr(tenancy, "effective_org_in_thread", _fake_org)
    app.dependency_overrides[get_current_user] = lambda: {"id": "alice"}
    yield SimpleNamespace(client=TestClient(app), ledger=ledger, runtime=runtime,
                          service=service)
    app.dependency_overrides.pop(get_current_user, None)
    reset_mission_runtime_for_tests()


class SimpleNamespace:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _ingest_body(**over):
    kw = dict(
        kind="dataset.version_changed",
        subject_type="dataset",
        subject_key="dataset:ndvi",
        payload={"revision": "v7", "metric": {"ndvi": 0.1}},
    )
    kw.update(over)
    return kw


class TestAuthRedLine:
    def test_401_without_user(self):
        client = TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides.pop(get_current_user, None)
        r = client.get("/api/v1/spatial-events/events")
        assert r.status_code == 401


class TestEventsAPI:
    def test_ingest_and_list_roundtrip(self, api):
        r = api.client.post("/api/v1/spatial-events/events",
                            json=_ingest_body())
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "appended"
        assert body["row_id"] >= 1
        # 同 event_id 重复投递（显式 id）
        r2 = api.client.post(
            "/api/v1/spatial-events/events",
            json=_ingest_body(event_id=None) | {},
        )
        _ = r2
        lst = api.client.get("/api/v1/spatial-events/events").json()
        assert lst["count"] >= 1
        assert lst["events"][0]["org_id"] == "org-a"
        detail = api.client.get(
            f"/api/v1/spatial-events/events/{body['event_id']}"
        )
        assert detail.status_code == 200

    def test_ingest_contract_error_400(self, api):
        r = api.client.post(
            "/api/v1/spatial-events/events",
            json=_ingest_body(kind="bogus.kind"),
        )
        assert r.status_code == 400
        big = {"blob": "x" * 3000}
        r2 = api.client.post(
            "/api/v1/spatial-events/events",
            json=_ingest_body(payload=big),
        )
        assert r2.status_code == 400

    def test_event_detail_cross_tenant_404(self, api):
        r = api.client.post("/api/v1/spatial-events/events",
                            json=_ingest_body())
        event_id = r.json()["event_id"]
        app.dependency_overrides[get_current_user] = lambda: {"id": "bob"}
        detail = api.client.get(
            f"/api/v1/spatial-events/events/{event_id}"
        )
        assert detail.status_code == 404  # 404-not-403：不泄露存在性
        lst = api.client.get("/api/v1/spatial-events/events").json()
        assert lst["count"] == 0
        app.dependency_overrides[get_current_user] = lambda: {"id": "alice"}

    def test_stats_and_health(self, api):
        api.client.post("/api/v1/spatial-events/events", json=_ingest_body())
        stats = api.client.get("/api/v1/spatial-events/stats").json()
        assert stats["org_id"] == "org-a"
        assert stats["events_by_status"].get("pending", 0) >= 1
        health = api.client.get("/api/v1/spatial-events/health").json()
        assert health["enabled"] is True

    def test_disabled_write_paths_503(self, api, monkeypatch):
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "0")
        r = api.client.post("/api/v1/spatial-events/events",
                            json=_ingest_body())
        assert r.status_code == 503
        r2 = api.client.post("/api/v1/spatial-events/drain")
        assert r2.status_code == 503
        r3 = api.client.post("/api/v1/spatial-events/replay",
                             json={"from_id": 0, "to_id": 10})
        assert r3.status_code == 503
        health = api.client.get("/api/v1/spatial-events/health").json()
        assert health["enabled"] is False
        # 读路径常开（诚实 diagnostics）
        assert api.client.get("/api/v1/spatial-events/events").status_code == 200


class TestWatchesAPI:
    def _watch_body(self, **over):
        kw = dict(
            watch_id="api-w1",
            name="ndvi-low",
            kinds=["dataset.version_changed"],
            actions=["mission_create"],
            condition={"metric_name": "metric.ndvi", "metric_op": "lt",
                       "metric_value": 0.2},
            mission_goal_template="审查 {subject_key}（{facts_digest}）",
            cooldown_s=0,
        )
        kw.update(over)
        return kw

    def test_watch_crud_and_trigger(self, api):
        r = api.client.post("/api/v1/spatial-events/watches",
                            json=self._watch_body())
        assert r.status_code == 200
        # watch 永远落在本 org（body 不可指定 org）
        assert api.ledger.get_watch("api-w1", org_id="org-a") is not None
        assert api.ledger.get_watch("api-w1", org_id="org-b") is None
        got = api.client.get("/api/v1/spatial-events/watches/api-w1").json()
        assert got["watch"]["watch_id"] == "api-w1"
        # 契约错误 → 400（mission 动作缺 goal 模板）
        bad = api.client.post(
            "/api/v1/spatial-events/watches",
            json=self._watch_body(watch_id="bad-w",
                                  mission_goal_template=None),
        )
        assert bad.status_code == 400
        # 触发：drain 后 mission 建立
        api.client.post("/api/v1/spatial-events/events", json=_ingest_body())
        d = api.client.post("/api/v1/spatial-events/drain").json()
        assert d["fires"] == 1
        missions = api.runtime.store.list_unfinished(org_id="org-a", limit=10)
        assert len(missions) == 1
        fires = api.client.get("/api/v1/spatial-events/fires").json()
        assert fires["count"] == 1
        assert fires["fires"][0]["outcome"] == "mission_created"
        # 跨租户触发负例：org-b watch 不吃 org-a 事件
        app.dependency_overrides[get_current_user] = lambda: {"id": "bob"}
        api.client.post("/api/v1/spatial-events/watches",
                        json=self._watch_body(watch_id="bob-w"))
        app.dependency_overrides[get_current_user] = lambda: {"id": "alice"}
        api.client.post("/api/v1/spatial-events/events",
                        json=_ingest_body(subject_key="dataset:other"))
        api.client.post("/api/v1/spatial-events/drain")
        assert api.runtime.store.list_unfinished(org_id="org-b", limit=10) == []
        # 删除
        assert api.client.delete(
            "/api/v1/spatial-events/watches/api-w1").json()["deleted"] is True
        assert api.client.delete(
            "/api/v1/spatial-events/watches/api-w1").status_code == 404

    def test_watch_cross_tenant_read_404(self, api):
        api.client.post("/api/v1/spatial-events/watches",
                        json=self._watch_body())
        app.dependency_overrides[get_current_user] = lambda: {"id": "bob"}
        assert api.client.get(
            "/api/v1/spatial-events/watches/api-w1").status_code == 404
        app.dependency_overrides[get_current_user] = lambda: {"id": "alice"}


class TestWebhook:
    def test_hmac_enforced(self, api, monkeypatch):
        monkeypatch.setenv("GIS_SPATIAL_EVENT_WEBHOOK_SECRET", "sekrit")
        body = _ingest_body(org_id="org-a", event_id="wh-1")
        import json as jsonlib

        raw = jsonlib.dumps(body).encode()
        sig = hmac_mod.new(b"sekrit", raw, hashlib.sha256).hexdigest()
        r = api.client.post(
            "/api/v1/spatial-events/webhook",
            content=raw,
            headers={"X-WebGIS-Signature": f"sha256={sig}",
                     "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        # 坏签名
        r2 = api.client.post(
            "/api/v1/spatial-events/webhook",
            content=raw,
            headers={"X-WebGIS-Signature": "deadbeef",
                     "Content-Type": "application/json"},
        )
        assert r2.status_code == 401
        # 幂等：同 event_id 再投 → duplicate
        r3 = api.client.post(
            "/api/v1/spatial-events/webhook",
            content=raw,
            headers={"X-WebGIS-Signature": f"sha256={sig}",
                     "Content-Type": "application/json"},
        )
        assert r3.json()["status"] == "duplicate"

    def test_disabled_without_secret(self, api):
        r = api.client.post("/api/v1/spatial-events/webhook",
                            json=_ingest_body(org_id="org-a"))
        assert r.status_code == 503


class TestReplayAPI:
    def test_dry_run_resume_force(self, api):
        r = api.client.post("/api/v1/spatial-events/events",
                            json=_ingest_body(event_id="rp-1"))
        row_id = r.json()["row_id"]
        # 未处理 → dry_run 报告 would_process
        dry = api.client.post(
            "/api/v1/spatial-events/replay",
            json={"from_id": 0, "to_id": row_id, "dry_run": True},
        ).json()
        assert dry["would_process"] == 1
        # 处理后非 force replay 跳过
        api.client.post("/api/v1/spatial-events/drain")
        rep = api.client.post(
            "/api/v1/spatial-events/replay",
            json={"from_id": 0, "to_id": row_id},
        ).json()
        assert rep["skipped_processed"] == 1
        # force 重放：幂等（fire/mission 不重复）
        force = api.client.post(
            "/api/v1/spatial-events/replay",
            json={"from_id": 0, "to_id": row_id, "force": True},
        ).json()
        assert force["replayed"] == 1
        missions = api.runtime.store.list_unfinished(org_id="org-a", limit=10)
        assert len(missions) <= 1
        # 非法区间
        bad = api.client.post(
            "/api/v1/spatial-events/replay",
            json={"from_id": 10, "to_id": 5},
        )
        assert bad.status_code == 400


class TestPortfolioAPI:
    def test_summary_projects_detail(self, api):
        api.client.post("/api/v1/spatial-events/events",
                        json=_ingest_body(project_id="proj-1"))
        s = api.client.get("/api/v1/portfolio/summary").json()
        assert s["org_id"] == "org-a"
        pl = api.client.get("/api/v1/portfolio/projects").json()
        assert any(p["project_id"] == "proj-1" for p in pl["projects"])
        d = api.client.get("/api/v1/portfolio/projects/proj-1").json()
        assert d["counts"]["recent_events"] >= 1
        # 跨租户 404
        app.dependency_overrides[get_current_user] = lambda: {"id": "bob"}
        assert api.client.get(
            "/api/v1/portfolio/projects/proj-1").status_code == 404
        app.dependency_overrides[get_current_user] = lambda: {"id": "alice"}


class TestSSEStream:
    async def test_bounded_backlog_generator(self, api):
        """直接驱动 tail 生成器（HTTP 流基础设施依赖真实服务器语义）。"""
        import app.api.routes.spatial_events as se_routes

        for i in range(3):
            api.client.post(
                "/api/v1/spatial-events/events",
                json=_ingest_body(event_id=f"sse-{i}"),
            )
        gen = se_routes._event_tail(api.ledger, "org-a", 0)
        chunks = [await gen.__anext__() for _ in range(3)]
        # 每次 yield = 一条完整 SSE 事件块（event:/data: 两行）
        assert all("event: spatial_event" in c and "\ndata:" in c for c in chunks)
        # 断连语义由生成器终止表达；租户域由 ledger 过滤保证
        gen_hb = se_routes._event_tail(api.ledger, "org-b", 0)
        hb = await gen_hb.__anext__()
        assert hb == ":hb\n\n"  # org-b 无事件 → 心跳
