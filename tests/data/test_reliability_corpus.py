"""Reliability Corpus（§三十四）—— V3 数据流回归案例集。

每条案例对应一个真实故障场景；断言的是「系统诚实且自洽」：
故障要么被显式报告，要么被安全回滚/降级，绝不静默伪造。
"""
import pytest

from app.lib.data.artifact_contract import from_artifact_record
from app.lib.data.fingerprints import ChangeClass, FingerprintSet, classify_change
from app.lib.data.versioning import SourceRevision, compare_revisions
from app.services.artifact_registry import (
    get_artifact,
    list_artifacts,
    register_artifact,
    update_record_metadata,
)
from app.services.data_ingest.pipeline import get_ingest_pipeline, reset_ingest_pipeline
from app.services.data_lifecycle.gc import plan_session_gc
from app.services.data_lifecycle.service import get_lifecycle_service
from app.services.session_data import session_data_manager
from app.services.workspace.snapshot import (
    get_workspace_snapshot_service,
    reset_workspace_snapshot_service,
)


def _fc(n=2, tag="a"):
    return {
        "type": "FeatureCollection",
        "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0 + i * 0.1, 39.0]},
             "properties": {"v": f"{tag}{i}"}}
            for i in range(n)
        ],
    }


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_ingest_pipeline()
    reset_workspace_snapshot_service()
    yield
    reset_ingest_pipeline()
    reset_workspace_snapshot_service()


class TestIngestReliability:
    async def test_duplicate_import_deduped(self):
        """§34 duplicate import：同一文件两次导入 = 一个逻辑对象。"""
        sid = "rc-dup"
        pipe = get_ingest_pipeline()
        r1 = await pipe.ingest(sid, _fc())
        r2 = await pipe.ingest(sid, _fc())
        assert r2.duplicate and r2.duplicate_of == r1.ref_id
        assert len(await list_artifacts(sid)) == 1

    async def test_interrupted_ingest_rolls_back(self, monkeypatch):
        """§34 interrupted ingest：store 后崩溃 → ref 被补偿删除。"""
        sid = "rc-interrupt"
        from app.services import artifact_registry as ar

        async def boom(*a, **kw):
            raise RuntimeError("crash between store and register")

        monkeypatch.setattr(ar, "register_artifact", boom)
        result = await get_ingest_pipeline().ingest(sid, _fc())
        assert not result.ok
        assert "ROLLED_BACK" in result.error_code
        assert not await session_data_manager.ref_exists(sid, result.ref_id)

    async def test_changed_file_same_name_is_new_artifact(self):
        """§34 changed file same name：同名不同内容 → 新产物 + 旧产物可感知。"""
        sid = "rc-samename"
        pipe = get_ingest_pipeline()
        r1 = await pipe.ingest(sid, _fc(tag="old"), name="same.geojson")
        r2 = await pipe.ingest(sid, _fc(tag="new"), name="same.geojson")
        assert r1.ref_id != r2.ref_id
        # 旧产物的修订证据允许变更检测（token 比较可判 CONTENT）
        svc = get_lifecycle_service()
        rec = await get_artifact(sid, r1.ref_id)
        change = await svc.detect_source_change(
            sid, r1.ref_id,
            recorded=SourceRevision(revision_token=rec.metadata.get("ingest_content_sha256", "x")),
        )
        assert change in (ChangeClass.CONTENT, ChangeClass.UNKNOWN)  # 绝非静默 valid


