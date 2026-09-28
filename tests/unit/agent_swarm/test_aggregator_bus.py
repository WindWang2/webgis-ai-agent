"""SwarmAggregator 凭证总线披露面与 fail-open 回写（ADR-0187 §6）。

collect 的披露纪律：失败/降级 capability 必须上提货单；无 store 验券
能力时保留（下游取券自然暴露）；merge 全程 fail-open（sink 异常只披露，
绝不改变集群 settle 结果）。
"""
from __future__ import annotations

from typing import Any, Optional


from app.services.agent_swarm.aggregator import (
    NullSink,
    SessionPlanSwarmSink,
    SwarmAggregator,
)
from app.services.agent_swarm.delegation_contracts import (
    MAX_MANIFEST_ENTRIES,
    SwarmAssetEntry,
    SwarmAssetManifest,
    SwarmReceiptStatus,
    SwarmSpecialistRole,
    SubagentReceipt,
    SwarmTaskDescriptor,
)


class FakeStore:
    """最小 SessionStoreProtocol 假件（alias 面 + ref_exists 验券探针）。"""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.aliases: dict[str, str] = {}
        self._seq = 0

    async def resolve_alias(self, session_id: str, alias: str) -> str:
        return self.aliases.get(f"{session_id}:{alias}", alias)

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        self.aliases[f"{session_id}:{alias}"] = ref_id

    async def store(self, session_id: str, payload: Any, prefix: str = "data") -> str:
        self._seq += 1
        ref = f"ref:{prefix}-{self._seq}"
        self.data[f"{session_id}:{ref}"] = payload
        return ref

    async def ref_exists(self, session_id: str, ref_id: str) -> bool:
        return f"{session_id}:{ref_id}" in self.data


class RaisingStore(FakeStore):
    """验券探针抛异常的 store（验券是增值面，异常必须 fail-open 保留）。"""

    async def ref_exists(self, session_id: str, ref_id: str) -> bool:
        raise ConnectionError("store probe down")


def _task(task_id: str, capability: str = "") -> SwarmTaskDescriptor:
    return SwarmTaskDescriptor(
        task_id=task_id,
        role=SwarmSpecialistRole.DATA_HUNTER,
        goal=f"task {task_id}",
        capability=capability,
    )


def _receipt(
    task_id: str,
    status: SwarmReceiptStatus,
    *,
    refs: Optional[list[str]] = None,
    degraded: bool = False,
    role: SwarmSpecialistRole = SwarmSpecialistRole.DATA_HUNTER,
) -> SubagentReceipt:
    return SubagentReceipt(
        assignment_id=f"asg-{task_id}",
        task_id=task_id,
        role=role,
        status=status,
        produced_refs=refs or [],
        degraded=degraded,
        summary=f"summary {task_id}",
    )


