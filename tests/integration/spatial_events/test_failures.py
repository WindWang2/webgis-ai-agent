"""故障注入测试：worker 崩溃重启恢复 / 乱序 / 重复投递 / stale lease / 取消。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
import app.models.mission  # noqa: F401
import app.models.spatial_events  # noqa: F401
from app.services.mission_runtime.service import MissionRuntimeService
from app.services.mission_runtime.store import MissionStore
from app.services.spatial_events import contracts as C
from app.services.spatial_events.ledger import SpatialEventLedger
from app.services.spatial_events.service import SpatialEventService

NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def env_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ledger = SpatialEventLedger(factory=factory)
    runtime = MissionRuntimeService(store=MissionStore(factory=factory))
    return factory, ledger, runtime


def _setup_watch(ledger, watch_id="w1", actions=("mission_create",)):
    ledger.upsert_watch(C.SpatialWatch(
        watch_id=watch_id, org_id="org-a", name="n",
        kinds=["dataset.version_changed"],
        condition=C.WatchCondition(
            metric_name="metric.ndvi", metric_op="lt", metric_value=0.2),
        actions=list(actions),
        mission_goal_template="审查 {subject_key}",
        cooldown_s=0,
    ))


def _evt(event_id, **over):
    kw = dict(
        kind="dataset.version_changed", org_id="org-a",
        subject_type="dataset", subject_key="dataset:ndvi",
        occurred_at=NOW, payload={"revision": "v1", "metric": {"ndvi": 0.1}},
    )
    kw["event_id"] = event_id
    kw.update(over)
    return C.SpatialEventEnvelope(**kw)


class TestCrashRecovery:
    async def test_processing_crash_exactly_once_side_effects(
        self, env_factory, monkeypatch
    ):
        _, ledger, runtime = env_factory
        _setup_watch(ledger)
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "1")
        monkeypatch.setenv("GIS_SPATIAL_EVENT_MISSION_BRIDGE", "1")

        worker_a = SpatialEventService(ledger, mission_runtime=runtime)
        worker_a.ingest_sync(_evt("crash-1"))
        rows = await ledger_async_claim(ledger, "worker-a")
        assert len(rows) == 1  # claimed → 模拟崩溃（不 mark_processed）

        # "重启"：全新 service 实例（同一持久层）
        worker_b = SpatialEventService(ledger, mission_runtime=runtime)
        # 恢复清扫：claimed_at 陈旧的 processing 复位
        monkeypatch.setattr(
            "app.services.spatial_events.flags.stale_claim_s", lambda: 0.0
        )
        summary = await worker_b.drain_once("worker-b")
        assert summary["processed"] == 1
        missions = runtime.store.list_unfinished(org_id="org-a", limit=10)
        assert len(missions) == 1
        # 再次重放（force 场景）：确定性 mission_id ⇒ 不双建
        row = ledger.get_event_by_event_id("crash-1")
        await worker_b._process_event(row)
        assert len(runtime.store.list_unfinished(org_id="org-a", limit=10)) == 1

    async def test_cursor_survives_restart(self, env_factory, monkeypatch):
        _, ledger, _ = env_factory
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "1")
        w1 = SpatialEventService(ledger, mission_runtime=None)
        w1.ingest_sync(_evt("c-1"))
        await w1.drain_once("w")
        # "重启"后 cursor 保留
        w2 = SpatialEventService(ledger, mission_runtime=None)
        assert w2.ledger.get_cursor("spatial-event-worker") == 1


async def ledger_async_claim(ledger, worker_id):
    import asyncio

    return await asyncio.to_thread(
        ledger.claim_batch, worker_id, limit=8
    )


class TestDuplicateAndOrder:
    async def test_out_of_order_arrival_causal_processing(
        self, env_factory, monkeypatch
    ):
        """乱序到达：ledger 因果序处理；watch 窗口按 occurred_at 判定。"""
        _, ledger, _ = env_factory
        _setup_watch(ledger, "w-win", actions=("notify_only",))
        ledger.upsert_watch(C.SpatialWatch(
            watch_id="w-win2", org_id="org-a", name="window",
            kinds=["dataset.version_changed"],
            condition=C.WatchCondition(window_s=60, window_min_events=3),
            actions=["notify_only"], cooldown_s=0,
        ))
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "1")
        svc = SpatialEventService(ledger, mission_runtime=None)
        # 后发生的事件先到（乱序）
        svc.ingest_sync(_evt("late", occurred_at=NOW + timedelta(seconds=50)))
        svc.ingest_sync(_evt("mid", occurred_at=NOW + timedelta(seconds=10)))
        svc.ingest_sync(_evt("early", occurred_at=NOW))
        summary = await svc.drain_once("w")
        assert summary["processed"] == 3
        fires = ledger.list_fires(org_id="org-a", watch_id="w-win2")
        assert len(fires) == 1  # 窗口满 3 → fire 一次（不管到达顺序）

    async def test_same_payload_different_subject_not_deduped(
        self, env_factory, monkeypatch
    ):
        _, ledger, _ = env_factory
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "1")
        ledger2 = ledger
        r1 = ledger2.append(_evt("d1", subject_key="dataset:a"))
        r2 = ledger2.append(_evt("d2", subject_key="dataset:b"))
        assert r1.status == "appended" and r2.status == "appended"
        assert r1.event_id != r2.event_id


class TestStaleLease:
    async def test_mission_lease_conflict_defers_not_overwrites(
        self, env_factory, monkeypatch
    ):
        """stale lease：目标 mission 被他方持锁 → bridge 让位（deferred），不强抢。"""
        import app.services.spatial_events.service as svc_mod

        _, ledger, runtime = env_factory
        _setup_watch(ledger, "w-rev", actions=("mission_revise",))
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "1")
        monkeypatch.setenv("GIS_SPATIAL_EVENT_MISSION_BRIDGE", "1")
        monkeypatch.setattr(svc_mod, "DEFERRED_BACKOFF_S", 0.0)  # 立即可重试
        # 预置活动 mission（org-a）
        m = runtime.create(org_id="org-a", root_goal="原始目标")
        # 他方持有 lease（epoch=E）
        got = runtime.store.acquire_lease(
            m.mission_id, owner="other-worker", org_id="org-a")
        assert got is not None

        svc = SpatialEventService(ledger, mission_runtime=runtime)
        svc.ingest_sync(_evt("rev-1"))
        summary = await svc.drain_once("w")
        # revise 需要 lease → FencingError/LEASE 失败 → deferred（可重试）
        assert summary["deferred"] == 1
        fire = ledger.get_fire("w-rev", "rev-1")
        assert fire["outcome"] == "deferred"
        rec = runtime.store.get_mission(m.mission_id, org_id="org-a")
        assert rec.root_goal == "原始目标"  # 未被强写
        # 他方释放后重试成功
        runtime.store.release_lease(
            m.mission_id, owner="other-worker", lease_epoch=got[0])
        summary2 = await svc.drain_once("w")
        assert summary2["processed"] == 1
        rec2 = runtime.store.get_mission(m.mission_id, org_id="org-a")
        assert rec2.goal_revision == 2  # revise 成功


class TestCancellationSemantics:
    async def test_watch_deleted_between_claim_and_process(
        self, env_factory, monkeypatch
    ):
        """claim 后、处理前删除 watch → 不产生触发（取消语义）。"""
        _, ledger, runtime = env_factory
        _setup_watch(ledger)
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "1")
        monkeypatch.setenv("GIS_SPATIAL_EVENT_MISSION_BRIDGE", "1")
        svc = SpatialEventService(ledger, mission_runtime=runtime)
        svc.ingest_sync(_evt("cx-1"))
        # 直接模拟 worker 已 claim 但尚未处理
        rows = ledger.claim_batch("w", limit=4)
        assert len(rows) == 1
        ledger.delete_watch("w1", org_id="org-a")
        outcome = await svc._process_event(rows[0])
        assert outcome["fires"] == 0
        assert runtime.store.list_unfinished(org_id="org-a", limit=10) == []