class TestStalenessReliability:
    async def test_stale_derived_after_upstream_overwrite(self):
        """§34 stale derived artifact：上游覆写 → 下游传播 stale。"""
        sid = "rc-stale"
        fc = _fc(3)
        ref = await session_data_manager.store(sid, fc, prefix="geojson")
        await register_artifact(sid, artifact_id=ref, producer_tool="t")
        await register_artifact(
            sid, artifact_id="ref:geojson-derived", producer_tool="kde", inputs=[ref]
        )
        # 上游覆写（revision bump）→ 传播
        await session_data_manager.overwrite(sid, ref, _fc(3, tag="changed"))
        svc = get_lifecycle_service()
        change = await svc.detect_source_change(sid, ref)
        report = await svc.propagate_staleness(sid, ref, change=change)
        assert report.verdict == "recompute"
        assert "ref:geojson-derived" in report.marked

    async def test_deleted_source_propagates_invalid(self):
        """§34 deleted source：上游载荷消失 → 下游判 invalid（而非照旧）。"""
        sid = "rc-deleted"
        ref = await session_data_manager.store(sid, _fc(), prefix="geojson")
        await register_artifact(sid, artifact_id=ref, producer_tool="t")
        await register_artifact(
            sid, artifact_id="ref:geojson-child", producer_tool="t", inputs=[ref]
        )
        impact = await get_lifecycle_service().impact_analysis(
            sid, ref, change=ChangeClass.NONE, upstream_alive=False
        )
        assert impact["verdict"] == "invalid"

    async def test_reuse_misses_after_schema_change(self):
        """§34 changed field type：schema 指纹变化 → recompute 裁决。"""
        old = FingerprintSet(content="c", schema="schema:v1", metadata="m")
        new = FingerprintSet(content="c", schema="schema:v2", metadata="m")
        assert classify_change(old, new) is ChangeClass.SCHEMA

    async def test_missing_dependency_in_lineage_is_tolerated(self):
        """§34 missing dependency：账本引用了不存在的上游 → 图不炸、如实降级。"""
        sid = "rc-missing-dep"
        await register_artifact(
            sid, artifact_id="ref:geojson-orphan", producer_tool="t",
            inputs=["ref:geojson-vanished"],
        )
        from app.services.data_catalog.lineage_query import session_lineage

        view = await session_lineage(sid, "ref:geojson-orphan")
        assert view is not None
        assert view.parents == []          # 缺失上游不进图
        contract = from_artifact_record(await get_artifact(sid, "ref:geojson-orphan"))
        assert contract.lineage.parents == ["ref:geojson-vanished"]  # 契约如实保留


class TestGcReliability:
    async def test_orphan_intermediate_planned_and_collected(self):
        """§34 orphan intermediate：无引用的 stale 中间产物可被安全回收。"""
        sid = "rc-orphan"
        ref = await session_data_manager.store(sid, _fc(), prefix="geojson")
        await register_artifact(sid, artifact_id=ref, producer_tool="t")
        orphan = await session_data_manager.store(sid, _fc(tag="orphan"), prefix="geojson")
        await register_artifact(sid, artifact_id=orphan, producer_tool="t")
        await update_record_metadata(sid, orphan, status="stale")
        plan = await plan_session_gc(sid)
        assert orphan in plan.candidate_ids
        assert ref not in plan.candidate_ids  # valid 不动
        from app.services.data_lifecycle.gc import execute_session_gc

        deleted = await execute_session_gc(sid)
        assert orphan in deleted


class TestWorkspaceReliability:
    async def test_workspace_reload_restores_ledger(self):
        """§34 workspace reload：账本 TTL 后，快照 register 恢复血缘与状态。"""
        sid = "rc-reload"
        ref = await session_data_manager.store(sid, _fc(), prefix="geojson")
        await register_artifact(
            sid, artifact_id=ref, producer_tool="query", artifact_type="poi_feature_set"
        )
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid, label="before-reload")
        # 模拟重启：清账本
        ledger_ref = await session_data_manager.resolve_alias(sid, "artifacts")
        if ledger_ref != "artifacts":
            await session_data_manager.delete_ref(sid, ledger_ref)
        assert await list_artifacts(sid) == []
        result = await svc.restore_snapshot(sid, snap.snapshot_id, mode="register")
        assert result["registered"] == 1
        rec = await get_artifact(sid, ref)
        assert rec is not None
        assert rec.artifact_type == "poi_feature_set"
        assert rec.metadata["restored_from"] == snap.snapshot_id

    async def test_replay_with_missing_artifact_reports_honestly(self):
        """§34 replay with missing artifact：载荷已失 → verify 如实报 missing。"""
        sid = "rc-replay"
        await register_artifact(
            sid, artifact_id="ref:geojson-gone", producer_tool="t",
            metadata={"size_bytes": 100},
        )
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid)
        verification = await svc.verify_snapshot(sid, snap.snapshot_id)
        assert verification.exists
        assert "ref:geojson-gone" in verification.artifacts_missing
        assert verification.restorable is False