class TestCollectDisclosure:
    """collect：manifest 披露面（失败 / 降级 / 丢弃）。"""

    async def test_succeeded_refs_become_entries(self):
        agg = SwarmAggregator(sink=NullSink())
        manifest = await agg.collect(
            run_id="r1",
            session_id="s1",
            receipts={"t1": _receipt("t1", SwarmReceiptStatus.SUCCEEDED, refs=["ref:a", "ref:b"])},
            tasks={"t1": _task("t1", "cap.a")},
        )
        assert [e.ref_id for e in manifest.entries] == ["ref:a", "ref:b"]
        assert manifest.entries[0].capability == "cap.a"
        assert manifest.entries[0].summary == "summary t1"
        assert manifest.failed_capabilities == []
        assert manifest.dropped_refs == []

    async def test_failed_capability_disclosed_without_entries(self):
        agg = SwarmAggregator(sink=NullSink())
        manifest = await agg.collect(
            run_id="r1",
            session_id="s1",
            receipts={"t1": _receipt("t1", SwarmReceiptStatus.FAILED)},
            tasks={"t1": _task("t1", "cap.fail")},
        )
        assert manifest.entries == []
        assert manifest.failed_capabilities == ["cap.fail"]

    async def test_failed_capabilities_deduplicated(self):
        agg = SwarmAggregator(sink=NullSink())
        manifest = await agg.collect(
            run_id="r1",
            session_id="s1",
            receipts={
                "t1": _receipt("t1", SwarmReceiptStatus.FAILED),
                "t2": _receipt("t2", SwarmReceiptStatus.FAILED),
            },
            tasks={"t1": _task("t1", "cap.same"), "t2": _task("t2", "cap.same")},
        )
        assert manifest.failed_capabilities == ["cap.same"]

    async def test_degraded_refs_enter_with_disclosure_prefix(self):
        agg = SwarmAggregator(sink=NullSink())
        manifest = await agg.collect(
            run_id="r1",
            session_id="s1",
            receipts={
                "t1": _receipt(
                    "t1", SwarmReceiptStatus.DEGRADED, refs=["ref:d"], degraded=True
                )
            },
            tasks={"t1": _task("t1", "cap.deg")},
        )
        assert [e.ref_id for e in manifest.entries] == ["ref:d"]
        assert manifest.entries[0].summary.startswith("[degraded]")
        assert manifest.failed_capabilities == ["cap.deg"]

    async def test_receipt_without_task_row_still_disclosed(self):
        agg = SwarmAggregator(sink=NullSink())
        manifest = await agg.collect(
            run_id="r1",
            session_id="s1",
            receipts={"ghost": _receipt("ghost", SwarmReceiptStatus.FAILED)},
            tasks={},
        )
        assert manifest.entries == []
        assert manifest.failed_capabilities == []  # 无 task → 无 capability 可披露
        assert manifest.dropped_refs == []

    async def test_entries_capped_and_overflow_dropped(self):
        agg = SwarmAggregator(sink=NullSink())
        receipts = {
            f"t{i}": _receipt(f"t{i}", SwarmReceiptStatus.SUCCEEDED, refs=[f"ref:r{i}"])
            for i in range(MAX_MANIFEST_ENTRIES + 5)
        }
        manifest = await agg.collect(
            run_id="r1", session_id="s1", receipts=receipts, tasks={}
        )
        assert len(manifest.entries) == MAX_MANIFEST_ENTRIES
        assert len(manifest.dropped_refs) == 5

    async def test_missing_refs_pruned_via_store_probe(self):
        store = FakeStore()
        agg = SwarmAggregator(sink=NullSink(), store=store)
        manifest = await agg.collect(
            run_id="r1",
            session_id="s1",
            receipts={"t1": _receipt("t1", SwarmReceiptStatus.SUCCEEDED, refs=["ref:ghost"])},
            tasks={"t1": _task("t1", "cap.a")},
        )
        assert manifest.entries == []
        assert manifest.dropped_refs == ["ref:ghost"]

    async def test_present_refs_survive_store_probe(self):
        store = FakeStore()
        await store.store("s1", {"x": 1}, prefix="data")
        ref = f"ref:data-{store._seq}"
        agg = SwarmAggregator(sink=NullSink(), store=store)
        manifest = await agg.collect(
            run_id="r1",
            session_id="s1",
            receipts={"t1": _receipt("t1", SwarmReceiptStatus.SUCCEEDED, refs=[ref])},
            tasks={"t1": _task("t1", "cap.a")},
        )
        assert [e.ref_id for e in manifest.entries] == [ref]

    async def test_store_probe_exception_fails_open_keep(self):
        store = RaisingStore()
        agg = SwarmAggregator(sink=NullSink(), store=store)
        manifest = await agg.collect(
            run_id="r1",
            session_id="s1",
            receipts={"t1": _receipt("t1", SwarmReceiptStatus.SUCCEEDED, refs=["ref:a"])},
            tasks={"t1": _task("t1", "cap.a")},
        )
        assert [e.ref_id for e in manifest.entries] == ["ref:a"]
        assert manifest.dropped_refs == []

    async def test_store_without_ref_exists_probe_keeps_refs(self):
        class _NoProbeStore:
            pass

        agg = SwarmAggregator(sink=NullSink(), store=_NoProbeStore())
        manifest = await agg.collect(
            run_id="r1",
            session_id="s1",
            receipts={"t1": _receipt("t1", SwarmReceiptStatus.SUCCEEDED, refs=["ref:a"])},
            tasks={"t1": _task("t1", "cap.a")},
        )
        assert [e.ref_id for e in manifest.entries] == ["ref:a"]

    async def test_manifest_identity_fields(self):
        agg = SwarmAggregator(sink=NullSink())
        manifest = await agg.collect(
            run_id="run-x",
            session_id="sess-y",
            receipts={"t1": _receipt("t1", SwarmReceiptStatus.SUCCEEDED, refs=["ref:a"])},
            tasks={"t1": _task("t1", "cap.a")},
        )
        assert manifest.run_id == "run-x"
        assert manifest.session_id == "sess-y"
        bounded = manifest.to_bounded_dict()
        assert bounded["run_id"] == "run-x"
        assert bounded["entries"][0]["ref_id"] == "ref:a"


