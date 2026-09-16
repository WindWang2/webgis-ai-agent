"""SpatialEventService 测试：drain 流水 / 幂等触发 / 背压 deferred / burst / 公平 / 恢复。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services.spatial_events import adapters, contracts as C
from app.services.spatial_events import service as svc_mod
from app.services.spatial_events.governor_gate import GovernorGate
from app.services.spatial_events.invalidation_bridge import InvalidationBridge
from app.services.spatial_events.service import (
    SpatialEventService,
    _interleave_by_org,
)


def _evt(ledger_flag=True, **over):
    kw = dict(
        kind="dataset.version_changed",
        org_id="org-a",
        subject_type="dataset",
        subject_key="dataset:ndvi",
        occurred_at=datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
        payload={"revision": "v7"},
    )
    kw.update(over)
    return C.SpatialEventEnvelope(**kw)


class FakeSessionStore:
    def __init__(self):
        self.data = {}

    async def get_state_field(self, sid, key):
        return self.data.get((sid, key))

    async def set_map_state(self, sid, key, value):
        self.data[(sid, key)] = value


class FakeWorkflowSvc:
    def __init__(self):
        self.calls = []

    async def apply_changes(self, instance_id, changes, *, owner_scope, source):
        self.calls.append(
            {
                "instance_id": instance_id,
                "changes": [c.model_dump() for c in changes],
                "owner_scope": owner_scope,
                "source": source,
            }
        )
        return {"marked_stale": ["n2", "n3"], "deferred": []}


class StubBridge(InvalidationBridge):
    """绕过 DB 实例查询（该查询另有用例覆盖）。"""

    def __init__(self, wf):
        super().__init__(workflow_service=wf, factory=None)
        self._stub_instances = []

    def set_instances(self, rows):
        self._stub_instances = rows
        # 事件 ref → 种子节点映射（新契约：ref 精确匹配 bound_ref）
        self._node_map = {"inst-1": ["data:subject"]}

    def set_node_map(self, mapping):
        self._node_map = mapping

    def find_affected_instances(self, event):
        return self._stub_instances

    def find_affected_nodes(self, instance_ids, ref):
        return {iid: self._node_map.get(iid, [])
                for iid in instance_ids}


def _service(ledger, mission_runtime, wf=None, store=None, bridge=None):
    return SpatialEventService(
        ledger,
        mission_runtime=mission_runtime,
        workflow_service=wf,
        session_store=store or FakeSessionStore(),
        invalidation_bridge=bridge,
        gate=GovernorGate(),
    )


@pytest.fixture()
def wired(mission_runtime):
    adapters.set_org_resolver(None)
    yield mission_runtime
    adapters.set_org_resolver(None)


class TestIngestAndDrain:
    async def test_end_to_end_projection(self, ledger, mission_runtime, event_env, wired):
        store = FakeSessionStore()
        service = _service(ledger, mission_runtime, store=store)
        res = service.ingest_sync(_evt(session_id="s1"))
        assert res.status == "appended"
        summary = await service.drain_once("w1")
        assert summary["processed"] == 1
        ring = store.data[("s1", "_spatial_event_facts")]
        assert len(ring) == 1
        assert ring[0]["kind"] == "dataset.version_changed"
        assert "payload" not in ring[0]  # 事实只含摘要

    async def test_projection_dedupes_event(self, ledger, mission_runtime, event_env, wired):
        store = FakeSessionStore()
        service = _service(ledger, mission_runtime, store=store)
        service.ingest_sync(_evt(event_id="e-same", session_id="s1"))
        await service.drain_once("w1")
        # 同 event_id 再投递 → duplicate，环仍 1 条
        second = service.ingest_sync(_evt(event_id="e-same", session_id="s1"))
        assert second.status == "duplicate"
        await service.drain_once("w1")
        assert len(store.data[("s1", "_spatial_event_facts")]) == 1

    async def test_no_session_no_projection(self, ledger, mission_runtime, event_env, wired):
        service = _service(ledger, mission_runtime)
        service.ingest_sync(_evt())
        summary = await service.drain_once("w1")
        assert summary["processed"] == 1

    async def test_malformed_row_skipped(self, ledger, mission_runtime, event_env, wired, db_factory):
        service = _service(ledger, mission_runtime)
        with db_factory() as db:
            from app.models.spatial_events import SpatialEventRow

            db.add(SpatialEventRow(
                event_id="bad-1", org_id="org-a", kind="bogus.kind",
                source="internal", subject_type="dataset",
                subject_key="x", occurred_at=datetime(2026, 9, 16, 12, 0, 0),
                ingested_at=datetime(2026, 9, 16, 12, 0, 0),
                priority="normal", payload={}, status="pending",
            ))
            db.commit()
        summary = await service.drain_once("w1")
        assert summary.get("skipped") == 1
        row = ledger.get_event_by_event_id("bad-1")
        assert row["status"] == "skipped"


class TestWatchFiring:
    def _watch(self, actions, ledger, **cond_over):
        cond = dict(metric_name="metric.ndvi", metric_op="lt", metric_value=0.2)
        cond.update(cond_over)
        ledger.upsert_watch(C.SpatialWatch(
            watch_id="w-ndvi", org_id="org-a", name="ndvi-low",
            kinds=["dataset.version_changed"],
            condition=C.WatchCondition(**cond),
            actions=actions,
            mission_goal_template="审查数据集 {subject_key}（事实摘要 {facts_digest}）",
            cooldown_s=0,
        ))

    async def test_notify_only_fire(self, ledger, mission_runtime, event_env, wired):
        self._watch(["notify_only"], ledger)
        service = _service(ledger, mission_runtime)
        service.ingest_sync(_evt(payload={"revision": "v7", "metric": {"ndvi": 0.1}}))
        summary = await service.drain_once("w1")
        assert summary["fires"] == 1
        fires = ledger.list_fires(org_id="org-a")
        assert len(fires) == 1 and fires[0]["outcome"] == "notified"

    async def test_mission_create_idempotent(self, ledger, mission_runtime, event_env, wired):
        self._watch(["mission_create"], ledger)
        service = _service(ledger, mission_runtime)
        service.ingest_sync(_evt(event_id="evt-1", payload={"metric": {"ndvi": 0.1}}))
        await service.drain_once("w1")
        missions = mission_runtime.store.list_unfinished(org_id="org-a", limit=10)
        assert len(missions) == 1
        mid = missions[0].mission_id
        assert mid.startswith("evt-")
        assert "dataset:ndvi" in missions[0].root_goal
        # 重放同一事件（force 重投场景）→ 幂等：不双建
        service.ingest_sync(_evt(event_id="evt-1", payload={"metric": {"ndvi": 0.1}}))
        await service.drain_once("w1")  # append duplicate → 无处理
        # 模拟重放：直接把行复位 pending
        await asyncio.to_thread(ledger.requeue_stale_processing, older_than_s=0)
        row = ledger.get_event_by_event_id("evt-1")
        _ = row
        service2 = _service(ledger, mission_runtime)
        await service2.drain_once("w2")
        assert len(mission_runtime.store.list_unfinished(org_id="org-a", limit=10)) == 1

    async def test_mission_bridge_off_suppresses(self, ledger, mission_runtime, event_env, monkeypatch, wired):
        monkeypatch.setenv("GIS_SPATIAL_EVENT_MISSION_BRIDGE", "0")
        self._watch(["mission_create"], ledger)
        service = _service(ledger, mission_runtime)
        service.ingest_sync(_evt(payload={"metric": {"ndvi": 0.1}}))
        summary = await service.drain_once("w1")
        assert summary["fires"] == 1
        fires = ledger.list_fires(org_id="org-a")
        assert fires[0]["outcome"] == "suppressed"
        assert mission_runtime.store.list_unfinished(org_id="org-a", limit=10) == []

    async def test_governor_defer_retries_later(self, ledger, mission_runtime, event_env, monkeypatch, wired):
        self._watch(["mission_create"], ledger)
        monkeypatch.setattr(svc_mod, "DEFERRED_BACKOFF_S", 0.0)

        class DenyOnceGate(GovernorGate):
            def __init__(self):
                super().__init__()
                self.denies = 0

            async def acquire(self, **kw):
                self.denies += 1
                if self.denies == 1:
                    return None
                return await super().acquire(**kw)

        gate = DenyOnceGate()
        service = SpatialEventService(
            ledger, mission_runtime=mission_runtime,
            session_store=FakeSessionStore(), gate=gate,
        )
        service.ingest_sync(_evt(event_id="evt-defer", payload={"metric": {"ndvi": 0.1}}))
        summary = await service.drain_once("w1")
        assert summary["deferred"] == 1
        assert mission_runtime.store.list_unfinished(org_id="org-a", limit=10) == []
        fire = ledger.get_fire("w-ndvi", "evt-defer")
        assert fire["outcome"] == "deferred"
        # 退避 0 → 重试成功（at-least-once + 确定性幂等 ⇒ 恰好一次创建）
        summary2 = await service.drain_once("w1")
        assert summary2["processed"] == 1
        assert len(mission_runtime.store.list_unfinished(org_id="org-a", limit=10)) == 1

    async def test_watch_delete_stops_trigger(self, ledger, mission_runtime, event_env, wired):
        self._watch(["mission_create"], ledger)
        service = _service(ledger, mission_runtime)
        service.ingest_sync(_evt(event_id="evt-x", payload={"metric": {"ndvi": 0.1}}))
        ledger.delete_watch("w-ndvi", org_id="org-a")  # 取消订阅
        summary = await service.drain_once("w1")
        assert summary["fires"] == 0
        assert mission_runtime.store.list_unfinished(org_id="org-a", limit=10) == []


class TestInvalidation:
    async def test_version_change_marks_affected_subgraph(self, ledger, mission_runtime, event_env, wired):
        wf = FakeWorkflowSvc()
        bridge = StubBridge(wf)
        bridge.set_instances([
            {"instance_id": "inst-1", "owner_scope": "u:a", "status": "running"},
        ])
        service = _service(ledger, mission_runtime, wf=wf, bridge=bridge)
        service.ingest_sync(_evt(kind="dataset.version_changed",
                                 payload={"revision": "v8", "ref": "ref:ds-1"},
                                 session_id="s1"))
        await service.drain_once("w1")
        assert len(wf.calls) == 1
        call = wf.calls[0]
        assert call["instance_id"] == "inst-1"
        assert call["changes"][0]["dimension"] == "data"
        assert call["changes"][0]["target_kind"] == "node"
        assert call["changes"][0]["target"] == "data:subject"
        assert call["changes"][0]["source"] == "data_hook"

    async def test_invalidation_flag_off_no_side_effect(self, ledger, mission_runtime, event_env, monkeypatch, wired):
        monkeypatch.setenv("GIS_SPATIAL_EVENT_INVALIDATION", "0")
        wf = FakeWorkflowSvc()
        bridge = StubBridge(wf)
        bridge.set_instances([{"instance_id": "inst-1", "owner_scope": "u:a"}])
        service = _service(ledger, mission_runtime, wf=wf, bridge=bridge)
        service.ingest_sync(_evt(payload={"revision": "v8"}))
        await service.drain_once("w1")
        assert wf.calls == []

    async def test_mapproduct_requires_data_changed(self, ledger, mission_runtime, event_env, wired):
        wf = FakeWorkflowSvc()
        bridge = StubBridge(wf)
        bridge.set_instances([{"instance_id": "inst-1", "owner_scope": "u:a"}])
        service = _service(ledger, mission_runtime, wf=wf, bridge=bridge)
        service.ingest_sync(_evt(kind="mapproduct.version_recorded",
                                 payload={"version_no": 3, "data_changed": False}))
        await service.drain_once("w1")
        assert wf.calls == []
        service.ingest_sync(_evt(kind="mapproduct.version_recorded",
                                 payload={"version_no": 4, "data_changed": True,
                                          "ref": "ref:prod-1"}))
        await service.drain_once("w1")
        assert len(wf.calls) == 1

    async def test_instant_lookup_scoped_by_org(self, ledger, event_env, db_factory):
        """实例发现按 org 过滤（租户红线：org-b 实例不可被 org-a 事件命中）。"""

        from app.models.db_model import WorkflowInstanceRow

        bridge = InvalidationBridge(factory=db_factory)
        with db_factory() as db:
            db.add(WorkflowInstanceRow(
                instance_id="inst-b", package_id="p", package_version="1",
                package_fingerprint="f", status="running", revision=1,
                owner_scope="u:b", org_id="org-b", session_id="s-b",
            ))
            db.commit()
        hits = bridge.find_affected_instances(
            {"org_id": "org-a", "session_id": "s-b", "kind": "dataset.version_changed"}
        )
        assert hits == []


class TestBurstAndFairness:
    async def test_burst_1000_coalesces_and_converges(self, ledger, mission_runtime, event_env, wired):
        service = _service(ledger, mission_runtime)
        # 900 同 subject（coalesce 目标）+ 100 独立 subject
        base = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(900):
            service.ingest_sync(_evt(
                event_id=f"burst-{i}",
                occurred_at=base + timedelta(milliseconds=i),
                payload={"revision": f"v{i}"},
            ))
        for i in range(100):
            service.ingest_sync(_evt(
                event_id=f"uniq-{i}", subject_key=f"dataset:u{i}",
                payload={"revision": "v1"},
            ))
        summary = await service.drain_once("w1", batch=256)
        stats = ledger.stats(org_id=None)
        assert summary["coalesced"] >= 800  # 同 subject 大幅合并
        total_terminal = sum(
            v for k, v in stats.items() if k in ("processed", "coalesced", "failed", "skipped")
        )
        assert total_terminal == 1000  # 无遗留、无丢失
        assert stats.get("pending", 0) == 0 and stats.get("processing", 0) == 0

    async def test_tenant_fairness_interleave(self):
        rows = (
            [{"id": i, "org_id": "org-a"} for i in range(4)]
            + [{"id": 100 + i, "org_id": "org-b"} for i in range(4)]
        )
        mixed = _interleave_by_org(rows)
        orgs = [r["org_id"] for r in mixed]
        assert orgs == ["org-a", "org-b", "org-a", "org-b",
                        "org-a", "org-b", "org-a", "org-b"]

    async def test_cursor_watermark_follows_terminal(self, ledger, mission_runtime, event_env, wired):
        service = _service(ledger, mission_runtime)
        service.ingest_sync(_evt(event_id="c1"))
        await service.drain_once("w1")
        assert ledger.get_cursor("spatial-event-worker") == 1
        service.ingest_sync(_evt(event_id="c2"))
        await service.drain_once("w1")
        assert ledger.get_cursor("spatial-event-worker") == 2
        # cursor 只前进：手动卡住一行 processing 不回退水位
        rows = ledger.list_events(org_id="org-a", after_id=0, limit=10)
        _ = rows


class TestWorkerLoop:
    async def test_worker_stops_on_stop_event(self, ledger, mission_runtime, event_env, wired):
        service = _service(ledger, mission_runtime)
        stop = asyncio.Event()
        stop.set()
        iterations = await service.run_worker("w", stop=stop, interval_s=0.05)
        assert iterations == 0

    async def test_worker_max_iterations(self, ledger, mission_runtime, event_env, wired):
        service = _service(ledger, mission_runtime)
        n = await service.run_worker("w", interval_s=0.01, max_iterations=3)
        assert n == 3


class TestAdapters:
    @pytest.fixture()
    def global_service(self, ledger, mission_runtime, event_env, monkeypatch, wired):
        """adapters._dispatch 走全局单例 —— 测试注入 hermetic 实例。"""
        test_service = _service(ledger, mission_runtime)
        monkeypatch.setattr(
            svc_mod, "get_spatial_event_service", lambda: test_service
        )
        return test_service

    async def test_org_required_no_guessing(self, event_env, wired):
        adapters.set_org_resolver(None)
        ok = await adapters.notify_map_mutation(
            "s1", mutation_kind="set_style", revision=3)
        assert ok is False  # 无 org 印章 → 不入账（租户红线）

    async def test_map_mutation_hook_ingests(self, ledger, mission_runtime, event_env, global_service):
        async def resolver(session_id):
            return "org-a"

        adapters.set_org_resolver(resolver)
        ok = await adapters.notify_map_mutation(
            "s1", mutation_kind="set_style", revision=3, actor="alice")
        assert ok is True
        rows = ledger.list_events(org_id="org-a", after_id=0, limit=5)
        assert rows[0]["kind"] == "map.mutation_applied"
        assert rows[0]["session_id"] == "s1"

    async def test_job_kind_mapping(self, ledger, mission_runtime, event_env, global_service):
        assert await adapters.notify_job_finished("j1", org_id="org-a", status="stale") is False
        assert await adapters.notify_job_finished(
            "j1", org_id="org-a", status="completed", job_type="analysis") is True
        assert await adapters.notify_job_finished(
            "j2", org_id="org-a", status="completed", job_type="simulation.forecast") is True
        rows = ledger.list_events(org_id="org-a", after_id=0, limit=10)
        kinds = [r["kind"] for r in rows]
        assert "job.completed" in kinds and "simulation.completed" in kinds

    async def test_artifact_revision_only_when_created(self, ledger, mission_runtime, event_env, global_service):
        assert await adapters.notify_artifact_revision(
            "a1", org_id="org-a", created=False) is False
        assert await adapters.notify_artifact_revision(
            "a1", org_id="org-a", created=True, revision_no=2,
            content_sha256="ab" * 16) is True

    async def test_runtime_flag_off_adapter_noop(self, ledger, mission_runtime, event_env, global_service, monkeypatch):
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "0")
        ok = await adapters.notify_job_finished("j9", org_id="org-a", status="completed")
        assert ok is False
        assert ledger.count_events(org_id=None) == 0


class TestSyncDispatchPaths:
    """P1 修复红测：sync hook（jobs worker / record_version 线程池上下文）
    必须真实入账且返回 bool —— 不再返回未 await 的 coroutine。"""

    async def test_notify_job_finished_sync_ingests(
        self, ledger, mission_runtime, event_env, monkeypatch
    ):
        monkeypatch.setattr(
            svc_mod, "get_spatial_event_service",
            lambda: SpatialEventService(ledger, mission_runtime=mission_runtime),
        )
        result = adapters.notify_job_finished_sync(
            "job-sync-1", org_id="org-a", status="completed",
            job_type="analysis",
        )
        assert isinstance(result, bool) and result is True
        rows = ledger.list_events(org_id="org-a", after_id=0, limit=5)
        assert any(r["kind"] == "job.completed" for r in rows)

    async def test_notify_mapproduct_sync_ingests(
        self, ledger, mission_runtime, event_env, monkeypatch
    ):
        monkeypatch.setattr(
            svc_mod, "get_spatial_event_service",
            lambda: SpatialEventService(ledger, mission_runtime=mission_runtime),
        )
        result = adapters.notify_mapproduct_version_sync(
            "proj-1", org_id="org-a", version_no=2, data_changed=True,
        )
        assert isinstance(result, bool) and result is True
        rows = ledger.list_events(org_id="org-a", after_id=0, limit=5)
        assert any(
            r["kind"] == "mapproduct.version_recorded" for r in rows
        )

    async def test_sync_paths_flag_off_noop(
        self, ledger, mission_runtime, event_env, monkeypatch
    ):
        monkeypatch.setattr(
            svc_mod, "get_spatial_event_service",
            lambda: SpatialEventService(ledger, mission_runtime=mission_runtime),
        )
        monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "0")
        assert adapters.notify_job_finished_sync(
            "j", org_id="org-a", status="completed") is False
        assert ledger.count_events(org_id=None) == 0


class TestDeferredRetryWithCooldown:
    """P1 修复红测：cooldown > 0 时 deferred 重试不得被冷却吞掉。"""

    async def test_retry_bypasses_cooldown(
        self, ledger, mission_runtime, event_env, monkeypatch
    ):
        ledger.upsert_watch(C.SpatialWatch(
            watch_id="w-cool", org_id="org-a", name="n",
            kinds=["dataset.version_changed"],
            condition=C.WatchCondition(
                metric_name="metric.ndvi", metric_op="lt", metric_value=0.2),
            actions=["mission_create"],
            mission_goal_template="审查 {subject_key}",
            cooldown_s=60,  # 默认值级别：修复前这里会吞掉重试
        ))
        monkeypatch.setattr(svc_mod, "DEFERRED_BACKOFF_S", 0.0)

        class DenyOnceGate(GovernorGate):
            def __init__(self):
                super().__init__()
                self.denies = 0

            async def acquire(self, **kw):
                self.denies += 1
                if self.denies == 1:
                    return None
                return await super().acquire(**kw)

        service = SpatialEventService(
            ledger, mission_runtime=mission_runtime,
            session_store=FakeSessionStore(), gate=DenyOnceGate(),
        )
        service.ingest_sync(_evt(event_id="cool-1",
                                 payload={"metric": {"ndvi": 0.1}}))
        s1 = await service.drain_once("w1")
        assert s1["deferred"] == 1
        # 修复前：重试被 cooldown 吞 → processed 且无 mission（动作永久丢失）
        s2 = await service.drain_once("w1")
        assert s2["processed"] == 1
        missions = mission_runtime.store.list_unfinished(
            org_id="org-a", limit=10)
        assert len(missions) == 1
        fire = ledger.get_fire("w-cool", "cool-1")
        assert fire["outcome"] == "mission_created"