class TestMergeFailOpen:
    """merge：sink 异常只披露，不影响调用方。"""

    async def test_null_sink_returns_none(self):
        agg = SwarmAggregator(sink=NullSink())
        ref = await agg.merge(SwarmAssetManifest(run_id="r1", session_id="s1"))
        assert ref is None

    async def test_sink_exception_is_swallowed(self):
        class _BrokenSink:
            async def merge(self, manifest: Any) -> Optional[str]:
                raise RuntimeError("plan store down")

        agg = SwarmAggregator(sink=_BrokenSink())
        ref = await agg.merge(SwarmAssetManifest(run_id="r1", session_id="s1"))
        assert ref is None  # fail-open：异常不向上

    async def test_sink_success_returns_ref(self):
        class _OkSink:
            async def merge(self, manifest: Any) -> str:
                return "ref:swarm-ok"

        agg = SwarmAggregator(sink=_OkSink())
        ref = await agg.merge(SwarmAssetManifest(run_id="r1", session_id="s1"))
        assert ref == "ref:swarm-ok"

    async def test_default_sink_is_session_plan_sink(self):
        agg = SwarmAggregator()
        assert isinstance(agg._sink, SessionPlanSwarmSink)


class TestSessionPlanSinkUpsert:
    """CapabilityProgress upsert：幂等更新而非重复追加。"""

    def test_upsert_appends_new_progress_row(self):
        plan = type("Plan", (), {"progress": []})()
        SessionPlanSwarmSink._upsert_progress(
            plan, SwarmAssetEntry(ref_id="ref:a", capability="cap.a"), _ProgressRow
        )
        assert len(plan.progress) == 1
        assert plan.progress[0].capability == "cap.a"
        assert plan.progress[0].status == "complete"
        assert plan.progress[0].bound_ref == "ref:a"

    def test_upsert_updates_existing_row_in_place(self):
        plan = type("Plan", (), {"progress": [_ProgressRow(capability="cap.a", status="pending", bound_ref="")]})()
        SessionPlanSwarmSink._upsert_progress(
            plan, SwarmAssetEntry(ref_id="ref:b", capability="cap.a"), _ProgressRow
        )
        assert len(plan.progress) == 1
        assert plan.progress[0].status == "complete"
        assert plan.progress[0].bound_ref == "ref:b"

    def test_upsert_distinct_capabilities_append(self):
        plan = type("Plan", (), {"progress": []})()
        SessionPlanSwarmSink._upsert_progress(plan, SwarmAssetEntry(ref_id="ref:a", capability="cap.a"), _ProgressRow)
        SessionPlanSwarmSink._upsert_progress(plan, SwarmAssetEntry(ref_id="ref:b", capability="cap.b"), _ProgressRow)
        assert [row.capability for row in plan.progress] == ["cap.a", "cap.b"]

    async def test_merge_with_fake_store_stores_manifest(self):
        """SessionPlanSwarmSink 全链路（会话锁 + 计划 upsert + manifest 落 store）。"""
        store = FakeStore()
        sink = SessionPlanSwarmSink(store=store)
        manifest = SwarmAssetManifest(
            run_id="r1",
            session_id="s1",
            entries=[SwarmAssetEntry(ref_id="ref:a", capability="cap.a")],
        )
        ref = await sink.merge(manifest)
        assert ref is not None and ref.startswith("ref:swarm-")
        stored = store.data[f"s1:{ref}"]
        assert stored["run_id"] == "r1"
        assert stored["entries"][0]["ref_id"] == "ref:a"
        # 计划回写面：CapabilityProgress upsert 落 session-plan 信封
        plan_ref = await store.resolve_alias("s1", "session-plan")
        plan_payload = store.data[f"s1:{plan_ref}"]
        progress = {row["capability"]: row for row in plan_payload["progress"]}
        assert progress["cap.a"]["status"] == "complete"
        assert progress["cap.a"]["bound_ref"] == "ref:a"



class _ProgressRow:
    """CapabilityProgress 结构替身（字段同名即可驱动 upsert）。"""

    def __init__(self, capability: str, status: str, bound_ref: str) -> None:
        self.capability = capability
        self.status = status
        self.bound_ref = bound_ref
